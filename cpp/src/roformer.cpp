#include "bt/roformer.hpp"

#include <onnxruntime_c_api.h>

#include <algorithm>
#include <cmath>
#include <cstring>
#include <numbers>
#include <utility>

namespace bt {
namespace {

std::string status_message(const OrtApi* api, OrtStatus* status) {
    if (status == nullptr) {
        return {};
    }
    std::string message = api->GetErrorMessage(status);
    api->ReleaseStatus(status);
    return message;
}

void fft_radix2(std::vector<double>& real, std::vector<double>& imag, bool inverse) {
    const int n = static_cast<int>(real.size());
    for (int i = 1, j = 0; i < n; ++i) {
        int bit = n >> 1;
        for (; (j & bit) != 0; bit >>= 1) {
            j ^= bit;
        }
        j ^= bit;
        if (i < j) {
            std::swap(real[static_cast<std::size_t>(i)], real[static_cast<std::size_t>(j)]);
            std::swap(imag[static_cast<std::size_t>(i)], imag[static_cast<std::size_t>(j)]);
        }
    }
    const double pi = std::numbers::pi;
    for (int len = 2; len <= n; len <<= 1) {
        const double angle = (inverse ? 2.0 : -2.0) * pi / static_cast<double>(len);
        const double wlen_re = std::cos(angle);
        const double wlen_im = std::sin(angle);
        for (int i = 0; i < n; i += len) {
            double w_re = 1.0;
            double w_im = 0.0;
            for (int j = 0; j < len / 2; ++j) {
                const int u = i + j;
                const int v = u + len / 2;
                const double tre = real[static_cast<std::size_t>(v)] * w_re - imag[static_cast<std::size_t>(v)] * w_im;
                const double tim = real[static_cast<std::size_t>(v)] * w_im + imag[static_cast<std::size_t>(v)] * w_re;
                real[static_cast<std::size_t>(v)] = real[static_cast<std::size_t>(u)] - tre;
                imag[static_cast<std::size_t>(v)] = imag[static_cast<std::size_t>(u)] - tim;
                real[static_cast<std::size_t>(u)] += tre;
                imag[static_cast<std::size_t>(u)] += tim;
                const double next_re = w_re * wlen_re - w_im * wlen_im;
                w_im = w_re * wlen_im + w_im * wlen_re;
                w_re = next_re;
            }
        }
    }
    if (inverse) {
        const double scale = 1.0 / static_cast<double>(n);
        for (int i = 0; i < n; ++i) {
            real[static_cast<std::size_t>(i)] *= scale;
            imag[static_cast<std::size_t>(i)] *= scale;
        }
    }
}

const std::vector<double>& hann_window() {
    static const std::vector<double> window = [] {
        std::vector<double> values(static_cast<std::size_t>(kRoformerFft));
        const double pi = std::numbers::pi;
        for (int n = 0; n < kRoformerFft; ++n) {
            values[static_cast<std::size_t>(n)] = 0.5 * (1.0 - std::cos(2.0 * pi * n / kRoformerFft));
        }
        return values;
    }();
    return window;
}

std::int64_t reflect_index(std::int64_t index, std::int64_t length) {
    if (length <= 1) {
        return 0;
    }
    const std::int64_t period = 2 * (length - 1);
    std::int64_t mod = index % period;
    if (mod < 0) {
        mod += period;
    }
    if (mod >= length) {
        mod = period - mod;
    }
    return mod;
}

float reflected_sample(const float* samples, std::int64_t frames, std::int64_t index) {
    if (index >= 0 && index < frames) {
        return samples[static_cast<std::size_t>(index)];
    }
    return samples[static_cast<std::size_t>(reflect_index(index, frames))];
}

void rfft_frame(const double* frame, float* real, float* imag) {
    std::vector<double> re(static_cast<std::size_t>(kRoformerFft));
    std::vector<double> im(static_cast<std::size_t>(kRoformerFft), 0.0);
    std::copy_n(frame, kRoformerFft, re.begin());
    fft_radix2(re, im, false);
    for (int bin = 0; bin < kRoformerBins; ++bin) {
        real[bin] = static_cast<float>(re[static_cast<std::size_t>(bin)]);
        imag[bin] = static_cast<float>(im[static_cast<std::size_t>(bin)]);
    }
}

void stft_channel(const float* samples, int frames, float* real, float* imag, int time_frames) {
    const int pad = kRoformerFft / 2;
    const std::vector<double>& window = hann_window();
    std::vector<double> padded(static_cast<std::size_t>(frames + kRoformerFft));
    for (int i = 0; i < frames + kRoformerFft; ++i) {
        padded[static_cast<std::size_t>(i)] = reflected_sample(samples, frames, static_cast<std::int64_t>(i - pad));
    }
    std::vector<double> frame(static_cast<std::size_t>(kRoformerFft));
    std::vector<float> bin_real(static_cast<std::size_t>(kRoformerBins));
    std::vector<float> bin_imag(static_cast<std::size_t>(kRoformerBins));
    for (int time = 0; time < time_frames; ++time) {
        const int start = time * kRoformerHop;
        for (int n = 0; n < kRoformerFft; ++n) {
            frame[static_cast<std::size_t>(n)] = padded[static_cast<std::size_t>(start + n)] * window[static_cast<std::size_t>(n)];
        }
        rfft_frame(frame.data(), bin_real.data(), bin_imag.data());
        for (int bin = 0; bin < kRoformerBins; ++bin) {
            const std::size_t index = static_cast<std::size_t>(bin * time_frames + time);
            real[index] = bin_real[static_cast<std::size_t>(bin)];
            imag[index] = bin_imag[static_cast<std::size_t>(bin)];
        }
    }
}

std::vector<float> istft_channel(const float* real, const float* imag, int time_frames, int frames, bool zero_dc) {
    const int pad = kRoformerFft / 2;
    const int ola = (time_frames - 1) * kRoformerHop + kRoformerFft;
    const std::vector<double>& window = hann_window();
    std::vector<double> acc(static_cast<std::size_t>(ola), 0.0);
    std::vector<double> env(static_cast<std::size_t>(ola), 0.0);
    std::vector<double> re(static_cast<std::size_t>(kRoformerFft));
    std::vector<double> im(static_cast<std::size_t>(kRoformerFft));
    for (int time = 0; time < time_frames; ++time) {
        std::fill(re.begin(), re.end(), 0.0);
        std::fill(im.begin(), im.end(), 0.0);
        for (int bin = 0; bin < kRoformerBins; ++bin) {
            const std::size_t index = static_cast<std::size_t>(bin * time_frames + time);
            re[static_cast<std::size_t>(bin)] = real[index];
            im[static_cast<std::size_t>(bin)] = imag[index];
        }
        if (zero_dc) {
            re[0] = 0.0;
            im[0] = 0.0;
        }
        im[static_cast<std::size_t>(kRoformerFft / 2)] = 0.0;
        for (int bin = 1; bin < kRoformerFft / 2; ++bin) {
            re[static_cast<std::size_t>(kRoformerFft - bin)] = re[static_cast<std::size_t>(bin)];
            im[static_cast<std::size_t>(kRoformerFft - bin)] = -im[static_cast<std::size_t>(bin)];
        }
        fft_radix2(re, im, true);
        const int offset = time * kRoformerHop;
        for (int n = 0; n < kRoformerFft; ++n) {
            const double weight = window[static_cast<std::size_t>(n)];
            acc[static_cast<std::size_t>(offset + n)] += re[static_cast<std::size_t>(n)] * weight;
            env[static_cast<std::size_t>(offset + n)] += weight * weight;
        }
    }
    std::vector<float> out(static_cast<std::size_t>(frames), 0.f);
    for (int i = 0; i < frames; ++i) {
        const int src = i + pad;
        if (src < 0 || src >= ola) {
            continue;
        }
        const double weight = env[static_cast<std::size_t>(src)];
        if (weight > 1e-8) {
            out[static_cast<std::size_t>(i)] = static_cast<float>(acc[static_cast<std::size_t>(src)] / weight);
        }
    }
    return out;
}

Audio stereo(const Audio& input) {
    if (input.channels == 2) {
        return input;
    }
    Audio output;
    output.sample_rate = input.sample_rate;
    output.channels = 2;
    output.frames = input.frames;
    output.samples.assign(static_cast<std::size_t>(2 * input.frames), 0.f);
    if (input.channels == 1) {
        std::copy_n(input.samples.data(), input.frames, output.samples.data());
        std::copy_n(input.samples.data(), input.frames, output.samples.data() + input.frames);
        return output;
    }
    const int keep = std::min(input.channels, 2);
    for (int channel = 0; channel < keep; ++channel) {
        std::copy_n(
            input.samples.data() + channel * input.frames, input.frames,
            output.samples.data() + channel * output.frames);
    }
    return output;
}

std::vector<float> fade_window(int window_size, int fade_size) {
    std::vector<float> window(static_cast<std::size_t>(window_size), 1.f);
    if (fade_size <= 1 || fade_size > window_size) {
        return window;
    }
    const float denom = static_cast<float>(fade_size - 1);
    for (int i = 0; i < fade_size; ++i) {
        window[static_cast<std::size_t>(window_size - fade_size + i)] = 1.f - static_cast<float>(i) / denom;
    }
    for (int i = 0; i < fade_size; ++i) {
        window[static_cast<std::size_t>(i)] = static_cast<float>(i) / denom;
    }
    return window;
}

struct TensorInfo {
    std::vector<int64_t> dims;
    ONNXTensorElementDataType type = ONNX_TENSOR_ELEMENT_DATA_TYPE_UNDEFINED;
};

std::expected<TensorInfo, std::string> tensor_info(const OrtApi* api, OrtSession* session, bool input, size_t index) {
    OrtTypeInfo* type_info = nullptr;
    OrtStatus* status = input ? api->SessionGetInputTypeInfo(session, index, &type_info)
                              : api->SessionGetOutputTypeInfo(session, index, &type_info);
    if (auto message = status_message(api, status); !message.empty()) {
        return std::unexpected(message);
    }
    const OrtTensorTypeAndShapeInfo* tensor = nullptr;
    status = api->CastTypeInfoToTensorInfo(type_info, &tensor);
    if (auto message = status_message(api, status); !message.empty()) {
        api->ReleaseTypeInfo(type_info);
        return std::unexpected(message);
    }
    if (tensor == nullptr) {
        api->ReleaseTypeInfo(type_info);
        return std::unexpected("BS-RoFormer ONNX value is not a tensor");
    }
    TensorInfo info;
    if (auto message = status_message(api, api->GetTensorElementType(tensor, &info.type)); !message.empty()) {
        api->ReleaseTypeInfo(type_info);
        return std::unexpected(message);
    }
    size_t count = 0;
    if (auto message = status_message(api, api->GetDimensionsCount(tensor, &count)); !message.empty()) {
        api->ReleaseTypeInfo(type_info);
        return std::unexpected(message);
    }
    info.dims.resize(count);
    if (count > 0) {
        if (auto message = status_message(api, api->GetDimensions(tensor, info.dims.data(), count)); !message.empty()) {
            api->ReleaseTypeInfo(type_info);
            return std::unexpected(message);
        }
    }
    api->ReleaseTypeInfo(type_info);
    return info;
}

std::expected<std::string, std::string> tensor_name(
    const OrtApi* api, OrtAllocator* allocator, OrtSession* session, bool input, size_t index) {
    char* name = nullptr;
    OrtStatus* status = input ? api->SessionGetInputName(session, index, allocator, &name)
                              : api->SessionGetOutputName(session, index, allocator, &name);
    if (auto message = status_message(api, status); !message.empty()) {
        return std::unexpected(message);
    }
    std::string owned = name == nullptr ? std::string{} : std::string(name);
    allocator->Free(allocator, name);
    return owned;
}

}  // namespace

void rfft_2048(const float* frame, float* real, float* imag) {
    std::vector<double> samples(static_cast<std::size_t>(kRoformerFft));
    for (int i = 0; i < kRoformerFft; ++i) {
        samples[static_cast<std::size_t>(i)] = frame[i];
    }
    rfft_frame(samples.data(), real, imag);
}

std::vector<float> roformer_stft_roundtrip(const float* samples, int frames) {
    if (samples == nullptr || frames <= 0 || frames % kRoformerHop != 0) {
        return {};
    }
    const int time_frames = frames / kRoformerHop + 1;
    std::vector<float> real(static_cast<std::size_t>(kRoformerBins * time_frames));
    std::vector<float> imag(real.size());
    stft_channel(samples, frames, real.data(), imag.data(), time_frames);
    return istft_channel(real.data(), imag.data(), time_frames, frames, false);
}

std::expected<Stems, std::string> separate_roformer(
    const Audio& audio, const RoformerChunkFn& run, int chunk_samples,
    const std::function<void(float)>& progress, const std::function<bool()>& cancelled) {
    if (!run) {
        return std::unexpected("segment runner is empty");
    }
    if (chunk_samples < 2) {
        return std::unexpected("segment is too short");
    }
    if (audio.sample_rate != kSampleRate) {
        return std::unexpected("separate_audio expects 44100 Hz audio");
    }
    if (audio.frames <= 0 || audio.channels <= 0) {
        return std::unexpected("audio has no samples");
    }

    const Audio mix = stereo(audio);
    const std::int64_t frames = mix.frames;
    const int step = chunk_samples / kRoformerOverlap;
    if (step < 1) {
        return std::unexpected("segment is too short");
    }
    const int fade_size = chunk_samples / 10;
    const std::int64_t border = static_cast<std::int64_t>(chunk_samples - step);
    const bool file_pad = border > 0 && frames > 2 * border;
    const std::int64_t padded_frames = file_pad ? frames + 2 * border : frames;

    std::vector<float> padded(static_cast<std::size_t>(2 * padded_frames), 0.f);
    for (int channel = 0; channel < 2; ++channel) {
        const float* src = mix.samples.data() + channel * frames;
        float* dst = padded.data() + channel * padded_frames;
        for (std::int64_t i = 0; i < padded_frames; ++i) {
            const std::int64_t origin = file_pad ? i - border : i;
            dst[i] = file_pad ? reflected_sample(src, frames, origin) : src[origin];
        }
    }

    std::vector<float> window = fade_window(chunk_samples, fade_size);
    std::vector<std::int64_t> starts;
    for (std::int64_t start = 0; start < padded_frames; start += step) {
        starts.push_back(start);
    }

    std::vector<float> accum(static_cast<std::size_t>(kStemCount * 2 * padded_frames), 0.f);
    std::vector<float> counter(static_cast<std::size_t>(padded_frames), 0.f);
    std::vector<float> chunk(static_cast<std::size_t>(2 * chunk_samples), 0.f);
    std::vector<float> raw(static_cast<std::size_t>(kStemCount * 2 * chunk_samples), 0.f);

    for (std::size_t index = 0; index < starts.size(); ++index) {
        if (cancelled && cancelled()) {
            return std::unexpected(std::string(kSeparationCancelled));
        }
        const std::int64_t start = starts[index];
        const std::int64_t seg_len = std::min<std::int64_t>(chunk_samples, padded_frames - start);
        const bool reflect = seg_len > chunk_samples / 2;
        for (int channel = 0; channel < 2; ++channel) {
            const float* src = padded.data() + channel * padded_frames + start;
            float* dst = chunk.data() + channel * chunk_samples;
            for (int i = 0; i < chunk_samples; ++i) {
                if (i < seg_len) {
                    dst[i] = src[i];
                } else if (reflect) {
                    dst[i] = src[reflect_index(i, seg_len)];
                } else {
                    dst[i] = 0.f;
                }
            }
        }

        std::fill(raw.begin(), raw.end(), 0.f);
        if (auto ran = run(chunk.data(), raw.data(), chunk_samples); !ran) {
            return std::unexpected(ran.error());
        }

        std::vector<float> weight = window;
        if (start == 0) {
            std::fill_n(weight.begin(), static_cast<std::size_t>(std::min(fade_size, chunk_samples)), 1.f);
        }
        if (start + step >= padded_frames) {
            const int tail = std::min(fade_size, chunk_samples);
            std::fill_n(weight.end() - tail, static_cast<std::size_t>(tail), 1.f);
        }
        for (int stem = 0; stem < kStemCount; ++stem) {
            for (int channel = 0; channel < 2; ++channel) {
                const float* src = raw.data() + (static_cast<std::int64_t>(stem * 2 + channel) * chunk_samples);
                float* dst = accum.data() + (static_cast<std::int64_t>(stem * 2 + channel) * padded_frames) + start;
                for (std::int64_t i = 0; i < seg_len; ++i) {
                    dst[i] += src[i] * weight[static_cast<std::size_t>(i)];
                }
            }
        }
        for (std::int64_t i = 0; i < seg_len; ++i) {
            counter[static_cast<std::size_t>(start + i)] += weight[static_cast<std::size_t>(i)];
        }
        if (progress) {
            progress(static_cast<float>(index + 1) / static_cast<float>(starts.size()));
        }
    }

    const std::int64_t trim = file_pad ? border : 0;
    Stems stems;
    stems.frames = frames;
    for (int stem = 0; stem < kStemCount; ++stem) {
        stems.audio[static_cast<std::size_t>(stem)].resize(static_cast<std::size_t>(2 * frames));
        for (int channel = 0; channel < 2; ++channel) {
            const float* src = accum.data() + (static_cast<std::int64_t>(stem * 2 + channel) * padded_frames) + trim;
            const float* weights = counter.data() + trim;
            float* dst = stems.audio[static_cast<std::size_t>(stem)].data() + channel * frames;
            for (std::int64_t i = 0; i < frames; ++i) {
                const float weight = weights[i];
                const float value = weight > 0.f ? src[i] / weight : 0.f;
                dst[i] = std::isfinite(value) ? value : 0.f;
            }
        }
    }
    return stems;
}

struct BsRoformerModel::Impl {
    const OrtApi* api = nullptr;
    OrtEnv* env = nullptr;
    OrtSessionOptions* options = nullptr;
    OrtSession* session = nullptr;
    OrtMemoryInfo* memory = nullptr;
    std::string spec_real = "spec_real";
    std::string spec_imag = "spec_imag";
    std::string out_real = "out_spec_real";
    std::string out_imag = "out_spec_imag";
    int time_frames = 0;
    int chunk_samples = 0;

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

BsRoformerModel::BsRoformerModel(std::unique_ptr<Impl> impl) : impl_(std::move(impl)) {}

BsRoformerModel::BsRoformerModel(BsRoformerModel&&) noexcept = default;
BsRoformerModel& BsRoformerModel::operator=(BsRoformerModel&&) noexcept = default;
BsRoformerModel::~BsRoformerModel() = default;

int BsRoformerModel::chunk_samples() const {
    return impl_->chunk_samples;
}

std::expected<BsRoformerModel, std::string> BsRoformerModel::open(
    const std::filesystem::path& model_path, int intra_op_threads) {
    const OrtApi* api = OrtGetApiBase()->GetApi(ORT_API_VERSION);
    if (api == nullptr) {
        return std::unexpected("ONNX Runtime API is unavailable");
    }
    const std::wstring path = model_path.wstring();
    auto attempt = [&](GraphOptimizationLevel level, bool memory_pattern,
                       int threads) -> std::expected<BsRoformerModel, std::string> {
        auto impl = std::make_unique<Impl>();
        impl->api = api;
        if (auto message = status_message(api, api->CreateEnv(ORT_LOGGING_LEVEL_WARNING, "bt-roformer", &impl->env));
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
        if (!memory_pattern) {
            if (auto message = status_message(api, api->DisableMemPattern(impl->options)); !message.empty()) {
                return std::unexpected(message);
            }
            if (auto message = status_message(api, api->DisableCpuMemArena(impl->options)); !message.empty()) {
                return std::unexpected(message);
            }
        }
        if (threads > 0) {
            if (auto message = status_message(api, api->SetIntraOpNumThreads(impl->options, threads));
                !message.empty()) {
                return std::unexpected(message);
            }
        }
        if (auto message = status_message(api, api->CreateSession(impl->env, path.c_str(), impl->options, &impl->session));
            !message.empty()) {
            return std::unexpected(message);
        }
        if (auto message = status_message(
                api, api->CreateCpuMemoryInfo(OrtDeviceAllocator, OrtMemTypeDefault, &impl->memory));
            !message.empty()) {
            return std::unexpected(message);
        }

        size_t input_count = 0;
        size_t output_count = 0;
        if (auto message = status_message(api, api->SessionGetInputCount(impl->session, &input_count)); !message.empty()) {
            return std::unexpected(message);
        }
        if (auto message = status_message(api, api->SessionGetOutputCount(impl->session, &output_count)); !message.empty()) {
            return std::unexpected(message);
        }
        if (input_count != 2 || output_count != 2) {
            return std::unexpected("BS-RoFormer ONNX must have spec_real, spec_imag, out_spec_real, and out_spec_imag");
        }
        OrtAllocator* allocator = nullptr;
        if (auto message = status_message(api, api->GetAllocatorWithDefaultOptions(&allocator)); !message.empty()) {
            return std::unexpected(message);
        }
        std::string inputs[2];
        std::string outputs[2];
        TensorInfo input_shapes[2];
        TensorInfo output_shapes[2];
        for (size_t index = 0; index < 2; ++index) {
            auto name = tensor_name(api, allocator, impl->session, true, index);
            if (!name) {
                return std::unexpected(name.error());
            }
            inputs[index] = std::move(*name);
            auto shape = tensor_info(api, impl->session, true, index);
            if (!shape) {
                return std::unexpected(shape.error());
            }
            input_shapes[index] = std::move(*shape);
            auto out_name = tensor_name(api, allocator, impl->session, false, index);
            if (!out_name) {
                return std::unexpected(out_name.error());
            }
            outputs[index] = std::move(*out_name);
            auto out_shape = tensor_info(api, impl->session, false, index);
            if (!out_shape) {
                return std::unexpected(out_shape.error());
            }
            output_shapes[index] = std::move(*out_shape);
        }

        auto find_name = [](const std::string* names, const char* wanted) -> int {
            for (int index = 0; index < 2; ++index) {
                if (names[index] == wanted) {
                    return index;
                }
            }
            return -1;
        };
        const int in_real = find_name(inputs, "spec_real");
        const int in_imag = find_name(inputs, "spec_imag");
        const int out_real = find_name(outputs, "out_spec_real");
        const int out_imag = find_name(outputs, "out_spec_imag");
        if (in_real < 0 || in_imag < 0 || out_real < 0 || out_imag < 0) {
            return std::unexpected(
                "BS-RoFormer ONNX inputs must be spec_real and spec_imag, outputs out_spec_real and out_spec_imag");
        }
        const TensorInfo& spec = input_shapes[in_real];
        const TensorInfo& masked = output_shapes[out_real];
        if (spec.type != ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT ||
            input_shapes[in_imag].type != ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT ||
            masked.type != ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT ||
            output_shapes[out_imag].type != ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT) {
            return std::unexpected("BS-RoFormer ONNX spectrograms must be float32");
        }
        if (spec.dims.size() != 4 || input_shapes[in_imag].dims != spec.dims || masked.dims.size() != 5 ||
            output_shapes[out_imag].dims != masked.dims) {
            return std::unexpected("BS-RoFormer ONNX shapes must be [1,2,1025,T] in and [1,6,2,1025,T] out");
        }
        const int64_t time = spec.dims[3];
        const bool shape_ok = (spec.dims[0] == 1 || spec.dims[0] == -1) && spec.dims[1] == 2 &&
            spec.dims[2] == kRoformerBins && time > 1 && (masked.dims[0] == 1 || masked.dims[0] == -1) &&
            masked.dims[1] == kStemCount && masked.dims[2] == 2 && masked.dims[3] == kRoformerBins &&
            masked.dims[4] == time;
        if (!shape_ok) {
            return std::unexpected("BS-RoFormer ONNX shapes must be [1,2,1025,T] in and [1,6,2,1025,T] out");
        }
        impl->time_frames = static_cast<int>(time);
        impl->chunk_samples = (impl->time_frames - 1) * kRoformerHop;
        return BsRoformerModel(std::move(impl));
    };

    const int preferred = intra_op_threads > 0 ? intra_op_threads : 0;
    auto opened = attempt(ORT_ENABLE_ALL, true, preferred);
    if (opened || opened.error().find("bad allocation") == std::string::npos) {
        return opened;
    }
    const int fallback = preferred > 0 ? preferred : 4;
    return attempt(ORT_ENABLE_BASIC, false, fallback);
}

std::expected<void, std::string> BsRoformerModel::run_chunk(const float* mix, float* stems) const {
    const int chunk = impl_->chunk_samples;
    const int time_frames = impl_->time_frames;
    const std::size_t spec_count = static_cast<std::size_t>(2 * kRoformerBins * time_frames);
    std::vector<float> spec_real(spec_count);
    std::vector<float> spec_imag(spec_count);
    for (int channel = 0; channel < 2; ++channel) {
        stft_channel(
            mix + static_cast<std::size_t>(channel) * static_cast<std::size_t>(chunk), chunk,
            spec_real.data() + static_cast<std::size_t>(channel * kRoformerBins * time_frames),
            spec_imag.data() + static_cast<std::size_t>(channel * kRoformerBins * time_frames), time_frames);
    }

    const OrtApi* api = impl_->api;
    const int64_t in_shape[] = {1, 2, kRoformerBins, time_frames};
    const std::size_t bytes = spec_count * sizeof(float);
    OrtValue* real_in = nullptr;
    OrtValue* imag_in = nullptr;
    OrtValue* real_out = nullptr;
    OrtValue* imag_out = nullptr;
    auto release = [&] {
        if (real_out != nullptr) api->ReleaseValue(real_out);
        if (imag_out != nullptr) api->ReleaseValue(imag_out);
        if (real_in != nullptr) api->ReleaseValue(real_in);
        if (imag_in != nullptr) api->ReleaseValue(imag_in);
    };
    if (auto message = status_message(api, api->CreateTensorWithDataAsOrtValue(
            impl_->memory, spec_real.data(), bytes, in_shape, 4, ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, &real_in));
        !message.empty()) {
        release();
        return std::unexpected(message);
    }
    if (auto message = status_message(api, api->CreateTensorWithDataAsOrtValue(
            impl_->memory, spec_imag.data(), bytes, in_shape, 4, ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, &imag_in));
        !message.empty()) {
        release();
        return std::unexpected(message);
    }

    const char* input_names[] = {impl_->spec_real.c_str(), impl_->spec_imag.c_str()};
    const char* output_names[] = {impl_->out_real.c_str(), impl_->out_imag.c_str()};
    const OrtValue* inputs[] = {real_in, imag_in};
    OrtValue* outputs[] = {nullptr, nullptr};
    const std::string ran = status_message(api, api->Run(
        impl_->session, nullptr, input_names, inputs, 2, output_names, 2, outputs));
    real_out = outputs[0];
    imag_out = outputs[1];
    if (!ran.empty()) {
        release();
        return std::unexpected(ran);
    }
    float* out_real = nullptr;
    float* out_imag = nullptr;
    if (auto message = status_message(api, api->GetTensorMutableData(real_out, reinterpret_cast<void**>(&out_real)));
        !message.empty()) {
        release();
        return std::unexpected(message);
    }
    if (auto message = status_message(api, api->GetTensorMutableData(imag_out, reinterpret_cast<void**>(&out_imag)));
        !message.empty()) {
        release();
        return std::unexpected(message);
    }

    const std::size_t plane = static_cast<std::size_t>(kRoformerBins * time_frames);
    for (int model_stem = 0; model_stem < kStemCount; ++model_stem) {
        const int app_stem = kRoformerStemToApp[static_cast<std::size_t>(model_stem)];
        for (int channel = 0; channel < 2; ++channel) {
            const std::size_t offset = (static_cast<std::size_t>(model_stem * 2 + channel)) * plane;
            const std::vector<float> wave = istft_channel(
                out_real + offset, out_imag + offset, time_frames, chunk, true);
            float* dst = stems + static_cast<std::size_t>(app_stem * 2 + channel) * static_cast<std::size_t>(chunk);
            std::copy(wave.begin(), wave.end(), dst);
        }
    }
    release();
    return {};
}

std::expected<Stems, std::string> BsRoformerModel::separate(
    const Audio& audio, const std::function<void(float)>& progress, const std::function<bool()>& cancelled) const {
    const int chunk = impl_->chunk_samples;
    return separate_roformer(
        audio,
        [&](const float* mix, float* stems, int samples) -> std::expected<void, std::string> {
            if (samples != chunk) {
                return std::unexpected("chunk length does not match the ONNX graph");
            }
            return run_chunk(mix, stems);
        },
        chunk, progress, cancelled);
}

}  // namespace bt
