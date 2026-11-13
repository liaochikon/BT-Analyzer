#include "bt/pitch_mask.hpp"
#include "bt/player.hpp"
#include "bt/time_stretch.hpp"

#include <cmath>
#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_floating_point.hpp>

#include <algorithm>
#include <array>
#include <memory>
#include <vector>

namespace {

std::shared_ptr<bt::Stems> constant_song(std::int64_t frames, float value, int stem) {
    auto stems = std::make_shared<bt::Stems>();
    stems->frames = frames;
    for (auto& audio : stems->audio) {
        audio.assign(static_cast<std::size_t>(frames * 2), 0.f);
    }
    std::fill(
        stems->audio[static_cast<std::size_t>(stem)].begin(),
        stems->audio[static_cast<std::size_t>(stem)].end(), value);
    return stems;
}

}  // namespace

TEST_CASE("a constant survives pitch-preserving stretch at unity") {
    bt::TimeStretcher stretcher;
    constexpr int kFrames = 44100;
    stretcher.set_source([](int start, int frames, float* interleaved) {
        if (start >= kFrames) {
            return 0;
        }
        const int count = std::min(frames, kFrames - start);
        for (int i = 0; i < count; ++i) {
            interleaved[i * 2] = 0.5f;
            interleaved[i * 2 + 1] = 0.5f;
        }
        return count;
    });
    stretcher.set_rate(1.f);
    stretcher.reset(0);
    std::vector<float> output(static_cast<std::size_t>(8192 * 2));
    const int count = stretcher.pull(output.data(), 8192);
    REQUIRE(count == 8192);
    REQUIRE_THAT(output[4000 * 2], Catch::Matchers::WithinAbs(0.5, 1e-3));
    REQUIRE_THAT(output[4000 * 2 + 1], Catch::Matchers::WithinAbs(0.5, 1e-3));
}

TEST_CASE("double speed walks the source about twice as fast") {
    bt::TimeStretcher stretcher;
    constexpr int kFrames = 200000;
    stretcher.set_source([](int start, int frames, float* interleaved) {
        if (start >= kFrames) {
            return 0;
        }
        const int count = std::min(frames, kFrames - start);
        for (int i = 0; i < count; ++i) {
            const float value = 0.2f * ((start + i) % 50) / 50.f;
            interleaved[i * 2] = value;
            interleaved[i * 2 + 1] = value;
        }
        return count;
    });
    stretcher.set_rate(2.f);
    stretcher.reset(0);
    std::vector<float> output(static_cast<std::size_t>(22050 * 2));
    REQUIRE(stretcher.pull(output.data(), 22050) == 22050);
    REQUIRE(stretcher.position() > 40000);
    REQUIRE(stretcher.position() < 50000);
}

TEST_CASE("player mixes solo and mute on the song timeline") {
    auto stems = std::make_shared<bt::Stems>();
    constexpr std::int64_t kFrames = 44100;
    stems->frames = kFrames;
    for (auto& audio : stems->audio) {
        audio.assign(static_cast<std::size_t>(kFrames * 2), 0.f);
    }
    std::fill(stems->audio[0].begin(), stems->audio[0].end(), 0.2f);
    std::fill(stems->audio[1].begin(), stems->audio[1].end(), 0.5f);

    bt::Player player;
    player.set_song(stems);
    std::array<float, bt::kStemCount> gain{};
    gain.fill(1.f);
    std::array<bool, bt::kStemCount> mute{};
    std::array<bool, bt::kStemCount> solo{};
    solo[1] = true;
    player.set_mix(gain, mute, solo);
    player.play();

    std::vector<float> output(static_cast<std::size_t>(8192 * 2));
    REQUIRE(player.render(output.data(), 8192) == 8192);
    REQUIRE_THAT(output[5000 * 2], Catch::Matchers::WithinAbs(0.5, 1e-2));

    mute[1] = true;
    player.set_mix(gain, mute, solo);
    player.seek(0.05);
    REQUIRE(player.render(output.data(), 4096) > 0);
    REQUIRE_THAT(output[3000 * 2], Catch::Matchers::WithinAbs(0.0, 1e-3));

    player.pause();
    player.seek(0.4);
    player.render(output.data(), 32);
    REQUIRE_THAT(player.snapshot().position, Catch::Matchers::WithinAbs(0.4, 1e-6));
    REQUIRE_FALSE(player.snapshot().playing);
}

