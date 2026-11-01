#include "bt/audio.hpp"
#include "bt/contract.hpp"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <exception>
#include <fstream>
#include <soxr.h>
#include <utility>

#pragma warning(push)
#pragma warning(disable: 4267 4456 4701)
#define DR_WAV_IMPLEMENTATION
#include "dr_wav.h"
#define DR_MP3_IMPLEMENTATION
#include "dr_mp3.h"
#define DR_FLAC_IMPLEMENTATION
#include "dr_flac.h"
#pragma warning(pop)

namespace bt {
namespace {

std::string utf8_of(const std::filesystem::path& path) {
    try {
        const std::u8string utf8 = path.u8string();
        return std::string(reinterpret_cast<const char*>(utf8.data()), utf8.size());
    } catch (const std::exception&) {
        return "path";
    }
}

std::string lower_ext(const std::filesystem::path& path) {
    std::string ext = utf8_of(path.extension());
    std::transform(ext.begin(), ext.end(), ext.begin(), [](unsigned char c) {
        return static_cast<char>(std::tolower(c));
    });
    return ext;
}

std::expected<std::vector<std::byte>, std::string> read_file(const std::filesystem::path& path) {
    std::ifstream input(path, std::ios::binary);
    if (!input) {
        return std::unexpected("cannot open " + utf8_of(path));
    }
    input.seekg(0, std::ios::end);
    const auto bytes = input.tellg();
    if (bytes < 0) {
        return std::unexpected("cannot read " + utf8_of(path));
    }
    input.seekg(0, std::ios::beg);
    std::vector<std::byte> data(static_cast<std::size_t>(bytes));
    if (!data.empty()) {
        input.read(reinterpret_cast<char*>(data.data()), static_cast<std::streamsize>(data.size()));
        if (!input) {
            return std::unexpected("cannot read " + utf8_of(path));
        }
    }
    return data;
}

Audio from_interleaved(const float* interleaved, int channels, std::int64_t frames, int sample_rate) {
    Audio audio;
    audio.sample_rate = sample_rate;
    audio.channels = channels;
    audio.frames = frames;
    audio.samples.assign(static_cast<std::size_t>(channels * frames), 0.f);
    for (int channel = 0; channel < channels; ++channel) {
        float* plane = audio.samples.data() + channel * frames;
        for (std::int64_t frame = 0; frame < frames; ++frame) {
            plane[frame] = interleaved[frame * channels + channel];
        }
    }
    return audio;
}

std::expected<Audio, std::string> decode_wav(const std::vector<std::byte>& bytes) {
    drwav wav{};
    if (!drwav_init_memory(&wav, bytes.data(), bytes.size(), nullptr)) {
        return std::unexpected("wav decode failed");
    }
    const int channels = static_cast<int>(wav.channels);
    const int sample_rate = static_cast<int>(wav.sampleRate);
    const auto frames = static_cast<std::int64_t>(wav.totalPCMFrameCount);
    std::vector<float> interleaved(static_cast<std::size_t>(frames * channels));
    const auto read = drwav_read_pcm_frames_f32(&wav, static_cast<drwav_uint64>(frames), interleaved.data());
    drwav_uninit(&wav);
    if (read != static_cast<drwav_uint64>(frames)) {
        return std::unexpected("wav ended early");
    }
    return from_interleaved(interleaved.data(), channels, frames, sample_rate);
}

// dr_mp3 is not the same decoder as libsndfile's bundled mpg123. Sample-level
// differences show up on some files; the ONNX overlap-add path is compared
// against Python using the resampled float mix, where the PCM is identical.
std::expected<Audio, std::string> decode_mp3(const std::vector<std::byte>& bytes) {
    drmp3 mp3{};
    if (!drmp3_init_memory(&mp3, bytes.data(), bytes.size(), nullptr)) {
        return std::unexpected("mp3 decode failed");
    }
    const int channels = mp3.channels;
    const int sample_rate = mp3.sampleRate;
    std::vector<float> interleaved;
    float chunk[4096 * 8];
    for (;;) {
        const auto got = drmp3_read_pcm_frames_f32(&mp3, 4096, chunk);
        if (got == 0) {
            break;
        }
        interleaved.insert(interleaved.end(), chunk, chunk + got * static_cast<drmp3_uint64>(channels));
    }
    drmp3_uninit(&mp3);
    if (channels <= 0 || interleaved.empty()) {
        return std::unexpected("mp3 contained no audio");
    }
    return from_interleaved(interleaved.data(), channels, static_cast<std::int64_t>(interleaved.size() / channels), sample_rate);
}

std::expected<Audio, std::string> decode_flac(const std::vector<std::byte>& bytes) {
    drflac* flac = drflac_open_memory(bytes.data(), bytes.size(), nullptr);
    if (flac == nullptr) {
        return std::unexpected("flac decode failed");
    }
    const int channels = static_cast<int>(flac->channels);
    const int sample_rate = static_cast<int>(flac->sampleRate);
    const auto frames = static_cast<std::int64_t>(flac->totalPCMFrameCount);
    std::vector<float> interleaved(static_cast<std::size_t>(frames * channels));
    const auto read = drflac_read_pcm_frames_f32(flac, static_cast<drflac_uint64>(frames), interleaved.data());
    drflac_close(flac);
    if (read != static_cast<drflac_uint64>(frames)) {
        return std::unexpected("flac ended early");
    }
    return from_interleaved(interleaved.data(), channels, frames, sample_rate);
}

std::expected<Audio, std::string> resample_hq(const Audio& input) {
    if (input.sample_rate == kSampleRate) {
        return input;
    }
    if (input.sample_rate <= 0 || input.channels <= 0 || input.frames <= 0) {
        return std::unexpected("cannot resample empty audio");
    }

    std::vector<float> interleaved(static_cast<std::size_t>(input.frames * input.channels));
    for (std::int64_t frame = 0; frame < input.frames; ++frame) {
        for (int channel = 0; channel < input.channels; ++channel) {
            interleaved[static_cast<std::size_t>(frame * input.channels + channel)] =
                input.samples[static_cast<std::size_t>(channel * input.frames + frame)];
        }
    }

    const auto capacity = static_cast<std::size_t>(
        std::llround(static_cast<double>(input.frames) * kSampleRate / input.sample_rate) + 1024);
    std::vector<float> converted(capacity * static_cast<std::size_t>(input.channels));
    const soxr_io_spec_t io = soxr_io_spec(SOXR_FLOAT32_I, SOXR_FLOAT32_I);
    const soxr_quality_spec_t quality = soxr_quality_spec(SOXR_HQ, 0);
    soxr_error_t error = nullptr;
    soxr_t resampler = soxr_create(
        input.sample_rate, kSampleRate, static_cast<unsigned>(input.channels),
        &error, &io, &quality, nullptr);
    if (resampler == nullptr) {
        return std::unexpected(error == nullptr ? "resample failed" : std::string("resample failed: ") + error);
    }
    std::size_t consumed = 0;
    std::size_t produced = 0;
    error = soxr_process(
        resampler, interleaved.data(), static_cast<std::size_t>(input.frames), &consumed,
        converted.data(), capacity, &produced);
    std::size_t flushed = 0;
    if (error == nullptr) {
        error = soxr_process(
            resampler, nullptr, 0, nullptr,
            converted.data() + produced * static_cast<std::size_t>(input.channels),
            capacity - produced, &flushed);
    }
    soxr_delete(resampler);
    if (error != nullptr) {
        return std::unexpected(std::string("resample failed: ") + error);
    }
    produced += flushed;

    Audio output;
    output.sample_rate = kSampleRate;
    output.channels = input.channels;
    output.frames = static_cast<std::int64_t>(produced);
    output.samples = std::move(from_interleaved(converted.data(), input.channels, output.frames, kSampleRate).samples);
    return output;
}

}  // namespace

std::expected<Audio, std::string> load_audio(const std::filesystem::path& path) {
    auto bytes = read_file(path);
    if (!bytes) {
        return std::unexpected(bytes.error());
    }
    const std::string ext = lower_ext(path);
    std::expected<Audio, std::string> decoded = std::unexpected("unsupported audio extension " + ext);
    if (ext == ".wav") {
        decoded = decode_wav(*bytes);
    } else if (ext == ".mp3") {
        decoded = decode_mp3(*bytes);
    } else if (ext == ".flac") {
        decoded = decode_flac(*bytes);
    }
    if (!decoded) {
        return decoded;
    }
    if (decoded->frames <= 0 || decoded->channels <= 0) {
        return std::unexpected("audio file has no samples");
    }
    return resample_hq(*decoded);
}

std::expected<void, std::string> write_float_wav(
    const std::filesystem::path& path, const float* channel_major, int channels,
    std::int64_t frames, int sample_rate) {
    if (channels <= 0 || frames < 0 || channel_major == nullptr) {
        return std::unexpected("cannot write empty wav");
    }
    std::vector<float> interleaved(static_cast<std::size_t>(frames * channels));
    for (std::int64_t frame = 0; frame < frames; ++frame) {
        for (int channel = 0; channel < channels; ++channel) {
            interleaved[static_cast<std::size_t>(frame * channels + channel)] =
                channel_major[channel * frames + frame];
        }
    }

    drwav_data_format format{};
    format.container = drwav_container_riff;
    format.format = DR_WAVE_FORMAT_IEEE_FLOAT;
    format.channels = static_cast<drwav_uint32>(channels);
    format.sampleRate = static_cast<drwav_uint32>(sample_rate);
    format.bitsPerSample = 32;
    drwav wav{};
    const std::wstring wide = path.wstring();
    if (!drwav_init_file_write_w(&wav, wide.c_str(), &format, nullptr)) {
        return std::unexpected("cannot write " + utf8_of(path));
    }
    const auto written = drwav_write_pcm_frames(&wav, static_cast<drwav_uint64>(frames), interleaved.data());
    drwav_uninit(&wav);
    if (written != static_cast<drwav_uint64>(frames)) {
        return std::unexpected("short write " + utf8_of(path));
    }
    return {};
}

std::expected<Audio, std::string> read_float_wav(const std::filesystem::path& path) {
    auto bytes = read_file(path);
    if (!bytes) {
        return std::unexpected(bytes.error());
    }
    return decode_wav(*bytes);
}

}  // namespace bt
