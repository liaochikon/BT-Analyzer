#pragma once

#include "bt/contract.hpp"

#include <cstdint>
#include <expected>
#include <filesystem>
#include <string>
#include <vector>

namespace bt {

// Channel-major float32. `samples[channel * frames + index]`.
struct Audio {
    int sample_rate = 0;
    int channels = 0;
    std::int64_t frames = 0;
    std::vector<float> samples;
};

// Decode wav, flac, or mp3 and resample to 44.1 kHz. Channel count is preserved.
std::expected<Audio, std::string> load_audio(const std::filesystem::path& path);

std::expected<void, std::string> write_float_wav(
    const std::filesystem::path& path, const float* channel_major, int channels,
    std::int64_t frames, int sample_rate = kSampleRate);

std::expected<Audio, std::string> read_float_wav(const std::filesystem::path& path);

}  // namespace bt
