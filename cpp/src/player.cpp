#include "bt/player.hpp"

#include "bt/pitch_mask.hpp"
#include "bt/time_stretch.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#pragma warning(push, 0)
#define MA_NO_DECODING
#define MA_NO_ENCODING
#define MA_NO_GENERATION
#define MA_NO_RESOURCE_MANAGER
#define MINIAUDIO_IMPLEMENTATION
#include "miniaudio.h"
#pragma warning(pop)

namespace bt {
namespace {

constexpr int kRingFrames = 8192;
constexpr int kBlockFrames = 512;
constexpr int kPitchMaskHarmonicSemitones = 12;
constexpr float kPitchMaskSmooth = 0.08f;

float decibel_to_gain(float db) {
    return std::pow(10.f, db / 20.f);
}

float midi_to_hz(float midi) {
    return 440.f * std::pow(2.f, (midi - 69.f) / 12.f);
}

std::string device_token(const ma_device_id& id) {
    const auto* bytes = reinterpret_cast<const unsigned char*>(&id);
    std::string token(sizeof(id) * 2, '0');
    for (std::size_t index = 0; index < sizeof(id); ++index) {
        std::snprintf(token.data() + index * 2, 3, "%02x", bytes[index]);
    }
    return token;
}

struct BiquadCoeffs {
    float b0 = 1.f;
    float b1 = 0.f;
    float b2 = 0.f;
    float a1 = 0.f;
    float a2 = 0.f;
};

struct BiquadState {
    float x1 = 0.f;
    float x2 = 0.f;
    float y1 = 0.f;
    float y2 = 0.f;

    void reset() {
        x1 = x2 = y1 = y2 = 0.f;
    }

    float process(float sample, const BiquadCoeffs& c) {
        const float y = c.b0 * sample + c.b1 * x1 + c.b2 * x2 - c.a1 * y1 - c.a2 * y2;
        x2 = x1;
        x1 = sample;
        y2 = y1;
        y1 = y;
        return y;
    }
};

BiquadCoeffs make_lowpass(float hz, float sample_rate) {
    const float nyquist = sample_rate * 0.5f;
    const float cutoff = std::clamp(hz, 20.f, nyquist * 0.95f);
    const float w0 = 2.f * 3.14159265358979323846f * cutoff / sample_rate;
    const float cos_w0 = std::cos(w0);
    const float sin_w0 = std::sin(w0);
    const float alpha = sin_w0 * 0.7071067811865476f;
    const float b0 = (1.f - cos_w0) * 0.5f;
    const float b1 = 1.f - cos_w0;
    const float b2 = b0;
    const float a0 = 1.f + alpha;
    const float a1 = -2.f * cos_w0;
    const float a2 = 1.f - alpha;
    return {b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0};
}

BiquadCoeffs make_highpass(float hz, float sample_rate) {
    const float nyquist = sample_rate * 0.5f;
    const float cutoff = std::clamp(hz, 20.f, nyquist * 0.45f);
    const float w0 = 2.f * 3.14159265358979323846f * cutoff / sample_rate;
    const float cos_w0 = std::cos(w0);
    const float sin_w0 = std::sin(w0);
    const float alpha = sin_w0 * 0.7071067811865476f;
    const float b0 = (1.f + cos_w0) * 0.5f;
    const float b1 = -(1.f + cos_w0);
    const float b2 = b0;
    const float a0 = 1.f + alpha;
    const float a1 = -2.f * cos_w0;
    const float a2 = 1.f - alpha;
    return {b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0};
}

}  // namespace

struct Player::Mix {
    std::array<float, kStemCount> gain{};
    std::array<float, kStemCount> pan{};
    std::array<bool, kStemCount> mute{};
    std::array<bool, kStemCount> solo{};

