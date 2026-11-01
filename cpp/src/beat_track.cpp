#include "bt/beat_track.hpp"

#include <algorithm>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <limits>
#include <numbers>
#include <vector>

namespace bt {
namespace {

constexpr int kFft = 2048;
constexpr int kHop = 512;
constexpr int kMels = 128;

int next_pow2(int value) {
    int size = 1;
    while (size < value) {
        size <<= 1;
    }
    return size;
}

void fft(std::vector<double>& real, std::vector<double>& imag, bool inverse) {
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
    for (int length = 2; length <= n; length <<= 1) {
        const double angle = (inverse ? 2.0 : -2.0) * std::numbers::pi / static_cast<double>(length);
        const double step_re = std::cos(angle);
        const double step_im = std::sin(angle);
        for (int start = 0; start < n; start += length) {
            double w_re = 1;
            double w_im = 0;
            const int half = length >> 1;
            for (int k = 0; k < half; ++k) {
                const std::size_t even = static_cast<std::size_t>(start + k);
                const std::size_t odd = static_cast<std::size_t>(start + k + half);
                const double t_re = w_re * real[odd] - w_im * imag[odd];
                const double t_im = w_re * imag[odd] + w_im * real[odd];
                real[odd] = real[even] - t_re;
                imag[odd] = imag[even] - t_im;
                real[even] += t_re;
                imag[even] += t_im;
                const double next_re = w_re * step_re - w_im * step_im;
                w_im = w_re * step_im + w_im * step_re;
                w_re = next_re;
            }
        }
    }
    if (inverse) {
        const double scale = 1.0 / static_cast<double>(n);
        for (int i = 0; i < n; ++i) {
            real[static_cast<std::size_t>(i)] *= scale;
            imag[static_cast<std::size_t>(i)] *= scale;
        }
    }
}

double hz_to_mel(double hz) {
    const double f_sp = 200.0 / 3.0;
    const double min_log_hz = 1000.0;
    const double min_log_mel = min_log_hz / f_sp;
    const double logstep = std::log(6.4) / 27.0;
    if (hz >= min_log_hz) {
        return min_log_mel + std::log(hz / min_log_hz) / logstep;
    }
    return hz / f_sp;
}

double mel_to_hz(double mel) {
    const double f_sp = 200.0 / 3.0;
    const double min_log_hz = 1000.0;
    const double min_log_mel = min_log_hz / f_sp;
    const double logstep = std::log(6.4) / 27.0;
    if (mel >= min_log_mel) {
        return min_log_hz * std::exp(logstep * (mel - min_log_mel));
    }
    return f_sp * mel;
}

struct MelWeight {
    int bin = 0;
    float weight = 0.f;
};

std::vector<std::vector<MelWeight>> mel_weights(int sample_rate) {
    constexpr int kBins = kFft / 2 + 1;
    const double fmax = 0.5 * static_cast<double>(sample_rate);
    const double min_mel = hz_to_mel(0);
    const double max_mel = hz_to_mel(fmax);
    std::vector<double> mel_hz(static_cast<std::size_t>(kMels + 2));
    for (int i = 0; i < kMels + 2; ++i) {
        const double mel = min_mel + (max_mel - min_mel) * static_cast<double>(i) / static_cast<double>(kMels + 1);
        mel_hz[static_cast<std::size_t>(i)] = mel_to_hz(mel);
    }
    std::vector<std::vector<MelWeight>> bands(kMels);
    for (int band = 0; band < kMels; ++band) {
        const double lower = mel_hz[static_cast<std::size_t>(band)];
        const double center = mel_hz[static_cast<std::size_t>(band + 1)];
        const double upper = mel_hz[static_cast<std::size_t>(band + 2)];
        const double enorm = 2.0 / std::max(upper - lower, 1e-12);
        for (int bin = 0; bin < kBins; ++bin) {
            const double freq = fmax * static_cast<double>(bin) / static_cast<double>(kBins - 1);
            double weight = 0;
            if (lower <= freq && freq < center && center > lower) {
                weight = (freq - lower) / (center - lower);
            } else if (center <= freq && freq < upper && upper > center) {
                weight = (upper - freq) / (upper - center);
            }
            if (weight > 0) {
                bands[static_cast<std::size_t>(band)].push_back(MelWeight{bin, static_cast<float>(weight * enorm)});
            }
        }
    }
    return bands;
}

std::vector<double> hann(int size) {
    std::vector<double> window(static_cast<std::size_t>(size));
    for (int i = 0; i < size; ++i) {
        window[static_cast<std::size_t>(i)] = 0.5 - 0.5 * std::cos(2.0 * std::numbers::pi * static_cast<double>(i) / static_cast<double>(size));
    }
    return window;
}

std::vector<double> power_frame(const float* padded, int start, const std::vector<double>& window) {
    std::vector<double> real(kFft);
    std::vector<double> imag(kFft);
    for (int i = 0; i < kFft; ++i) {
        real[static_cast<std::size_t>(i)] = static_cast<double>(padded[start + i]) * window[static_cast<std::size_t>(i)];
    }
    fft(real, imag, false);
    std::vector<double> power(kFft / 2 + 1);
    for (int bin = 0; bin <= kFft / 2; ++bin) {
        const double re = real[static_cast<std::size_t>(bin)];
        const double im = imag[static_cast<std::size_t>(bin)];
        power[static_cast<std::size_t>(bin)] = re * re + im * im;
    }
    return power;
}

double median_of(std::vector<double>& values) {
    if (values.empty()) {
        return 0;
    }
    const std::size_t mid = values.size() / 2;
    std::nth_element(values.begin(), values.begin() + static_cast<std::ptrdiff_t>(mid), values.end());
    if ((values.size() % 2) == 1) {
        return values[mid];
    }
    const double upper = values[mid];
    const double lower = *std::max_element(values.begin(), values.begin() + static_cast<std::ptrdiff_t>(mid));
    return 0.5 * (lower + upper);
}

std::vector<double> autocorrelate(const std::vector<double>& frame) {
    const int n = static_cast<int>(frame.size());
    const int n_pad = next_pow2(2 * n - 1);
    std::vector<double> real(static_cast<std::size_t>(n_pad), 0);
    std::vector<double> imag(static_cast<std::size_t>(n_pad), 0);
    for (int i = 0; i < n; ++i) {
        real[static_cast<std::size_t>(i)] = frame[static_cast<std::size_t>(i)];
    }
    fft(real, imag, false);
    for (int i = 0; i < n_pad; ++i) {
        const double re = real[static_cast<std::size_t>(i)];
        const double im = imag[static_cast<std::size_t>(i)];
        real[static_cast<std::size_t>(i)] = re * re + im * im;
        imag[static_cast<std::size_t>(i)] = 0;
    }
    fft(real, imag, true);
    real.resize(static_cast<std::size_t>(n));
    return real;
}

bool local_max(const std::vector<double>& values, int index) {
    const int last = static_cast<int>(values.size()) - 1;
    const double previous = values[static_cast<std::size_t>(std::max(0, index - 1))];
    const double next = values[static_cast<std::size_t>(std::min(last, index + 1))];
    return values[static_cast<std::size_t>(index)] > previous && values[static_cast<std::size_t>(index)] >= next;
}

}  // namespace

std::expected<BeatGrid, std::string> track_beats(
    const float* mono, std::int64_t frames, int sample_rate, const std::function<bool()>& cancelled) {
    if (mono == nullptr || frames < sample_rate || sample_rate < 8000) {
        return std::unexpected("audio is too short to track beats");
    }
    const auto stop = [&] {
        return cancelled && cancelled();
    };
    const int n = static_cast<int>(frames);
    const int pad = kFft / 2;
    std::vector<float> padded(static_cast<std::size_t>(n + kFft), 0.f);
    std::copy(mono, mono + n, padded.begin() + pad);
    const int n_frames = 1 + n / kHop;
    const auto window = hann(kFft);
    const auto bands = mel_weights(sample_rate);
    std::vector<double> mel(static_cast<std::size_t>(kMels * n_frames), 0);
    for (int frame = 0; frame < n_frames; ++frame) {
        if ((frame % 64) == 0 && stop()) {
            return std::unexpected("cancelled");
        }
        const auto power = power_frame(padded.data(), frame * kHop, window);
        for (int band = 0; band < kMels; ++band) {
            double sum = 0;
            for (const MelWeight& weight : bands[static_cast<std::size_t>(band)]) {
                sum += static_cast<double>(weight.weight) * power[static_cast<std::size_t>(weight.bin)];
            }
            mel[static_cast<std::size_t>(band * n_frames + frame)] = sum;
        }
    }
    const double amin = 1e-10;
    const double ref = std::log10(std::max(amin, 1.0));
    double loudest = -std::numeric_limits<double>::infinity();
    for (double& value : mel) {
        value = 10.0 * (std::log10(std::max(amin, value)) - ref);
        loudest = std::max(loudest, value);
    }
    const double floor = loudest - 80.0;
    for (double& value : mel) {
        value = std::max(value, floor);
    }

    std::vector<double> onset(static_cast<std::size_t>(n_frames), 0);
    constexpr int kOnsetPad = 1 + kFft / (2 * kHop);
    for (int frame = 0; frame < n_frames; ++frame) {
        const int source = frame - kOnsetPad;
        if (source < 0 || source + 1 >= n_frames) {
            continue;
        }
        std::vector<double> flux(kMels);
        for (int band = 0; band < kMels; ++band) {
            const double now = mel[static_cast<std::size_t>(band * n_frames + source + 1)];
            const double previous = mel[static_cast<std::size_t>(band * n_frames + source)];
            flux[static_cast<std::size_t>(band)] = std::max(0.0, now - previous);
        }
        onset[static_cast<std::size_t>(frame)] = median_of(flux);
    }
    bool any = false;
    for (double value : onset) {
        any = any || value > 0;
    }
    if (!any) {
        return std::unexpected("no onsets");
    }

    const double frame_rate = static_cast<double>(sample_rate) / static_cast<double>(kHop);
    const int win = std::max(8, static_cast<int>(std::floor(8.0 * static_cast<double>(sample_rate) / static_cast<double>(kHop))));
    const int side = win / 2;
    std::vector<double> padded_onset(static_cast<std::size_t>(n_frames + side * 2), 0);
    const double left_edge = onset.front();
    const double right_edge = onset.back();
    for (int i = 0; i < side; ++i) {
        padded_onset[static_cast<std::size_t>(i)] = left_edge * (static_cast<double>(i) / static_cast<double>(side));
        padded_onset[static_cast<std::size_t>(side + n_frames + i)] =
            right_edge + (0.0 - right_edge) * (static_cast<double>(i + 1) / static_cast<double>(side));
    }
    std::copy(onset.begin(), onset.end(), padded_onset.begin() + side);
    const auto ac_window = hann(win);
    std::vector<double> mean_ac(static_cast<std::size_t>(win), 0);
    int used = 0;
    std::vector<double> windowed(static_cast<std::size_t>(win));
    for (int frame = 0; frame < n_frames; ++frame) {
        if ((frame % 32) == 0 && stop()) {
            return std::unexpected("cancelled");
        }
        for (int i = 0; i < win; ++i) {
            windowed[static_cast<std::size_t>(i)] =
                padded_onset[static_cast<std::size_t>(frame + i)] * ac_window[static_cast<std::size_t>(i)];
        }
        auto lags = autocorrelate(windowed);
        double mag = 0;
        for (double lag : lags) {
            mag = std::max(mag, std::fabs(lag));
        }
        if (mag > 1e-12) {
            for (int i = 0; i < win; ++i) {
                mean_ac[static_cast<std::size_t>(i)] += lags[static_cast<std::size_t>(i)] / mag;
            }
            ++used;
        }
    }
    if (used == 0) {
        return std::unexpected("no tempo");
    }
    for (double& lag : mean_ac) {
        lag /= static_cast<double>(used);
    }

    const double start_bpm = 120.0;
    constexpr double kMaxTempo = 320.0;
    int best = 1;
    double best_score = -std::numeric_limits<double>::infinity();
    for (int lag = 1; lag < win; ++lag) {
        const double bpm = 60.0 * frame_rate / static_cast<double>(lag);
        double prior = -std::numeric_limits<double>::infinity();
        if (bpm < kMaxTempo) {
            const double log_ratio = std::log2(bpm) - std::log2(start_bpm);
            prior = -0.5 * log_ratio * log_ratio;
        }
        const double score = std::log1p(1e6 * std::max(0.0, mean_ac[static_cast<std::size_t>(lag)])) + prior;
        if (score > best_score) {
            best_score = score;
            best = lag;
        }
    }
    const double bpm = 60.0 * frame_rate / static_cast<double>(best);
    const double frames_per_beat = std::round(frame_rate * 60.0 / bpm);
    if (!(frames_per_beat >= 2)) {
        return std::unexpected("tempo is out of range");
    }

    double onset_mean = 0;
    for (double value : onset) {
        onset_mean += value;
    }
    onset_mean /= static_cast<double>(n_frames);
    double onset_var = 0;
    for (double value : onset) {
        const double delta = value - onset_mean;
        onset_var += delta * delta;
    }
    const double onset_std = std::sqrt(onset_var / static_cast<double>(std::max(1, n_frames - 1)));
    std::vector<double> normal(static_cast<std::size_t>(n_frames));
    for (int i = 0; i < n_frames; ++i) {
        normal[static_cast<std::size_t>(i)] = onset[static_cast<std::size_t>(i)] / (onset_std + 1e-20);
    }
    const int radius = static_cast<int>(frames_per_beat);
    const int kernel = radius * 2 + 1;
    std::vector<double> kernel_weight(static_cast<std::size_t>(kernel));
    for (int k = 0; k < kernel; ++k) {
        const double x = (static_cast<double>(k - radius) * 32.0) / frames_per_beat;
        kernel_weight[static_cast<std::size_t>(k)] = std::exp(-0.5 * x * x);
    }
    std::vector<double> local(static_cast<std::size_t>(n_frames), 0);
    for (int i = 0; i < n_frames; ++i) {
        double sum = 0;
        const int begin = std::max(0, i + kernel / 2 - n_frames + 1);
        const int end = std::min(i + kernel / 2, kernel);
        for (int k = begin; k < end; ++k) {
            sum += kernel_weight[static_cast<std::size_t>(k)] * normal[static_cast<std::size_t>(i + kernel / 2 - k)];
        }
        local[static_cast<std::size_t>(i)] = sum;
    }

    std::vector<int> backlink(static_cast<std::size_t>(n_frames), -1);
    std::vector<double> score(static_cast<std::size_t>(n_frames), 0);
    const double score_threshold = 0.01 * *std::max_element(local.begin(), local.end());
    bool first_beat = true;
    constexpr double kTightness = 100.0;
    for (int i = 0; i < n_frames; ++i) {
        if ((i % 256) == 0 && stop()) {
            return std::unexpected("cancelled");
        }
        const int start = i - static_cast<int>(std::llround(frames_per_beat * 0.5));
        const int last = i - static_cast<int>(std::llround(frames_per_beat * 2.0));
        double best_link = -std::numeric_limits<double>::infinity();
        int location = -1;
        if (start >= 0) {
            for (int loc = start; loc >= last && loc >= 0; --loc) {
                const double gap = static_cast<double>(i - loc);
                const double penalty = kTightness * std::pow(std::log(gap) - std::log(frames_per_beat), 2.0);
                const double candidate = score[static_cast<std::size_t>(loc)] - penalty;
                if (candidate > best_link) {
                    best_link = candidate;
                    location = loc;
                }
            }
        }
        score[static_cast<std::size_t>(i)] = local[static_cast<std::size_t>(i)] + (location >= 0 ? best_link : 0);
        if (first_beat && local[static_cast<std::size_t>(i)] < score_threshold) {
            backlink[static_cast<std::size_t>(i)] = -1;
        } else {
            backlink[static_cast<std::size_t>(i)] = location;
        }
        first_beat = false;
    }

    std::vector<char> is_peak(static_cast<std::size_t>(n_frames), 0);
    for (int i = 0; i < n_frames; ++i) {
        is_peak[static_cast<std::size_t>(i)] = local_max(score, i) ? 1 : 0;
    }
    std::vector<double> peaks;
    for (int i = 0; i < n_frames; ++i) {
        if (is_peak[static_cast<std::size_t>(i)] != 0) {
            peaks.push_back(score[static_cast<std::size_t>(i)]);
        }
    }
    double peak_median = 0;
    if (!peaks.empty()) {
        peak_median = median_of(peaks);
    }
    const double tail_threshold = 0.5 * peak_median;
    int tail = n_frames - 1;
    for (int i = n_frames - 1; i >= 0; --i) {
        if (is_peak[static_cast<std::size_t>(i)] != 0 && score[static_cast<std::size_t>(i)] >= tail_threshold) {
            tail = i;
            break;
        }
    }
    std::vector<char> beats(static_cast<std::size_t>(n_frames), 0);
    for (int i = tail; i >= 0; i = backlink[static_cast<std::size_t>(i)]) {
        beats[static_cast<std::size_t>(i)] = 1;
        if (backlink[static_cast<std::size_t>(i)] < 0) {
            break;
        }
    }

    std::vector<double> beat_scores;
    for (int i = 0; i < n_frames; ++i) {
        if (beats[static_cast<std::size_t>(i)] != 0) {
            beat_scores.push_back(local[static_cast<std::size_t>(i)]);
        }
    }
    const double hann5[5] = {0, 0.5, 1, 0.5, 0};
    std::vector<double> smooth;
    const int conv = static_cast<int>(beat_scores.size()) + 4;
    for (int i = 2; i < conv && i < 2 + n_frames; ++i) {
        double sum = 0;
        for (int k = 0; k < 5; ++k) {
            const int j = i - k;
            if (j >= 0 && j < static_cast<int>(beat_scores.size())) {
                sum += hann5[k] * beat_scores[static_cast<std::size_t>(j)];
            }
        }
        smooth.push_back(sum);
    }
    double mean_square = 0;
    for (double value : smooth) {
        mean_square += value * value;
    }
    if (!smooth.empty()) {
        mean_square /= static_cast<double>(smooth.size());
    }
    const double trim = 0.5 * std::sqrt(mean_square);
    for (int i = 0; i < n_frames && local[static_cast<std::size_t>(i)] <= trim; ++i) {
        beats[static_cast<std::size_t>(i)] = 0;
    }
    for (int i = n_frames - 1; i >= 0 && local[static_cast<std::size_t>(i)] <= trim; --i) {
        beats[static_cast<std::size_t>(i)] = 0;
    }

    BeatGrid grid;
    grid.bpm = static_cast<float>(bpm);
    for (int i = 0; i < n_frames; ++i) {
        if (beats[static_cast<std::size_t>(i)] != 0) {
            grid.times.push_back(static_cast<double>(i) * static_cast<double>(kHop) / static_cast<double>(sample_rate));
        }
    }
    if (grid.times.empty() || !(grid.bpm > 0.f)) {
        return std::unexpected("no beats");
    }
    grid.offset = grid.times.front();
    return grid;
}

void extend_beat_edges(BeatGrid& grid, double duration) {
    if (grid.times.empty() || !(duration > 0.0)) {
        return;
    }
    double interval = 0.0;
    if (grid.times.size() >= 2) {
        double bpm_sum = 0.0;
        int count = 0;
        for (std::size_t index = 1; index < grid.times.size(); ++index) {
            const double gap = grid.times[index] - grid.times[index - 1];
            if (gap > 1e-4) {
                bpm_sum += 60.0 / gap;
                ++count;
            }
        }
        if (count > 0 && bpm_sum > 0.0) {
            interval = 60.0 * static_cast<double>(count) / bpm_sum;
        }
    }
    if (!(interval > 1e-3) && grid.bpm > 1.f) {
        interval = 60.0 / static_cast<double>(grid.bpm);
    }
    if (!(interval > 1e-3)) {
        return;
    }
    const int limit = static_cast<int>(duration / interval) + 2;
    std::vector<double> prefix;
    double cursor = grid.times.front();
    for (int step = 0; step < limit; ++step) {
        const double time = cursor - interval;
        if (time < -1e-4) {
            break;
        }
        prefix.push_back(time <= 1e-4 ? 0.0 : time);
        if (time <= 1e-4) {
            break;
        }
        cursor = time;
    }
    std::vector<double> suffix;
    cursor = grid.times.back();
    for (int step = 0; step < limit; ++step) {
        const double time = cursor + interval;
        if (time > duration + 1e-4) {
            break;
        }
        suffix.push_back(time >= duration - 1e-4 ? duration : time);
        if (time >= duration - 1e-4) {
            break;
        }
        cursor = time;
    }
    if (prefix.empty() && suffix.empty()) {
        return;
    }
    std::reverse(prefix.begin(), prefix.end());
    prefix.insert(prefix.end(), grid.times.begin(), grid.times.end());
    prefix.insert(prefix.end(), suffix.begin(), suffix.end());
    grid.times = std::move(prefix);
    grid.offset = grid.times.front();
}

std::expected<BeatGrid, std::string> load_beats(const std::filesystem::path& path) {
    std::ifstream input(path);
    std::string header;
    BeatGrid grid;
    if (!(input >> header) || header != "bt-beats") {
        return std::unexpected("beat cache is unreadable");
    }
    int version = 0;
    if (!(input >> version) || !(input >> grid.bpm >> grid.offset) || !(grid.bpm > 0.f) || grid.offset < 0) {
        return std::unexpected("beat cache is unreadable");
    }
    if (version == 1) {
        return grid;
    }
    if (version != 2) {
        return std::unexpected("beat cache is unreadable");
    }
    std::size_t count = 0;
    if (!(input >> count) || count == 0 || count > 1000000) {
        return std::unexpected("beat cache is unreadable");
    }
    grid.times.resize(count);
    double previous = -1;
    for (double& time : grid.times) {
        if (!(input >> time) || time < previous) {
            return std::unexpected("beat cache is unreadable");
        }
        previous = time;
    }
    return grid;
}

std::expected<void, std::string> save_beats(const std::filesystem::path& path, const BeatGrid& grid) {
    std::error_code error;
    std::filesystem::create_directories(path.parent_path(), error);
    std::ofstream output(path, std::ios::trunc);
    if (!output) {
        return std::unexpected("could not write beat cache");
    }
    output << std::setprecision(std::numeric_limits<double>::max_digits10);
    if (grid.times.empty()) {
        output << "bt-beats 1\n" << grid.bpm << ' ' << grid.offset << '\n';
    } else {
        output << "bt-beats 2\n" << grid.bpm << ' ' << grid.offset << '\n' << grid.times.size() << '\n';
        for (double time : grid.times) {
            output << time << '\n';
        }
    }
    if (!output) {
        return std::unexpected("could not write beat cache");
    }
    return {};
}

}  // namespace bt
