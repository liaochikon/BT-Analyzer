#include "tab.hpp"
#include "notes.hpp"

#include "bt/basic_pitch.hpp"

#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_floating_point.hpp>

#include <array>
#include <cmath>
#include <filesystem>
#include <set>
#include <string>
#include <vector>

namespace {

btapp::App make_app(std::vector<bt::Note> notes) {
    btapp::App app;
    app.bpm = 120.f;
    app.beat_offset = 0;
    app.grid_division = btapp::BeatDivision::Quarter;
    app.notes[static_cast<std::size_t>(btapp::kGuitarStem)] = std::move(notes);
    app.note_status[static_cast<std::size_t>(btapp::kGuitarStem)] = btapp::NoteStatus::Ready;
    app.selected = btapp::kGuitarStem;
    app.note_stage = btapp::NoteStage::Midi;
    return app;
}

void select_all(btapp::App& app) {
    const auto& notes = app.notes[static_cast<std::size_t>(btapp::kGuitarStem)];
    app.roll_selected.assign(notes.size(), 1);
}

std::string combo_key(const btapp::TabCombo& combo) {
    std::string key;
    for (const btapp::TabSpot& spot : combo.spots) {
        key += std::to_string(spot.string);
        key += ':';
        key += std::to_string(spot.fret);
        key += ';';
    }
    return key;
}

}  // namespace

TEST_CASE("tab gate warns on unquantized onsets") {
    auto app = make_app({{64, 0.11, 0.25, 90}});
    const btapp::TabGate gate = btapp::evaluate_tab_gate(app, btapp::kGuitarStem);
    REQUIRE_FALSE(gate.ok);
    REQUIRE_FALSE(gate.quantized);
    REQUIRE(gate.unquantized == 1);
}

TEST_CASE("tab gate accepts quantized playable notes") {
    auto app = make_app({{64, 0.0, 0.125, 90}, {60, 0.5, 0.625, 80}});
    const btapp::TabGate gate = btapp::evaluate_tab_gate(app, btapp::kGuitarStem);
    REQUIRE(gate.ok);
    REQUIRE(gate.quantized);
    REQUIRE(gate.in_range);
    REQUIRE(gate.chords_ok);
}

TEST_CASE("tab gate warns when chords need more than six strings") {
    std::vector<bt::Note> notes;
    for (int i = 0; i < 7; ++i) {
        notes.push_back({60, 0.0, 0.1, 90});
    }
    auto app = make_app(std::move(notes));
    const btapp::TabGate gate = btapp::evaluate_tab_gate(app, btapp::kGuitarStem);
    REQUIRE_FALSE(gate.ok);
    REQUIRE_FALSE(gate.chords_ok);
}

TEST_CASE("tab convert assigns unique strings per onset") {
    auto app = make_app({{64, 0.0, 0.1, 90}, {59, 0.0, 0.1, 90}, {55, 0.0, 0.1, 80}});
    REQUIRE(btapp::evaluate_tab_gate(app, btapp::kGuitarStem).ok);
    REQUIRE(btapp::convert_stem_to_tab(app, btapp::kGuitarStem));
    const auto& notes = app.notes[static_cast<std::size_t>(btapp::kGuitarStem)];
    REQUIRE(notes.size() == 3);
    std::array<bool, btapp::kGuitarStringCount> used{};
    for (const bt::Note& note : notes) {
        REQUIRE(btapp::note_has_fingering(note));
        REQUIRE(btapp::fingering_matches_pitch(note));
        REQUIRE_FALSE(used[static_cast<std::size_t>(note.string)]);
        used[static_cast<std::size_t>(note.string)] = true;
    }
}

