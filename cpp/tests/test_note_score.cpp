#include "bt/note_score.hpp"
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

}  // namespace

TEST_CASE("note score ranks matching midi above silent false notes") {
    constexpr int kFrames = bt::kSampleRate;  // 1s
    const float hz = bt::midi_note_to_hz(69.f);
    const auto mono = tone(kFrames, hz);

    bt::Note good{69, 0.05, 0.95, 100};
    const bt::NoteScore good_score = bt::score_notes_against_audio(&good, 1, mono.data(), kFrames);

    bt::Note ghost{76, 0.05, 0.95, 100};  // absent pitch
    const bt::NoteScore ghost_score = bt::score_notes_against_audio(&ghost, 1, mono.data(), kFrames);

    REQUIRE(good_score.support > ghost_score.support);
    REQUIRE(good_score.score > ghost_score.score);
}

TEST_CASE("note score flags octave-as-fundamental harmonic false positive") {
    constexpr int kFrames = bt::kSampleRate;
    // Strong energy at 880 Hz only; claiming MIDI 69 (440) is a harmonic miss.
    const auto mono = tone(kFrames, 880.f, 0.45f);
    bt::Note claimed{69, 0.05, 0.95, 100};
    const bt::NoteScore score = bt::score_notes_against_audio(&claimed, 1, mono.data(), kFrames);
    REQUIRE(score.harmonic_fp > 0.5f);
}

TEST_CASE("midi energy mask keeps tone pitch and drops silent pitch") {
    constexpr int kFrames = bt::kSampleRate;
    const float hz = bt::midi_note_to_hz(69.f);
    const auto mono = tone(kFrames, hz);
    const bt::Note notes[] = {{69, 0.05, 0.90, 100}, {80, 0.05, 0.90, 100}};
    const auto kept = bt::mask_notes_by_midi_energy(notes, 2, mono.data(), kFrames);
    bool has69 = false;
    bool has80 = false;
    for (const bt::Note& n : kept) {
        has69 = has69 || n.pitch == 69;
        has80 = has80 || n.pitch == 80;
    }
    REQUIRE(has69);
    REQUIRE_FALSE(has80);
}

TEST_CASE("midi energy mask keeps low bass tone") {
    constexpr int kFrames = bt::kSampleRate;
    const int pitch = 36;  // C2
    const auto mono = tone(kFrames, bt::midi_note_to_hz(static_cast<float>(pitch)), 0.5f);
    const bt::Note notes[] = {{pitch, 0.05, 0.90, 100}, {pitch + 12, 0.05, 0.90, 100}};
    const auto kept = bt::mask_notes_by_midi_energy(notes, 2, mono.data(), kFrames);
    bool has_bass = false;
    bool has_octave = false;
    for (const bt::Note& n : kept) {
        has_bass = has_bass || n.pitch == pitch;
        has_octave = has_octave || n.pitch == pitch + 12;
    }
    REQUIRE(has_bass);
    REQUIRE_FALSE(has_octave);
}
