#include "bt/separator.hpp"

#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_floating_point.hpp>

#include <algorithm>
#include <cmath>
#include <string>

namespace {

void identity(const float* mix, float* stems, int segment_samples) {
    for (int stem = 0; stem < bt::kStemCount; ++stem) {
        std::copy_n(mix, 2 * segment_samples, stems + static_cast<std::size_t>(stem) * 2 * segment_samples);
    }
}

bt::Audio ramp(std::int64_t frames) {
    bt::Audio audio;
    audio.sample_rate = bt::kSampleRate;
    audio.channels = 2;
    audio.frames = frames;
    audio.samples.resize(static_cast<std::size_t>(2 * frames));
    for (std::int64_t i = 0; i < frames; ++i) {
        const float value = -0.4f + 1.2f * static_cast<float>(i) / static_cast<float>(frames);
        audio.samples[static_cast<std::size_t>(i)] = value;
        audio.samples[static_cast<std::size_t>(frames + i)] = value * 0.5f;
    }
    return audio;
}

}  // namespace

TEST_CASE("contract matches the static htdemucs_6s graph") {
    REQUIRE(bt::kSegmentSamples == 343980);
    REQUIRE(bt::kStemNames[4] == "guitar");
    REQUIRE(bt::kStemNames[5] == "piano");
    REQUIRE(bt::kModelKey == "htdemucs_6s");
}

TEST_CASE("pad segment borrows context and center trim recovers the chunk") {
    const auto mix = ramp(10);
    float padded[2 * 8] = {};
    const auto chunk = bt::pad_segment(mix.samples.data(), mix.frames, 6, 8, padded);
    REQUIRE(chunk == 4);
    const int trim = (8 - static_cast<int>(chunk)) / 2;
    for (int channel = 0; channel < 2; ++channel) {
        for (int i = 0; i < 4; ++i) {
            REQUIRE(padded[channel * 8 + trim + i] == mix.samples[static_cast<std::size_t>(channel * 10 + 6 + i)]);
        }
    }
}

TEST_CASE("triangular window peaks in the middle") {
    const auto weight = bt::triangular_weight(8);
    REQUIRE(weight.size() == 8);
    REQUIRE(weight.front() < weight[3]);
    REQUIRE(weight.back() < weight[4]);
    REQUIRE_THAT(weight[3], Catch::Matchers::WithinAbs(1.0, 1e-6));
}

TEST_CASE("identity segment survives overlap-add and file normalization") {
    const auto mix = ramp(100);
    bt::SeparateConfig config;
    config.segment_samples = 32;
    config.overlap = 0.25f;
    auto stems = bt::separate_audio(mix, identity, config);
    REQUIRE(stems.has_value());
    REQUIRE(stems->frames == mix.frames);
    for (const auto& stem : stems->audio) {
        for (std::size_t i = 0; i < mix.samples.size(); ++i) {
            REQUIRE_THAT(stem[i], Catch::Matchers::WithinAbs(mix.samples[i], 1e-5));
        }
    }
}

TEST_CASE("separate rejects an empty mix and a bad overlap") {
    bt::Audio empty;
    empty.sample_rate = bt::kSampleRate;
    auto missing = bt::separate_audio(empty, identity);
    REQUIRE_FALSE(missing.has_value());

    bt::SeparateConfig config;
    config.segment_samples = 8;
    config.overlap = 1.f;
    auto bad = bt::separate_audio(ramp(8), identity, config);
    REQUIRE_FALSE(bad.has_value());
}

TEST_CASE("separate stops before the next segment when cancelled") {
    const auto mix = ramp(100);
    bt::SeparateConfig config;
    config.segment_samples = 32;
    config.overlap = 0.25f;
    int calls = 0;
    auto stopped = bt::separate_audio(
        mix,
        [&](const float* segment, float* stems, int segment_samples) {
            ++calls;
            identity(segment, stems, segment_samples);
        },
        config, {}, [&] { return calls >= 1; });
    REQUIRE_FALSE(stopped.has_value());
    REQUIRE(stopped.error() == bt::kSeparationCancelled);
    REQUIRE(calls == 1);

    int skipped = 0;
    auto idle = bt::separate_audio(
        mix, [&](const float*, float*, int) { ++skipped; }, config, {}, [] { return true; });
    REQUIRE_FALSE(idle.has_value());
    REQUIRE(idle.error() == bt::kSeparationCancelled);
    REQUIRE(skipped == 0);
}