TEST_CASE("quantize only selected notes and rebuilds rhythm tree") {
    auto app = make_app({{64, 0.11, 0.21, 90}, {60, 0.61, 0.71, 90}});
    app.grid_division = btapp::BeatDivision::Beat;  // 0.5s at 120 BPM
    app.roll_selected = {1, 0};  // only first note
    btapp::quantize_stem_notes(app, btapp::kGuitarStem, 4.0);
    const auto& notes = app.notes[static_cast<std::size_t>(btapp::kGuitarStem)];
    REQUIRE_THAT(notes[0].start, Catch::Matchers::WithinAbs(0.0, 1e-6));
    // End also snaps to the grid (0.21 → 0, then bumped to next mark 0.5).
    REQUIRE_THAT(notes[0].end, Catch::Matchers::WithinAbs(0.5, 1e-6));
    REQUIRE_THAT(notes[1].start, Catch::Matchers::WithinAbs(0.61, 1e-6));
    REQUIRE_THAT(notes[1].end, Catch::Matchers::WithinAbs(0.71, 1e-6));
    REQUIRE(btapp::tab_group_snapped(app, btapp::kGuitarStem, {0}));
    REQUIRE_FALSE(btapp::tab_score_empty(app.tab_scores[static_cast<std::size_t>(btapp::kGuitarStem)]));
}

TEST_CASE("quantize snaps note tails independently") {
    auto app = make_app({{64, 0.05, 0.62, 90}});
    app.grid_division = btapp::BeatDivision::Beat;  // 0.5s at 120 BPM
    app.roll_selected = {1};
    btapp::quantize_stem_notes(app, btapp::kGuitarStem, 4.0);
    const auto& notes = app.notes[static_cast<std::size_t>(btapp::kGuitarStem)];
    REQUIRE_THAT(notes[0].start, Catch::Matchers::WithinAbs(0.0, 1e-6));
    REQUIRE_THAT(notes[0].end, Catch::Matchers::WithinAbs(0.5, 1e-6));
}

TEST_CASE("sync from midi does not truncate overlapping tails") {
    auto app = make_app({{64, 0.0, 0.8, 90}, {60, 0.5, 0.6, 90}});
    app.bpm = 120.f;
    REQUIRE(btapp::tab_score_build(app, btapp::kGuitarStem));
    auto& notes = app.notes[static_cast<std::size_t>(btapp::kGuitarStem)];
    const double long_end = notes[0].end;
    REQUIRE(btapp::tab_score_sync_from_midi(app, btapp::kGuitarStem));
    REQUIRE_THAT(notes[0].end, Catch::Matchers::WithinAbs(long_end, 1e-9));
    REQUIRE_THAT(
        btapp::tab_display_end(notes, {0}, 0, btapp::tab_onset_window_seconds(app)),
        Catch::Matchers::WithinAbs(0.5, 1e-6));
}

TEST_CASE("tab staff shows all guitar MIDI notes without publish") {
    auto app = make_app({{64, 0.0, 0.1, 90}, {59, 0.5, 0.6, 90}, {55, 1.0, 1.1, 80}});
    REQUIRE(btapp::tab_note_on_staff(app, btapp::kGuitarStem, 0));
    REQUIRE(btapp::tab_note_on_staff(app, btapp::kGuitarStem, 1));
    REQUIRE(btapp::tab_note_on_staff(app, btapp::kGuitarStem, 2));
    REQUIRE_FALSE(btapp::note_has_fingering(app.notes[static_cast<std::size_t>(btapp::kGuitarStem)][0]));
}

TEST_CASE("notes cache round-trips on_tab in v3") {
    const auto path = std::filesystem::temp_directory_path() / "bt-notes-tab-ontab.notes";
    std::filesystem::remove(path);
    std::vector<bt::Note> notes{{60, 0.25, 0.75, 96, -1, -1, true}};
    REQUIRE(bt::save_notes(path, notes));
    const auto loaded = bt::load_notes(path);
    REQUIRE(loaded);
    REQUIRE(loaded->size() == 1);
    REQUIRE((*loaded)[0].on_tab);
    REQUIRE((*loaded)[0].string == -1);
    std::filesystem::remove(path);
}

TEST_CASE("tab convert soft-allows unquantized notes") {
    auto app = make_app({{64, 0.11, 0.25, 90}});
    REQUIRE_FALSE(btapp::evaluate_tab_gate(app, btapp::kGuitarStem).ok);
    REQUIRE(btapp::convert_stem_to_tab(app, btapp::kGuitarStem));
    const auto& notes = app.notes[static_cast<std::size_t>(btapp::kGuitarStem)];
    REQUIRE(btapp::note_has_fingering(notes[0]));
    REQUIRE(btapp::fingering_matches_pitch(notes[0]));
}

