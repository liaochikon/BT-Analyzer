#include "bt/core/tab.hpp"

#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_floating_point.hpp>

#include <array>
#include <string>
#include <vector>

namespace {

bt::core::TabWorkspace workspace_with(
    std::array<std::vector<bt::Note>, bt::kStemCount>& notes,
    std::array<bt::core::TabScore, bt::kStemCount>& scores) {
    bt::core::TabWorkspace workspace;
    workspace.notes = &notes;
    workspace.scores = &scores;
    workspace.beats.bpm = 120.f;
    workspace.beats.offset = 0;
    workspace.grid_division = bt::core::BeatDivision::Beat;
    workspace.length = 4.0;
    return workspace;
}

}  // namespace

TEST_CASE("tab duration quarters, step, and nearest label", "[core][tab]") {
    using bt::core::TabDuration;
    REQUIRE_THAT(bt::core::tab_duration_quarters(TabDuration::Quarter, false), Catch::Matchers::WithinAbs(1.0, 1e-9));
    REQUIRE_THAT(bt::core::tab_duration_quarters(TabDuration::Quarter, true), Catch::Matchers::WithinAbs(1.5, 1e-9));
    REQUIRE_THAT(bt::core::tab_duration_quarters(TabDuration::Eighth, false), Catch::Matchers::WithinAbs(0.5, 1e-9));
    REQUIRE(bt::core::tab_duration_step(TabDuration::Quarter, 1) == TabDuration::Eighth);
    REQUIRE(bt::core::tab_duration_step(TabDuration::SixtyFourth, 1) == TabDuration::SixtyFourth);
    REQUIRE(bt::core::tab_duration_from_quarters(2.0) == TabDuration::Half);
    REQUIRE(std::string(bt::core::tab_duration_label_key(TabDuration::Whole)) == "tab.dur_whole");
}

TEST_CASE("resolve fingerings leaves no undetermined guitar notes", "[core][tab]") {
    std::array<std::vector<bt::Note>, bt::kStemCount> notes{};
    std::array<bt::core::TabScore, bt::kStemCount> scores{};
    notes[static_cast<std::size_t>(bt::core::kGuitarStem)] = {
        {64, 0.0, 0.1, 90},
        {59, 0.0, 0.1, 90},
        {55, 0.0, 0.1, 80},
    };
    auto workspace = workspace_with(notes, scores);
    REQUIRE(bt::core::resolve_fingerings(workspace, bt::core::kGuitarStem));
    const auto& guitar = notes[static_cast<std::size_t>(bt::core::kGuitarStem)];
    std::array<bool, bt::core::kGuitarStringCount> used{};
    for (const bt::Note& note : guitar) {
        REQUIRE(bt::core::note_has_fingering(note));
        REQUIRE(bt::core::fingering_matches_pitch(note));
        REQUIRE_FALSE(used[static_cast<std::size_t>(note.string)]);
        used[static_cast<std::size_t>(note.string)] = true;
    }
}

TEST_CASE("quantize snaps selected note edges onto the beat grid", "[core][tab]") {
    std::array<std::vector<bt::Note>, bt::kStemCount> notes{};
    std::array<bt::core::TabScore, bt::kStemCount> scores{};
    notes[static_cast<std::size_t>(bt::core::kGuitarStem)] = {
        {64, 0.11, 0.21, 90},
        {60, 0.61, 0.71, 90},
    };
    auto workspace = workspace_with(notes, scores);
    REQUIRE(bt::core::quantize_notes(workspace, bt::core::kGuitarStem, {1, 0}));
    const auto& guitar = notes[static_cast<std::size_t>(bt::core::kGuitarStem)];
    REQUIRE_THAT(guitar[0].start, Catch::Matchers::WithinAbs(0.0, 1e-6));
    REQUIRE_THAT(guitar[0].end, Catch::Matchers::WithinAbs(0.5, 1e-6));
    REQUIRE_THAT(guitar[1].start, Catch::Matchers::WithinAbs(0.61, 1e-6));
    REQUIRE_THAT(guitar[1].end, Catch::Matchers::WithinAbs(0.71, 1e-6));
    REQUIRE(bt::core::tab_group_snapped(workspace, bt::core::kGuitarStem, {0}));
    REQUIRE_FALSE(bt::core::tab_score_empty(scores[static_cast<std::size_t>(bt::core::kGuitarStem)]));
    for (const bt::Note& note : guitar) {
        REQUIRE(bt::core::note_has_fingering(note));
    }
}