    Mix() { gain.fill(1.f); }
};

struct Player::PitchMask {
    bool enabled = false;
    int stem = 0;
    int lo_midi = 21;
    int hi_midi = 108;
    PitchMaskBackend backend = PitchMaskBackend::Harmonic;
    std::shared_ptr<const std::vector<PitchMaskNote>> notes;
    std::uint64_t notes_revision = 0;
};

struct Player::State {
    TimeStretcher stretcher;
    std::mutex mix_mu;
    Mix published_mix;
    Mix block_mix;
    PitchMask published_mask;
    PitchMask block_mask;
    BiquadCoeffs highpass{};
    BiquadCoeffs lowpass{};
    BiquadState highpass_l{};
    BiquadState highpass_r{};
    BiquadState lowpass_l{};
    BiquadState lowpass_r{};
    float applied_lo_hz = 0.f;
    float applied_hi_hz = 0.f;
    float target_lo_hz = 0.f;
    float target_hi_hz = 0.f;
    bool filter_armed = false;
    std::mutex spectral_mu;
    std::shared_ptr<const std::vector<float>> spectral_audio;
    std::int64_t spectral_frames = 0;
    int spectral_stem = -1;
    int spectral_lo = 0;
    int spectral_hi = 0;
    PitchMaskBackend spectral_backend = PitchMaskBackend::Harmonic;
    std::uint64_t spectral_notes_revision = 0;
    const Stems* spectral_source = nullptr;
    std::mutex mask_mu;
    std::condition_variable mask_cv;
    std::condition_variable mask_idle_cv;
    bool mask_stop = false;
    bool mask_request = false;
    bool mask_busy = false;
    std::atomic<bool> mask_building{false};
    std::atomic<float> mask_progress{0.f};
    std::thread mask_worker;
    std::mutex song_mu;
    std::shared_ptr<const Stems> published_stems;
    std::uint64_t published_generation = 0;
    std::uint64_t applied_generation = 0;
    std::shared_ptr<const Stems> stems;
    std::atomic<bool> playing{false};
    std::atomic<bool> ready{false};
    std::atomic<bool> loop{false};
    std::atomic<double> position{0};
    std::atomic<double> length{0};
    std::atomic<double> loop_a{-1};
    std::atomic<double> loop_b{-1};
    std::atomic<float> speed{1.f};
    std::atomic<float> master_db{0.f};
    std::atomic<float> master_linear{1.f};
    std::atomic<bool> metro{false};
    std::atomic<float> bpm{120.f};
    std::atomic<float> click_db{0.f};
    std::atomic<double> beat_offset{0};
    std::mutex beat_mu;
    bool follow_beats = false;
    std::shared_ptr<const std::vector<double>> beat_times;
    double metro_song = 0;
    int click_left = 0;
    float click_phase = 0.f;
    float click_freq = 1175.f;
    float click_amp = 0.f;
    std::atomic<bool> counting{false};
    std::atomic<int> count_beats{0};
    std::atomic<int> count_interval{0};
    std::atomic<std::uint64_t> count_serial{0};
    std::uint64_t applied_count_serial = 0;
    int count_left = 0;
    int count_wait = 0;
    int count_gap = 0;
    int count_total = 0;
    std::atomic<std::uint64_t> seek_serial{0};
    std::atomic<double> seek_to{0};
    std::uint64_t applied_seek = 0;
    float applied_speed = 1.f;
    std::vector<float> ring = std::vector<float>(static_cast<std::size_t>(kRingFrames * 2));
    std::atomic<std::uint64_t> read_cursor{0};
    std::atomic<std::uint64_t> write_cursor{0};
    std::atomic<std::uint64_t> discard_before{0};
    std::mutex wait_mu;
    std::condition_variable wait_cv;
    bool wake = false;
    std::atomic<bool> running{false};
    std::thread worker;
    ma_device device{};
    bool device_open = false;
    ma_context context{};
    bool context_open = false;

