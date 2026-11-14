#pragma once

#include "bt/basic_pitch.hpp"
#include "bt/contract.hpp"

#include <cstdint>
#include <vector>

namespace bt {

// Interpretable post-filters for harmonic / octave ghost notes.
// Intended after (optional) energy mask: BP proposes → energy → deghost.
enum class DeghostMethod {
    BankRatio,     // E(pitch-interval) vs E(self) in onset window (no partner note required)
    NoteOverlap,   // overlapping lower BP note at harmonic interval + energy ratio
    OnsetAware,    // NoteOverlap, but keep if candidate has independent onset excite
    Residual,      // keep only if energy remains after explaining via lower notes' partials
};

struct DeghostConfig {
    const char* name = "bank_oct";
    DeghostMethod method = DeghostMethod::BankRatio;
    bool use_octave = true;       // -12 (2nd harmonic)
    bool use_fifth_oct = true;    // -19 (3rd harmonic ≈ octave+fifth)
    bool use_double_oct = false;  // -24 (4th harmonic)
    float energy_ratio = 1.35f;   // treat as ghost if E(low) >= E(high) * ratio
    float min_overlap = 0.55f;    // fraction of high-note duration covered by low note
    float onset_sep_sec = 0.045f; // OnsetAware: independent if excite peaks differ by this
    float onset_excite_rel = 0.35f; // OnsetAware: candidate excite vs local peak
    float residual_keep = 0.35f;  // Residual: keep if unexplained / E(high) >= this
    int fft = 2048;
    int hop = 512;
};

DeghostConfig deghost_default();
std::vector<DeghostConfig> deghost_ablation_presets();

std::vector<Note> deghost_harmonic_notes(
    const Note* notes, std::size_t note_count, const float* mono, std::int64_t frames,
    int sample_rate = kSampleRate, const DeghostConfig& config = deghost_default());

}  // namespace bt