TEST_CASE("a loop returns the playhead to A") {
    bt::Player player;
    player.set_song(constant_song(44100, 0.25f, 0));
    std::array<float, bt::kStemCount> gain{};
    gain.fill(1.f);
    player.set_mix(gain, {}, {});
    player.set_loop_a(0.05);
    player.set_loop_b(0.15);
    player.set_loop_enabled(true);
    player.seek(0.14);
    player.play();
    std::vector<float> output(static_cast<std::size_t>(4410 * 2));
    REQUIRE(player.render(output.data(), 4410) > 0);
    REQUIRE(player.snapshot().position < 0.15);
    REQUIRE(player.snapshot().position >= 0.05);
}

TEST_CASE("count in plays four beats then starts") {
    bt::Player player;
    player.set_song(constant_song(44100 * 4, 0.2f, 0));
    std::array<float, bt::kStemCount> gain{};
    gain.fill(1.f);
    player.set_mix(gain, {}, {});
    std::vector<float> silence(64 * 2);
    player.render(silence.data(), 64);
    player.seek(1.0);
    player.count_in(4);
    REQUIRE(player.snapshot().counting);
    REQUIRE_FALSE(player.snapshot().playing);

    const int interval = 22050;
    std::vector<float> count(static_cast<std::size_t>(interval * 4 * 2));
    REQUIRE(player.render(count.data(), interval * 4) == interval * 4);
    REQUIRE(std::fabs(count[0]) > 0.01f);
    REQUIRE(std::fabs(count[static_cast<std::size_t>(interval * 2)]) > 0.01f);
    REQUIRE(player.snapshot().counting);
    REQUIRE_FALSE(player.snapshot().playing);
    REQUIRE_THAT(player.snapshot().position, Catch::Matchers::WithinAbs(1.0, 1e-3));

    std::vector<float> song(64 * 2);
    player.render(song.data(), 64);
    REQUIRE(player.snapshot().playing);
    REQUIRE_FALSE(player.snapshot().counting);
    REQUIRE(player.snapshot().position > 1.0);

    player.pause();
    player.seek(1.0);
    player.count_in(4);
    player.render(count.data(), 1000);
    player.pause();
    player.render(silence.data(), 64);
    REQUIRE_FALSE(player.snapshot().counting);
    REQUIRE_FALSE(player.snapshot().playing);
    REQUIRE_THAT(player.snapshot().position, Catch::Matchers::WithinAbs(1.0, 1e-3));
}

TEST_CASE("count in follows the beat at the playhead") {
    bt::Player player;
    player.set_song(constant_song(44100 * 8, 0.2f, 0));
    std::array<float, bt::kStemCount> gain{};
    gain.fill(1.f);
    player.set_mix(gain, {}, {});
    std::vector<float> silence(32 * 2);
    player.render(silence.data(), 32);
    auto times = std::make_shared<const std::vector<double>>(std::vector<double>{0.0, 1.0, 2.0});
    player.set_metronome(false, 120.f, 0.f, 0.0, true, times);
    player.seek(1.0);
    player.count_in(4);
    std::vector<float> count(static_cast<std::size_t>(44100 * 4 * 2));
    player.render(count.data(), 44100 * 4);
    REQUIRE(player.snapshot().counting);
    REQUIRE_FALSE(player.snapshot().playing);
}

TEST_CASE("stop returns to A while looping") {
    bt::Player player;
    player.set_song(constant_song(44100, 0.25f, 0));
    std::array<float, bt::kStemCount> gain{};
    gain.fill(1.f);
    player.set_mix(gain, {}, {});
    player.set_loop_a(0.2);
    player.set_loop_b(0.4);
    player.set_loop_enabled(true);
    player.seek(0.3);
    player.play();
    player.stop();
    REQUIRE_FALSE(player.snapshot().playing);
    REQUIRE_THAT(player.snapshot().position, Catch::Matchers::WithinAbs(0.2, 1e-6));

    player.set_loop_enabled(false);
    player.seek(0.3);
    player.stop();
    REQUIRE_THAT(player.snapshot().position, Catch::Matchers::WithinAbs(0.0, 1e-6));
}

