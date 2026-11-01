#include "bt/beat_track.hpp"

#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_floating_point.hpp>

#include <cmath>
#include <filesystem>
#include <vector>

TEST_CASE("a click train reports its tempo and first beat") {
    constexpr int kRate = 44100;
    constexpr double kBpm = 120.0;
    constexpr double kOffset = 0.5;
    constexpr double kSeconds = 8.0;
    std::vector<float> mono(static_cast<std::size_t>(kRate * kSeconds), 0.f);
    const double interval = 60.0 / kBpm;
    for (double time = kOffset; time < kSeconds - 0.05; time += interval) {
        const int start = static_cast<int>(time * kRate);
        for (int i = 0; i < 180; ++i) {
            const float envelope = std::exp(-static_cast<float>(i) / 40.f);
            mono[static_cast<std::size_t>(start + i)] += envelope * std::sin(2.f * 3.14159265f * 1000.f * static_cast<float>(i) / kRate);
        }
    }

    const auto grid = bt::track_beats(mono.data(), static_cast<std::int64_t>(mono.size()), kRate);
    REQUIRE(grid);
    REQUIRE_THAT(grid->bpm, Catch::Matchers::WithinAbs(kBpm, 4.0));
    REQUIRE_THAT(grid->offset, Catch::Matchers::WithinAbs(kOffset, 0.08));
    REQUIRE(grid->times.size() > 8);
    REQUIRE_THAT(grid->times.front(), Catch::Matchers::WithinAbs(grid->offset, 1e-6));
}

TEST_CASE("beat cache round-trips") {
    const auto directory = std::filesystem::temp_directory_path() / "bt-beat-cache";
    std::filesystem::create_directories(directory);
    const auto path = directory / "beats.txt";
    const bt::BeatGrid grid{117.5f, 0.42};
    REQUIRE(bt::save_beats(path, grid));
    const auto loaded = bt::load_beats(path);
    REQUIRE(loaded);
    REQUIRE_THAT(loaded->bpm, Catch::Matchers::WithinAbs(117.5, 1e-3));
    REQUIRE_THAT(loaded->offset, Catch::Matchers::WithinAbs(0.42, 1e-6));
    REQUIRE(loaded->times.empty());

    bt::BeatGrid detected;
    detected.bpm = 100.f;
    detected.offset = 0.2;
    detected.times = {0.2, 0.81, 1.4};
    REQUIRE(bt::save_beats(path, detected));
    const auto round = bt::load_beats(path);
    REQUIRE(round);
    REQUIRE(round->times.size() == 3);
    REQUIRE_THAT(round->times[0], Catch::Matchers::WithinAbs(0.2, 1e-6));
    REQUIRE_THAT(round->times[1], Catch::Matchers::WithinAbs(0.81, 1e-6));
    REQUIRE_THAT(round->times[2], Catch::Matchers::WithinAbs(1.4, 1e-6));
    std::filesystem::remove_all(directory);
}

TEST_CASE("edge blanks continue from the nearest beat at the regions average tempo") {
    bt::BeatGrid grid;
    grid.bpm = 90.f;
    grid.offset = 1.0;
    grid.times = {1.0, 1.5, 2.0, 2.5};
    bt::extend_beat_edges(grid, 4.0);
    REQUIRE(grid.times.size() == 9);
    REQUIRE_THAT(grid.times.front(), Catch::Matchers::WithinAbs(0.0, 1e-6));
    REQUIRE_THAT(grid.times[1], Catch::Matchers::WithinAbs(0.5, 1e-6));
    REQUIRE_THAT(grid.times[2], Catch::Matchers::WithinAbs(1.0, 1e-6));
    REQUIRE_THAT(grid.times.back(), Catch::Matchers::WithinAbs(4.0, 1e-6));
    REQUIRE_THAT(grid.offset, Catch::Matchers::WithinAbs(0.0, 1e-6));

    bt::BeatGrid uneven;
    uneven.bpm = 120.f;
    uneven.times = {1.0, 1.4, 2.0};
    bt::extend_beat_edges(uneven, 3.0);
    REQUIRE_THAT(uneven.times.front(), Catch::Matchers::WithinAbs(0.04, 1e-6));
    REQUIRE_THAT(uneven.times[1], Catch::Matchers::WithinAbs(0.52, 1e-6));
    REQUIRE_THAT(uneven.times[2], Catch::Matchers::WithinAbs(1.0, 1e-6));
    REQUIRE_THAT(uneven.times.back(), Catch::Matchers::WithinAbs(2.96, 1e-6));

    bt::BeatGrid alone;
    alone.bpm = 120.f;
    alone.times = {1.0};
    bt::extend_beat_edges(alone, 2.5);
    REQUIRE(alone.times.size() == 6);
    REQUIRE_THAT(alone.times.front(), Catch::Matchers::WithinAbs(0.0, 1e-6));
    REQUIRE_THAT(alone.times.back(), Catch::Matchers::WithinAbs(2.5, 1e-6));

    bt::BeatGrid empty;
    bt::extend_beat_edges(empty, 8.0);
    REQUIRE(empty.times.empty());
}
