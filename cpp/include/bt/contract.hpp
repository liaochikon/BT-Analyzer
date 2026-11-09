#pragma once

#include <array>
#include <cstdint>
#include <string_view>

namespace bt {

inline constexpr int kSampleRate = 44100;
inline constexpr int kSegmentSamples = 343980;
inline constexpr double kSegmentSeconds = 7.8;
inline constexpr float kDefaultOverlap = 0.25f;
inline constexpr int kStemCount = 6;
inline constexpr std::array<std::string_view, kStemCount> kStemNames = {
    "drums", "bass", "other", "vocals", "guitar", "piano"};

inline constexpr std::string_view kInputName = "mix";
inline constexpr std::string_view kOutputName = "stems";
// Project folders for this branch. Kept apart from htdemucs_6s caches.
inline constexpr std::string_view kModelKey = "bs_roformer_sw";

}  // namespace bt
