#include "bt/note_score.hpp"

#include "bt/pitch_mask.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <numbers>
#include <utility>
#include <vector>

namespace bt {
namespace {

// Coarser than pitch-mask STFT: enough for ranking, far fewer frames / bins.
constexpr int kFft = 1024;
constexpr int kHop = 1024;
constexpr int kBins = kFft / 2 + 1;
constexpr int kTopPeaks = 4;
constexpr float kPeakRel = 4.f;
constexpr float kSupportRel = 3.f;
constexpr float kSupportPeakFrac = 0.12f;
constexpr float kHarmonicRatio = 1.5f;
constexpr float kHarmonicF0Weak = 0.25f;
constexpr float kLambdaHarmonic = 0.25f;
constexpr float kSemitoneTol = 1.f;
constexpr int kLocalNoiseHalf = 12;
constexpr int kLocalExclude = 2;

float hz_to_midi(float hz) {
    if (hz <= 1.f) {
        return 0.f;
    }
    return 69.f + 12.f * std::log2(hz / 440.f);
}

void fft_radix2(std::vector<float>& real, std::vector<float>& imag) {
    const int n = static_cast<int>(real.size());
    for (int i = 1, j = 0; i < n; ++i) {
        int bit = n >> 1;
        for (; (j & bit) != 0; bit >>= 1) {
            j ^= bit;
        }
        j ^= bit;
        if (i < j) {
            std::swap(real[static_cast<std::size_t>(i)], real[static_cast<std::size_t>(j)]);
            std::swap(imag[static_cast<std::size_t>(i)], imag[static_cast<std::size_t>(j)]);
        }
    }
    const float pi = std::numbers::pi_v<float>;
    for (int len = 2; len <= n; len <<= 1) {
        const float angle = -2.f * pi / static_cast<float>(len);
        const float wlen_re = std::cos(angle);
        const float wlen_im = std::sin(angle);
        for (int i = 0; i < n; i += len) {
            float w_re = 1.f;
            float w_im = 0.f;
            for (int j = 0; j < len / 2; ++j) {
                const int u = i + j;
                const int v = u + len / 2;
                const float tre =
                    real[static_cast<std::size_t>(v)] * w_re - imag[static_cast<std::size_t>(v)] * w_im;
                const float tim =
                    real[static_cast<std::size_t>(v)] * w_im + imag[static_cast<std::size_t>(v)] * w_re;
                real[static_cast<std::size_t>(v)] = real[static_cast<std::size_t>(u)] - tre;
                imag[static_cast<std::size_t>(v)] = imag[static_cast<std::size_t>(u)] - tim;
                real[static_cast<std::size_t>(u)] += tre;
                imag[static_cast<std::size_t>(u)] += tim;
                const float next_re = w_re * wlen_re - w_im * wlen_im;
                w_im = w_re * wlen_im + w_im * wlen_re;
                w_re = next_re;
            }
        }
    }
}

int bin_of_hz(float hz, int sample_rate) {
    const float hz_per_bin = static_cast<float>(sample_rate) / static_cast<float>(kFft);
    return std::clamp(static_cast<int>(std::lround(hz / hz_per_bin)), 1, kBins - 1);
}

float bin_energy(const float* mag, int bin) {
    float energy = mag[bin];
    if (bin > 1) {
        energy = std::max(energy, mag[bin - 1]);
    }
    if (bin + 1 < kBins) {
        energy = std::max(energy, mag[bin + 1]);
    }
    return energy;
}

bool note_active(const Note* notes, std::size_t note_count, double time, int pitch, float tol) {
    for (std::size_t i = 0; i < note_count; ++i) {
        const Note& note = notes[i];
        if (time < note.start || time >= note.end) {
            continue;
        }
        if (std::fabs(static_cast<float>(note.pitch - pitch)) <= tol) {
            return true;
        }
    }
    return false;
}

struct FrameMag {
    std::vector<float> mag;  // frames * bins
    int frames = 0;
    int bins = 0;
};

std::vector<float> make_hann(int fft) {
    std::vector<float> values(static_cast<std::size_t>(fft));
    const float pi = std::numbers::pi_v<float>;
    for (int n = 0; n < fft; ++n) {
        values[static_cast<std::size_t>(n)] =
            0.5f * (1.f - std::cos(2.f * pi * static_cast<float>(n) / static_cast<float>(fft)));
    }
    return values;
}

FrameMag build_stft_sized(const float* mono, std::int64_t frames, int sample_rate, int fft, int hop) {
    FrameMag out;
    if (!mono || frames <= 0 || sample_rate <= 0 || hop <= 0 || fft < 16 || (fft & (fft - 1)) != 0) {
        return out;
    }
    const int bins = fft / 2 + 1;
    const int pad = fft / 2;
    out.frames = static_cast<int>((frames + hop - 1) / hop) + 1;
    out.bins = bins;
    out.mag.assign(static_cast<std::size_t>(out.frames) * static_cast<std::size_t>(bins), 0.f);
    const auto window = make_hann(fft);
    std::vector<float> real(static_cast<std::size_t>(fft));
    std::vector<float> imag(static_cast<std::size_t>(fft));
    for (int time = 0; time < out.frames; ++time) {
        const int start = time * hop - pad;
        for (int n = 0; n < fft; ++n) {
            const std::int64_t index = static_cast<std::int64_t>(start + n);
            float sample = 0.f;
            if (index >= 0 && index < frames) {
                sample = mono[static_cast<std::size_t>(index)];
            }
            real[static_cast<std::size_t>(n)] = sample * window[static_cast<std::size_t>(n)];
            imag[static_cast<std::size_t>(n)] = 0.f;
        }
        fft_radix2(real, imag);
        float* row = out.mag.data() + static_cast<std::size_t>(time) * static_cast<std::size_t>(bins);
        for (int bin = 0; bin < bins; ++bin) {
            const float re = real[static_cast<std::size_t>(bin)];
            const float im = imag[static_cast<std::size_t>(bin)];
            row[bin] = std::sqrt(re * re + im * im);
        }
    }
    return out;
}

FrameMag build_stft_with_hop(const float* mono, std::int64_t frames, int sample_rate, int hop) {
    return build_stft_sized(mono, frames, sample_rate, kFft, hop);
}

FrameMag build_stft(const float* mono, std::int64_t frames, int sample_rate) {
    return build_stft_with_hop(mono, frames, sample_rate, kHop);
}

float frame_median(const float* mag) {
    std::array<float, kBins> sorted{};
    std::copy_n(mag, kBins, sorted.begin());
    std::nth_element(sorted.begin(), sorted.begin() + kBins / 2, sorted.end());
    return sorted[kBins / 2];
}

float frame_peak(const float* mag) {
    float peak = 0.f;
    for (int bin = 1; bin < kBins; ++bin) {
        peak = std::max(peak, mag[bin]);
    }
    return peak;
}

float local_noise_floor(const float* mag, int f0_bin) {
    std::array<float, kLocalNoiseHalf * 2> values{};
    int count = 0;
    const int lo = std::max(1, f0_bin - kLocalNoiseHalf);
    const int hi = std::min(kBins - 1, f0_bin + kLocalNoiseHalf);
    for (int bin = lo; bin <= hi; ++bin) {
        if (std::abs(bin - f0_bin) <= kLocalExclude) {
            continue;
        }
        values[static_cast<std::size_t>(count++)] = mag[bin];
    }
    if (count <= 0) {
        return frame_median(mag);
    }
    std::nth_element(values.begin(), values.begin() + count / 2, values.begin() + count);
    return values[static_cast<std::size_t>(count / 2)];
}

NoteScore score_notes_against_stft(
    const Note* notes, std::size_t note_count, const FrameMag& stft, int sample_rate) {
    NoteScore out;
    if (stft.frames <= 0 || sample_rate <= 0) {
        return out;
    }
    const float hz_per_bin = static_cast<float>(sample_rate) / static_cast<float>(kFft);
    const double frame_sec = static_cast<double>(kHop) / static_cast<double>(sample_rate);

    std::uint64_t peak_total = 0;
    std::uint64_t peak_hit = 0;
    for (int time = 0; time < stft.frames; ++time) {
        const float* mag = stft.mag.data() + static_cast<std::size_t>(time) * static_cast<std::size_t>(kBins);
        const float median = std::max(frame_median(mag), 1e-8f);
        const float floor = median * kPeakRel;
        struct Peak {
            int bin = 0;
            float mag = 0.f;
        };
        Peak peaks[kTopPeaks]{};
        int peak_count = 0;
        for (int bin = 2; bin < kBins - 2; ++bin) {
            const float value = mag[bin];
            if (value < floor || value <= mag[bin - 1] || value <= mag[bin + 1]) {
                continue;
            }
            int slot = peak_count;
            if (peak_count < kTopPeaks) {
                ++peak_count;
            } else {
                slot = 0;
                for (int i = 1; i < kTopPeaks; ++i) {
                    if (peaks[i].mag < peaks[slot].mag) {
                        slot = i;
                    }
                }
                if (value <= peaks[slot].mag) {
                    continue;
                }
            }
            peaks[slot] = {bin, value};
        }
        const double time_sec = static_cast<double>(time) * frame_sec;
        for (int i = 0; i < peak_count; ++i) {
            const float midi = hz_to_midi(static_cast<float>(peaks[i].bin) * hz_per_bin);
            if (midi < 20.f || midi > 110.f) {
                continue;
            }
            ++peak_total;
            if (note_active(notes, note_count, time_sec, static_cast<int>(std::lround(midi)), kSemitoneTol)) {
                ++peak_hit;
            }
        }
    }
    out.coverage = peak_total > 0 ? static_cast<float>(peak_hit) / static_cast<float>(peak_total) : 0.f;

    std::uint64_t support_frames = 0;
    std::uint64_t support_hit = 0;
    std::uint64_t harmonic_bad = 0;
    std::uint64_t harmonic_total = 0;
    if (notes != nullptr) {
        for (std::size_t n = 0; n < note_count; ++n) {
            const Note& note = notes[n];
            if (!(note.end > note.start) || note.pitch < 21 || note.pitch > 108) {
                continue;
            }
            const int start_frame = std::max(0, static_cast<int>(note.start / frame_sec));
            const int end_frame =
                std::min(stft.frames, static_cast<int>(std::ceil(note.end / frame_sec)));
            if (end_frame <= start_frame) {
                continue;
            }
            const int f0_bin = bin_of_hz(midi_note_to_hz(static_cast<float>(note.pitch)), sample_rate);
            const int h2_bin = bin_of_hz(midi_note_to_hz(static_cast<float>(note.pitch)) * 2.f, sample_rate);
            float f0_sum = 0.f;
            float h2_sum = 0.f;
            float peak_sum = 0.f;
            int counted = 0;
            for (int time = start_frame; time < end_frame; ++time) {
                const float* mag =
                    stft.mag.data() + static_cast<std::size_t>(time) * static_cast<std::size_t>(kBins);
                const float peak = std::max(frame_peak(mag), 1e-8f);
                const float noise = std::max(local_noise_floor(mag, f0_bin), 1e-8f);
                const float f0 = bin_energy(mag, f0_bin);
                const float h2 = bin_energy(mag, h2_bin);
                f0_sum += f0;
                h2_sum += h2;
                peak_sum += peak;
                ++counted;
                ++support_frames;
                const float floor = std::max(noise * kSupportRel, peak * kSupportPeakFrac);
                if (f0 >= floor) {
                    ++support_hit;
                }
            }
            if (counted > 0) {
                ++harmonic_total;
                const float f0_mean = f0_sum / static_cast<float>(counted);
                const float h2_mean = h2_sum / static_cast<float>(counted);
                const float peak_mean = peak_sum / static_cast<float>(counted);
                if (h2_mean > f0_mean * kHarmonicRatio && f0_mean < peak_mean * kHarmonicF0Weak) {
                    ++harmonic_bad;
                }
            }
        }
    }
    out.support = support_frames > 0 ? static_cast<float>(support_hit) / static_cast<float>(support_frames) : 0.f;
    out.harmonic_fp =
        harmonic_total > 0 ? static_cast<float>(harmonic_bad) / static_cast<float>(harmonic_total) : 0.f;
    const float denom = out.coverage + out.support;
    const float f1 = denom > 1e-8f ? 2.f * out.coverage * out.support / denom : 0.f;
    out.score = f1 - kLambdaHarmonic * out.harmonic_fp;
    return out;
}


// MIDI-band energy mask: BP additive, energy subtractive (configurable for ablation).
constexpr int kMidiLo = 21;
constexpr int kMidiHi = 108;
constexpr int kMidiCount = kMidiHi - kMidiLo + 1;
constexpr double kExcitePreSec = 0.04;
constexpr double kExcitePostSec = 0.10;
constexpr double kLocalPadSec = 0.25;

float band_core_semi(int pitch) {
    if (pitch < 48) {
        return 1.25f;
    }
    if (pitch < 60) {
        return 0.85f;
    }
    return 0.55f;
}

float band_skirt_semi(int pitch) {
    return band_core_semi(pitch) + 0.75f;
}

float midi_band_weight(float continuous_midi, int pitch) {
    const float core = band_core_semi(pitch);
    const float skirt = band_skirt_semi(pitch);
    const float dist = std::fabs(continuous_midi - static_cast<float>(pitch));
    if (dist <= core) {
        return 1.f - 0.4f * (dist / std::max(core, 1e-3f));
    }
    if (dist <= skirt) {
        const float t = (dist - core) / std::max(skirt - core, 1e-3f);
        return 0.3f * (1.f - t);
    }
    return 0.f;
}

struct MidiEnergyBank {
    std::vector<float> energy;
    std::vector<float> excite;
    int frames = 0;
    double frame_sec = 0.0;
};

MidiEnergyBank build_midi_energy_bank(
    const float* mono, std::int64_t frames, int sample_rate, const EnergyMaskConfig& config) {
    MidiEnergyBank bank;
    const int fft = config.fft > 0 ? config.fft : 2048;
    const int hop = config.hop > 0 ? config.hop : 512;
    const FrameMag stft = build_stft_sized(mono, frames, sample_rate, fft, hop);
    if (stft.frames <= 0 || stft.bins <= 0) {
        return bank;
    }
    bank.frames = stft.frames;
    bank.frame_sec = static_cast<double>(hop) / static_cast<double>(sample_rate);
    bank.energy.assign(static_cast<std::size_t>(bank.frames) * static_cast<std::size_t>(kMidiCount), 0.f);
    bank.excite.assign(bank.energy.size(), 0.f);

    const float hz_per_bin = static_cast<float>(sample_rate) / static_cast<float>(fft);
    for (int time = 0; time < bank.frames; ++time) {
        const float* mag = stft.mag.data() + static_cast<std::size_t>(time) * static_cast<std::size_t>(stft.bins);
        float* row = bank.energy.data() + static_cast<std::size_t>(time) * static_cast<std::size_t>(kMidiCount);
        for (int bin = 1; bin < stft.bins - 1; ++bin) {
            const float hz_center = static_cast<float>(bin) * hz_per_bin;
            if (hz_center < 20.f || hz_center > 5000.f) {
                continue;
            }
            int span_lo = kMidiHi;
            int span_hi = kMidiLo;
            if (config.use_span_paint) {
                const float hz_lo = std::max(20.f, (static_cast<float>(bin) - 0.5f) * hz_per_bin);
                const float hz_hi = (static_cast<float>(bin) + 0.5f) * hz_per_bin;
                span_lo = std::max(kMidiLo, static_cast<int>(std::floor(hz_to_midi(hz_lo))));
                span_hi = std::min(kMidiHi, static_cast<int>(std::ceil(hz_to_midi(hz_hi))));
                for (int pitch = span_lo; pitch <= span_hi; ++pitch) {
                    row[pitch - kMidiLo] += mag[bin];
                }
            }
            const float midi = hz_to_midi(hz_center);
            const int lo = std::max(kMidiLo, static_cast<int>(std::floor(midi - 2.f)));
            const int hi = std::min(kMidiHi, static_cast<int>(std::ceil(midi + 2.f)));
            for (int pitch = lo; pitch <= hi; ++pitch) {
                if (config.use_span_paint && pitch >= span_lo && pitch <= span_hi) {
                    continue;
                }
                const float w = midi_band_weight(midi, pitch);
                if (w <= 0.f) {
                    continue;
                }
                row[pitch - kMidiLo] += mag[bin] * w;
            }
        }
    }
    for (int time = 0; time < bank.frames; ++time) {
        float* xrow = bank.excite.data() + static_cast<std::size_t>(time) * static_cast<std::size_t>(kMidiCount);
        const float* erow =
            bank.energy.data() + static_cast<std::size_t>(time) * static_cast<std::size_t>(kMidiCount);
        if (time == 0) {
            for (int p = 0; p < kMidiCount; ++p) {
                xrow[p] = erow[p];
            }
        } else {
            const float* prev =
                bank.energy.data() + static_cast<std::size_t>(time - 1) * static_cast<std::size_t>(kMidiCount);
            for (int p = 0; p < kMidiCount; ++p) {
                xrow[p] = std::max(0.f, erow[p] - prev[p]);
            }
        }
    }
    return bank;
}

bool note_passes_energy_mask(const Note& note, const MidiEnergyBank& bank, const EnergyMaskConfig& config) {
    if (!(note.end > note.start) || note.pitch < kMidiLo || note.pitch > kMidiHi || bank.frames <= 0) {
        return false;
    }
    const int p = note.pitch - kMidiLo;
    const float thr_scale = (note.pitch < 48) ? config.bass_thr_scale : 1.f;
    const int local_lo = std::max(
        0, static_cast<int>(std::floor((note.start - kLocalPadSec) / bank.frame_sec)));
    const int local_hi = std::min(
        bank.frames, static_cast<int>(std::ceil((note.end + kLocalPadSec) / bank.frame_sec)));
    float local_peak_e = 0.f;
    float local_peak_x = 0.f;
    for (int t = local_lo; t < local_hi; ++t) {
        const std::size_t base = static_cast<std::size_t>(t) * static_cast<std::size_t>(kMidiCount);
        local_peak_e = std::max(local_peak_e, bank.energy[base + static_cast<std::size_t>(p)]);
        local_peak_x = std::max(local_peak_x, bank.excite[base + static_cast<std::size_t>(p)]);
    }
    if (local_peak_e < 1e-8f) {
        return false;
    }
    const float sustain_thr = local_peak_e * config.sustain_rel * thr_scale;
    const float excite_thr =
        std::max(local_peak_x * config.excite_rel * thr_scale, local_peak_e * config.sustain_rel * thr_scale * 0.5f);

    const int excite_lo = std::max(
        0, static_cast<int>(std::floor((note.start - kExcitePreSec) / bank.frame_sec)));
    const int excite_hi = std::min(
        bank.frames,
        static_cast<int>(std::ceil((note.start + kExcitePostSec) / bank.frame_sec)));
    float best_excite = 0.f;
    float best_onset_e = 0.f;
    float best_lateral = 0.f;
    for (int t = excite_lo; t < excite_hi; ++t) {
        const float* xrow =
            bank.excite.data() + static_cast<std::size_t>(t) * static_cast<std::size_t>(kMidiCount);
        const float* erow =
            bank.energy.data() + static_cast<std::size_t>(t) * static_cast<std::size_t>(kMidiCount);
        best_excite = std::max(best_excite, xrow[p]);
        best_onset_e = std::max(best_onset_e, erow[p]);
        float neighbor = 0.f;
        if (p > 0) {
            neighbor = std::max(neighbor, erow[p - 1]);
        }
        if (p + 1 < kMidiCount) {
            neighbor = std::max(neighbor, erow[p + 1]);
        }
        best_lateral = std::max(best_lateral, erow[p] / std::max(neighbor, 1e-8f));
    }

    const bool fail_onset = best_excite < excite_thr && best_onset_e < sustain_thr;
    const bool fail_lateral =
        note.pitch >= config.lateral_min_pitch && best_lateral < config.lateral_ratio;
    bool fail_harmonic = false;
    if (config.harmonic_ratio > 0.f && note.pitch - 12 >= kMidiLo) {
        float below = 0.f;
        for (int t = excite_lo; t < excite_hi; ++t) {
            const float* erow =
                bank.energy.data() + static_cast<std::size_t>(t) * static_cast<std::size_t>(kMidiCount);
            below = std::max(below, erow[p - 12]);
        }
        fail_harmonic = below > best_onset_e * config.harmonic_ratio;
    }

    const int sus_lo = std::max(0, static_cast<int>(std::floor(note.start / bank.frame_sec)));
    const int sus_hi = std::min(bank.frames, static_cast<int>(std::ceil(note.end / bank.frame_sec)));
    if (sus_hi <= sus_lo) {
        return false;
    }
    int ok = 0;
    for (int t = sus_lo; t < sus_hi; ++t) {
        const float* erow =
            bank.energy.data() + static_cast<std::size_t>(t) * static_cast<std::size_t>(kMidiCount);
        if (erow[p] >= sustain_thr) {
            ++ok;
        }
    }
    const bool fail_sustain =
        static_cast<float>(ok) / static_cast<float>(sus_hi - sus_lo) < config.min_sustain_frac;

    if (config.conservative) {
        const int fails = static_cast<int>(fail_onset) + static_cast<int>(fail_lateral) +
                          static_cast<int>(fail_harmonic) + static_cast<int>(fail_sustain);
        return fails < 2;
    }
    return !fail_onset && !fail_lateral && !fail_harmonic && !fail_sustain;
}

}  // namespace

EnergyMaskConfig energy_mask_default() {
    return {};  // fft2048 / hop512 — default BP transcription mask
}

std::vector<EnergyMaskConfig> energy_mask_ablation_presets() {
    std::vector<EnergyMaskConfig> out;
    auto add = [&](EnergyMaskConfig c) { out.push_back(c); };

    add(energy_mask_default());

    EnergyMaskConfig fft1024 = energy_mask_default();
    fft1024.name = "fft1024";
    fft1024.fft = 1024;
    add(fft1024);

    EnergyMaskConfig hop256 = energy_mask_default();
    hop256.name = "hop256";
    hop256.hop = 256;
    add(hop256);

    EnergyMaskConfig fine = energy_mask_default();
    fine.name = "fine";
    fine.hop = 256;
    add(fine);

    EnergyMaskConfig loose = energy_mask_default();
    loose.name = "loose";
    loose.excite_rel = 0.06f;
    loose.sustain_rel = 0.05f;
    loose.min_sustain_frac = 0.12f;
    loose.lateral_ratio = 0.55f;
    loose.harmonic_ratio = 1.8f;
    add(loose);

    EnergyMaskConfig tight = energy_mask_default();
    tight.name = "tight";
    tight.excite_rel = 0.14f;
    tight.sustain_rel = 0.12f;
    tight.min_sustain_frac = 0.28f;
    tight.lateral_ratio = 0.85f;
    tight.harmonic_ratio = 1.2f;
    add(tight);

    EnergyMaskConfig no_lat = energy_mask_default();
    no_lat.name = "no_lateral";
    no_lat.lateral_min_pitch = 128;
    add(no_lat);

    EnergyMaskConfig no_harm = energy_mask_default();
    no_harm.name = "no_harmonic";
    no_harm.harmonic_ratio = 0.f;
    add(no_harm);

    EnergyMaskConfig cons = energy_mask_default();
    cons.name = "conservative";
    cons.conservative = true;
    add(cons);

    EnergyMaskConfig soft_bass = energy_mask_default();
    soft_bass.name = "soft_bass";
    soft_bass.bass_thr_scale = 0.65f;
    add(soft_bass);

    EnergyMaskConfig no_span = energy_mask_default();
    no_span.name = "no_span";
    no_span.use_span_paint = false;
    add(no_span);

    EnergyMaskConfig best_guess = energy_mask_default();
    best_guess.name = "combo_fine_loose";
    best_guess.hop = 256;
    best_guess.excite_rel = 0.07f;
    best_guess.sustain_rel = 0.06f;
    best_guess.min_sustain_frac = 0.14f;
    best_guess.bass_thr_scale = 0.7f;
    best_guess.conservative = true;
    add(best_guess);

    return out;
}

NoteScore score_notes_against_audio(
    const Note* notes, std::size_t note_count, const float* mono, std::int64_t frames, int sample_rate) {
    if (!mono || frames <= 0 || sample_rate <= 0) {
        return {};
    }
    return score_notes_against_stft(notes, note_count, build_stft(mono, frames, sample_rate), sample_rate);
}

std::vector<Note> mask_notes_by_midi_energy(
    const Note* notes, std::size_t note_count, const float* mono, std::int64_t frames, int sample_rate,
    const EnergyMaskConfig& config) {
    if (notes == nullptr || note_count == 0) {
        return {};
    }
    if (!mono || frames <= 0 || sample_rate <= 0) {
        return std::vector<Note>(notes, notes + note_count);
    }
    EnergyMaskConfig cfg = config;
    if (cfg.fft <= 0) {
        cfg.fft = 2048;
    }
    if (cfg.hop <= 0) {
        cfg.hop = 512;
    }
    if (cfg.name == nullptr) {
        cfg.name = "fft2048";
    }
    const MidiEnergyBank bank = build_midi_energy_bank(mono, frames, sample_rate, cfg);
    if (bank.frames <= 0) {
        return std::vector<Note>(notes, notes + note_count);
    }
    std::vector<Note> kept;
    kept.reserve(note_count);
    for (std::size_t i = 0; i < note_count; ++i) {
        if (note_passes_energy_mask(notes[i], bank, cfg)) {
            kept.push_back(notes[i]);
        }
    }
    return kept;
}

}  // namespace bt
