#include "bt/player.hpp"

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

float decibel_to_gain(float db) {
    return std::pow(10.f, db / 20.f);
}

std::string device_token(const ma_device_id& id) {
    const auto* bytes = reinterpret_cast<const unsigned char*>(&id);
    std::string token(sizeof(id) * 2, '0');
    for (std::size_t index = 0; index < sizeof(id); ++index) {
        std::snprintf(token.data() + index * 2, 3, "%02x", bytes[index]);
    }
    return token;
}

}  // namespace

struct Player::Mix {
    std::array<float, kStemCount> gain{};
    std::array<float, kStemCount> pan{};
    std::array<bool, kStemCount> mute{};
    std::array<bool, kStemCount> solo{};

    Mix() { gain.fill(1.f); }
};

struct Player::State {
    TimeStretcher stretcher;
    std::mutex mix_mu;
    Mix published_mix;
    Mix block_mix;
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
}

Player::~Player() {
    if (state_ == nullptr) {
        return;
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
    }
    const float speed = state_->speed.load();
    if (speed != state_->applied_speed) {
        state_->applied_speed = speed;
        state_->stretcher.set_rate(speed);
        state_->stretcher.reset(state_->position.load() * static_cast<double>(kSampleRate));
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
        const float gain = mix.gain[static_cast<std::size_t>(stem)] * master;
        const float pan = std::clamp(mix.pan[static_cast<std::size_t>(stem)], -1.f, 1.f);
        const float theta = (pan + 1.f) * 0.78539816339f;
        const float pan_l = std::cos(theta) * 1.41421356237f;
        const float pan_r = std::sin(theta) * 1.41421356237f;
        const float* left = audio.data() + start;
        const float* right = audio.data() + stems->frames + start;
        for (int i = 0; i < available; ++i) {
            interleaved[i * 2] += left[i] * gain * pan_l;
            interleaved[i * 2 + 1] += right[i] * gain * pan_r;
        }
    }
    return available;
}

int Player::synthesize(float* interleaved, int frames) {
    {
        std::lock_guard lock(state_->mix_mu);
        state_->block_mix = state_->published_mix;
    }
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
