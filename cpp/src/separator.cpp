#include "bt/separator.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <exception>
#include <string>

namespace bt {
namespace {

Audio stereo(const Audio& input) {
    if (input.channels == 2) {
        return input;
    }
    Audio output;
    output.sample_rate = input.sample_rate;
    output.channels = 2;
    output.frames = input.frames;
    output.samples.assign(static_cast<std::size_t>(2 * input.frames), 0.f);
    const std::int64_t frames = input.frames;
    if (input.channels == 1) {
        std::copy_n(input.samples.data(), frames, output.samples.data());
        std::copy_n(input.samples.data(), frames, output.samples.data() + frames);
        return output;
    }
    const int keep = std::min(input.channels, 2);
    for (int channel = 0; channel < keep; ++channel) {
        std::copy_n(input.samples.data() + channel * frames, frames, output.samples.data() + channel * frames);
    }
    return output;
}

struct Norm {
    std::vector<float> samples;
    float mean = 0.f;
    float deviation = 1.f;
};

Norm normalize_file(const Audio& stereo_audio) {
    const std::int64_t frames = stereo_audio.frames;
    const float* left = stereo_audio.samples.data();
    const float* right = left + frames;
    double sum = 0.0;
    for (std::int64_t i = 0; i < frames; ++i) {
        sum += 0.5 * (static_cast<double>(left[i]) + static_cast<double>(right[i]));
    }
    const double mean = frames > 0 ? sum / static_cast<double>(frames) : 0.0;
    double square = 0.0;
    for (std::int64_t i = 0; i < frames; ++i) {
        const double value = 0.5 * (static_cast<double>(left[i]) + static_cast<double>(right[i])) - mean;
        square += value * value;
    }
    const double denom = frames > 1 ? static_cast<double>(frames - 1) : 1.0;
    const float deviation = static_cast<float>(std::sqrt(square / denom) + 1e-8);

    Norm norm;
    norm.mean = static_cast<float>(mean);
    norm.deviation = deviation;
    norm.samples.resize(stereo_audio.samples.size());
    for (std::size_t i = 0; i < stereo_audio.samples.size(); ++i) {
        norm.samples[i] = (stereo_audio.samples[i] - norm.mean) / deviation;
    }
    return norm;
}

}  // namespace

std::vector<float> triangular_weight(int segment_samples) {
    if (segment_samples < 2) {
        return {};
    }
    std::vector<float> weight(static_cast<std::size_t>(segment_samples));
    const int half = segment_samples / 2;
    int cursor = 0;
    for (int value = 1; value <= half; ++value) {
        weight[static_cast<std::size_t>(cursor++)] = static_cast<float>(value);
    }
    for (int value = segment_samples - half; value >= 1; --value) {
        weight[static_cast<std::size_t>(cursor++)] = static_cast<float>(value);
    }
    const float peak = *std::max_element(weight.begin(), weight.end());
    for (float& value : weight) {
        value /= peak;
    }
    return weight;
}

std::int64_t pad_segment(
    const float* mix, std::int64_t frames, std::int64_t offset, int segment_samples, float* padded) {
    const std::int64_t chunk_length = std::min<std::int64_t>(segment_samples, frames - offset);
    const std::int64_t delta = segment_samples - chunk_length;
    const std::int64_t start = offset - delta / 2;
    const std::int64_t end = start + segment_samples;
    const std::int64_t src_start = std::max<std::int64_t>(0, start);
    const std::int64_t src_end = std::min(frames, end);
    const std::int64_t dst_start = src_start - start;
    const std::int64_t copy_count = src_end - src_start;
    std::fill_n(padded, static_cast<std::size_t>(2 * segment_samples), 0.f);
    for (int channel = 0; channel < 2; ++channel) {
        const float* src = mix + channel * frames + src_start;
        float* dst = padded + channel * segment_samples + dst_start;
        std::copy_n(src, copy_count, dst);
    }
    return chunk_length;
}

std::expected<Stems, std::string> separate_audio(
    const Audio& audio, const SegmentRunner& run, const SeparateConfig& config,
    const std::function<void(float)>& progress, const std::function<bool()>& cancelled) {
    if (!run) {
        return std::unexpected("segment runner is empty");
    }
    if (!(config.overlap >= 0.f && config.overlap < 1.f)) {
        return std::unexpected("overlap must be in [0, 1)");
    }
    if (config.segment_samples < 2) {
        return std::unexpected("segment is too short");
    }
    if (audio.sample_rate != kSampleRate) {
        return std::unexpected("separate_audio expects 44100 Hz audio");
    }
    if (audio.frames <= 0 || audio.channels <= 0) {
        return std::unexpected("audio has no samples");
    }

    const Audio mix = stereo(audio);
    const Norm norm = normalize_file(mix);
    const std::int64_t frames = mix.frames;
    const int segment = config.segment_samples;
    const std::int64_t stride = std::max<std::int64_t>(1, static_cast<std::int64_t>((1.0 - config.overlap) * segment));
    const std::vector<float> weight = triangular_weight(segment);
    if (static_cast<int>(weight.size()) != segment) {
        return std::unexpected("triangular window does not cover the segment");
    }

    std::vector<std::int64_t> offsets;
    for (std::int64_t offset = 0; offset < frames; offset += stride) {
        offsets.push_back(offset);
    }

    std::vector<float> accum(static_cast<std::size_t>(kStemCount * 2 * frames), 0.f);
    std::vector<float> sum_weight(static_cast<std::size_t>(frames), 0.f);
    std::vector<float> padded(static_cast<std::size_t>(2 * segment), 0.f);
    std::vector<float> raw(static_cast<std::size_t>(kStemCount * 2 * segment), 0.f);

    for (std::size_t index = 0; index < offsets.size(); ++index) {
        if (cancelled && cancelled()) {
            return std::unexpected(std::string(kSeparationCancelled));
        }
        const std::int64_t offset = offsets[index];
        const std::int64_t chunk_length = pad_segment(norm.samples.data(), frames, offset, segment, padded.data());
        std::fill(raw.begin(), raw.end(), 0.f);
        run(padded.data(), raw.data(), segment);

        const std::int64_t delta = segment - chunk_length;
        const std::int64_t trim = delta / 2;
        for (int stem = 0; stem < kStemCount; ++stem) {
            for (int channel = 0; channel < 2; ++channel) {
                const float* src = raw.data() + (static_cast<std::int64_t>(stem * 2 + channel) * segment) + trim;
                float* dst = accum.data() + (static_cast<std::int64_t>(stem * 2 + channel) * frames) + offset;
                for (std::int64_t i = 0; i < chunk_length; ++i) {
                    dst[i] += src[i] * weight[static_cast<std::size_t>(i)];
                }
            }
        }
        for (std::int64_t i = 0; i < chunk_length; ++i) {
            sum_weight[static_cast<std::size_t>(offset + i)] += weight[static_cast<std::size_t>(i)];
        }
        if (progress) {
            progress(static_cast<float>(index + 1) / static_cast<float>(offsets.size()));
        }
    }

    for (float value : sum_weight) {
        if (!(value > 0.f)) {
            return std::unexpected("overlap-add left a sample with zero weight");
        }
    }

    Stems stems;
    stems.frames = frames;
    for (int stem = 0; stem < kStemCount; ++stem) {
        stems.audio[static_cast<std::size_t>(stem)].resize(static_cast<std::size_t>(2 * frames));
        for (int channel = 0; channel < 2; ++channel) {
            const float* src = accum.data() + (static_cast<std::int64_t>(stem * 2 + channel) * frames);
            float* dst = stems.audio[static_cast<std::size_t>(stem)].data() + channel * frames;
            for (std::int64_t i = 0; i < frames; ++i) {
                dst[i] = src[i] / sum_weight[static_cast<std::size_t>(i)] * norm.deviation + norm.mean;
            }
        }
    }
    return stems;
}

std::expected<void, std::string> write_stems(const std::filesystem::path& directory, const Stems& stems) {
    std::error_code error;
    std::filesystem::create_directories(directory, error);
    if (error) {
        std::string name = "path";
        try {
            const std::u8string utf8 = directory.u8string();
            name.assign(reinterpret_cast<const char*>(utf8.data()), utf8.size());
        } catch (const std::exception&) {
        }
        return std::unexpected("cannot create " + name);
    }
    for (int stem = 0; stem < kStemCount; ++stem) {
        const auto path = directory / (std::string(kStemNames[static_cast<std::size_t>(stem)]) + ".wav");
        auto written = write_float_wav(path, stems.audio[static_cast<std::size_t>(stem)].data(), 2, stems.frames);
        if (!written) {
            return written;
        }
    }
    return {};
}

}  // namespace bt
