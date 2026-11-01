#include "bt/session.hpp"

#include <catch2/catch_test_macros.hpp>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <filesystem>
#include <mutex>
#include <string>

namespace {

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

    void enter() {
        std::unique_lock lock(mutex);
        entered = true;
        cv.notify_all();
        cv.wait(lock, [&] { return go; });
    }

    void wait_until_entered() {
        std::unique_lock lock(mutex);
        cv.wait(lock, [&] { return entered; });
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

}  // namespace

TEST_CASE("session reuses a cache entry for the same file bytes") {
    TempDir temp("bt-session-cache");
    const auto song = temp.path / "song.wav";
    const auto copy = temp.path / "copy.wav";
    const auto audio = ramp(400, 1.f);
    REQUIRE(bt::write_float_wav(song, audio.samples.data(), audio.channels, audio.frames).has_value());

    std::atomic<int> calls{0};
    auto created = bt::Session::create(options_with(temp.path / "cache", [&](const float* mix, float* stems, int n) {
        ++calls;
        identity(mix, stems, n);
    }));
    REQUIRE(created.has_value());
    auto session = std::move(*created);

    const auto first = require_settled(*session, session->open_song(song));
    REQUIRE(first->phase == bt::SongPhase::Ready);
    REQUIRE_FALSE(first->from_cache);
    REQUIRE(first->project_name == "song");
    REQUIRE(first->project_hash.size() == 64);
    REQUIRE(first->cache_directory.filename() == std::filesystem::path("song_" + first->project_hash));
    REQUIRE(std::filesystem::exists(first->cache_directory / "song.txt"));
    REQUIRE(first->mix != nullptr);
    REQUIRE(first->stems != nullptr);
    REQUIRE(first->gain[4] == 1.f);
    const int computed = calls.load();
    REQUIRE(computed > 0);

    auto loaded = bt::load_audio(song);
    REQUIRE(loaded.has_value());
    bt::SeparateConfig config;
    config.segment_samples = 32;
    config.overlap = 0.25f;
    auto expected = bt::separate_audio(*loaded, identity, config);
    REQUIRE(expected.has_value());
    REQUIRE(first->stems->frames == expected->frames);
    REQUIRE(first->stems->audio[4] == expected->audio[4]);

    const auto second = require_settled(*session, session->open_song(song));
    REQUIRE(second->phase == bt::SongPhase::Ready);
    REQUIRE(second->from_cache);
    REQUIRE(calls.load() == computed);
    REQUIRE(second->stems->audio[0] == first->stems->audio[0]);

    std::filesystem::copy_file(song, copy);
    const auto aliased = require_settled(*session, session->open_song(copy));
    REQUIRE(aliased->from_cache);
    REQUIRE(aliased->project_hash == first->project_hash);
    REQUIRE(aliased->project_name == "song");
    REQUIRE(calls.load() == computed);
    REQUIRE(aliased->source == copy);

    session.reset();
    std::atomic<int> reopened{0};
    auto again = bt::Session::create(options_with(temp.path / "cache", [&](const float* mix, float* stems, int n) {
        ++reopened;
        identity(mix, stems, n);
    }));
    REQUIRE(again.has_value());
    session = std::move(*again);
    const auto warmed = require_settled(*session, session->open_song(song));
    REQUIRE(warmed->from_cache);
    REQUIRE(reopened.load() == 0);
    REQUIRE(warmed->stems->audio[5] == expected->audio[5]);

    const auto other = ramp(400, -0.5f);
    REQUIRE(bt::write_float_wav(song, other.samples.data(), other.channels, other.frames).has_value());
    const auto changed = require_settled(*session, session->open_song(song));
    REQUIRE(changed->phase == bt::SongPhase::Ready);
    REQUIRE_FALSE(changed->from_cache);
    REQUIRE(reopened.load() > 0);
}

TEST_CASE("session cancel takes effect before the next segment and writes no cache") {
    TempDir temp("bt-session-cancel");
    const auto song = temp.path / "song.wav";
    const auto audio = ramp(400, 1.f);
    REQUIRE(bt::write_float_wav(song, audio.samples.data(), audio.channels, audio.frames).has_value());

    std::atomic<int> calls{0};
    Gate gate;
    auto created = bt::Session::create(options_with(temp.path / "cache", [&](const float* mix, float* stems, int n) {
        if (++calls == 1) {
            gate.enter();
        }
        identity(mix, stems, n);
    }));
    REQUIRE(created.has_value());
    auto session = std::move(*created);
    ReleaseGate releaser{&gate};

    const auto job = session->open_song(song);
    gate.wait_until_entered();
    session->cancel();
    gate.release();

    const auto snapshot = require_settled(*session, job);
    REQUIRE(snapshot->phase == bt::SongPhase::Cancelled);
    REQUIRE(snapshot->stems == nullptr);
    REQUIRE(calls.load() == 1);
    REQUIRE_FALSE(std::filesystem::exists(temp.path / "cache" / bt::kModelKey));
}

TEST_CASE("a newer open replaces the song being separated") {
    TempDir temp("bt-session-replace");
    const auto first_song = temp.path / "a.wav";
    const auto second_song = temp.path / "b.wav";
    const auto first_audio = ramp(400, 1.f);
    const auto second_audio = ramp(80, 0.25f);
    REQUIRE(bt::write_float_wav(first_song, first_audio.samples.data(), 2, first_audio.frames).has_value());
    REQUIRE(bt::write_float_wav(second_song, second_audio.samples.data(), 2, second_audio.frames).has_value());

    Gate gate;
    std::atomic<int> calls{0};
    auto created = bt::Session::create(options_with(temp.path / "cache", [&](const float* mix, float* stems, int n) {
        if (++calls == 1) {
            gate.enter();
        }
        identity(mix, stems, n);
    }));
    REQUIRE(created.has_value());
    auto session = std::move(*created);
    ReleaseGate releaser{&gate};

    const auto original = session->open_song(first_song);
    gate.wait_until_entered();
    const auto replacement = session->open_song(second_song);
    gate.release();

    const auto cancelled = require_settled(*session, original);
    const auto ready = require_settled(*session, replacement);
    REQUIRE(cancelled->phase == bt::SongPhase::Cancelled);
    REQUIRE(ready->phase == bt::SongPhase::Ready);
    REQUIRE(ready->source == second_song);
    REQUIRE_FALSE(ready->from_cache);
}

TEST_CASE("session reports a missing file and keeps mixer changes") {
    TempDir temp("bt-session-mixer");
    auto created = bt::Session::create(options_with(temp.path / "cache", identity));
    REQUIRE(created.has_value());
    auto session = std::move(*created);

    REQUIRE(session->snapshot()->phase == bt::SongPhase::Empty);
    REQUIRE(session->snapshot()->gain[0] == 1.f);

    const auto missing = require_settled(*session, session->open_song(temp.path / "missing.wav"));
    REQUIRE(missing->phase == bt::SongPhase::Failed);
    REQUIRE_FALSE(missing->detail.empty());

    const auto song = temp.path / "song.wav";
    const auto audio = ramp(64, 1.f);
    REQUIRE(bt::write_float_wav(song, audio.samples.data(), 2, audio.frames).has_value());
    const auto ready = require_settled(*session, session->open_song(song));
    REQUIRE(ready->phase == bt::SongPhase::Ready);

    const auto revision = session->set_solo(1, true);
    REQUIRE(session->set_gain(4, 0.25f) > revision);
    const auto mute = session->set_mute(4, true);
    const auto panned = session->set_pan(4, -0.5f);
    REQUIRE(panned > mute);
    REQUIRE(session->wait_for_revision(panned, std::chrono::seconds(5)));
    const auto mixed = session->snapshot();
    REQUIRE(mixed->gain[4] == 0.25f);
    REQUIRE(mixed->mute[4]);
    REQUIRE(mixed->solo[1]);
    REQUIRE(mixed->pan[4] == -0.5f);
    REQUIRE(mixed->stems == ready->stems);
}

TEST_CASE("wav write keeps a filename outside the system code page") {
    TempDir temp("bt-unicode-wav");
    const auto file = temp.path / std::filesystem::path(std::u8string(u8"Bôa")) / "mix.wav";
    std::filesystem::create_directories(file.parent_path());
    const auto audio = ramp(32, 1.f);
    REQUIRE(bt::write_float_wav(file, audio.samples.data(), audio.channels, audio.frames).has_value());
    const auto loaded = bt::read_float_wav(file);
    REQUIRE(loaded.has_value());
    REQUIRE(loaded->frames == 32);
}
