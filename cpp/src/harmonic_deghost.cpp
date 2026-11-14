#include "bt/harmonic_deghost.hpp"

#include "bt/pitch_mask.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <numbers>
#include <vector>

namespace bt {
namespace {

constexpr int kMidiLo = 21;
constexpr int kMidiHi = 108;
constexpr int kMidiCount = kMidiHi - kMidiLo + 1;
constexpr double kExcitePreSec = 0.04;
constexpr double kExcitePostSec = 0.12;

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
    for (int len = 2; len <= n; len <<= 1) {
        const float ang = -2.f * std::numbers::pi_v<float> / static_cast<float>(len);
        const float wlen_r = std::cos(ang);
        const float wlen_i = std::sin(ang);
        for (int i = 0; i < n; i += len) {
            float wr = 1.f;
            float wi = 0.f;
            for (int j = 0; j < len / 2; ++j) {
                const int u = i + j;
                const int v = u + len / 2;
                const float ur = real[static_cast<std::size_t>(u)];
                const float ui = imag[static_cast<std::size_t>(u)];
                const float vr = real[static_cast<std::size_t>(v)] * wr - imag[static_cast<std::size_t>(v)] * wi;
                const float vi = real[static_cast<std::size_t>(v)] * wi + imag[static_cast<std::size_t>(v)] * wr;
                real[static_cast<std::size_t>(u)] = ur + vr;
                imag[static_cast<std::size_t>(u)] = ui + vi;
                real[static_cast<std::size_t>(v)] = ur - vr;
                imag[static_cast<std::size_t>(v)] = ui - vi;
                const float next_wr = wr * wlen_r - wi * wlen_i;
                wi = wr * wlen_i + wi * wlen_r;
                wr = next_wr;
            }
        }
    }
}

struct EnergyBank {
    std::vector<float> energy;
    std::vector<float> excite;
    int frames = 0;
    double frame_sec = 0.0;
};

EnergyBank build_bank(const float* mono, std::int64_t frames, int sample_rate, int fft, int hop) {
    EnergyBank bank;
    if (!mono || frames <= 0 || sample_rate <= 0 || fft < 64 || hop < 1) {
        return bank;
    }
    const int bins = fft / 2 + 1;
    std::vector<float> window(static_cast<std::size_t>(fft));
    for (int i = 0; i < fft; ++i) {
        window[static_cast<std::size_t>(i)] =
            0.5f - 0.5f * std::cos(2.f * std::numbers::pi_v<float> * static_cast<float>(i) / static_cast<float>(fft));
    }
    const int n_frames = 1 + static_cast<int>((frames - 1) / hop);
    bank.frames = std::max(0, n_frames);
    bank.frame_sec = static_cast<double>(hop) / static_cast<double>(sample_rate);
    bank.energy.assign(static_cast<std::size_t>(bank.frames) * static_cast<std::size_t>(kMidiCount), 0.f);
    bank.excite.assign(bank.energy.size(), 0.f);

    std::vector<float> real(static_cast<std::size_t>(fft));
    std::vector<float> imag(static_cast<std::size_t>(fft));
    const float hz_per_bin = static_cast<float>(sample_rate) / static_cast<float>(fft);

    for (int t = 0; t < bank.frames; ++t) {
        const std::int64_t start = static_cast<std::int64_t>(t) * hop;
        std::fill(real.begin(), real.end(), 0.f);
        std::fill(imag.begin(), imag.end(), 0.f);
        for (int i = 0; i < fft; ++i) {
            const std::int64_t idx = start + i;
            if (idx >= 0 && idx < frames) {
                real[static_cast<std::size_t>(i)] = mono[idx] * window[static_cast<std::size_t>(i)];
            }
        }
        fft_radix2(real, imag);
        float* row = bank.energy.data() + static_cast<std::size_t>(t) * static_cast<std::size_t>(kMidiCount);
        for (int bin = 1; bin < bins - 1; ++bin) {
            const float re = real[static_cast<std::size_t>(bin)];
            const float im = imag[static_cast<std::size_t>(bin)];
            const float mag = std::sqrt(re * re + im * im);
            const float hz = static_cast<float>(bin) * hz_per_bin;
            if (hz < 20.f || hz > 5000.f) {
                continue;
            }
            const float midi = hz_to_midi(hz);
            const int lo = std::max(kMidiLo, static_cast<int>(std::floor(midi - 1.f)));
            const int hi = std::min(kMidiHi, static_cast<int>(std::ceil(midi + 1.f)));
            for (int pitch = lo; pitch <= hi; ++pitch) {
                const float dist = std::fabs(midi - static_cast<float>(pitch));
                const float w = dist <= 0.5f ? 1.f : std::max(0.f, 1.f - (dist - 0.5f));
                row[pitch - kMidiLo] += mag * w;
            }
        }
    }
    for (int t = 0; t < bank.frames; ++t) {
        float* xrow = bank.excite.data() + static_cast<std::size_t>(t) * static_cast<std::size_t>(kMidiCount);
        const float* erow =
            bank.energy.data() + static_cast<std::size_t>(t) * static_cast<std::size_t>(kMidiCount);
        if (t == 0) {
            for (int p = 0; p < kMidiCount; ++p) {
                xrow[p] = erow[p];
            }
        } else {
            const float* prev =
                bank.energy.data() + static_cast<std::size_t>(t - 1) * static_cast<std::size_t>(kMidiCount);
            for (int p = 0; p < kMidiCount; ++p) {
                xrow[p] = std::max(0.f, erow[p] - prev[p]);
            }
        }
    }
    return bank;
}

float peak_in_window(const EnergyBank& bank, int pitch, double t0, double t1, bool excite) {
    if (pitch < kMidiLo || pitch > kMidiHi || bank.frames <= 0 || !(t1 > t0)) {
        return 0.f;
    }
    const int p = pitch - kMidiLo;
    const int lo = std::max(0, static_cast<int>(std::floor(t0 / bank.frame_sec)));
    const int hi = std::min(bank.frames, static_cast<int>(std::ceil(t1 / bank.frame_sec)));
    float best = 0.f;
    const float* src = excite ? bank.excite.data() : bank.energy.data();
    for (int t = lo; t < hi; ++t) {
        best = std::max(
            best, src[static_cast<std::size_t>(t) * static_cast<std::size_t>(kMidiCount) + static_cast<std::size_t>(p)]);
    }
    return best;
}

float mean_in_overlap(const EnergyBank& bank, int pitch, double t0, double t1) {
    if (pitch < kMidiLo || pitch > kMidiHi || bank.frames <= 0 || !(t1 > t0)) {
        return 0.f;
    }
    const int p = pitch - kMidiLo;
    const int lo = std::max(0, static_cast<int>(std::floor(t0 / bank.frame_sec)));
    const int hi = std::min(bank.frames, static_cast<int>(std::ceil(t1 / bank.frame_sec)));
    if (hi <= lo) {
        return 0.f;
    }
    double sum = 0.0;
    for (int t = lo; t < hi; ++t) {
        sum += bank.energy
            [static_cast<std::size_t>(t) * static_cast<std::size_t>(kMidiCount) + static_cast<std::size_t>(p)];
    }
    return static_cast<float>(sum / static_cast<double>(hi - lo));
}

bool interval_enabled(const DeghostConfig& cfg, int delta) {
    if (delta == 12) {
        return cfg.use_octave;
    }
    if (delta == 19) {
        return cfg.use_fifth_oct;
    }
    if (delta == 24) {
        return cfg.use_double_oct;
    }
    return false;
}

std::vector<int> intervals_of(const DeghostConfig& cfg) {
    std::vector<int> out;
    if (cfg.use_octave) {
        out.push_back(12);
    }
    if (cfg.use_fifth_oct) {
        out.push_back(19);
    }
    if (cfg.use_double_oct) {
        out.push_back(24);
    }
    return out;
}

double overlap_frac(const Note& high, const Note& low) {
    const double dur = high.end - high.start;
    if (dur <= 1e-9) {
        return 0.0;
    }
    const double a = std::max(high.start, low.start);
    const double b = std::min(high.end, low.end);
    if (b <= a) {
        return 0.0;
    }
    return (b - a) / dur;
}

bool independent_onset(const EnergyBank& bank, const Note& high, const Note& low, const DeghostConfig& cfg) {
    const float high_x = peak_in_window(
        bank, high.pitch, high.start - kExcitePreSec, high.start + kExcitePostSec, true);
    const float high_e = peak_in_window(
        bank, high.pitch, high.start - kExcitePreSec, high.start + kExcitePostSec, false);
    if (high_e < 1e-8f || high_x < high_e * cfg.onset_excite_rel) {
        return false;
    }
    const float low_x = peak_in_window(
        bank, low.pitch, low.start - kExcitePreSec, low.start + kExcitePostSec, true);
    // Separate attack from the lower note's onset.
    if (std::fabs(high.start - low.start) >= cfg.onset_sep_sec && high_x >= low_x * 0.45f) {
        return true;
    }
    // Or a clear second excite peak near high.start while low is already sustaining.
    if (high.start > low.start + cfg.onset_sep_sec && high_x >= low_x * 0.55f) {
        return true;
    }
    return false;
}

bool bank_ratio_ghost(const EnergyBank& bank, const Note& high, const DeghostConfig& cfg) {
    const float self_e = peak_in_window(
        bank, high.pitch, high.start - kExcitePreSec, high.start + kExcitePostSec, false);
    if (self_e < 1e-8f) {
        return true;
    }
    for (int delta : intervals_of(cfg)) {
        const int low_pitch = high.pitch - delta;
        if (low_pitch < kMidiLo) {
            continue;
        }
        const float low_e = peak_in_window(
            bank, low_pitch, high.start - kExcitePreSec, high.start + kExcitePostSec, false);
        if (low_e >= self_e * cfg.energy_ratio) {
            return true;
        }
    }
    return false;
}

bool note_overlap_ghost(
    const EnergyBank& bank, const Note& high, const std::vector<Note>& notes, std::size_t self_index,
    const DeghostConfig& cfg, bool onset_aware) {
    for (std::size_t i = 0; i < notes.size(); ++i) {
        if (i == self_index) {
            continue;
        }
        const Note& low = notes[i];
        const int delta = high.pitch - low.pitch;
        if (!interval_enabled(cfg, delta)) {
            continue;
        }
        if (overlap_frac(high, low) < cfg.min_overlap) {
            continue;
        }
        const double a = std::max(high.start, low.start);
        const double b = std::min(high.end, low.end);
        const float e_high = mean_in_overlap(bank, high.pitch, a, b);
        const float e_low = mean_in_overlap(bank, low.pitch, a, b);
        if (e_high < 1e-8f) {
            return true;
        }
        if (e_low < e_high * cfg.energy_ratio) {
            continue;
        }
        if (onset_aware && independent_onset(bank, high, low, cfg)) {
            continue;
        }
        return true;
    }
    return false;
}

bool residual_ghost(
    const EnergyBank& bank, const Note& high, const std::vector<Note>& notes, std::size_t self_index,
    const DeghostConfig& cfg) {
    const double a0 = high.start;
    const double a1 = high.end;
    const float e_high = mean_in_overlap(bank, high.pitch, a0, a1);
    if (e_high < 1e-8f) {
        return true;
    }
    float explained = 0.f;
    for (std::size_t i = 0; i < notes.size(); ++i) {
        if (i == self_index) {
            continue;
        }
        const Note& low = notes[i];
        const int delta = high.pitch - low.pitch;
        if (!interval_enabled(cfg, delta)) {
            continue;
        }
        if (overlap_frac(high, low) < cfg.min_overlap) {
            continue;
        }
        const double a = std::max(high.start, low.start);
        const double b = std::min(high.end, low.end);
        const float e_low = mean_in_overlap(bank, low.pitch, a, b);
        // Partial strength falls with harmonic number; 2nd≈0.55, 3rd≈0.35, 4th≈0.25 of fundamental.
        float harm_w = 0.55f;
        if (delta == 19) {
            harm_w = 0.35f;
        } else if (delta == 24) {
            harm_w = 0.25f;
        }
        explained += std::min(e_high, e_low * harm_w);
    }
    const float unexplained = std::max(0.f, e_high - explained);
    return (unexplained / e_high) < cfg.residual_keep;
}

}  // namespace

