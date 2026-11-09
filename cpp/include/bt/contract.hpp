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
// Each key is one saves subdirectory. The same song can exist under more than one.
inline constexpr std::array<std::string_view, 2> kSeparationKeys{"bs_roformer_sw", "htdemucs_6s"};
inline constexpr std::string_view kModelKey = kSeparationKeys[0];

inline constexpr bool is_separation_key(std::string_view key) {
    for (const std::string_view known : kSeparationKeys) {
        if (known == key) {
            return true;
        }
    }
    return false;
}

}  // namespace bt
