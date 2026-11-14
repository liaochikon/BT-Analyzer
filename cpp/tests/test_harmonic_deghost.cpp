#include "bt/harmonic_deghost.hpp"
#include "bt/pitch_mask.hpp"

#include <catch2/catch_test_macros.hpp>

#include <cmath>
#include <numbers>
#include <vector>

namespace {

std::vector<float> tone(int frames, float hz, float amp = 0.4f, int sample_rate = bt::kSampleRate) {
    std::vector<float> mono(static_cast<std::size_t>(frames), 0.f);
    const double two_pi = 2.0 * std::numbers::pi_v<double>;
    for (int i = 0; i < frames; ++i) {
        const double phase = two_pi * static_cast<double>(hz) * static_cast<double>(i) / sample_rate;
        mono[static_cast<std::size_t>(i)] = amp * static_cast<float>(std::sin(phase));
    }
    return mono;
}

std::vector<float> add_tone(
    std::vector<float> mono, float hz, float amp, double start_sec, double end_sec,
    int sample_rate = bt::kSampleRate) {
    const double two_pi = 2.0 * std::numbers::pi_v<double>;
    const int lo = std::max(0, static_cast<int>(start_sec * sample_rate));
    const int hi = std::min(static_cast<int>(mono.size()), static_cast<int>(end_sec * sample_rate));
    for (int i = lo; i < hi; ++i) {
        const double phase = two_pi * static_cast<double>(hz) * static_cast<double>(i) / sample_rate;
        mono[static_cast<std::size_t>(i)] += amp * static_cast<float>(std::sin(phase));
    }
    return mono;
}

bool has_pitch(const std::vector<bt::Note>& notes, int pitch) {
    for (const bt::Note& n : notes) {
        if (n.pitch == pitch) {
            return true;
        }
    }
    return false;
}

}  // namespace

TEST_CASE("bank_oct drops octave ghost of strong fundamental") {
    constexpr int kFrames = bt::kSampleRate;
    const int f0 = 57;  // A3
    auto mono = tone(kFrames, bt::midi_note_to_hz(static_cast<float>(f0)), 0.5f);
    // Add a weaker 2nd harmonic so the octave pitch has energy but is explained by f0.
    mono = add_tone(std::move(mono), bt::midi_note_to_hz(static_cast<float>(f0)) * 2.f, 0.18f, 0.0, 1.0);

    const bt::Note notes[] = {{f0, 0.05, 0.90, 100}, {f0 + 12, 0.05, 0.90, 100}};
    bt::DeghostConfig cfg = bt::deghost_default();
    cfg.method = bt::DeghostMethod::BankRatio;
    cfg.use_fifth_oct = false;
    cfg.name = "bank_oct";
    const auto kept = bt::deghost_harmonic_notes(notes, 2, mono.data(), kFrames, bt::kSampleRate, cfg);
    REQUIRE(has_pitch(kept, f0));
    REQUIRE_FALSE(has_pitch(kept, f0 + 12));
}

TEST_CASE("note_oct19 drops overlapping octave partner") {
    constexpr int kFrames = bt::kSampleRate;
    const int f0 = 48;
    auto mono = tone(kFrames, bt::midi_note_to_hz(static_cast<float>(f0)), 0.55f);
    mono = add_tone(std::move(mono), bt::midi_note_to_hz(static_cast<float>(f0)) * 2.f, 0.2f, 0.0, 1.0);

    const bt::Note notes[] = {{f0, 0.05, 0.95, 100}, {f0 + 12, 0.10, 0.90, 100}};
    bt::DeghostConfig cfg = bt::deghost_default();
    cfg.method = bt::DeghostMethod::NoteOverlap;
    cfg.use_fifth_oct = false;
    const auto kept = bt::deghost_harmonic_notes(notes, 2, mono.data(), kFrames, bt::kSampleRate, cfg);
    REQUIRE(has_pitch(kept, f0));
    REQUIRE_FALSE(has_pitch(kept, f0 + 12));
}

TEST_CASE("onset_aware keeps separately attacked true octave") {
    constexpr int kFrames = bt::kSampleRate * 2;
    const int f0 = 60;
    const int oct = 72;
    // Low note first half; high note starts later with its own strong sine (true double stop).
    std::vector<float> mono(static_cast<std::size_t>(kFrames), 0.f);
    mono = add_tone(std::move(mono), bt::midi_note_to_hz(static_cast<float>(f0)), 0.5f, 0.05, 1.8);
    mono = add_tone(std::move(mono), bt::midi_note_to_hz(static_cast<float>(oct)), 0.5f, 0.55, 1.8);

    const bt::Note notes[] = {{f0, 0.05, 1.80, 100}, {oct, 0.55, 1.80, 100}};
    bt::DeghostConfig cfg = bt::deghost_default();
    cfg.method = bt::DeghostMethod::OnsetAware;
    cfg.use_fifth_oct = false;
    cfg.min_overlap = 0.40f;
    cfg.energy_ratio = 1.1f;
    const auto kept = bt::deghost_harmonic_notes(notes, 2, mono.data(), kFrames, bt::kSampleRate, cfg);
    REQUIRE(has_pitch(kept, f0));
    REQUIRE(has_pitch(kept, oct));
}

TEST_CASE("residual drops explained partial more than unexplained tone") {
    constexpr int kFrames = bt::kSampleRate;
    const int f0 = 55;
    auto mono = tone(kFrames, bt::midi_note_to_hz(static_cast<float>(f0)), 0.55f);
    mono = add_tone(std::move(mono), bt::midi_note_to_hz(static_cast<float>(f0)) * 2.f, 0.15f, 0.0, 1.0);

    const bt::Note ghost_pair[] = {{f0, 0.05, 0.95, 100}, {f0 + 12, 0.05, 0.95, 100}};
    bt::DeghostConfig cfg = bt::deghost_default();
    cfg.method = bt::DeghostMethod::Residual;
    cfg.use_fifth_oct = false;
    cfg.residual_keep = 0.35f;
    const auto dropped =
        bt::deghost_harmonic_notes(ghost_pair, 2, mono.data(), kFrames, bt::kSampleRate, cfg);
    REQUIRE(has_pitch(dropped, f0));
    REQUIRE_FALSE(has_pitch(dropped, f0 + 12));
}
