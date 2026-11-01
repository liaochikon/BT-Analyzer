#include "beats.hpp"
#include "notes.hpp"
#include "ui.hpp"

#include "bt/audio.hpp"
#include "bt/beat_track.hpp"

#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_floating_point.hpp>

#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <filesystem>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace {

constexpr int kCancelLoops = 30;
constexpr int kRerunPasses = 10;
constexpr int kDeviceSwitches = 50;

void identity(const float* mix, float* stems, int segment_samples) {
    for (int stem = 0; stem < bt::kStemCount; ++stem) {
        std::copy_n(mix, 2 * segment_samples, stems + static_cast<std::size_t>(stem) * 2 * segment_samples);
    }
}

bt::Audio ramp(std::int64_t frames, float scale) {
    bt::Audio audio;
    audio.sample_rate = bt::kSampleRate;
    audio.channels = 2;
    audio.frames = frames;
    audio.samples.resize(static_cast<std::size_t>(2 * frames));
    for (std::int64_t i = 0; i < frames; ++i) {
        const float value = scale * (-0.4f + 1.2f * static_cast<float>(i) / static_cast<float>(frames));
        audio.samples[static_cast<std::size_t>(i)] = value;
        audio.samples[static_cast<std::size_t>(frames + i)] = value * 0.5f;
    }
    return audio;
}

struct TempDir {
    std::filesystem::path path;

    explicit TempDir(const std::string& name) {
        path = std::filesystem::temp_directory_path() / name;
        std::filesystem::remove_all(path);
        std::filesystem::create_directories(path);
    }

    ~TempDir() {
        std::error_code error;
        std::filesystem::remove_all(path, error);
    }
};

struct Gate {
    std::mutex mutex;
    std::condition_variable cv;
    bool entered = false;
    bool go = false;

    void reset() {
        std::lock_guard lock(mutex);
        entered = false;
        go = false;
    }

    void enter() {
        std::unique_lock lock(mutex);
        entered = true;
        cv.notify_all();
        cv.wait(lock, [&] { return go; });
    }

    bool wait_entered() {
        std::unique_lock lock(mutex);
        return cv.wait_for(lock, std::chrono::seconds(5), [&] { return entered; });
    }

    void release() {
        std::lock_guard lock(mutex);
        go = true;
        cv.notify_all();
    }
};

struct ReleaseGate {
    Gate* gate = nullptr;
    ~ReleaseGate() {
        if (gate != nullptr) {
            gate->release();
        }
    }
};

struct Hold {
    std::mutex mutex;
    std::condition_variable cv;
    bool release = false;
    std::atomic<int> active{0};
    std::atomic<int> started{0};

    ~Hold() { let_go(); }

    void let_go() {
        std::lock_guard lock(mutex);
        release = true;
        cv.notify_all();
    }

    void arm() {
        std::lock_guard lock(mutex);
        release = false;
    }

    void enter(const std::function<bool()>& cancelled) {
        active.fetch_add(1);
        started.fetch_add(1);
        cv.notify_all();
        for (;;) {
            {
                std::lock_guard lock(mutex);
                if (release) {
                    break;
                }
            }
            if (cancelled()) {
                break;
            }
            std::unique_lock lock(mutex);
            cv.wait_for(lock, std::chrono::milliseconds(10));
        }
        active.fetch_sub(1);
    }

    void wait_until(const std::function<bool()>& ready) {
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
        while (!ready()) {
            REQUIRE(std::chrono::steady_clock::now() < deadline);
            std::unique_lock lock(mutex);
            cv.wait_for(lock, std::chrono::milliseconds(10));
        }
    }
};

bt::SessionOptions options_with(const std::filesystem::path& cache, const bt::SegmentRunner& run) {
    bt::SessionOptions options;
    options.cache_directory = cache;
    options.run_segment = run;
    options.separate.segment_samples = 32;
    options.separate.overlap = 0.25f;
    return options;
}

std::shared_ptr<const bt::SongSnapshot> require_settled(bt::Session& session, std::uint64_t job) {
    auto snapshot = session.wait_for(job, std::chrono::seconds(5));
    REQUIRE(snapshot != nullptr);
    return snapshot;
}

void bind_player(btapp::App& app, const std::shared_ptr<const bt::SongSnapshot>& song) {
    const bool ready = song != nullptr && song->phase == bt::SongPhase::Ready && song->stems != nullptr &&
        song->job != app.closed_job;
    if (ready && song->stems != app.loaded) {
        app.player->set_song(song->stems);
        app.loaded = song->stems;
    } else if (!ready && app.loaded) {
        app.player->clear_song();
        app.loaded = nullptr;
    }
}