TEST_CASE("tab convert soft-assigns crowded chords") {
    // Six open-string pitches plus one more force a soft clash (7 > 6 strings).
    // Soft-fill still assigns every note (string reuse allowed) so TAB never stays "?".
    std::vector<bt::Note> notes{
        {64, 0.0, 0.1, 90}, {59, 0.0, 0.1, 90}, {55, 0.0, 0.1, 90}, {50, 0.0, 0.1, 90},
        {45, 0.0, 0.1, 90}, {40, 0.0, 0.1, 90}, {60, 0.0, 0.1, 90},
    };
    auto app = make_app(std::move(notes));
    REQUIRE_FALSE(btapp::evaluate_tab_gate(app, btapp::kGuitarStem).ok);
    REQUIRE(btapp::convert_stem_to_tab(app, btapp::kGuitarStem));
    const auto& out = app.notes[static_cast<std::size_t>(btapp::kGuitarStem)];
    int fingered = 0;
    std::array<int, btapp::kGuitarStringCount> per_string{};
    for (const bt::Note& note : out) {
        REQUIRE(btapp::note_has_fingering(note));
        REQUIRE(btapp::fingering_matches_pitch(note));
        ++per_string[static_cast<std::size_t>(note.string)];
        ++fingered;
    }
    REQUIRE(fingered == 7);
    int reused = 0;
    for (int count : per_string) {
        if (count > 1) {
            reused += count - 1;
        }
    }
    REQUIRE(reused >= 1);
}

TEST_CASE("resolve fingerings after transcription leaves no undetermined notes") {
    std::vector<bt::Note> notes{
        {64, 0.0, 0.1, 90}, {60, 0.02, 0.12, 90}, {55, 0.04, 0.14, 80}, {50, 0.5, 0.6, 90},
        {45, 0.5, 0.6, 90}, {40, 0.5, 0.6, 90}, {67, 0.5, 0.6, 90}, {62, 0.5, 0.6, 90},
        {59, 0.5, 0.6, 90}, {72, 1.0, 1.1, 90},
    };
    auto app = make_app(std::move(notes));
    app.tab_onset_ms = 40;
    REQUIRE(btapp::tab_resolve_fingerings(app, btapp::kGuitarStem, false));
    REQUIRE(btapp::tab_score_build(app, btapp::kGuitarStem, false));
    for (const bt::Note& note : app.notes[static_cast<std::size_t>(btapp::kGuitarStem)]) {
        REQUIRE(btapp::note_has_fingering(note));
    }
}

TEST_CASE("notes cache round-trips fingering in v2") {
    const auto path = std::filesystem::temp_directory_path() / "bt-notes-tab-roundtrip.notes";
    std::filesystem::remove(path);
    std::vector<bt::Note> notes{{60, 0.25, 0.75, 96, 2, 5}};
    REQUIRE(bt::save_notes(path, notes));
    const auto loaded = bt::load_notes(path);
    REQUIRE(loaded);
    REQUIRE(loaded->size() == 1);
    REQUIRE((*loaded)[0].string == 2);
    REQUIRE((*loaded)[0].fret == 5);
    std::filesystem::remove(path);
}

TEST_CASE("resolve fingerings picks a shape for every onset from neighbors") {
    auto app = make_app({{64, 0.0, 0.1, 90}, {60, 0.5, 0.6, 90}, {67, 1.0, 1.1, 90}});
    REQUIRE(btapp::tab_resolve_fingerings(app, btapp::kGuitarStem));
    const auto& notes = app.notes[static_cast<std::size_t>(btapp::kGuitarStem)];
    for (const bt::Note& note : notes) {
        REQUIRE(btapp::note_has_fingering(note));
        REQUIRE(btapp::fingering_matches_pitch(note));
    }
}

TEST_CASE("fingering combos are unique playable assignments") {
    auto app = make_app({{64, 0.0, 0.1, 90}, {60, 0.0, 0.1, 90}});
    const std::vector<int> indices{0, 1};
    const auto combos = btapp::enumerate_fingering_combos(app, btapp::kGuitarStem, indices);
    REQUIRE(combos.size() >= 2);
    std::set<std::string> seen;
    for (const btapp::TabCombo& combo : combos) {
        REQUIRE(combo.spots.size() == 2);
        REQUIRE(combo.spots[0].string != combo.spots[1].string);
        const auto& notes = app.notes[static_cast<std::size_t>(btapp::kGuitarStem)];
        REQUIRE(btapp::kGuitarOpenPitch[combo.spots[0].string] + combo.spots[0].fret == notes[0].pitch);
        REQUIRE(btapp::kGuitarOpenPitch[combo.spots[1].string] + combo.spots[1].fret == notes[1].pitch);
        REQUIRE(seen.insert(combo_key(combo)).second);
    }
}

