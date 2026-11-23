#include "bt/audio.hpp"
#include "bt/core/engine.hpp"

#include <catch2/catch_test_macros.hpp>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <filesystem>
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

bt::core::EngineOptions test_options(const std::filesystem::path& cache) {
    bt::core::EngineOptions options;
    options.session.cache_directory = cache;
    options.session.run_segment = identity;
    options.session.separate.segment_samples = 32;
    options.session.separate.overlap = 0.25f;
    return options;
}

}  // namespace

TEST_CASE("core engine imports audio headlessly and reaches Ready", "[core][headless]") {
    TempDir temp("bt-core-engine-import");
    const auto song = temp.path / "song.wav";
    const auto audio = ramp(400, 1.f);
    REQUIRE(bt::write_float_wav(song, audio.samples.data(), audio.channels, audio.frames).has_value());

    auto created = bt::core::Engine::create(test_options(temp.path / "cache"));
    REQUIRE(created);
    auto& engine = **created;

    const auto job = engine.import_audio(song);
    auto snapshot = engine.wait_for(job, std::chrono::seconds(5));
    REQUIRE(snapshot != nullptr);
    REQUIRE(snapshot->phase == bt::SongPhase::Ready);
    REQUIRE(snapshot->stems != nullptr);
    REQUIRE(snapshot->stems->frames > 0);
}

TEST_CASE("core engine drums stem transcription is empty without a pitch model", "[core][headless]") {
    TempDir temp("bt-core-engine-drums");
    const auto song = temp.path / "song.wav";
    const auto audio = ramp(128, 0.5f);
    REQUIRE(bt::write_float_wav(song, audio.samples.data(), audio.channels, audio.frames).has_value());

    auto created = bt::core::Engine::create(test_options(temp.path / "cache"));
    REQUIRE(created);
    auto& engine = **created;

    const auto job = engine.import_audio(song);
    REQUIRE(engine.wait_for(job, std::chrono::seconds(5)) != nullptr);

    auto notes = engine.transcribe_stem(0);
    REQUIRE(notes);
    REQUIRE(notes->empty());
}

TEST_CASE("core engine guitar transcription fails clearly without pitch model", "[core][headless]") {
    TempDir temp("bt-core-engine-nopitch");
    const auto song = temp.path / "song.wav";
    const auto audio = ramp(128, 0.5f);
    REQUIRE(bt::write_float_wav(song, audio.samples.data(), audio.channels, audio.frames).has_value());

    auto created = bt::core::Engine::create(test_options(temp.path / "cache"));
    REQUIRE(created);
    auto& engine = **created;

    const auto job = engine.import_audio(song);
    REQUIRE(engine.wait_for(job, std::chrono::seconds(5)) != nullptr);

    auto notes = engine.transcribe_stem(4);  // guitar
    REQUIRE_FALSE(notes);
    REQUIRE(notes.error().find("pitch model") != std::string::npos);
}
