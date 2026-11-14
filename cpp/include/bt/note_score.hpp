#pragma once

#include "bt/basic_pitch.hpp"
#include "bt/contract.hpp"

#include <cstdint>
#include <string_view>
#include <vector>

namespace bt {

struct NoteScore {
    float coverage = 0.f;
    float support = 0.f;
    float harmonic_fp = 0.f;
    float score = 0.f;
};

// Reference-free MIDI vs mono audio consistency (independent STFT, not Basic Pitch activations).
NoteScore score_notes_against_audio(
    const Note* notes, std::size_t note_count, const float* mono, std::int64_t frames,
    int sample_rate = kSampleRate);

// Configurable MIDI-band energy mask (BP additive, energy subtractive).
// Default path for BP transcription post-filter: fft2048 / hop512 (Bach10 mix winner).
struct EnergyMaskConfig {
    const char* name = "fft2048";
    int fft = 2048;
    int hop = 512;
    float excite_rel = 0.10f;
    float sustain_rel = 0.08f;
    float min_sustain_frac = 0.18f;
    float lateral_ratio = 0.70f;
    int lateral_min_pitch = 55;     // skip lateral below this MIDI
    float harmonic_ratio = 1.4f;    // reject if octave-below > self * ratio; <=0 disables
    bool use_span_paint = true;     // paint MIDI notes covered by each FFT bin's Hz span
    bool conservative = false;      // drop only when 2+ checks fail
    float bass_thr_scale = 1.f;     // multiply excite/sustain thr for pitch < 48 (<1 = looser)
};

EnergyMaskConfig energy_mask_default();
// Named ablation presets used by bt-bach10-eval --sweep.
std::vector<EnergyMaskConfig> energy_mask_ablation_presets();

std::vector<Note> mask_notes_by_midi_energy(
    const Note* notes, std::size_t note_count, const float* mono, std::int64_t frames,
    int sample_rate = kSampleRate, const EnergyMaskConfig& config = energy_mask_default());

}  // namespace bt