TEST_CASE("fingering mode cycle wraps and preserves pitch") {
    auto app = make_app({{64, 0.0, 0.2, 90}, {59, 0.0, 0.2, 90}});
    select_all(app);
    REQUIRE(btapp::tab_enter_fingering_mode(app, btapp::kGuitarStem));
    REQUIRE(app.tab_fingering_mode);
    const int count = btapp::tab_combo_count(app, btapp::kGuitarStem);
    REQUIRE(count >= 2);

    const auto& notes = app.notes[static_cast<std::size_t>(btapp::kGuitarStem)];
    const int pitch0 = notes[0].pitch;
    const int pitch1 = notes[1].pitch;
    const int start_string0 = notes[0].string;
    const int start_string1 = notes[1].string;
    const int start_index = app.tab_combo_index;

    bool changed = false;
    for (int step = 0; step < count; ++step) {
        REQUIRE(btapp::tab_cycle_combo(app, btapp::kGuitarStem, 1));
        REQUIRE(notes[0].pitch == pitch0);
        REQUIRE(notes[1].pitch == pitch1);
        REQUIRE(btapp::fingering_matches_pitch(notes[0]));
        REQUIRE(btapp::fingering_matches_pitch(notes[1]));
        REQUIRE(notes[0].string != notes[1].string);
        if (notes[0].string != start_string0 || notes[1].string != start_string1) {
            changed = true;
        }
    }
    REQUIRE(changed);
    REQUIRE(app.tab_combo_index == start_index);
    REQUIRE(notes[0].string == start_string0);
    REQUIRE(notes[1].string == start_string1);

    btapp::tab_exit_fingering_mode(app);
    REQUIRE_FALSE(app.tab_fingering_mode);
}

TEST_CASE("set_note_string rejects impossible string for pitch") {
    bt::Note note{64, 0.0, 0.1, 90, 0, 0};  // high e open
    REQUIRE(btapp::set_note_string(note, 5, true));  // low E fret 24
    REQUIRE(note.pitch == 64);
    REQUIRE(note.string == 5);
    REQUIRE(note.fret == 24);
    // Pitch below high-e open cannot move to string 0.
    note = {40, 0.0, 0.1, 90, 5, 0};
    REQUIRE_FALSE(btapp::set_note_string(note, 0, true));
    REQUIRE(note.string == 5);
    REQUIRE(note.pitch == 40);
}

TEST_CASE("tab score builds uniform quarters from bpm lattice without changing MIDI") {
    auto app = make_app({{64, 0.0, 0.1, 90}, {60, 0.5, 0.6, 90}});
    app.bpm = 120.f;
    app.beat_offset = 0;
    auto& notes = app.notes[static_cast<std::size_t>(btapp::kGuitarStem)];
    const double start0 = notes[0].start;
    const double end0 = notes[0].end;
    const double start1 = notes[1].start;
    const double end1 = notes[1].end;
    REQUIRE(btapp::tab_score_build(app, btapp::kGuitarStem));
    const btapp::TabScore& score = app.tab_scores[static_cast<std::size_t>(btapp::kGuitarStem)];
    REQUIRE_FALSE(score.measures.empty());
    int note_beats = 0;
    for (const btapp::TabMeasure& measure : score.measures) {
        REQUIRE(measure.beats.size() == 4);
        for (const btapp::TabBeat& beat : measure.beats) {
            REQUIRE(beat.duration == btapp::TabDuration::Quarter);
            REQUIRE_FALSE(beat.dotted);
            if (!beat.rest) {
                ++note_beats;
            }
        }
    }
    REQUIRE(note_beats == 2);
    REQUIRE(notes[0].start == start0);
    REQUIRE(notes[0].end == end0);
    REQUIRE(notes[1].start == start1);
    REQUIRE(notes[1].end == end1);
}