void pump(btapp::App& app) {
    const auto song = app.session->snapshot();
    REQUIRE(song != nullptr);
    btapp::sync_beats(app, song);
    btapp::sync_notes(app, *song);
    bind_player(app, song);
}

int running_stems(const btapp::App& app) {
    int running = 0;
    for (btapp::NoteStatus status : app.note_status) {
        if (status == btapp::NoteStatus::Running) {
            ++running;
        }
    }
    return running;
}

bool notes_ready(const btapp::App& app) {
    for (btapp::NoteStatus status : app.note_status) {
        if (status != btapp::NoteStatus::Ready) {
            return false;
        }
    }
    return true;
}

bool has_pitch(const btapp::App& app, int pitch) {
    for (const auto& stem : app.notes) {
        for (const btapp::MidiNote& note : stem) {
            if (note.pitch == pitch) {
                return true;
            }
        }
    }
    return false;
}

std::shared_ptr<bt::Stems> quiet_song(std::int64_t frames) {
    auto stems = std::make_shared<bt::Stems>();
    stems->frames = frames;
    for (auto& audio : stems->audio) {
        audio.assign(static_cast<std::size_t>(frames * 2), 0.05f);
    }
    return stems;
}

}  // namespace

TEST_CASE("runtime cancel during separation then play the next song") {
    TempDir temp("bt-runtime-cancel");
    Gate gate;
    std::atomic<int> block_once{0};
    auto created = bt::Session::create(options_with(temp.path / "cache", [&](const float* mix, float* stems, int n) {
        if (block_once.exchange(0) > 0) {
            gate.enter();
        }
        identity(mix, stems, n);
    }));
    REQUIRE(created.has_value());
    auto session = std::move(*created);
    bt::Player player;
    btapp::App app;
    app.session = session.get();
    app.player = &player;

    std::array<float, bt::kStemCount> gain{};
    gain.fill(1.f);
    player.set_mix(gain, {}, {});

    for (int loop = 0; loop < kCancelLoops; ++loop) {
        gate.reset();
        block_once.store(1);
        ReleaseGate releaser{&gate};
        const auto first_audio = ramp(96, 0.15f + 0.01f * static_cast<float>(loop));
        const auto second_audio = ramp(96, -0.2f - 0.01f * static_cast<float>(loop));
        const auto first_song = temp.path / ("a-" + std::to_string(loop) + ".wav");
        const auto second_song = temp.path / ("b-" + std::to_string(loop) + ".wav");
        REQUIRE(bt::write_float_wav(first_song, first_audio.samples.data(), first_audio.channels, first_audio.frames).has_value());
        REQUIRE(bt::write_float_wav(second_song, second_audio.samples.data(), second_audio.channels, second_audio.frames).has_value());

        const auto original = session->open_song(first_song);
        REQUIRE(gate.wait_entered());
        std::weak_ptr<const bt::Audio> leaked;
        {
            const auto mid = session->snapshot();
            REQUIRE(mid != nullptr);
            REQUIRE(mid->phase == bt::SongPhase::Separating);
            REQUIRE(mid->mix != nullptr);
            leaked = mid->mix;
        }
        session->cancel();
        const auto replacement = session->open_song(second_song);
        gate.release();

        const auto cancelled = require_settled(*session, original);
        const auto ready = require_settled(*session, replacement);
        REQUIRE(cancelled->phase == bt::SongPhase::Cancelled);
        REQUIRE(cancelled->mix == nullptr);
        REQUIRE(cancelled->stems == nullptr);
        REQUIRE(ready->phase == bt::SongPhase::Ready);
        REQUIRE(ready->source == second_song);
        REQUIRE(ready->stems != nullptr);
        REQUIRE(leaked.expired());

        bind_player(app, ready);
        player.play();
        std::vector<float> heard(static_cast<std::size_t>(128 * 2));
        REQUIRE(player.render(heard.data(), 128) == 128);
        REQUIRE(std::isfinite(heard[0]));
        const auto transport = player.snapshot();
        REQUIRE(transport.ready);
        REQUIRE(std::isfinite(transport.position));
        REQUIRE_THAT(
            transport.length,
            Catch::Matchers::WithinAbs(static_cast<double>(second_audio.frames) / bt::kSampleRate, 1e-6));
        player.pause();
    }
}