DeghostConfig deghost_default() {
    DeghostConfig c;
    c.name = "note_oct19";
    c.method = DeghostMethod::NoteOverlap;
    c.use_octave = true;
    c.use_fifth_oct = true;
    c.use_double_oct = false;
    return c;
}

std::vector<DeghostConfig> deghost_ablation_presets() {
    std::vector<DeghostConfig> out;
    auto add = [&](DeghostConfig c) { out.push_back(c); };

    DeghostConfig bank_oct = deghost_default();
    bank_oct.name = "bank_oct";
    bank_oct.method = DeghostMethod::BankRatio;
    bank_oct.use_fifth_oct = false;
    add(bank_oct);

    DeghostConfig bank_oct19 = bank_oct;
    bank_oct19.name = "bank_oct19";
    bank_oct19.use_fifth_oct = true;
    add(bank_oct19);

    DeghostConfig note_oct = deghost_default();
    note_oct.name = "note_oct";
    note_oct.method = DeghostMethod::NoteOverlap;
    note_oct.use_fifth_oct = false;
    add(note_oct);

    DeghostConfig note_oct19 = deghost_default();
    note_oct19.name = "note_oct19";
    add(note_oct19);

    DeghostConfig onset_oct19 = deghost_default();
    onset_oct19.name = "onset_oct19";
    onset_oct19.method = DeghostMethod::OnsetAware;
    add(onset_oct19);

    DeghostConfig residual_oct19 = deghost_default();
    residual_oct19.name = "residual_oct19";
    residual_oct19.method = DeghostMethod::Residual;
    add(residual_oct19);

    DeghostConfig residual_tight = residual_oct19;
    residual_tight.name = "residual_tight";
    residual_tight.residual_keep = 0.50f;
    residual_tight.energy_ratio = 1.2f;
    add(residual_tight);

    DeghostConfig onset_loose = onset_oct19;
    onset_loose.name = "onset_loose";
    onset_loose.energy_ratio = 1.15f;
    onset_loose.min_overlap = 0.40f;
    onset_loose.onset_excite_rel = 0.25f;
    add(onset_loose);

    return out;
}