TEST_CASE("tab score duration edit is local until next note and writes MIDI end") {
    auto app = make_app({{64, 0.0, 0.1, 90}, {59, 0.5, 0.6, 90}});
    app.bpm = 120.f;
    app.beat_offset = 0;
    REQUIRE(btapp::tab_score_build(app, btapp::kGuitarStem));
    auto& notes = app.notes[static_cast<std::size_t>(btapp::kGuitarStem)];
    const double start1 = notes[1].start;
    const double end1 = notes[1].end;
    REQUIRE(btapp::tab_score_set_duration(
        app, btapp::kGuitarStem, 0, 0, btapp::TabDuration::Eighth, false));
    const btapp::TabScore& score = app.tab_scores[static_cast<std::size_t>(btapp::kGuitarStem)];
    REQUIRE(score.measures[0].beats[0].duration == btapp::TabDuration::Eighth);
    REQUIRE(score.measures[0].beats[0].rest == false);
    // Freed half-beat becomes a rest immediately after, before the next note.
    REQUIRE(score.measures[0].beats[1].rest);
    REQUIRE(score.measures[0].beats[1].duration == btapp::TabDuration::Eighth);
    // Next note-beat stays a quarter and is untouched in MIDI.
    REQUIRE_FALSE(score.measures[0].beats[2].rest);
    REQUIRE(score.measures[0].beats[2].duration == btapp::TabDuration::Quarter);
    REQUIRE_THAT(notes[0].start, Catch::Matchers::WithinAbs(0.0, 1e-6));
    REQUIRE_THAT(notes[0].end, Catch::Matchers::WithinAbs(0.25, 1e-6));
    REQUIRE_THAT(notes[1].start, Catch::Matchers::WithinAbs(start1, 1e-6));
    REQUIRE_THAT(notes[1].end, Catch::Matchers::WithinAbs(end1, 1e-6));
    double fill = 0;
    for (const btapp::TabBeat& beat : score.measures[0].beats) {
        fill += btapp::tab_duration_quarters(beat.duration, beat.dotted);
    }
    REQUIRE_THAT(fill, Catch::Matchers::WithinAbs(4.0, 1e-6));
}

TEST_CASE("tab score snap group writes MIDI starts and marks snapped") {
    auto app = make_app({{64, 0.0, 0.1, 90}, {60, 0.5, 0.6, 90}});
    app.bpm = 120.f;
    app.beat_offset = 0;
    REQUIRE(btapp::tab_score_build(app, btapp::kGuitarStem));
    auto& notes = app.notes[static_cast<std::size_t>(btapp::kGuitarStem)];
    auto& score = app.tab_scores[static_cast<std::size_t>(btapp::kGuitarStem)];
    // Detach note 0 and park it between lattice points → unsapped display state.
    notes[0].start = 0.18;
    notes[0].end = 0.28;
    for (btapp::TabMeasure& measure : score.measures) {
        for (btapp::TabBeat& beat : measure.beats) {
            beat.note_indices.erase(
                std::remove(beat.note_indices.begin(), beat.note_indices.end(), 0), beat.note_indices.end());
            beat.rest = beat.note_indices.empty();
        }
    }
    REQUIRE_FALSE(btapp::tab_group_snapped(app, btapp::kGuitarStem, {0}));
    REQUIRE(btapp::tab_score_snap_group(app, btapp::kGuitarStem, {0}, 0, 0));
    REQUIRE_THAT(notes[0].start, Catch::Matchers::WithinAbs(0.0, 1e-6));
    REQUIRE(btapp::tab_group_snapped(app, btapp::kGuitarStem, {0}));
}

TEST_CASE("tab duration quarters and stepping") {
    REQUIRE_THAT(btapp::tab_duration_quarters(btapp::TabDuration::Quarter, false), Catch::Matchers::WithinAbs(1.0, 1e-9));
    REQUIRE_THAT(btapp::tab_duration_quarters(btapp::TabDuration::Eighth, true), Catch::Matchers::WithinAbs(0.75, 1e-9));
    REQUIRE(btapp::tab_duration_step(btapp::TabDuration::Quarter, 1) == btapp::TabDuration::Eighth);
    REQUIRE(btapp::tab_duration_step(btapp::TabDuration::Whole, -1) == btapp::TabDuration::Whole);
}