    State() : stretcher(2) {}
};

Player::Player() : state_(std::make_unique<State>()) {
    state_->stretcher.set_source([this](int start, int frames, float* interleaved) {
        return read_source(start, frames, interleaved);
    });
    state_->mask_worker = std::thread([this] { mask_thread_main(); });
}

Player::~Player() {
    if (state_ == nullptr) {
        return;
    }
    {
        std::lock_guard lock(state_->mask_mu);
        state_->mask_stop = true;
        state_->mask_request = true;
    }
    state_->mask_cv.notify_all();
    if (state_->mask_worker.joinable()) {
        state_->mask_worker.join();
    }
    state_->running.store(false);
    wake();
    if (state_->worker.joinable()) {
        state_->worker.join();
    }
    if (state_->device_open) {
        ma_device_stop(&state_->device);
        ma_device_uninit(&state_->device);
        state_->device_open = false;
    }
    if (state_->context_open) {
        ma_context_uninit(&state_->context);
        state_->context_open = false;
    }
}

void Player::wake() {
    {
        std::lock_guard lock(state_->wait_mu);
        state_->wake = true;
    }
    state_->wait_cv.notify_one();
}

void Player::discard_ring() {
    state_->discard_before.store(state_->write_cursor.load(std::memory_order_relaxed), std::memory_order_release);
}

void Player::set_song(std::shared_ptr<const Stems> stems) {
    state_->playing.store(false);
    state_->counting.store(false);
    state_->count_serial.fetch_add(1, std::memory_order_release);
    state_->position.store(0);
    {
        std::lock_guard lock(state_->song_mu);
        state_->published_stems = std::move(stems);
        ++state_->published_generation;
    }
    discard_ring();
    wake();
}

void Player::clear_song() {
    set_song(nullptr);
}

void Player::set_mix(
    const std::array<float, kStemCount>& gain, const std::array<bool, kStemCount>& mute,
    const std::array<bool, kStemCount>& solo, const std::array<float, kStemCount>& pan) {
    std::lock_guard lock(state_->mix_mu);
    state_->published_mix.gain = gain;
    state_->published_mix.mute = mute;
    state_->published_mix.solo = solo;
    state_->published_mix.pan = pan;
}

void Player::set_pitch_mask(
    bool enabled, int stem, int lo_midi, int hi_midi, PitchMaskBackend backend,
    std::shared_ptr<const std::vector<PitchMaskNote>> notes, std::uint64_t notes_revision) {
    int lo = std::clamp(lo_midi, 0, 127);
    int hi = std::clamp(hi_midi, 0, 127);
    if (lo > hi) {
        std::swap(lo, hi);
    }
    stem = std::clamp(stem, 0, kStemCount - 1);
    {
        std::lock_guard lock(state_->mix_mu);
        state_->published_mask.enabled = enabled;
        state_->published_mask.stem = stem;
        state_->published_mask.lo_midi = lo;
        state_->published_mask.hi_midi = hi;
        state_->published_mask.backend = backend;
        state_->published_mask.notes = std::move(notes);
        state_->published_mask.notes_revision = notes_revision;
    }
    const bool cached =
        backend == PitchMaskBackend::Spectral || backend == PitchMaskBackend::Harmonic;
    if (!enabled || !cached || lo >= hi) {
        clear_spectral_mask();
        {
            std::lock_guard lock(state_->mask_mu);
            state_->mask_request = false;
        }
        state_->mask_building.store(false);
        state_->mask_progress.store(0.f);
        state_->mask_idle_cv.notify_all();
        return;
    }
    state_->mask_building.store(true);
    state_->mask_progress.store(0.f);
    request_spectral_mask_rebuild();
}

bool Player::pitch_mask_building() const {
    return state_->mask_building.load();
}

float Player::pitch_mask_progress() const {
    return std::clamp(state_->mask_progress.load(), 0.f, 1.f);
}

void Player::wait_pitch_mask() {
    for (;;) {
        PitchMask mask;
        std::shared_ptr<const Stems> stems;
        {
            std::lock_guard lock(state_->mix_mu);
            mask = state_->published_mask;
        }
        {
            std::lock_guard lock(state_->song_mu);
            stems = state_->published_stems;
        }
        const bool cached =
            mask.backend == PitchMaskBackend::Spectral || mask.backend == PitchMaskBackend::Harmonic;
        if (!mask.enabled || !cached || mask.lo_midi >= mask.hi_midi) {
            std::unique_lock lock(state_->mask_mu);
            if (!state_->mask_busy && !state_->mask_request) {
                return;
            }
            state_->mask_idle_cv.wait(lock, [&] {
                return !state_->mask_busy && !state_->mask_request;
            });
            return;
        }
        bool ready = false;
        {
            std::lock_guard lock(state_->spectral_mu);
            ready = spectral_cache_matches(mask, stems.get());
        }
        if (ready) {
            std::unique_lock lock(state_->mask_mu);
            if (!state_->mask_busy && !state_->mask_request) {
                return;
            }
            state_->mask_idle_cv.wait(lock, [&] {
                return !state_->mask_busy && !state_->mask_request;
            });
            continue;
        }
        request_spectral_mask_rebuild();
        std::unique_lock lock(state_->mask_mu);
        state_->mask_idle_cv.wait(lock, [&] {
            return !state_->mask_busy && !state_->mask_request;
        });
    }
}

void Player::reset_pitch_mask_filters() {
    state_->highpass_l.reset();
    state_->highpass_r.reset();
    state_->lowpass_l.reset();
    state_->lowpass_r.reset();
}

void Player::clear_spectral_mask() {
    std::lock_guard lock(state_->spectral_mu);
    if (!state_->spectral_audio) {
        return;
    }
    state_->spectral_audio.reset();
    state_->spectral_frames = 0;
    state_->spectral_stem = -1;
    state_->spectral_notes_revision = 0;
    state_->spectral_source = nullptr;
    discard_ring();
    wake();
}

bool Player::spectral_cache_matches(const PitchMask& mask, const Stems* stems) const {
    return state_->spectral_audio && stems != nullptr && state_->spectral_source == stems &&
           state_->spectral_stem == mask.stem && state_->spectral_lo == mask.lo_midi &&
           state_->spectral_hi == mask.hi_midi && state_->spectral_frames == stems->frames &&
           state_->spectral_backend == mask.backend &&
           state_->spectral_notes_revision == mask.notes_revision;
}

void Player::request_spectral_mask_rebuild() {
    {
        std::lock_guard lock(state_->mask_mu);
        state_->mask_request = true;
    }
    state_->mask_cv.notify_one();
}

void Player::mask_thread_main() {
    for (;;) {
        {
            std::unique_lock lock(state_->mask_mu);
            state_->mask_cv.wait(lock, [&] { return state_->mask_stop || state_->mask_request; });
            if (state_->mask_stop) {
                state_->mask_busy = false;
                state_->mask_request = false;
                state_->mask_idle_cv.notify_all();
                return;
            }
            state_->mask_request = false;
            state_->mask_busy = true;
        }
        state_->mask_building.store(true);
        state_->mask_progress.store(0.f);

        PitchMask mask;
        {
            std::lock_guard lock(state_->mix_mu);
            mask = state_->published_mask;
        }
        const bool cached =
            mask.backend == PitchMaskBackend::Spectral || mask.backend == PitchMaskBackend::Harmonic;
        auto finish_idle = [&]() {
            state_->mask_building.store(false);
            {
                std::lock_guard lock(state_->mask_mu);
                state_->mask_busy = false;
            }
            state_->mask_idle_cv.notify_all();
        };
        if (!mask.enabled || !cached || mask.lo_midi >= mask.hi_midi) {
            clear_spectral_mask();
            state_->mask_progress.store(0.f);
            finish_idle();
            continue;
        }

        std::shared_ptr<const Stems> stems;
        {
            std::lock_guard lock(state_->song_mu);
            stems = state_->published_stems;
        }
        if (!stems || stems->frames <= 0) {
            clear_spectral_mask();
            state_->mask_progress.store(0.f);
            finish_idle();
            continue;
        }
        const auto& source = stems->audio[static_cast<std::size_t>(mask.stem)];
        if (static_cast<std::int64_t>(source.size()) < stems->frames * 2) {
            clear_spectral_mask();
            state_->mask_progress.store(0.f);
            finish_idle();
            continue;
        }

        bool already = false;
        {
            std::lock_guard lock(state_->spectral_mu);
            already = spectral_cache_matches(mask, stems.get());
        }
        if (already) {
            state_->mask_progress.store(1.f);
            finish_idle();
            continue;
        }

        auto filtered = std::make_shared<std::vector<float>>(static_cast<std::size_t>(stems->frames * 2));
        auto report = [&](float fraction) {
            state_->mask_progress.store(std::clamp(fraction, 0.f, 1.f));
        };
        if (mask.backend == PitchMaskBackend::Harmonic) {
            const PitchMaskNote* notes = mask.notes ? mask.notes->data() : nullptr;
            const std::size_t count = mask.notes ? mask.notes->size() : 0;
            spectral_harmonic_planar(
                source.data(), filtered->data(), stems->frames, mask.lo_midi, mask.hi_midi, notes,
                count, kSampleRate, report);
        } else {
            spectral_brickwall_planar(
                source.data(), filtered->data(), stems->frames, mask.lo_midi, mask.hi_midi,
                kSampleRate, report);
        }

        PitchMask latest;
        {
            std::lock_guard lock(state_->mix_mu);
            latest = state_->published_mask;
        }
        const bool still_wanted =
            latest.enabled && latest.backend == mask.backend && latest.stem == mask.stem &&
            latest.lo_midi == mask.lo_midi && latest.hi_midi == mask.hi_midi &&
            latest.notes_revision == mask.notes_revision;
        if (!still_wanted) {
            {
                std::lock_guard lock(state_->mask_mu);
                state_->mask_busy = false;
                if (!state_->mask_stop) {
                    state_->mask_request = true;
                    state_->mask_building.store(true);
                    state_->mask_progress.store(0.f);
                } else {
                    state_->mask_building.store(false);
                }
            }
            state_->mask_cv.notify_one();
            state_->mask_idle_cv.notify_all();
            continue;
        }

        {
            std::lock_guard lock(state_->spectral_mu);
            state_->spectral_audio = std::move(filtered);
            state_->spectral_frames = stems->frames;
            state_->spectral_stem = mask.stem;
            state_->spectral_lo = mask.lo_midi;
            state_->spectral_hi = mask.hi_midi;
            state_->spectral_backend = mask.backend;
            state_->spectral_notes_revision = mask.notes_revision;
            state_->spectral_source = stems.get();
        }
        discard_ring();
        wake();
        state_->mask_progress.store(1.f);
        finish_idle();
    }
}

void Player::sync_pitch_mask_filters() {
    const PitchMask& mask = state_->block_mask;
    const bool want =
        mask.enabled && mask.backend == PitchMaskBackend::Dsp && mask.lo_midi < mask.hi_midi;
    if (!want) {
        if (state_->filter_armed) {
            reset_pitch_mask_filters();
            state_->filter_armed = false;
        }
        return;
    }

    const float target_lo = midi_to_hz(static_cast<float>(mask.lo_midi));
    const float target_hi =
        midi_to_hz(static_cast<float>(mask.hi_midi + kPitchMaskHarmonicSemitones));
    state_->target_lo_hz = target_lo;
    state_->target_hi_hz = std::max(target_hi, target_lo * 1.05f);

    if (!state_->filter_armed) {
        state_->applied_lo_hz = state_->target_lo_hz;
        state_->applied_hi_hz = state_->target_hi_hz;
        state_->highpass = make_highpass(state_->applied_lo_hz, static_cast<float>(kSampleRate));
        state_->lowpass = make_lowpass(state_->applied_hi_hz, static_cast<float>(kSampleRate));
        reset_pitch_mask_filters();
        state_->filter_armed = true;
        return;
    }

    state_->applied_lo_hz += (state_->target_lo_hz - state_->applied_lo_hz) * kPitchMaskSmooth;
    state_->applied_hi_hz += (state_->target_hi_hz - state_->applied_hi_hz) * kPitchMaskSmooth;
    if (state_->applied_hi_hz < state_->applied_lo_hz * 1.05f) {
        state_->applied_hi_hz = state_->applied_lo_hz * 1.05f;
    }
    state_->highpass = make_highpass(state_->applied_lo_hz, static_cast<float>(kSampleRate));
    state_->lowpass = make_lowpass(state_->applied_hi_hz, static_cast<float>(kSampleRate));
}

void Player::play() {
    const bool was_counting = state_->counting.exchange(false);
    state_->count_serial.fetch_add(1, std::memory_order_release);
    if (was_counting) {
        discard_ring();
    }
    state_->playing.store(true);
    wake();
}

void Player::pause() {
    state_->counting.store(false);
    state_->count_serial.fetch_add(1, std::memory_order_release);
    state_->playing.store(false);
    discard_ring();
    wake();
}

void Player::count_in(int beats) {
    if (!state_->ready.load() || beats <= 0) {
        return;
    }
    double interval = 60.0 / std::clamp(static_cast<double>(state_->bpm.load()), 40.0, 240.0);
    {
        std::lock_guard lock(state_->beat_mu);
        if (state_->follow_beats && state_->beat_times && state_->beat_times->size() >= 2) {
            const auto& times = *state_->beat_times;
            const double position = state_->position.load();
            const auto it = std::lower_bound(times.begin(), times.end(), position - 1e-4);
            double gap = 0;
            if (it == times.end()) {
                gap = times.back() - times[times.size() - 2];
            } else if (it == times.begin()) {
                gap = times[1] - times[0];
            } else {
                gap = *it - *(it - 1);
            }
            if (gap >= 0.2 && gap <= 1.5) {
                interval = gap;
            }
        }
    }
    const double speed = std::max(0.1, static_cast<double>(state_->speed.load()));
    const int samples = std::max(1, static_cast<int>(std::llround(interval / speed * static_cast<double>(kSampleRate))));
    state_->count_beats.store(std::clamp(beats, 1, 16));
    state_->count_interval.store(samples);
    state_->counting.store(true);
    state_->count_serial.fetch_add(1, std::memory_order_release);
    state_->playing.store(false);
    discard_ring();
    wake();
}

void Player::stop() {
    pause();
    const double loop_a = state_->loop_a.load();
    const double loop_b = state_->loop_b.load();
    const bool loop = state_->loop.load() && loop_a >= 0 && loop_b > loop_a + 0.01;
    seek(loop ? loop_a : 0);
}

void Player::seek(double seconds) {
    if (state_->counting.exchange(false)) {
        state_->count_serial.fetch_add(1, std::memory_order_release);
        discard_ring();
    }
    const double length = state_->length.load();
    if (length > 0) {
        seconds = std::clamp(seconds, 0.0, length);
    } else {
        seconds = std::max(0.0, seconds);
    }
    state_->position.store(seconds);
    state_->seek_to.store(seconds);
    state_->seek_serial.fetch_add(1, std::memory_order_release);
    discard_ring();
    wake();
}

void Player::set_speed(float speed) {
    state_->speed.store(std::clamp(speed, 0.1f, 2.f));
    wake();
}

void Player::set_master_db(float db) {
    db = std::clamp(db, -60.f, 6.f);
    state_->master_db.store(db);
    state_->master_linear.store(decibel_to_gain(db));
}

void Player::set_loop_enabled(bool enabled) {
    state_->loop.store(enabled);
}

void Player::set_loop_a(double seconds) {
    const double length = state_->length.load();
    if (length > 0) {
        seconds = std::clamp(seconds, 0.0, length);
    }
    state_->loop_a.store(seconds);
}

void Player::set_loop_b(double seconds) {
    const double length = state_->length.load();
    if (length > 0) {
        seconds = std::clamp(seconds, 0.0, length);
    }
    state_->loop_b.store(seconds);
}

void Player::clear_loop() {
    state_->loop.store(false);
    state_->loop_a.store(-1);
    state_->loop_b.store(-1);
}

void Player::set_metronome(
    bool enabled, float bpm, float click_db, double beat_offset, bool follow_beats,
    std::shared_ptr<const std::vector<double>> beats) {
    state_->metro.store(enabled);
    state_->bpm.store(std::clamp(bpm, 40.f, 240.f));
    state_->click_db.store(std::clamp(click_db, -60.f, 6.f));
    state_->beat_offset.store(std::max(0.0, beat_offset));
    std::lock_guard lock(state_->beat_mu);
    state_->follow_beats = follow_beats;
    state_->beat_times = std::move(beats);
}

void Player::arm_click(bool downbeat) {
    const float level = std::pow(10.f, state_->click_db.load() / 20.f);
    state_->click_amp = std::min(0.35f, level * (downbeat ? 0.2f : 0.12f));
    state_->click_freq = downbeat ? 1760.f : 1175.f;
    state_->click_phase = 0.f;
    state_->click_left = static_cast<int>(0.045 * kSampleRate);
}

void Player::accept_count_in() {
    const auto serial = state_->count_serial.load(std::memory_order_acquire);
    if (serial == state_->applied_count_serial) {
        return;
    }
    state_->applied_count_serial = serial;
    if (!state_->counting.load()) {
        state_->count_left = 0;
        state_->count_wait = 0;
        return;
    }
    state_->count_total = std::max(1, state_->count_beats.load());
    state_->count_left = state_->count_total;
    state_->count_gap = std::max(1, state_->count_interval.load());
    state_->count_wait = 0;
    state_->click_left = 0;
}

int Player::render_count_in(float* interleaved, int frames) {
    std::fill_n(interleaved, frames * 2, 0.f);
    int cursor = 0;
    while (cursor < frames) {
        if (state_->count_wait <= 0) {
            if (state_->count_left <= 0) {
                state_->counting.store(false);
                state_->playing.store(true);
                const int rest = frames - cursor;
                if (rest <= 0) {
                    return cursor;
                }
                return cursor + synthesize(interleaved + cursor * 2, rest);
            }
            arm_click(state_->count_left == state_->count_total);
            --state_->count_left;
            state_->count_wait = state_->count_gap;
        }
        const int step = std::min(state_->count_wait, frames - cursor);
        render_click(interleaved + cursor * 2, step);
        state_->count_wait -= step;
        cursor += step;
    }
    return frames;
}

void Player::render_click(float* interleaved, int frames) {
    const int total = static_cast<int>(0.045 * kSampleRate);
    for (int i = 0; i < frames && state_->click_left > 0; ++i) {
        const float elapsed = 1.f - static_cast<float>(state_->click_left) / static_cast<float>(total);
        const float sample = (state_->click_phase < 0.5f ? 1.f : -1.f) * state_->click_amp * std::exp(-elapsed * 6.f);
        interleaved[i * 2] += sample;
        interleaved[i * 2 + 1] += sample;
        state_->click_phase += state_->click_freq / static_cast<float>(kSampleRate);
        if (state_->click_phase >= 1.f) {
            state_->click_phase -= 1.f;
        }
        --state_->click_left;
    }
}

void Player::mix_metronome(float* interleaved, int frames, double from, double to) {
    if (!state_->metro.load()) {
        state_->click_left = 0;
        return;
    }
    if (frames <= 0) {
        return;
    }
    if (!(to > from)) {
        render_click(interleaved, frames);
        return;
    }
    bool follow = false;
    std::shared_ptr<const std::vector<double>> times;
    {
        std::lock_guard lock(state_->beat_mu);
        follow = state_->follow_beats;
        times = state_->beat_times;
    }
    int cursor = 0;
    const double span = to - from;
    auto click_at = [&](double time, bool downbeat) {
        if (time < from - 1e-4 || !(time < to - 1e-9)) {
            return;
        }
        int at = static_cast<int>(std::llround((time - from) / span * static_cast<double>(frames)));
        at = std::clamp(at, 0, frames);
        if (at > cursor) {
            render_click(interleaved + cursor * 2, at - cursor);
            cursor = at;
        }
        arm_click(downbeat);
    };
    if (follow) {
        if (times) {
            const auto begin = times->begin();
            auto it = std::lower_bound(begin, times->end(), from - 1e-4);
            for (; it != times->end() && *it < to - 1e-9; ++it) {
                click_at(*it, (it - begin) % 4 == 0);
            }
        }
    } else {
        const double bpm = std::clamp(static_cast<double>(state_->bpm.load()), 40.0, 240.0);
        const double interval = 60.0 / bpm;
        const double offset = state_->beat_offset.load();
        long long index = static_cast<long long>(std::ceil((from - offset - 1e-4) / interval));
        if (index < 0) {
            index = 0;
        }
        while (offset + static_cast<double>(index) * interval < to - 1e-9) {
            click_at(offset + static_cast<double>(index) * interval, index % 4 == 0);
            ++index;
            if (index > 1000000) {
                break;
            }
        }
    }
    if (cursor < frames) {
        render_click(interleaved + cursor * 2, frames - cursor);
    }
}

Player::Snapshot Player::snapshot() const {
    Snapshot snap;
    snap.playing = state_->playing.load();
    snap.ready = state_->ready.load();
    snap.position = state_->position.load();
    snap.length = state_->length.load();
    snap.speed = state_->speed.load();
    snap.master_db = state_->master_db.load();
    snap.loop = state_->loop.load();
    snap.loop_a = state_->loop_a.load();
    snap.loop_b = state_->loop_b.load();
    snap.counting = state_->counting.load();
    return snap;
}

void Player::install_song() {
    std::shared_ptr<const Stems> stems;
    std::uint64_t generation = 0;
    {
        std::lock_guard lock(state_->song_mu);
        stems = state_->published_stems;
        generation = state_->published_generation;
    }
    if (generation == state_->applied_generation) {
        return;
    }
    state_->applied_generation = generation;
    state_->stems = std::move(stems);
    const bool ready = state_->stems != nullptr && state_->stems->frames > 0;
    state_->ready.store(ready);
    const double length = ready ? static_cast<double>(state_->stems->frames) / static_cast<double>(kSampleRate) : 0;
    state_->length.store(length);
    state_->position.store(0);
    state_->metro_song = 0;
    state_->click_left = 0;
    state_->applied_speed = state_->speed.load();
    state_->stretcher.set_rate(state_->applied_speed);
    state_->stretcher.reset(0);
    state_->applied_seek = state_->seek_serial.load(std::memory_order_acquire);
    reset_pitch_mask_filters();
    {
        std::lock_guard lock(state_->spectral_mu);
        if (state_->spectral_source != state_->stems.get()) {
            state_->spectral_audio.reset();
            state_->spectral_frames = 0;
            state_->spectral_stem = -1;
            state_->spectral_notes_revision = 0;
            state_->spectral_source = nullptr;
        }
    }
}

void Player::apply_transport() {
    const auto serial = state_->seek_serial.load(std::memory_order_acquire);
    if (serial != state_->applied_seek) {
        state_->applied_seek = serial;
        const double seconds = state_->seek_to.load();
        state_->stretcher.reset(seconds * static_cast<double>(kSampleRate));
        state_->position.store(seconds);
        state_->metro_song = seconds;
        state_->click_left = 0;
        reset_pitch_mask_filters();
    }
    const float speed = state_->speed.load();
    if (speed != state_->applied_speed) {
        state_->applied_speed = speed;
        state_->stretcher.set_rate(speed);
        state_->stretcher.reset(state_->position.load() * static_cast<double>(kSampleRate));
        reset_pitch_mask_filters();
    }
}

int Player::read_source(int start, int frames, float* interleaved) {
    const auto& stems = state_->stems;
    if (!stems || frames <= 0 || start >= stems->frames) {
        return 0;
    }
    const int available = static_cast<int>(std::min<std::int64_t>(frames, stems->frames - start));
    std::fill_n(interleaved, available * 2, 0.f);
    const Mix& mix = state_->block_mix;
    const PitchMask& mask = state_->block_mask;
    const bool dsp_mask = state_->filter_armed && mask.enabled &&
                          mask.backend == PitchMaskBackend::Dsp && mask.lo_midi < mask.hi_midi;
    const bool cached_mask = mask.enabled && (mask.backend == PitchMaskBackend::Spectral ||
                                              mask.backend == PitchMaskBackend::Harmonic) &&
                             mask.lo_midi < mask.hi_midi;
    std::shared_ptr<const std::vector<float>> spectral;
    std::int64_t spectral_frames = 0;
    int spectral_stem = -1;
    if (cached_mask) {
        std::lock_guard lock(state_->spectral_mu);
        // Exact match preferred; otherwise keep same-stem stale cache while rebuild runs.
        if (state_->spectral_audio && state_->spectral_source == stems.get() &&
            state_->spectral_stem == mask.stem && state_->spectral_backend == mask.backend) {
            spectral = state_->spectral_audio;
            spectral_frames = state_->spectral_frames;
            spectral_stem = state_->spectral_stem;
        }
    }
    bool any_solo = false;
    for (bool solo : mix.solo) {
        any_solo = any_solo || solo;
    }
    const float master = state_->master_linear.load();
    for (int stem = 0; stem < kStemCount; ++stem) {
        const bool audible = !mix.mute[static_cast<std::size_t>(stem)] &&
                             (!any_solo || mix.solo[static_cast<std::size_t>(stem)]);
        if (!audible) {
            continue;
        }
        const auto& audio = stems->audio[static_cast<std::size_t>(stem)];
        if (static_cast<std::int64_t>(audio.size()) < (stems->frames) * 2) {
            continue;
        }
        const bool use_spectral =
            spectral && stem == spectral_stem && spectral_frames == stems->frames &&
            static_cast<std::int64_t>(spectral->size()) >= stems->frames * 2;
        // While a new mask is building, keep the previous cache (same stem) to avoid hitches.
        // Before the first cache exists, silence the masked stem instead of leaking dry audio.
        if (cached_mask && stem == mask.stem && !use_spectral) {
            continue;
        }
        const float* planar = use_spectral ? spectral->data() : audio.data();
        const float gain = mix.gain[static_cast<std::size_t>(stem)] * master;
        const float pan = std::clamp(mix.pan[static_cast<std::size_t>(stem)], -1.f, 1.f);
        const float theta = (pan + 1.f) * 0.78539816339f;
        const float pan_l = std::cos(theta) * 1.41421356237f;
        const float pan_r = std::sin(theta) * 1.41421356237f;
        const float* left = planar + start;
        const float* right = planar + stems->frames + start;
        const bool filter_this = dsp_mask && stem == mask.stem;
        for (int i = 0; i < available; ++i) {
            float sample_l = left[i] * gain * pan_l;
            float sample_r = right[i] * gain * pan_r;
            if (filter_this) {
                sample_l = state_->highpass_l.process(sample_l, state_->highpass);
                sample_l = state_->lowpass_l.process(sample_l, state_->lowpass);
                sample_r = state_->highpass_r.process(sample_r, state_->highpass);
                sample_r = state_->lowpass_r.process(sample_r, state_->lowpass);
            }
            interleaved[i * 2] += sample_l;
            interleaved[i * 2 + 1] += sample_r;
        }
    }
    return available;
}

int Player::synthesize(float* interleaved, int frames) {
    {
        std::lock_guard lock(state_->mix_mu);
        state_->block_mix = state_->published_mix;
        const bool stem_changed = state_->block_mask.stem != state_->published_mask.stem;
        const bool enabled_changed = state_->block_mask.enabled != state_->published_mask.enabled;
        state_->block_mask = state_->published_mask;
        if (stem_changed || enabled_changed) {
            reset_pitch_mask_filters();
            state_->filter_armed = false;
        }
    }
    sync_pitch_mask_filters();
    int done = 0;
    while (done < frames) {
        const double position = state_->stretcher.position() / static_cast<double>(kSampleRate);
        const double loop_a = state_->loop_a.load();
        const double loop_b = state_->loop_b.load();
        const bool loop = state_->loop.load() && loop_a >= 0 && loop_b > loop_a + 0.01;
        if (loop && position >= loop_b) {
            state_->stretcher.reset(loop_a * static_cast<double>(kSampleRate));
            state_->position.store(loop_a);
            state_->metro_song = loop_a;
            continue;
        }
        int chunk = frames - done;
        if (loop && position < loop_b) {
            const double rate = std::max(static_cast<double>(state_->applied_speed), 0.1);
            const int remaining = static_cast<int>(std::ceil((loop_b - position) / rate));
            chunk = std::min(chunk, std::max(remaining, 1));
        }
        const double song_before = state_->metro_song;
        const int count = state_->stretcher.pull(interleaved + done * 2, chunk);
        const double song_after = state_->stretcher.position() / static_cast<double>(kSampleRate);
        state_->position.store(song_after);
        if (count > 0) {
            double scan_to = song_after;
            bool wrapped = false;
            if (loop && song_before < loop_b && song_after >= loop_b) {
                scan_to = loop_b;
                wrapped = true;
            }
            if (song_after + 1e-3 < song_before) {
                state_->click_left = 0;
                state_->metro_song = song_after;
            } else {
                mix_metronome(interleaved + done * 2, count, song_before, scan_to);
                state_->metro_song = wrapped ? loop_a : song_after;
            }
        }
        if (count <= 0) {
            state_->playing.store(false);
            std::fill_n(interleaved + done * 2, (frames - done) * 2, 0.f);
            break;
        }
        done += count;
    }
    return done;
}

int Player::render(float* interleaved, int frames) {
    install_song();
    apply_transport();
    accept_count_in();
    wait_pitch_mask();
    if (interleaved == nullptr || frames <= 0) {
        return 0;
    }
    if (state_->counting.load()) {
        return render_count_in(interleaved, frames);
    }
    if (!state_->playing.load() || !state_->ready.load()) {
        std::fill_n(interleaved, frames * 2, 0.f);
        return frames;
    }
    return synthesize(interleaved, frames);
}

int Player::ring_space() const {
    const auto read = std::max(
        state_->read_cursor.load(std::memory_order_acquire),
        state_->discard_before.load(std::memory_order_acquire));
    const auto write = state_->write_cursor.load(std::memory_order_relaxed);
    return kRingFrames - static_cast<int>(write - read);
}

void Player::ring_write(const float* interleaved, int frames) {
    const auto write = state_->write_cursor.load(std::memory_order_relaxed);
    for (int i = 0; i < frames; ++i) {
        const auto slot = static_cast<std::size_t>((write + static_cast<std::uint64_t>(i)) % kRingFrames);
        state_->ring[slot * 2] = interleaved[i * 2];
        state_->ring[slot * 2 + 1] = interleaved[i * 2 + 1];
    }
    state_->write_cursor.store(write + static_cast<std::uint64_t>(frames), std::memory_order_release);
}

int Player::ring_read(float* interleaved, int frames) {
    auto read = state_->read_cursor.load(std::memory_order_relaxed);
    const auto discard = state_->discard_before.load(std::memory_order_acquire);
    if (read < discard) {
        read = discard;
    }
    const auto write = state_->write_cursor.load(std::memory_order_acquire);
    const int available = static_cast<int>(write - read);
    const int count = std::max(0, std::min(frames, available));
    for (int i = 0; i < count; ++i) {
        const auto slot = static_cast<std::size_t>((read + static_cast<std::uint64_t>(i)) % kRingFrames);
        interleaved[i * 2] = state_->ring[slot * 2];
        interleaved[i * 2 + 1] = state_->ring[slot * 2 + 1];
    }
    state_->read_cursor.store(read + static_cast<std::uint64_t>(count), std::memory_order_release);
    if (count < frames) {
        std::fill_n(interleaved + count * 2, (frames - count) * 2, 0.f);
    }
    return count;
}

void Player::thread_main() {
    std::vector<float> block(static_cast<std::size_t>(kBlockFrames * 2));
    while (state_->running.load()) {
        install_song();
        apply_transport();
        accept_count_in();
        const bool active = state_->playing.load() || state_->counting.load();
        if (!active || !state_->ready.load() || ring_space() < kBlockFrames) {
            std::unique_lock lock(state_->wait_mu);
            state_->wait_cv.wait_for(lock, std::chrono::milliseconds(5), [&] {
                return !state_->running.load() || state_->wake;
            });
            state_->wake = false;
            continue;
        }
        const int count = state_->counting.load() ? render_count_in(block.data(), kBlockFrames) : synthesize(block.data(), kBlockFrames);
        if (count > 0) {
            ring_write(block.data(), count);
        }
    }
}

bool Player::ensure_context() {
    if (state_->context_open) {
        return true;
    }
    const ma_context_config config = ma_context_config_init();
    if (ma_context_init(nullptr, 0, &config, &state_->context) != MA_SUCCESS) {
        device_error_ = "playback device failed to open";
        return false;
    }
    state_->context_open = true;
    return true;
}

std::expected<void, std::string> Player::open_playback(const void* device_id) {
    if (!ensure_context()) {
        return std::unexpected(device_error_);
    }
    const bool starting_worker = !state_->running.load();
    if (starting_worker) {
        state_->running.store(true);
        state_->worker = std::thread([this] { thread_main(); });
    }
    if (state_->device_open) {
        ma_device_stop(&state_->device);
        ma_device_uninit(&state_->device);
        state_->device_open = false;
    }

    ma_device_config config = ma_device_config_init(ma_device_type_playback);
    config.playback.format = ma_format_f32;
    config.playback.channels = 2;
    config.playback.pDeviceID = static_cast<const ma_device_id*>(device_id);
    config.sampleRate = kSampleRate;
    config.pUserData = this;
    config.dataCallback = [](ma_device* device, void* output, const void*, ma_uint32 frame_count) {
        auto* player = static_cast<Player*>(device->pUserData);
        player->ring_read(static_cast<float*>(output), static_cast<int>(frame_count));
    };
    if (ma_device_init(&state_->context, &config, &state_->device) != MA_SUCCESS) {
        if (device_id != nullptr) {
            auto fallback = open_playback(nullptr);
            (void)fallback;
            device_error_ = "playback device failed to open";
            return std::unexpected(device_error_);
        }
        if (starting_worker) {
            state_->running.store(false);
            wake();
            if (state_->worker.joinable()) {
                state_->worker.join();
            }
        }
        device_error_ = "playback device failed to open";
        return std::unexpected(device_error_);
    }
    state_->device_open = true;
    if (ma_device_start(&state_->device) != MA_SUCCESS) {
        ma_device_uninit(&state_->device);
        state_->device_open = false;
        if (device_id != nullptr) {
            auto fallback = open_playback(nullptr);
            (void)fallback;
            device_error_ = "playback device failed to start";
            return std::unexpected(device_error_);
        }
        if (starting_worker) {
            state_->running.store(false);
            wake();
            if (state_->worker.joinable()) {
                state_->worker.join();
            }
        }
        device_error_ = "playback device failed to start";
        return std::unexpected(device_error_);
    }
    device_error_.clear();
    return {};
}

std::vector<PlaybackDevice> Player::playback_devices() {
    std::vector<PlaybackDevice> devices;
    if (!ensure_context()) {
        return devices;
    }
    ma_device_info* infos = nullptr;
    ma_uint32 count = 0;
    if (ma_context_get_devices(&state_->context, &infos, &count, nullptr, nullptr) != MA_SUCCESS || infos == nullptr) {
        return devices;
    }
    devices.reserve(count);
    for (ma_uint32 index = 0; index < count; ++index) {
        PlaybackDevice device;
        device.id = device_token(infos[index].id);
        device.name = infos[index].name;
        devices.push_back(std::move(device));
    }
    return devices;
}

std::expected<void, std::string> Player::start_device() {
    if (state_->device_open) {
        return {};
    }
    return open_playback(nullptr);
}

std::expected<void, std::string> Player::use_device(std::string_view id) {
    if (id.empty()) {
        return open_playback(nullptr);
    }
    if (!ensure_context()) {
        return std::unexpected(device_error_);
    }
    ma_device_info* infos = nullptr;
    ma_uint32 count = 0;
    if (ma_context_get_devices(&state_->context, &infos, &count, nullptr, nullptr) != MA_SUCCESS || infos == nullptr) {
        device_error_ = "playback device is unavailable";
        return std::unexpected(device_error_);
    }
    for (ma_uint32 index = 0; index < count; ++index) {
        if (device_token(infos[index].id) != id) {
            continue;
        }
        const ma_device_id chosen = infos[index].id;
        return open_playback(&chosen);
    }
    device_error_ = "playback device is unavailable";
    return std::unexpected(device_error_);
}

}  // namespace bt
