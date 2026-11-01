#include "bt/basic_pitch.hpp"

#include <onnxruntime_c_api.h>
#include <soxr.h>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <fstream>
#include <iomanip>
#include <system_error>
#include <utility>

namespace bt {
namespace {

constexpr int kPitchRate = 22050;
constexpr int kHop = 256;
constexpr int kBins = 88;
constexpr int kMidiOffset = 21;
constexpr int kWindowFrames = (kPitchRate / kHop) * 2;
constexpr int kAudioSamples = kPitchRate * 2 - kHop;
constexpr int kOverlapFrames = 30;
constexpr int kOverlapSamples = kOverlapFrames * kHop;
constexpr int kHopSamples = kAudioSamples - kOverlapSamples;
constexpr int kEnergyTolerance = 11;
constexpr int kMaxFreqIndex = 87;
constexpr double kWindowOffset =
    (static_cast<double>(kHop) / kPitchRate) * (static_cast<double>(kWindowFrames) - (static_cast<double>(kAudioSamples) / kHop)) +
    0.0018;

std::string status_message(const OrtApi* api, OrtStatus* status) {
    if (status == nullptr) {
        return {};
    }
    std::string message = api->GetErrorMessage(status);
    api->ReleaseStatus(status);
    return message;
}

int index_of(int time, int bin) {
    return time * kBins + bin;
}

double hz_to_midi(float hz) {
    return 69.0 + 12.0 * std::log2(static_cast<double>(hz) / 440.0);
}

double frame_time(int frame) {
    const double original = static_cast<double>(frame) * static_cast<double>(kHop) / kPitchRate;
    const double window = std::floor(static_cast<double>(frame) / static_cast<double>(kWindowFrames));
    return std::max(0.0, original - kWindowOffset * window);
}

void constrain_frequency(std::vector<float>& onsets, std::vector<float>& frames, int time_count, const PitchParameters& parameters) {
    int minimum = 0;
    int maximum = kBins;
    if (parameters.minimum_frequency > 0.f) {
        minimum = std::clamp(static_cast<int>(std::lround(hz_to_midi(parameters.minimum_frequency) - kMidiOffset)), 0, kBins);
    }
    if (parameters.maximum_frequency > 0.f) {
        maximum = std::clamp(static_cast<int>(std::lround(hz_to_midi(parameters.maximum_frequency) - kMidiOffset)), 0, kBins);
    }
    for (int time = 0; time < time_count; ++time) {
        for (int bin = 0; bin < minimum; ++bin) {
            onsets[static_cast<std::size_t>(index_of(time, bin))] = 0.f;
            frames[static_cast<std::size_t>(index_of(time, bin))] = 0.f;
        }
        for (int bin = maximum; bin < kBins; ++bin) {
            onsets[static_cast<std::size_t>(index_of(time, bin))] = 0.f;
            frames[static_cast<std::size_t>(index_of(time, bin))] = 0.f;
        }
    }
}

void infer_onsets(std::vector<float>& onsets, const std::vector<float>& frames, int time_count) {
    constexpr int kDiff = 2;
    std::vector<float> frame_diff(static_cast<std::size_t>(time_count * kBins), 0.f);
    float max_onset = 0.f;
    for (float value : onsets) {
        max_onset = std::max(max_onset, value);
    }
    float max_diff = 0.f;
    for (int time = 0; time < time_count; ++time) {
        for (int bin = 0; bin < kBins; ++bin) {
            float lowest = 1.e9f;
            const float current = frames[static_cast<std::size_t>(index_of(time, bin))];
            for (int lag = 1; lag <= kDiff; ++lag) {
                const float previous = time >= lag ? frames[static_cast<std::size_t>(index_of(time - lag, bin))] : 0.f;
                lowest = std::min(lowest, current - previous);
            }
            float value = std::max(0.f, lowest);
            if (time < kDiff) {
                value = 0.f;
            }
            frame_diff[static_cast<std::size_t>(index_of(time, bin))] = value;
            max_diff = std::max(max_diff, value);
        }
    }
    if (max_diff > 0.f) {
        for (float& value : frame_diff) {
            value = max_onset * value / max_diff;
        }
    }
    for (std::size_t i = 0; i < onsets.size(); ++i) {
        onsets[i] = std::max(onsets[i], frame_diff[i]);
    }
}

void clear_neighbors(std::vector<float>& energy, int time, int bin) {
    energy[static_cast<std::size_t>(index_of(time, bin))] = 0.f;
    if (bin < kMaxFreqIndex) {
        energy[static_cast<std::size_t>(index_of(time, bin + 1))] = 0.f;
    }
    if (bin > 0) {
        energy[static_cast<std::size_t>(index_of(time, bin - 1))] = 0.f;
    }
}

float mean_column(const std::vector<float>& frames, int start, int end, int bin) {
    if (end <= start) {
        return 0.f;
    }
    double sum = 0;
    for (int time = start; time < end; ++time) {
        sum += frames[static_cast<std::size_t>(index_of(time, bin))];
    }
    return static_cast<float>(sum / static_cast<double>(end - start));
}

struct OrtValueGuard {
    const OrtApi* api = nullptr;
    OrtValue* value = nullptr;
    ~OrtValueGuard() {
        if (api != nullptr && value != nullptr) {
            api->ReleaseValue(value);
        }
    }
};

struct TensorShape {
    std::vector<int64_t> dims;
};

std::expected<TensorShape, std::string> tensor_shape(const OrtApi* api, OrtValue* value) {
    OrtTensorTypeAndShapeInfo* info = nullptr;
    if (auto message = status_message(api, api->GetTensorTypeAndShape(value, &info)); !message.empty()) {
        return std::unexpected(message);
    }
    size_t count = 0;
    if (auto message = status_message(api, api->GetDimensionsCount(info, &count)); !message.empty()) {
        api->ReleaseTensorTypeAndShapeInfo(info);
        return std::unexpected(message);
    }
    TensorShape shape;
    shape.dims.resize(count);
    if (auto message = status_message(api, api->GetDimensions(info, shape.dims.data(), count)); !message.empty()) {
        api->ReleaseTensorTypeAndShapeInfo(info);
        return std::unexpected(message);
    }
    api->ReleaseTensorTypeAndShapeInfo(info);
    return shape;
}

std::vector<float> trim_window(const float* data, int time_count, int bins) {
    const int drop = kOverlapFrames / 2;
    int start = 0;
    int end = time_count;
    if (time_count > drop * 2) {
        start = drop;
        end = time_count - drop;
    }
    std::vector<float> kept(static_cast<std::size_t>((end - start) * bins));
    std::copy_n(data + static_cast<std::size_t>(start * bins), kept.size(), kept.begin());
    return kept;
}

}  // namespace

PitchParameters pitch_parameters_for_stem(int stem) {
    PitchParameters parameters;
    if (stem == 1) {
        parameters.minimum_frequency = 30.f;
        parameters.maximum_frequency = 400.f;
    } else if (stem == 3) {
        parameters.melodia = true;
    }
    return parameters;
}

std::vector<Note> notes_from_activations(const PitchActivations& activations, const PitchParameters& parameters) {
    if (activations.frames <= 0 || activations.note == nullptr || activations.onset == nullptr) {
        return {};
    }
    const int time_count = activations.frames;
    const std::size_t cells = static_cast<std::size_t>(time_count) * static_cast<std::size_t>(kBins);
    std::vector<float> frames(activations.note, activations.note + cells);
    std::vector<float> onsets(activations.onset, activations.onset + cells);
    constrain_frequency(onsets, frames, time_count, parameters);
    infer_onsets(onsets, frames, time_count);

    std::vector<std::pair<int, int>> peaks;
    if (time_count >= 3) {
        for (int time = 1; time < time_count - 1; ++time) {
            for (int bin = 0; bin < kBins; ++bin) {
                const float value = onsets[static_cast<std::size_t>(index_of(time, bin))];
                const float previous = onsets[static_cast<std::size_t>(index_of(time - 1, bin))];
                const float next = onsets[static_cast<std::size_t>(index_of(time + 1, bin))];
                if (value >= parameters.onset_threshold && value > previous && value > next) {
                    peaks.emplace_back(time, bin);
                }
            }
        }
    }
    std::reverse(peaks.begin(), peaks.end());

    std::vector<float> remaining = frames;
    const int min_note_len = std::max(0, static_cast<int>(std::lround(
        static_cast<double>(parameters.minimum_note_ms) / 1000.0 * (static_cast<double>(kPitchRate) / kHop))));
    std::vector<std::tuple<int, int, int, float>> events;
    auto push_note = [&](int start, int end, int bin) {
        if (end - start <= min_note_len || bin < 0 || bin >= kBins) {
            return;
        }
        events.emplace_back(start, end, bin + kMidiOffset, mean_column(frames, start, end, bin));
    };

    for (const auto& [start, bin] : peaks) {
        if (start >= time_count - 1) {
            continue;
        }
        int time = start + 1;
        int below = 0;
        while (time < time_count - 1 && below < kEnergyTolerance) {
            if (remaining[static_cast<std::size_t>(index_of(time, bin))] < parameters.frame_threshold) {
                ++below;
            } else {
                below = 0;
            }
            ++time;
        }
        time -= below;
        if (time - start <= min_note_len) {
            continue;
        }
        for (int cursor = start; cursor < time; ++cursor) {
            clear_neighbors(remaining, cursor, bin);
        }
        push_note(start, time, bin);
    }

    if (parameters.melodia) {
        while (true) {
            const auto peak = std::max_element(remaining.begin(), remaining.end());
            if (peak == remaining.end() || *peak <= parameters.frame_threshold) {
                break;
            }
            const int flat = static_cast<int>(peak - remaining.begin());
            const int mid = flat / kBins;
            const int bin = flat % kBins;
            remaining[static_cast<std::size_t>(flat)] = 0.f;

            int time = mid + 1;
            int below = 0;
            while (time < time_count - 1 && below < kEnergyTolerance) {
                if (remaining[static_cast<std::size_t>(index_of(time, bin))] < parameters.frame_threshold) {
                    ++below;
                } else {
                    below = 0;
                }
                clear_neighbors(remaining, time, bin);
                ++time;
            }
            const int end = time - 1 - below;

            time = mid - 1;
            below = 0;
            while (time > 0 && below < kEnergyTolerance) {
                if (remaining[static_cast<std::size_t>(index_of(time, bin))] < parameters.frame_threshold) {
                    ++below;
                } else {
                    below = 0;
                }
                clear_neighbors(remaining, time, bin);
                --time;
            }
            const int note_start = time + 1 + below;
            if (note_start < 0 || end >= time_count || end - note_start <= min_note_len) {
                continue;
            }
            push_note(note_start, end, bin);
        }
    }

    std::vector<Note> notes;
    notes.reserve(events.size());
    for (const auto& [start, end, pitch, amplitude] : events) {
        const double start_time = frame_time(start);
        const double end_time = frame_time(end);
        if (!(end_time > start_time)) {
            continue;
        }
        int velocity = static_cast<int>(std::lround(static_cast<double>(amplitude) * 127.0));
        velocity = std::clamp(velocity, 1, 127);
        notes.push_back(Note{pitch, start_time, end_time, velocity});
    }
    std::stable_sort(notes.begin(), notes.end(), [](const Note& left, const Note& right) {
        return left.start < right.start;
    });
    return notes;
}

std::expected<void, std::string> save_notes(const std::filesystem::path& path, const std::vector<Note>& notes) {
    std::error_code error;
    std::filesystem::create_directories(path.parent_path(), error);
    const auto temporary = path.parent_path() / (path.filename().wstring() + L".tmp");
    {
        std::ofstream output(temporary, std::ios::trunc);
        if (!output) {
            return std::unexpected("cannot write notes cache");
        }
        output << "bt-notes 1\n";
        output << std::setprecision(17);
        for (const Note& note : notes) {
            output << note.pitch << ' ' << note.start << ' ' << note.end << ' ' << note.velocity << '\n';
        }
        if (!output) {
            std::filesystem::remove(temporary, error);
            return std::unexpected("cannot write notes cache");
        }
    }
    std::filesystem::rename(temporary, path, error);
    if (error) {
        std::filesystem::remove(path, error);
        std::filesystem::rename(temporary, path, error);
    }
    if (error) {
        std::filesystem::remove(temporary, error);
        return std::unexpected("cannot commit notes cache");
    }
    return {};
}

std::expected<std::vector<Note>, std::string> load_notes(const std::filesystem::path& path) {
    std::ifstream input(path);
    if (!input) {
        return std::unexpected("cannot read notes cache");
    }
    std::string header;
    std::getline(input, header);
    if (header != "bt-notes 1") {
        return std::unexpected("unrecognized notes cache");
    }
    std::vector<Note> notes;
    Note note;
    while (input >> note.pitch >> note.start >> note.end >> note.velocity) {
        notes.push_back(note);
    }
    return notes;
}

struct BasicPitchModel::Impl {
    const OrtApi* api = nullptr;
    OrtEnv* env = nullptr;
    OrtSessionOptions* options = nullptr;
    OrtSession* session = nullptr;
    OrtMemoryInfo* memory = nullptr;