TEST_CASE("runtime a late transcription or beat grid does not attach to the next song") {
    TempDir temp("bt-runtime-late");
    auto created = bt::Session::create(options_with(temp.path / "cache", identity));
    REQUIRE(created.has_value());
    auto session = std::move(*created);

    std::atomic<int> calls{0};
    auto notes = std::make_unique<btapp::NoteService>(
        [&](const float*, std::int64_t, const bt::PitchParameters&, const std::function<void(float)>&,
            const std::function<bool()>& cancelled) -> std::expected<std::vector<bt::Note>, std::string> {
            if (cancelled()) {
                return std::unexpected(std::string("cancelled"));
            }
            bt::Note note;
            note.pitch = ++calls == 1 ? 71 : 40;
            note.end = 0.2;
            return std::vector<bt::Note>{note};
        });
    auto beats = std::make_unique<btapp::BeatService>();
    bt::Player player;
    btapp::App app;
    app.session = session.get();
    app.player = &player;
    app.note_service = notes.get();
    app.beat_service = beats.get();
    app.pitch_ready = true;

    const auto first_audio = ramp(96, 0.3f);
    const auto second_audio = ramp(96, 0.6f);
    const auto first_song = temp.path / "late-a.wav";
    const auto second_song = temp.path / "late-b.wav";
    REQUIRE(bt::write_float_wav(first_song, first_audio.samples.data(), 2, first_audio.frames).has_value());
    REQUIRE(bt::write_float_wav(second_song, second_audio.samples.data(), 2, second_audio.frames).has_value());

    REQUIRE(require_settled(*session, session->open_song(first_song))->phase == bt::SongPhase::Ready);
    btapp::sync_notes(app, *session->snapshot());
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
    while (notes->parked_results() < 1) {
        REQUIRE(std::chrono::steady_clock::now() < deadline);
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
    const auto first = session->snapshot();
    REQUIRE(first != nullptr);
    bt::BeatGrid planted;
    planted.bpm = 180.f;
    planted.offset = 0.25;
    REQUIRE(bt::save_beats(first->cache_directory / "beats.txt", planted).has_value());
    beats->request(first->job, first->cache_directory / "beats.txt", first);
    while (beats->parked_results() < 1) {
        REQUIRE(std::chrono::steady_clock::now() < deadline);
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }

    const auto replacement = require_settled(*session, session->open_song(second_song));
    REQUIRE(replacement->phase == bt::SongPhase::Ready);
    pump(app);
    REQUIRE(app.note_song == replacement->job);
    REQUIRE(app.beat_song == replacement->job);
    REQUIRE_FALSE(has_pitch(app, 71));
    REQUIRE(app.bpm == 120.f);
    REQUIRE(app.beat_detail.empty());
}

TEST_CASE("runtime rerun preempts the single transcription slot") {
    TempDir temp("bt-runtime-rerun");
    auto created = bt::Session::create(options_with(temp.path / "cache", identity));
    REQUIRE(created.has_value());
    auto session = std::move(*created);

    Hold hold;
    auto notes = std::make_unique<btapp::NoteService>(
        [&](const float*, std::int64_t, const bt::PitchParameters& parameters, const std::function<void(float)>&,
            const std::function<bool()>& cancelled) -> std::expected<std::vector<bt::Note>, std::string> {
            hold.enter(cancelled);
            if (cancelled()) {
                return std::unexpected(std::string("cancelled"));
            }
            bt::Note note;
            note.pitch = static_cast<int>(std::lround(parameters.onset_threshold * 100.f));
            note.end = 0.2;
            return std::vector<bt::Note>{note};
        });
    auto beats = std::make_unique<btapp::BeatService>();
    bt::Player player;
    btapp::App app;
    app.session = session.get();
    app.player = &player;
    app.note_service = notes.get();
    app.beat_service = beats.get();
    app.pitch_ready = true;

    for (int pass = 0; pass < kRerunPasses; ++pass) {
        hold.arm();
        const auto audio = ramp(96, 0.2f + 0.02f * static_cast<float>(pass));
        const auto song_path = temp.path / ("rerun-" + std::to_string(pass) + ".wav");
        REQUIRE(bt::write_float_wav(song_path, audio.samples.data(), 2, audio.frames).has_value());
        const auto ready = require_settled(*session, session->open_song(song_path));
        REQUIRE(ready->phase == bt::SongPhase::Ready);
        const int mark = hold.started.load();
        pump(app);
        hold.wait_until([&] { return hold.started.load() > mark && hold.active.load() > 0; });
        REQUIRE(running_stems(app) == 1);
        REQUIRE_FALSE(btapp::interface_locked(app));

        const int stem = 1 + (pass % 5);
        const float onset = 0.21f + 0.03f * static_cast<float>(pass);
        app.pitch_applied[static_cast<std::size_t>(stem)].onset = onset;
        const int queued = hold.started.load();
        btapp::rerun_notes(app, stem);
        pump(app);
        hold.wait_until([&] { return hold.started.load() > queued && hold.active.load() > 0; });
        const auto settle = std::chrono::steady_clock::now() + std::chrono::seconds(5);
        while (running_stems(app) != 1 || app.note_status[static_cast<std::size_t>(stem)] != btapp::NoteStatus::Running) {
            REQUIRE(std::chrono::steady_clock::now() < settle);
            pump(app);
            std::this_thread::sleep_for(std::chrono::milliseconds(5));
        }
        REQUIRE(hold.active.load() == 1);
        REQUIRE(btapp::interface_locked(app));

        hold.let_go();
        const auto done = std::chrono::steady_clock::now() + std::chrono::seconds(5);
        while (!notes_ready(app)) {
            REQUIRE(std::chrono::steady_clock::now() < done);
            pump(app);
            REQUIRE(running_stems(app) <= 1);
            std::this_thread::sleep_for(std::chrono::milliseconds(5));
        }
        REQUIRE_FALSE(btapp::interface_locked(app));
        const int pitch = static_cast<int>(std::lround(onset * 100.f));
        REQUIRE(app.notes[static_cast<std::size_t>(stem)].size() == 1);
        REQUIRE(app.notes[static_cast<std::size_t>(stem)][0].pitch == pitch);
        const auto cache = ready->cache_directory / (std::string(bt::kStemNames[static_cast<std::size_t>(stem)]) + ".notes");
        const auto stored = bt::load_notes(cache);
        REQUIRE(stored.has_value());
        REQUIRE(stored->size() == 1);
        REQUIRE((*stored)[0].pitch == pitch);
        REQUIRE(std::filesystem::exists(ready->cache_directory / "drums.notes"));
    }
}

TEST_CASE("runtime playback survives mix changes and device switches") {
    constexpr std::int64_t kFrames = 88200;
    bt::Player player;
    if (auto started = player.start_device(); !started) {
        auto missing = player.use_device("not-a-real-device");
        REQUIRE_FALSE(missing.has_value());
        SKIP("playback device is unavailable");
    }

    player.set_song(quiet_song(kFrames));
    player.set_master_db(-60.f);
    player.play();
    const auto devices = player.playback_devices();
    std::array<float, bt::kStemCount> gain{};
    std::array<bool, bt::kStemCount> mute{};
    std::array<bool, bt::kStemCount> solo{};
    std::array<float, bt::kStemCount> pan{};
    gain.fill(1.f);

    for (int step = 0; step < kDeviceSwitches; ++step) {
        const auto index = static_cast<std::size_t>(step % bt::kStemCount);
        mute[index] = step % 2 == 0;
        solo[index] = step % 3 == 0;
        pan[index] = step % 2 == 0 ? -1.f : 1.f;
        gain[index] = 0.5f;
        player.set_mix(gain, mute, solo, pan);
        player.set_speed(step % 2 == 0 ? 0.5f : 1.5f);
        player.set_master_db(step % 2 == 0 ? -60.f : -12.f);
        player.set_loop_a(0.2);
        player.set_loop_b(0.8);
        player.set_loop_enabled(step % 2 == 0);
        if (step % 10 == 0) {
            player.seek(0.3);
        }
        std::string id;
        if (!devices.empty() && step % 2 == 1) {
            id = devices[static_cast<std::size_t>(step) % devices.size()].id;
        }
        if (auto switched = player.use_device(id); !switched) {
            REQUIRE(player.use_device("").has_value());
        }
        const auto transport = player.snapshot();
        REQUIRE(transport.ready);
        REQUIRE(transport.length > 1.0);
        REQUIRE(std::isfinite(transport.position));
        REQUIRE(transport.position >= 0.0);
        REQUIRE(transport.position <= transport.length + 0.05);
    }

    auto missing = player.use_device("not-a-real-device");
    REQUIRE_FALSE(missing.has_value());
    REQUIRE_FALSE(player.device_error().empty());
    REQUIRE(player.use_device("").has_value());
    REQUIRE(player.device_error().empty());

    player.set_loop_enabled(false);
    player.set_speed(1.f);
    player.seek(0);
    player.play();
    std::this_thread::sleep_for(std::chrono::seconds(1));
    const auto transport = player.snapshot();
    REQUIRE(std::isfinite(transport.position));
    REQUIRE(transport.position > 0.4);
    REQUIRE(transport.length > 1.0);
    player.stop();
}