TEST_CASE("metronome clicks on song beats without following master or speed") {
    bt::Player player;
    player.set_song(constant_song(44100 * 2, 0.f, 0));
    std::array<float, bt::kStemCount> gain{};
    gain.fill(1.f);
    player.set_mix(gain, {}, {});
    player.set_master_db(-60.f);
    player.set_metronome(true, 120.f, 0.f, 0.0);
    player.play();

    std::vector<float> output(static_cast<std::size_t>(44100 * 2));
    REQUIRE(player.render(output.data(), 44100) == 44100);
    auto energy = [&](int frame) {
        return std::fabs(output[static_cast<std::size_t>(frame) * 2]) +
               std::fabs(output[static_cast<std::size_t>(frame) * 2 + 1]);
    };
    REQUIRE(energy(80) > 0.02f);
    REQUIRE(energy(8000) < 0.005f);
    REQUIRE(energy(22050 + 80) > 0.02f);

    player.set_metronome(true, 120.f, 0.f, 0.5);
    player.seek(0);
    REQUIRE(player.render(output.data(), 44100) == 44100);
    REQUIRE(energy(80) < 0.005f);
    REQUIRE(energy(22050 + 80) > 0.02f);

    auto times = std::make_shared<const std::vector<double>>(std::vector<double>{0.0, 0.75});
    player.set_metronome(true, 120.f, 0.f, 0.0, true, times);
    player.seek(0);
    REQUIRE(player.render(output.data(), 44100) == 44100);
    REQUIRE(energy(80) > 0.02f);
    REQUIRE(energy(22050) < 0.005f);
    REQUIRE(energy(33075 + 80) > 0.02f);

    player.set_metronome(true, 120.f, 0.f, 0.0);
    player.set_speed(2.f);
    player.seek(0);
    REQUIRE(player.render(output.data(), 22050) == 22050);
    REQUIRE(energy(80) > 0.02f);
    REQUIRE(energy(11025 + 80) > 0.02f);
    REQUIRE(energy(4000) < 0.005f);
}

TEST_CASE("hard right pan keeps the left channel silent") {
    bt::Player player;
    player.set_song(constant_song(44100, 0.5f, 0));
    std::array<float, bt::kStemCount> gain{};
    gain.fill(1.f);
    std::array<float, bt::kStemCount> pan{};
    pan[0] = 1.f;
    player.set_mix(gain, {}, {}, pan);
    player.play();
    std::vector<float> output(static_cast<std::size_t>(2048 * 2));
    REQUIRE(player.render(output.data(), 2048) == 2048);
    REQUIRE_THAT(output[1000 * 2], Catch::Matchers::WithinAbs(0.0, 1e-3));
    REQUIRE(std::fabs(output[1000 * 2 + 1]) > 0.4f);
}

TEST_CASE("pitch mask highpass removes DC on the selected stem") {
    bt::Player player;
    player.set_song(constant_song(44100, 0.5f, 0));
    std::array<float, bt::kStemCount> gain{};
    gain.fill(1.f);
    player.set_mix(gain, {}, {});
    player.set_pitch_mask(true, 0, 40, 80, bt::Player::PitchMaskBackend::Dsp);
    player.play();
    std::vector<float> warm(static_cast<std::size_t>(8192 * 2));
    REQUIRE(player.render(warm.data(), 8192) == 8192);
    std::vector<float> output(static_cast<std::size_t>(4096 * 2));
    REQUIRE(player.render(output.data(), 4096) == 4096);
    REQUIRE_THAT(output[2000 * 2], Catch::Matchers::WithinAbs(0.0, 2e-2));
    REQUIRE_THAT(output[2000 * 2 + 1], Catch::Matchers::WithinAbs(0.0, 2e-2));

    player.set_pitch_mask(true, 1, 40, 80, bt::Player::PitchMaskBackend::Dsp);
    player.seek(0);
    REQUIRE(player.render(warm.data(), 8192) == 8192);
    REQUIRE(player.render(output.data(), 4096) == 4096);
    REQUIRE(std::fabs(output[2000 * 2]) > 0.4f);
}