    ~Impl() {
        if (api == nullptr) {
            return;
        }
        if (memory != nullptr) {
            api->ReleaseMemoryInfo(memory);
        }
        if (session != nullptr) {
            api->ReleaseSession(session);
        }
        if (options != nullptr) {
            api->ReleaseSessionOptions(options);
        }
        if (env != nullptr) {
            api->ReleaseEnv(env);
        }
    }
};

BasicPitchModel::BasicPitchModel(std::unique_ptr<Impl> impl) : impl_(std::move(impl)) {}
BasicPitchModel::BasicPitchModel(BasicPitchModel&&) noexcept = default;
BasicPitchModel& BasicPitchModel::operator=(BasicPitchModel&&) noexcept = default;
BasicPitchModel::~BasicPitchModel() = default;

std::expected<BasicPitchModel, std::string> BasicPitchModel::open(const std::filesystem::path& model_path) {
    const OrtApi* api = OrtGetApiBase()->GetApi(ORT_API_VERSION);
    if (api == nullptr) {
        return std::unexpected("ONNX Runtime API is unavailable");
    }
    const std::wstring path = model_path.wstring();
    auto attempt = [&](GraphOptimizationLevel level) -> std::expected<BasicPitchModel, std::string> {
        auto impl = std::make_unique<Impl>();
        impl->api = api;
        if (auto message = status_message(api, api->CreateEnv(ORT_LOGGING_LEVEL_WARNING, "bt-pitch", &impl->env));
            !message.empty()) {
            return std::unexpected(message);
        }
        if (auto message = status_message(api, api->CreateSessionOptions(&impl->options)); !message.empty()) {
            return std::unexpected(message);
        }
        if (auto message = status_message(api, api->SetSessionGraphOptimizationLevel(impl->options, level));
            !message.empty()) {
            return std::unexpected(message);
        }
        if (level != ORT_ENABLE_ALL) {
            if (auto message = status_message(api, api->DisableMemPattern(impl->options)); !message.empty()) {
                return std::unexpected(message);
            }
            if (auto message = status_message(api, api->DisableCpuMemArena(impl->options)); !message.empty()) {
                return std::unexpected(message);
            }
        }
        if (auto message = status_message(api, api->CreateSession(impl->env, path.c_str(), impl->options, &impl->session));
            !message.empty()) {
            return std::unexpected(message);
        }
        if (auto message = status_message(api, api->CreateCpuMemoryInfo(OrtDeviceAllocator, OrtMemTypeDefault, &impl->memory));
            !message.empty()) {
            return std::unexpected(message);
        }
        return BasicPitchModel(std::move(impl));
    };
    auto opened = attempt(ORT_ENABLE_ALL);
    if (opened || opened.error().find("bad allocation") == std::string::npos) {
        return opened;
    }
    return attempt(ORT_ENABLE_BASIC);
}

std::expected<std::vector<Note>, std::string> BasicPitchModel::transcribe(
    const float* samples, std::int64_t frames, const PitchParameters& parameters,
    const std::function<void(float)>& progress, const std::function<bool()>& cancelled) const {
    if (samples == nullptr || frames <= 0) {
        return std::vector<Note>{};
    }
    const std::int64_t padded_length = frames + kOverlapSamples / 2;
    int windows = 0;
    for (std::int64_t cursor = 0; cursor < padded_length; cursor += kHopSamples) {
        ++windows;
    }
    std::vector<float> note_kept;
    std::vector<float> onset_kept;
    std::vector<float> window(static_cast<std::size_t>(kAudioSamples), 0.f);
    const char* input_names[] = {"serving_default_input_2:0"};
    const char* output_names[] = {"StatefulPartitionedCall:1", "StatefulPartitionedCall:2"};
    const int64_t shape[] = {1, kAudioSamples, 1};
    int done = 0;
    for (std::int64_t cursor = 0; cursor < padded_length; cursor += kHopSamples) {
        if (cancelled && cancelled()) {
            return std::unexpected("cancelled");
        }
        std::fill(window.begin(), window.end(), 0.f);
        for (int sample = 0; sample < kAudioSamples; ++sample) {
            const std::int64_t source = cursor + sample - kOverlapSamples / 2;
            if (source >= 0 && source < frames) {
                window[static_cast<std::size_t>(sample)] = samples[source];
            }
        }
        OrtValueGuard input{impl_->api, nullptr};
        const std::size_t bytes = window.size() * sizeof(float);
        if (auto message = status_message(
                impl_->api, impl_->api->CreateTensorWithDataAsOrtValue(
                    impl_->memory, window.data(), bytes, shape, 3, ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, &input.value));
            !message.empty()) {
            return std::unexpected(message);
        }
        OrtValue* raw_outputs[2] = {nullptr, nullptr};
        if (auto message = status_message(
                impl_->api, impl_->api->Run(
                    impl_->session, nullptr, input_names, &input.value, 1, output_names, 2, raw_outputs));
            !message.empty()) {
            return std::unexpected(message);
        }
        OrtValueGuard note_value{impl_->api, raw_outputs[0]};
        OrtValueGuard onset_value{impl_->api, raw_outputs[1]};
        auto copy_output = [&](OrtValue* value, std::vector<float>& destination) -> std::expected<void, std::string> {
            auto dims = tensor_shape(impl_->api, value);
            if (!dims) {
                return std::unexpected(dims.error());
            }
            int time_count = 0;
            int bins = 0;
            if (dims->dims.size() == 3) {
                time_count = static_cast<int>(dims->dims[1]);
                bins = static_cast<int>(dims->dims[2]);
            } else if (dims->dims.size() == 2) {
                time_count = static_cast<int>(dims->dims[0]);
                bins = static_cast<int>(dims->dims[1]);
            } else {
                return std::unexpected("basic pitch output rank is unsupported");
            }
            if (bins != kBins || time_count <= 0) {
                return std::unexpected("basic pitch output shape is unsupported");
            }
            float* data = nullptr;
            if (auto message = status_message(impl_->api, impl_->api->GetTensorMutableData(value, reinterpret_cast<void**>(&data)));
                !message.empty()) {
                return std::unexpected(message);
            }
            const auto kept = trim_window(data, time_count, bins);
            destination.insert(destination.end(), kept.begin(), kept.end());
            return {};
        };
        if (auto copied = copy_output(note_value.value, note_kept); !copied) {
            return std::unexpected(copied.error());
        }
        if (auto copied = copy_output(onset_value.value, onset_kept); !copied) {
            return std::unexpected(copied.error());
        }
        ++done;
        if (progress && windows > 0) {
            progress(static_cast<float>(done) / static_cast<float>(windows));
        }
    }

    const int kept_frames = static_cast<int>(note_kept.size() / static_cast<std::size_t>(kBins));
    const double expected_windows = static_cast<double>(frames) / static_cast<double>(kHopSamples);
    const int frames_per_window = kWindowFrames - kOverlapFrames;
    const int keep = std::min(kept_frames, static_cast<int>(expected_windows * static_cast<double>(frames_per_window)));
    if (keep <= 0 || onset_kept.size() < note_kept.size()) {
        return std::vector<Note>{};
    }
    return notes_from_activations(PitchActivations{keep, note_kept.data(), onset_kept.data()}, parameters);
}

std::expected<std::vector<float>, std::string> stem_to_basic_pitch_audio(const std::vector<float>& channel_major, std::int64_t frames) {
    if (frames <= 0 || static_cast<std::int64_t>(channel_major.size()) < frames * 2) {
        return std::unexpected("stem audio is incomplete");
    }
    std::vector<float> mono(static_cast<std::size_t>(frames));
    const float* left = channel_major.data();
    const float* right = channel_major.data() + frames;
    for (std::int64_t frame = 0; frame < frames; ++frame) {
        mono[static_cast<std::size_t>(frame)] = 0.5f * (left[frame] + right[frame]);
    }
    const auto capacity = static_cast<std::size_t>(std::llround(static_cast<double>(frames) * kPitchRate / kSampleRate) + 1024);
    std::vector<float> converted(capacity);
    const soxr_io_spec_t io = soxr_io_spec(SOXR_FLOAT32_I, SOXR_FLOAT32_I);
    const soxr_quality_spec_t quality = soxr_quality_spec(SOXR_HQ, 0);
    soxr_error_t error = nullptr;
    soxr_t resampler = soxr_create(kSampleRate, kPitchRate, 1, &error, &io, &quality, nullptr);
    if (resampler == nullptr) {
        return std::unexpected(error == nullptr ? "resample failed" : std::string("resample failed: ") + error);
    }
    std::size_t consumed = 0;
    std::size_t produced = 0;
    error = soxr_process(resampler, mono.data(), static_cast<std::size_t>(frames), &consumed, converted.data(), capacity, &produced);
    std::size_t flushed = 0;
    if (error == nullptr) {
        error = soxr_process(resampler, nullptr, 0, nullptr, converted.data() + produced, capacity - produced, &flushed);
    }
    soxr_delete(resampler);
    if (error != nullptr) {
        return std::unexpected(std::string("resample failed: ") + error);
    }
    produced += flushed;
    converted.resize(produced);
    return converted;
}

}  // namespace bt
