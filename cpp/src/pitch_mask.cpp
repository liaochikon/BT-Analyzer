#include "bt/pitch_mask.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <functional>
#include <numbers>
#include <vector>

namespace bt {
namespace {

constexpr float kHarmonicCents = 50.f;
constexpr int kMaxHarmonics = 16;

void fft_radix2(std::vector<float>& real, std::vector<float>& imag, bool inverse) {
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
        const float angle = (inverse ? 2.f : -2.f) * pi / static_cast<float>(len);
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
    if (inverse) {
        const float scale = 1.f / static_cast<float>(n);
        for (int i = 0; i < n; ++i) {
            real[static_cast<std::size_t>(i)] *= scale;
            imag[static_cast<std::size_t>(i)] *= scale;
        }
    }
}

const std::vector<float>& hann_window() {
    static const std::vector<float> window = [] {
        std::vector<float> values(static_cast<std::size_t>(kPitchMaskFft));
        const float pi = std::numbers::pi_v<float>;
        for (int n = 0; n < kPitchMaskFft; ++n) {
            values[static_cast<std::size_t>(n)] =
                0.5f * (1.f - std::cos(2.f * pi * static_cast<float>(n) / static_cast<float>(kPitchMaskFft)));
        }
        return values;
    }();
    return window;
}

std::int64_t reflect_index(std::int64_t index, std::int64_t length) {
    if (length <= 1) {
        return 0;
    }
    const std::int64_t period = 2 * (length - 1);
    std::int64_t mod = index % period;
    if (mod < 0) {
        mod += period;
    }
    if (mod >= length) {
        mod = period - mod;
    }
    return mod;
}

float reflected_sample(const float* samples, std::int64_t frames, std::int64_t index) {
    return samples[static_cast<std::size_t>(reflect_index(index, frames))];
}

float edge_gain(int bin, float lo_bin, float hi_bin) {
    if (bin < lo_bin - 1.f || bin > hi_bin + 1.f) {
        return 0.f;
    }
    if (bin >= lo_bin && bin <= hi_bin) {
        return 1.f;
    }
    if (bin < lo_bin) {
        const float t = 1.f - (lo_bin - static_cast<float>(bin));
        return 0.5f - 0.5f * std::cos(std::numbers::pi_v<float> * std::clamp(t, 0.f, 1.f));
    }
    const float t = 1.f - (static_cast<float>(bin) - hi_bin);
    return 0.5f - 0.5f * std::cos(std::numbers::pi_v<float> * std::clamp(t, 0.f, 1.f));
}

void mark_band(std::vector<float>& gains, float lo_bin, float hi_bin) {
    const int begin = std::max(0, static_cast<int>(std::floor(lo_bin)) - 1);
    const int end = std::min(kPitchMaskBins - 1, static_cast<int>(std::ceil(hi_bin)) + 1);
    for (int bin = begin; bin <= end; ++bin) {
        gains[static_cast<std::size_t>(bin)] =
            std::max(gains[static_cast<std::size_t>(bin)], edge_gain(bin, lo_bin, hi_bin));
    }
}

using FrameMaskFn = std::function<void(int time_index, double time_sec, std::vector<float>& gains)>;

void spectral_process_mono(
    const float* input, float* output, std::int64_t frames, int sample_rate, const FrameMaskFn& mask_fn,
    const PitchMaskProgress& progress, float progress_from, float progress_to) {
    if (!input || !output || frames <= 0 || sample_rate <= 0 || !mask_fn) {
        if (progress) {
            progress(progress_to);
        }
        return;
    }
    std::fill_n(output, static_cast<std::size_t>(frames), 0.f);

    const int pad = kPitchMaskFft / 2;
    const int time_frames =
        static_cast<int>((frames + kPitchMaskHop - 1) / kPitchMaskHop) + 1;
    const int ola = (time_frames - 1) * kPitchMaskHop + kPitchMaskFft;
    const std::vector<float>& window = hann_window();
    const int report_every = std::max(1, time_frames / 40);

    std::vector<float> acc(static_cast<std::size_t>(ola), 0.f);
    std::vector<float> env(static_cast<std::size_t>(ola), 0.f);
    std::vector<float> frame(static_cast<std::size_t>(kPitchMaskFft));
    std::vector<float> re(static_cast<std::size_t>(kPitchMaskFft));
    std::vector<float> im(static_cast<std::size_t>(kPitchMaskFft));
    std::vector<float> gains(static_cast<std::size_t>(kPitchMaskBins), 0.f);

    for (int time = 0; time < time_frames; ++time) {
        const int start = time * kPitchMaskHop - pad;
        for (int n = 0; n < kPitchMaskFft; ++n) {
            frame[static_cast<std::size_t>(n)] =
                reflected_sample(input, frames, static_cast<std::int64_t>(start + n)) *
                window[static_cast<std::size_t>(n)];
        }
        re = frame;
        std::fill(im.begin(), im.end(), 0.f);
        fft_radix2(re, im, false);

        std::fill(gains.begin(), gains.end(), 0.f);
        const double time_sec =
            (static_cast<double>(time) * kPitchMaskHop) / static_cast<double>(sample_rate);
        mask_fn(time, time_sec, gains);

        for (int bin = 0; bin < kPitchMaskBins; ++bin) {
            const float gain = gains[static_cast<std::size_t>(bin)];
            re[static_cast<std::size_t>(bin)] *= gain;
            im[static_cast<std::size_t>(bin)] *= gain;
        }
        im[static_cast<std::size_t>(kPitchMaskFft / 2)] = 0.f;
        for (int bin = 1; bin < kPitchMaskFft / 2; ++bin) {
            re[static_cast<std::size_t>(kPitchMaskFft - bin)] = re[static_cast<std::size_t>(bin)];
            im[static_cast<std::size_t>(kPitchMaskFft - bin)] = -im[static_cast<std::size_t>(bin)];
        }
        fft_radix2(re, im, true);

        const int offset = time * kPitchMaskHop;
        for (int n = 0; n < kPitchMaskFft; ++n) {
            const float weight = window[static_cast<std::size_t>(n)];
            acc[static_cast<std::size_t>(offset + n)] += re[static_cast<std::size_t>(n)] * weight;
            env[static_cast<std::size_t>(offset + n)] += weight * weight;
        }
        if (progress && (time % report_every == 0 || time + 1 == time_frames)) {
            const float local =
                static_cast<float>(time + 1) / static_cast<float>(std::max(1, time_frames));
            progress(progress_from + (progress_to - progress_from) * local);
        }
    }

    for (std::int64_t i = 0; i < frames; ++i) {
        const int src = static_cast<int>(i) + pad;
        if (src < 0 || src >= ola) {
            continue;
        }
        const float weight = env[static_cast<std::size_t>(src)];
        if (weight > 1e-8f) {
            output[static_cast<std::size_t>(i)] = acc[static_cast<std::size_t>(src)] / weight;
        }
    }
    if (progress) {
        progress(progress_to);
    }
}

FrameMaskFn make_brickwall_mask(int lo_midi, int hi_midi, int sample_rate) {
    const float nyquist = static_cast<float>(sample_rate) * 0.5f;
    const float hz_per_bin = static_cast<float>(sample_rate) / static_cast<float>(kPitchMaskFft);
    const float lo_hz = std::clamp(midi_note_to_hz(static_cast<float>(lo_midi)), 20.f, nyquist);
    const float hi_hz = std::clamp(midi_note_to_hz(static_cast<float>(hi_midi)), lo_hz, nyquist);
    const float lo_bin = lo_hz / hz_per_bin;
    const float hi_bin = hi_hz / hz_per_bin;
    return [lo_bin, hi_bin](int, double, std::vector<float>& gains) {
        mark_band(gains, lo_bin, hi_bin);
    };
}

FrameMaskFn make_harmonic_mask(
    int lo_midi, int hi_midi, const PitchMaskNote* notes, std::size_t note_count, int sample_rate) {
    const float nyquist = static_cast<float>(sample_rate) * 0.5f;
    const float hz_per_bin = static_cast<float>(sample_rate) / static_cast<float>(kPitchMaskFft);
    const float ratio = std::pow(2.f, kHarmonicCents / 1200.f);
    std::vector<PitchMaskNote> kept;
    kept.reserve(note_count);
    for (std::size_t i = 0; i < note_count; ++i) {
        const PitchMaskNote& note = notes[i];
        if (note.pitch < lo_midi || note.pitch > hi_midi || note.end <= note.start) {
            continue;
        }
        kept.push_back(note);
    }
    return [kept = std::move(kept), nyquist, hz_per_bin, ratio](
               int, double time_sec, std::vector<float>& gains) {
        for (const PitchMaskNote& note : kept) {
            if (time_sec < note.start || time_sec >= note.end) {
                continue;
            }
            const float f0 = midi_note_to_hz(static_cast<float>(note.pitch));
            for (int harm = 1; harm <= kMaxHarmonics; ++harm) {
                const float freq = f0 * static_cast<float>(harm);
                if (freq > nyquist) {
                    break;
                }
                const float lo_hz = freq / ratio;
                const float hi_hz = std::min(freq * ratio, nyquist);
                mark_band(gains, lo_hz / hz_per_bin, hi_hz / hz_per_bin);
            }
        }
    };
}

}  // namespace

float midi_note_to_hz(float midi) {
    return 440.f * std::pow(2.f, (midi - 69.f) / 12.f);
}

void spectral_brickwall_mono(
    const float* input, float* output, std::int64_t frames, int lo_midi, int hi_midi, int sample_rate,
    const PitchMaskProgress& progress) {
    if (!input || !output || frames <= 0 || sample_rate <= 0) {
        if (progress) {
            progress(1.f);
        }
        return;
    }
    if (lo_midi > hi_midi) {
        std::swap(lo_midi, hi_midi);
    }
    if (lo_midi >= hi_midi) {
        std::fill_n(output, static_cast<std::size_t>(frames), 0.f);
        if (progress) {
            progress(1.f);
        }
        return;
    }
    spectral_process_mono(
        input, output, frames, sample_rate, make_brickwall_mask(lo_midi, hi_midi, sample_rate), progress,
        0.f, 1.f);
}

void spectral_brickwall_planar(
    const float* input, float* output, std::int64_t frames, int lo_midi, int hi_midi, int sample_rate,
    const PitchMaskProgress& progress) {
    if (!input || !output || frames <= 0) {
        if (progress) {
            progress(1.f);
        }
        return;
    }
    spectral_brickwall_mono(input, output, frames, lo_midi, hi_midi, sample_rate, [&](float local) {
        if (progress) {
            progress(local * 0.5f);
        }
    });
    spectral_brickwall_mono(
        input + frames, output + frames, frames, lo_midi, hi_midi, sample_rate, [&](float local) {
            if (progress) {
                progress(0.5f + local * 0.5f);
            }
        });
}

void spectral_harmonic_mono(
    const float* input, float* output, std::int64_t frames, int lo_midi, int hi_midi,
    const PitchMaskNote* notes, std::size_t note_count, int sample_rate,
    const PitchMaskProgress& progress) {
    if (!input || !output || frames <= 0 || sample_rate <= 0) {
        if (progress) {
            progress(1.f);
        }
        return;
    }
    if (lo_midi > hi_midi) {
        std::swap(lo_midi, hi_midi);
    }
    if (lo_midi >= hi_midi || notes == nullptr || note_count == 0) {
        std::fill_n(output, static_cast<std::size_t>(frames), 0.f);
        if (progress) {
            progress(1.f);
        }
        return;
    }
    spectral_process_mono(
        input, output, frames, sample_rate,
        make_harmonic_mask(lo_midi, hi_midi, notes, note_count, sample_rate), progress, 0.f, 1.f);
}

void spectral_harmonic_planar(
    const float* input, float* output, std::int64_t frames, int lo_midi, int hi_midi,
    const PitchMaskNote* notes, std::size_t note_count, int sample_rate,
    const PitchMaskProgress& progress) {
    if (!input || !output || frames <= 0) {
        if (progress) {
            progress(1.f);
        }
        return;
    }
    spectral_harmonic_mono(
        input, output, frames, lo_midi, hi_midi, notes, note_count, sample_rate, [&](float local) {
            if (progress) {
                progress(local * 0.5f);
            }
        });
    spectral_harmonic_mono(
        input + frames, output + frames, frames, lo_midi, hi_midi, notes, note_count, sample_rate,
        [&](float local) {
            if (progress) {
                progress(0.5f + local * 0.5f);
            }
        });
}

}  // namespace bt
