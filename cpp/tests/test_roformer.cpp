#include "bt/roformer.hpp"

#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_floating_point.hpp>

#include <algorithm>
#include <cmath>
#include <numbers>
#include <vector>

namespace {

bt::Audio ramp(std::int64_t frames) {
    bt::Audio audio;
    audio.sample_rate = bt::kSampleRate;
    audio.channels = 2;
    audio.frames = frames;
    audio.samples.resize(static_cast<std::size_t>(2 * frames));
    for (std::int64_t i = 0; i < frames; ++i) {
        const float value = -0.8f + 1.6f * static_cast<float>(i) / static_cast<float>(frames);
        audio.samples[static_cast<std::size_t>(i)] = value;
        audio.samples[static_cast<std::size_t>(frames + i)] = value * -0.35f + 0.1f;
    }
    return audio;
}

std::expected<void, std::string> copy_mix(const float* mix, float* stems, int chunk_samples) {
    for (int stem = 0; stem < bt::kStemCount; ++stem) {
        std::copy_n(mix, 2 * chunk_samples, stems + static_cast<std::size_t>(stem) * 2 * chunk_samples);
    }
    return {};
}

}  // namespace

TEST_CASE("roformer stem order maps onto the mixer") {
    REQUIRE(bt::kRoformerStemToApp[0] == 1);
    REQUIRE(bt::kRoformerStemToApp[1] == 0);
    REQUIRE(bt::kRoformerStemToApp[2] == 2);
    REQUIRE(bt::kRoformerStemToApp[3] == 3);
    REQUIRE(bt::kRoformerStemToApp[4] == 4);
    REQUIRE(bt::kRoformerStemToApp[5] == 5);
    REQUIRE(bt::kStemNames[bt::kRoformerStemToApp[0]] == "bass");
    REQUIRE(bt::kStemNames[bt::kRoformerStemToApp[1]] == "drums");
}

TEST_CASE("rfft matches the real DFT sign") {
    constexpr int n = bt::kRoformerFft;
    std::vector<float> ones(static_cast<std::size_t>(n), 1.f);
    std::vector<float> real(static_cast<std::size_t>(bt::kRoformerBins));
    std::vector<float> imag(real.size());
    bt::rfft_2048(ones.data(), real.data(), imag.data());
    REQUIRE_THAT(real[0], Catch::Matchers::WithinAbs(static_cast<double>(n), 1e-3));
    REQUIRE_THAT(imag[0], Catch::Matchers::WithinAbs(0.0, 1e-3));
    REQUIRE_THAT(real[1], Catch::Matchers::WithinAbs(0.0, 1e-2));

    const double pi = std::numbers::pi;
    std::vector<float> sine(static_cast<std::size_t>(n));
    for (int i = 0; i < n; ++i) {
        sine[static_cast<std::size_t>(i)] = static_cast<float>(std::sin(2.0 * pi * i / n));
    }
    bt::rfft_2048(sine.data(), real.data(), imag.data());
    REQUIRE_THAT(real[1], Catch::Matchers::WithinAbs(0.0, 1e-2));
    REQUIRE_THAT(imag[1], Catch::Matchers::WithinAbs(-0.5 * n, 1e-2));
    REQUIRE_THAT(real[0], Catch::Matchers::WithinAbs(0.0, 1e-2));
}

TEST_CASE("stft roundtrip keeps the waveform") {
    constexpr int frames = bt::kRoformerHop * 16;
    const double pi = std::numbers::pi;
    std::vector<float> signal(static_cast<std::size_t>(frames));
    for (int i = 0; i < frames; ++i) {
        const float ramp = static_cast<float>(i) / static_cast<float>(frames) - 0.5f;
        signal[static_cast<std::size_t>(i)] = ramp + 0.25f * static_cast<float>(std::sin(2.0 * pi * 5.0 * i / frames));
    }
    const auto back = bt::roformer_stft_roundtrip(signal.data(), frames);
    REQUIRE(back.size() == static_cast<std::size_t>(frames));
    for (int i = 0; i < frames; ++i) {
        REQUIRE_THAT(back[static_cast<std::size_t>(i)], Catch::Matchers::WithinAbs(signal[static_cast<std::size_t>(i)], 2e-4));
    }
    REQUIRE(bt::roformer_stft_roundtrip(signal.data(), frames - 1).empty());
}

TEST_CASE("roformer overlap-add reconstructs the mix") {
    const int chunk = 100;
    for (const std::int64_t frames : {40, 80, 180, 250}) {
        const auto mix = ramp(frames);
        auto stems = bt::separate_roformer(mix, copy_mix, chunk);
        REQUIRE(stems.has_value());
        REQUIRE(stems->frames == frames);
        for (const auto& stem : stems->audio) {
            REQUIRE(stem.size() == mix.samples.size());
            for (std::size_t i = 0; i < mix.samples.size(); ++i) {
                REQUIRE_THAT(stem[i], Catch::Matchers::WithinAbs(mix.samples[i], 1e-4));
            }
        }
    }
}

TEST_CASE("roformer cancel stops between chunks") {
    int checks = 0;
    auto stopped = bt::separate_roformer(ramp(180), copy_mix, 100, {}, [&] { return ++checks >= 2; });
    REQUIRE_FALSE(stopped.has_value());
    REQUIRE(stopped.error() == bt::kSeparationCancelled);
    REQUIRE(checks == 2);
}

TEST_CASE("roformer rejects an empty mix") {
    bt::Audio empty;
    empty.sample_rate = bt::kSampleRate;
    auto missing = bt::separate_roformer(empty, copy_mix, 100);
    REQUIRE_FALSE(missing.has_value());
}