TEST_CASE("harmonic pitch mask keeps note partials") {
    constexpr int kFrames = 44100;
    std::vector<float> mono(static_cast<std::size_t>(kFrames));
    for (int i = 0; i < kFrames; ++i) {
        const float t = static_cast<float>(i) / static_cast<float>(bt::kSampleRate);
        // A4 (440) with 2nd harmonic, plus an unrelated 100 Hz tone.
        mono[static_cast<std::size_t>(i)] =
            0.45f * std::sin(2.f * 3.14159265f * 440.f * t) +
            0.25f * std::sin(2.f * 3.14159265f * 880.f * t) +
            0.45f * std::sin(2.f * 3.14159265f * 100.f * t);
    }
    std::vector<bt::PitchMaskNote> notes{bt::PitchMaskNote{69, 0.0, 1.0}};
    std::vector<float> filtered(static_cast<std::size_t>(kFrames));
    bt::spectral_harmonic_mono(
        mono.data(), filtered.data(), kFrames, 60, 76, notes.data(), notes.size());

    auto energy = [](const std::vector<float>& x, float hz) {
        double re = 0;
        double im = 0;
        for (int i = 0; i < static_cast<int>(x.size()); ++i) {
            const double phase =
                2.0 * 3.141592653589793 * static_cast<double>(hz) * static_cast<double>(i) /
                bt::kSampleRate;
            re += static_cast<double>(x[static_cast<std::size_t>(i)]) * std::cos(phase);
            im += static_cast<double>(x[static_cast<std::size_t>(i)]) * std::sin(phase);
        }
        return std::sqrt(re * re + im * im) / static_cast<double>(x.size());
    };

    REQUIRE(energy(filtered, 100.f) < energy(mono, 100.f) * 0.2);
    REQUIRE(energy(filtered, 440.f) > energy(mono, 440.f) * 0.7);
    REQUIRE(energy(filtered, 880.f) > energy(mono, 880.f) * 0.7);

    std::vector<float> empty_out(static_cast<std::size_t>(kFrames), 1.f);
    bt::spectral_harmonic_mono(mono.data(), empty_out.data(), kFrames, 60, 76, nullptr, 0);
    REQUIRE_THAT(empty_out[1000], Catch::Matchers::WithinAbs(0.0, 1e-6));
}

TEST_CASE("spectral pitch mask removes out-of-band energy") {
    constexpr int kFrames = 44100;
    std::vector<float> mono(static_cast<std::size_t>(kFrames));
    for (int i = 0; i < kFrames; ++i) {
        const float t = static_cast<float>(i) / static_cast<float>(bt::kSampleRate);
        mono[static_cast<std::size_t>(i)] =
            0.5f * std::sin(2.f * 3.14159265f * 100.f * t) +
            0.5f * std::sin(2.f * 3.14159265f * 880.f * t);
    }
    std::vector<float> filtered(static_cast<std::size_t>(kFrames));
    // Keep around A5 (880 Hz); reject 100 Hz.
    bt::spectral_brickwall_mono(mono.data(), filtered.data(), kFrames, 80, 84);

    auto energy = [](const std::vector<float>& x, float hz) {
        double re = 0;
        double im = 0;
        for (int i = 0; i < static_cast<int>(x.size()); ++i) {
            const double phase =
                2.0 * 3.141592653589793 * static_cast<double>(hz) * static_cast<double>(i) /
                bt::kSampleRate;
            re += static_cast<double>(x[static_cast<std::size_t>(i)]) * std::cos(phase);
            im += static_cast<double>(x[static_cast<std::size_t>(i)]) * std::sin(phase);
        }
        return std::sqrt(re * re + im * im) / static_cast<double>(x.size());
    };

    REQUIRE(energy(filtered, 100.f) < energy(mono, 100.f) * 0.15);
    REQUIRE(energy(filtered, 880.f) > energy(mono, 880.f) * 0.7);

    auto stems = constant_song(kFrames, 0.f, 0);
    for (int i = 0; i < kFrames; ++i) {
        stems->audio[0][static_cast<std::size_t>(i)] = mono[static_cast<std::size_t>(i)];
        stems->audio[0][static_cast<std::size_t>(kFrames + i)] = mono[static_cast<std::size_t>(i)];
    }
    bt::Player player;
    player.set_song(stems);
    std::array<float, bt::kStemCount> gain{};
    gain.fill(1.f);
    player.set_mix(gain, {}, {});
    player.set_pitch_mask(true, 0, 80, 84, bt::Player::PitchMaskBackend::Spectral);
    player.play();
    std::vector<float> output(static_cast<std::size_t>(kFrames * 2));
    REQUIRE(player.render(output.data(), kFrames) == kFrames);
    std::vector<float> left(static_cast<std::size_t>(kFrames));
    for (int i = 0; i < kFrames; ++i) {
        left[static_cast<std::size_t>(i)] = output[static_cast<std::size_t>(i * 2)];
    }
    REQUIRE(energy(left, 100.f) < energy(mono, 100.f) * 0.2);
    REQUIRE(energy(left, 880.f) > energy(mono, 880.f) * 0.5);
}
