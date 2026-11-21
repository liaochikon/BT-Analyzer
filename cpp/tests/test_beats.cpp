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

TEST_CASE("a steady pulse locks to one bpm lattice") {
    bt::BeatGrid grid;
    grid.bpm = 120.f;
    const double interval = 0.5;
    for (int index = 0; index < 32; ++index) {
        const double wobble = (index % 2 == 0) ? 0.018 : -0.012;
        grid.times.push_back(0.4 + index * interval + wobble);
    }
    bt::regularize_beat_grid(grid, 16.0);
    REQUIRE(grid.spans.size() == 1);
    REQUIRE(grid.spans.front().start == 0.0);
    REQUIRE_THAT(grid.spans.front().end, Catch::Matchers::WithinAbs(16.0, 1e-6));
    REQUIRE_THAT(grid.spans.front().bpm, Catch::Matchers::WithinAbs(120.0, 1.0));
    REQUIRE(grid.times.size() > 20);
    for (std::size_t index = 1; index < grid.times.size(); ++index) {
        REQUIRE_THAT(grid.times[index] - grid.times[index - 1], Catch::Matchers::WithinAbs(0.5, 0.01));
    }
}

TEST_CASE("a lasting tempo change splits the waveform into spans") {
    bt::BeatGrid grid;
    grid.bpm = 100.f;
    for (double time = 0.0; time < 6.0 - 1e-6; time += 0.6) {
        grid.times.push_back(time);
    }
    for (double time = 6.0; time < 12.0 - 1e-6; time += 60.0 / 140.0) {
        grid.times.push_back(time);
    }
    bt::regularize_beat_grid(grid, 12.0);
    REQUIRE(grid.spans.size() == 2);
    REQUIRE_THAT(grid.spans[0].bpm, Catch::Matchers::WithinAbs(100.0, 2.0));
    REQUIRE_THAT(grid.spans[1].bpm, Catch::Matchers::WithinAbs(140.0, 2.0));
    REQUIRE_THAT(grid.spans[0].start, Catch::Matchers::WithinAbs(0.0, 1e-6));
    REQUIRE_THAT(grid.spans[0].end, Catch::Matchers::WithinAbs(6.0, 0.2));
    REQUIRE_THAT(grid.spans[1].end, Catch::Matchers::WithinAbs(12.0, 1e-6));
}

TEST_CASE("a double time stretch stays on the same bpm") {
    bt::BeatGrid grid;
    grid.bpm = 120.f;
    for (int index = 0; index < 16; ++index) {
        grid.times.push_back(index * 0.5);
    }
    const double join = grid.times.back();
    for (int index = 1; index <= 12; ++index) {
        grid.times.push_back(join + index * 0.25);
    }
    const double resume = grid.times.back();
    for (int index = 1; index <= 16; ++index) {
        grid.times.push_back(resume + index * 0.5);
    }
    bt::regularize_beat_grid(grid, grid.times.back() + 0.5);
    REQUIRE(grid.spans.size() == 1);
    REQUIRE_THAT(grid.spans.front().bpm, Catch::Matchers::WithinAbs(120.0, 2.0));
}
