#pragma once

#include "bt/audio.hpp"
#include "bt/contract.hpp"

#include <array>
#include <expected>
#include <filesystem>
#include <functional>
#include <string>
#include <string_view>
#include <vector>

namespace bt {

struct Stems {
    std::int64_t frames = 0;
    // Each entry is stereo channel-major audio of `frames` samples.
    std::array<std::vector<float>, kStemCount> audio;
};

// mix is stereo channel-major of `segment_samples` frames.
// stems is (stem, channel, sample), contiguous, length kStemCount * 2 * segment_samples.
using SegmentRunner = std::function<void(const float* mix, float* stems, int segment_samples)>;

struct SeparateConfig {
    float overlap = kDefaultOverlap;
    int segment_samples = kSegmentSamples;
};

// Returned by separate_audio when `cancelled` becomes true between segments.
inline constexpr std::string_view kSeparationCancelled = "cancelled";

// Host half of htdemucs_6s: stereo fold, file normalization, triangular overlap-add.
// `audio` must already be 44.1 kHz. The runner sees one static segment at a time.
// `cancelled` is checked before each segment and does not interrupt the one already running.
std::expected<Stems, std::string> separate_audio(
    const Audio& audio,
    const SegmentRunner& run,
    const SeparateConfig& config = {},
    const std::function<void(float)>& progress = {},
    const std::function<bool()>& cancelled = {});

std::expected<void, std::string> write_stems(
    const std::filesystem::path& directory, const Stems& stems);

std::vector<float> triangular_weight(int segment_samples);

// Writes a stereo channel-major segment. Returns the unpadded chunk length.
std::int64_t pad_segment(
    const float* mix, std::int64_t frames, std::int64_t offset, int segment_samples, float* padded);

}  // namespace bt
