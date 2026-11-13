#pragma once

#include "bt/contract.hpp"

#include <cstdint>
#include <functional>
#include <vector>

namespace bt {

inline constexpr int kPitchMaskFft = 2048;
inline constexpr int kPitchMaskHop = 512;
inline constexpr int kPitchMaskBins = kPitchMaskFft / 2 + 1;

struct PitchMaskNote {
    int pitch = 0;
    double start = 0;
    double end = 0;
};

using PitchMaskProgress = std::function<void(float)>;

float midi_note_to_hz(float midi);

// Brick-wall band mask via STFT. Bins outside [lo_midi, hi_midi] are zeroed.
void spectral_brickwall_mono(
    const float* input, float* output, std::int64_t frames, int lo_midi, int hi_midi,
    int sample_rate = kSampleRate, const PitchMaskProgress& progress = {});

void spectral_brickwall_planar(
    const float* input, float* output, std::int64_t frames, int lo_midi, int hi_midi,
    int sample_rate = kSampleRate, const PitchMaskProgress& progress = {});

// Keep fundamentals (+ harmonics) of notes whose pitch is in [lo_midi, hi_midi]
// and that overlap each STFT frame. No notes → silence (no brickwall fallback).
void spectral_harmonic_mono(
    const float* input, float* output, std::int64_t frames, int lo_midi, int hi_midi,
    const PitchMaskNote* notes, std::size_t note_count, int sample_rate = kSampleRate,
    const PitchMaskProgress& progress = {});

void spectral_harmonic_planar(
    const float* input, float* output, std::int64_t frames, int lo_midi, int hi_midi,
    const PitchMaskNote* notes, std::size_t note_count, int sample_rate = kSampleRate,
    const PitchMaskProgress& progress = {});

}  // namespace bt