std::vector<Note> deghost_harmonic_notes(
    const Note* notes, std::size_t note_count, const float* mono, std::int64_t frames, int sample_rate,
    const DeghostConfig& config) {
    if (notes == nullptr || note_count == 0) {
        return {};
    }
    if (!mono || frames <= 0 || sample_rate <= 0) {
        return std::vector<Note>(notes, notes + note_count);
    }
    DeghostConfig cfg = config;
    if (cfg.fft <= 0) {
        cfg.fft = 2048;
    }
    if (cfg.hop <= 0) {
        cfg.hop = 512;
    }
    const EnergyBank bank = build_bank(mono, frames, sample_rate, cfg.fft, cfg.hop);
    if (bank.frames <= 0) {
        return std::vector<Note>(notes, notes + note_count);
    }

    std::vector<Note> input(notes, notes + note_count);
    // Stable order: low pitch first so partners exist when scanning highs.
    std::vector<std::size_t> order(input.size());
    for (std::size_t i = 0; i < order.size(); ++i) {
        order[i] = i;
    }
    std::stable_sort(order.begin(), order.end(), [&](std::size_t a, std::size_t b) {
        if (input[a].pitch != input[b].pitch) {
            return input[a].pitch < input[b].pitch;
        }
        return input[a].start < input[b].start;
    });

    std::vector<char> drop(input.size(), 0);
    // Evaluate high→low so stacked ghosts collapse toward the fundamental.
    for (std::size_t oi = order.size(); oi-- > 0;) {
        const std::size_t i = order[oi];
        const Note& n = input[i];
        if (!(n.end > n.start) || n.pitch < kMidiLo || n.pitch > kMidiHi) {
            drop[i] = 1;
            continue;
        }
        bool ghost = false;
        switch (cfg.method) {
        case DeghostMethod::BankRatio:
            ghost = bank_ratio_ghost(bank, n, cfg);
            break;
        case DeghostMethod::NoteOverlap:
            ghost = note_overlap_ghost(bank, n, input, i, cfg, false);
            break;
        case DeghostMethod::OnsetAware:
            ghost = note_overlap_ghost(bank, n, input, i, cfg, true);
            break;
        case DeghostMethod::Residual:
            ghost = residual_ghost(bank, n, input, i, cfg);
            break;
        }
        if (ghost) {
            drop[i] = 1;
        }
    }

    std::vector<Note> kept;
    kept.reserve(input.size());
    for (std::size_t i = 0; i < input.size(); ++i) {
        if (!drop[i]) {
            kept.push_back(input[i]);
        }
    }
    return kept;
}

}  // namespace bt
