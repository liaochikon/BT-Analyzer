#include "tab.hpp"

#include "i18n.hpp"
#include "monitor.hpp"
#include "notes.hpp"

#include "imgui.h"

#include <algorithm>
#include <cstdio>
#include <limits>
#include <string>

namespace btapp {
namespace {

void clamp_score_caret(App& app, int stem) {
    const TabScore& score = app.tab_scores[static_cast<std::size_t>(stem)];
    if (score.measures.empty()) {
        app.tab_score_measure = 0;
        app.tab_score_beat = 0;
        return;
    }
    app.tab_score_measure = std::clamp(app.tab_score_measure, 0, static_cast<int>(score.measures.size()) - 1);
    const TabMeasure& measure = score.measures[static_cast<std::size_t>(app.tab_score_measure)];
    if (measure.beats.empty()) {
        app.tab_score_beat = 0;
    } else {
        app.tab_score_beat = std::clamp(app.tab_score_beat, 0, static_cast<int>(measure.beats.size()) - 1);
    }
    app.tab_score_string = std::clamp(app.tab_score_string, 0, kGuitarStringCount - 1);
}

void park_score_caret_on_first_note(App& app, int stem) {
    const TabScore& score = app.tab_scores[static_cast<std::size_t>(stem)];
    for (int m = 0; m < static_cast<int>(score.measures.size()); ++m) {
        const TabMeasure& measure = score.measures[static_cast<std::size_t>(m)];
        for (int b = 0; b < static_cast<int>(measure.beats.size()); ++b) {
            if (!measure.beats[static_cast<std::size_t>(b)].rest) {
                app.tab_score_measure = m;
                app.tab_score_beat = b;
                if (!measure.beats[static_cast<std::size_t>(b)].note_indices.empty()) {
                    const int note_i = measure.beats[static_cast<std::size_t>(b)].note_indices.front();
                    const auto& notes = app.notes[static_cast<std::size_t>(stem)];
                    if (note_i >= 0 && note_i < static_cast<int>(notes.size()) &&
                        note_has_fingering(notes[static_cast<std::size_t>(note_i)])) {
                        app.tab_score_string =
                            std::clamp(notes[static_cast<std::size_t>(note_i)].string, 0, kGuitarStringCount - 1);
                    }
                }
                return;
            }
        }
    }
    clamp_score_caret(app, stem);
}

int matching_combo_index(const App& app, int stem, const TabOnsetGroup& group) {
    auto workspace = tab_workspace(const_cast<App&>(app));
    return bt::core::matching_fingering_combo(workspace, stem, group);
}

bool apply_combo_to_notes(App& app, int stem, const TabOnsetGroup& group, int combo_index) {
    auto workspace = tab_workspace(app);
    begin_note_gesture(app, stem);
    const bool ok = bt::core::write_fingering_combo(workspace, stem, group, combo_index);
    tab_workspace_pull(app, workspace);
    finish_note_gesture(app, stem);
    return ok;
}

void localize_gate(const App& app, int stem, TabGate& gate) {
    gate.reasons.clear();
    if (stem < 0 || stem >= bt::kStemCount) {
        gate.reasons.push_back(t("tab.gate_bad_stem"));
        return;
    }
    if (app.notes[static_cast<std::size_t>(stem)].empty()) {
        gate.reasons.push_back(t("tab.gate_empty"));
        return;
    }
    if (!gate.quantized) {
        char text[128];
        std::snprintf(text, sizeof text, t("tab.gate_unquantized"), gate.unquantized);
        gate.reasons.push_back(text);
    }
    if (!gate.in_range) {
        char text[128];
        std::snprintf(text, sizeof text, t("tab.gate_out_of_range"), gate.out_of_range);
        gate.reasons.push_back(text);
    }
    if (!gate.chords_ok) {
        char text[128];
        std::snprintf(text, sizeof text, t("tab.gate_chord_clash"), gate.chord_clashes);
        gate.reasons.push_back(text);
    }
}

}  // namespace

bt::core::TabWorkspace tab_workspace(App& app) {
    bt::core::TabWorkspace workspace;
    workspace.notes = &app.notes;
    workspace.scores = &app.tab_scores;
    if (app.beat_times != nullptr) {
        workspace.beats.times = *app.beat_times;
    }
    workspace.beats.spans = app.beat_spans;
    workspace.beats.bpm = app.bpm;
    workspace.beats.offset = app.beat_offset;
    workspace.grid_division = app.grid_division;
    workspace.fingering.hand_span = app.tab_hand_span;
    workspace.fingering.onset_ms = app.tab_onset_ms;
    workspace.fingering.prefer_fret = app.fret_prefer;
    workspace.fingering.hand_center = app.hand_center;
    if (app.player != nullptr) {
        workspace.length = app.player->snapshot().length;
    }
    return workspace;
}

void tab_workspace_pull(App& app, const bt::core::TabWorkspace& workspace) {
    app.hand_center = workspace.fingering.hand_center;
}

bool tab_note_on_staff(const App& app, int stem, int note_index) {
    if (stem != kGuitarStem || note_index < 0) {
        return false;
    }
    const auto& notes = app.notes[static_cast<std::size_t>(stem)];
    return note_index < static_cast<int>(notes.size());
}

bool stem_fully_fingered(const App& app, int stem) {
    if (stem < 0 || stem >= bt::kStemCount) {
        return false;
    }
    const auto& notes = app.notes[static_cast<std::size_t>(stem)];
    if (notes.empty()) {
        return false;
    }
    for (const MidiNote& note : notes) {
        if (!note_has_fingering(note) || !fingering_matches_pitch(note)) {
            return false;
        }
    }
    return true;
}

TabGate evaluate_tab_gate(const App& app, int stem) {
    auto workspace = tab_workspace(const_cast<App&>(app));
    TabGate gate = bt::core::evaluate_tab_gate(workspace, stem);
    localize_gate(app, stem, gate);
    return gate;
}

bool convert_stem_to_tab(App& app, int stem) {
    if (stem < 0 || stem >= bt::kStemCount) {
        return false;
    }
    auto& notes = app.notes[static_cast<std::size_t>(stem)];
    if (notes.empty()) {
        return false;
    }
    begin_note_gesture(app, stem);
    auto workspace = tab_workspace(app);
    const bool ok = bt::core::convert_notes_to_tab(workspace, stem, app.roll_selected);
    tab_workspace_pull(app, workspace);
    finish_note_gesture(app, stem);
    if (ok) {
        log_stem(LogLevel::Info, stem, "log.tab_convert");
    }
    return ok;
}

TabExportResult export_stem_tab_text(const App& app, int stem, const std::filesystem::path& path) {
    auto workspace = tab_workspace(const_cast<App&>(app));
    TabExportResult result = bt::core::export_tab_text(workspace, stem, path);
    if (!result.ok) {
        if (result.error == "Invalid stem") {
            result.error = t("tab.gate_bad_stem");
        } else if (result.error == "No notes") {
            result.error = t("tab.gate_empty");
        } else if (result.error == "Generate TAB fingerings first") {
            result.error = t("tab.export_need_convert");
        } else if (result.error == "Could not write TAB file") {
            result.error = t("tab.export_write_failed");
        }
        log_stem(LogLevel::Error, stem, "log.tab_export_failed", " · " + result.error);
    } else {
        log_stem(LogLevel::Info, stem, "log.tab_export_ok", " · " + path.filename().string());
    }
    return result;
}

std::vector<TabCombo> enumerate_fingering_combos(const App& app, int stem, const std::vector<int>& note_indices) {
    auto workspace = tab_workspace(const_cast<App&>(app));
    return bt::core::enumerate_fingering_combos(workspace, stem, note_indices);
}

std::vector<TabOnsetGroup> build_fingering_groups(const App& app, int stem, const std::vector<int>& note_indices) {
    auto workspace = tab_workspace(const_cast<App&>(app));
    return bt::core::build_fingering_groups(workspace, stem, note_indices);
}

TabGroupConflict tab_group_conflict(const App& app, int stem, const std::vector<int>& note_indices) {
    auto workspace = tab_workspace(const_cast<App&>(app));
    return bt::core::tab_group_conflict(workspace, stem, note_indices);
}

bool tab_resolve_fingerings(App& app, int stem, bool record_history) {
    if (stem < 0 || stem >= bt::kStemCount) {
        return false;
    }
    if (app.notes[static_cast<std::size_t>(stem)].empty()) {
        return false;
    }
    const bool nested = app.note_gesture;
    if (record_history && !nested) {
        begin_note_gesture(app, stem);
    }
    auto workspace = tab_workspace(app);
    const bool changed = bt::core::resolve_fingerings(workspace, stem);
    tab_workspace_pull(app, workspace);
    if (record_history && !nested) {
        if (!changed) {
            app.note_gesture = false;
            app.note_gesture_before.clear();
        } else {
            finish_note_gesture(app, stem);
        }
    }
    // Auto-resolve paths (transcription / layout) pass record_history=false — skip noise.
    if (changed && record_history) {
        log_stem(LogLevel::Info, stem, "log.tab_resolve");
    }
    return changed;
}

bool tab_apply_combo(App& app, int stem, int group_index, int combo_index) {
    if (stem < 0 || stem >= bt::kStemCount || !app.tab_fingering_mode) {
        return false;
    }
    const auto groups = build_fingering_groups(app, stem, app.tab_mode_notes);
    if (group_index < 0 || group_index >= static_cast<int>(groups.size())) {
        return false;
    }
    const TabOnsetGroup& group = groups[static_cast<std::size_t>(group_index)];
    if (group.combos.empty()) {
        return false;
    }
    const int wrapped = ((combo_index % static_cast<int>(group.combos.size())) + static_cast<int>(group.combos.size())) %
                        static_cast<int>(group.combos.size());
    if (!apply_combo_to_notes(app, stem, group, wrapped)) {
        return false;
    }
    app.tab_focus_group = group_index;
    app.tab_combo_index = wrapped;
    return true;
}

bool tab_cycle_notes_combo(App& app, int stem, const std::vector<int>& note_indices, int delta) {
    if (stem < 0 || stem >= bt::kStemCount || note_indices.empty() || delta == 0) {
        return false;
    }
    const auto groups = build_fingering_groups(app, stem, note_indices);
    if (groups.empty() || groups.front().combos.empty()) {
        return false;
    }
    const TabOnsetGroup& group = groups.front();
    const int count = static_cast<int>(group.combos.size());
    const int current = matching_combo_index(app, stem, group);
    const int next = current < 0 ? 0 : ((current + delta) % count + count) % count;
    if (!apply_combo_to_notes(app, stem, group, next)) {
        return false;
    }
    if (app.tab_fingering_mode && app.tab_mode_notes.size() == note_indices.size()) {
        bool same = true;
        for (std::size_t i = 0; i < note_indices.size(); ++i) {
            if (app.tab_mode_notes[i] != note_indices[i]) {
                same = false;
                break;
            }
        }
        if (same) {
            app.tab_combo_index = next;
        }
    }
    return true;
}

bool tab_focus_group_notes(App& app, int stem, const std::vector<int>& note_indices) {
    if (stem < 0 || stem >= bt::kStemCount || note_indices.empty()) {
        return false;
    }
    auto& notes = app.notes[static_cast<std::size_t>(stem)];
    std::vector<int> selected;
    selected.reserve(note_indices.size());
    for (int index : note_indices) {
        if (index < 0 || index >= static_cast<int>(notes.size())) {
            continue;
        }
        selected.push_back(index);
    }
    if (selected.empty()) {
        return false;
    }
    bool needs = false;
    for (int index : selected) {
        const MidiNote& note = notes[static_cast<std::size_t>(index)];
        if (!note_has_fingering(note) || !fingering_matches_pitch(note)) {
            needs = true;
            break;
        }
    }
    if (needs) {
        tab_resolve_fingerings(app, stem);
    }
    auto groups = build_fingering_groups(app, stem, selected);
    if (groups.empty()) {
        return false;
    }
    const TabOnsetGroup& group = groups.front();
    if (!group.combos.empty() && matching_combo_index(app, stem, group) < 0) {
        auto workspace = tab_workspace(app);
        const int best = bt::core::preferred_fingering_combo(workspace, group);
        if (best >= 0) {
            apply_combo_to_notes(app, stem, group, best);
        }
        groups = build_fingering_groups(app, stem, selected);
    }
    app.roll_selected.assign(notes.size(), 0);
    for (int index : selected) {
        app.roll_selected[static_cast<std::size_t>(index)] = 1;
    }
    if (app.tab_fingering_mode) {
        app.tab_mode_notes = selected;
        app.tab_focus_group = 0;
        app.tab_combo_index = groups.empty() ? 0 : std::max(0, matching_combo_index(app, stem, groups.front()));
    } else {
        app.tab_mode_notes.clear();
        app.tab_focus_group = 0;
        app.tab_combo_index = 0;
    }
    return true;
}

bool tab_publish_selection(App& app, int stem) {
    if (stem < 0 || stem >= bt::kStemCount) {
        return false;
    }
    auto& notes = app.notes[static_cast<std::size_t>(stem)];
    if (app.roll_selected.size() != notes.size()) {
        return false;
    }
    bool any = false;
    for (char bit : app.roll_selected) {
        if (bit) {
            any = true;
            break;
        }
    }
    if (!any) {
        return false;
    }
    begin_note_gesture(app, stem);
    for (int index = 0; index < static_cast<int>(notes.size()); ++index) {
        if (!app.roll_selected[static_cast<std::size_t>(index)]) {
            continue;
        }
        MidiNote& note = notes[static_cast<std::size_t>(index)];
        clear_fingering(note);
        note.on_tab = true;
    }
    finish_note_gesture(app, stem);
    tab_score_build(app, stem);
    return true;
}

bool tab_unpublish_note(App& app, int stem, int note_index) {
    if (stem < 0 || stem >= bt::kStemCount || note_index < 0) {
        return false;
    }
    auto& notes = app.notes[static_cast<std::size_t>(stem)];
    if (note_index >= static_cast<int>(notes.size())) {
        return false;
    }
    MidiNote& note = notes[static_cast<std::size_t>(note_index)];
    if (!note.on_tab && !note_has_fingering(note)) {
        return false;
    }
    begin_note_gesture(app, stem);
    clear_fingering(note);
    note.on_tab = false;
    if (app.tab_fingering_mode) {
        app.tab_mode_notes.erase(
            std::remove(app.tab_mode_notes.begin(), app.tab_mode_notes.end(), note_index), app.tab_mode_notes.end());
        if (app.tab_mode_notes.empty()) {
            tab_exit_fingering_mode(app);
        }
    }
    finish_note_gesture(app, stem);
    tab_score_build(app, stem);
    return true;
}

bool tab_unpublish_selection(App& app, int stem) {
    if (stem < 0 || stem >= bt::kStemCount) {
        return false;
    }
    auto& notes = app.notes[static_cast<std::size_t>(stem)];
    if (app.roll_selected.size() != notes.size()) {
        return false;
    }
    bool any = false;
    for (int index = 0; index < static_cast<int>(notes.size()); ++index) {
        if (!app.roll_selected[static_cast<std::size_t>(index)]) {
            continue;
        }
        const MidiNote& note = notes[static_cast<std::size_t>(index)];
        if (note.on_tab || note_has_fingering(note)) {
            any = true;
            break;
        }
    }
    if (!any) {
        return false;
    }
    begin_note_gesture(app, stem);
    for (int index = 0; index < static_cast<int>(notes.size()); ++index) {
        if (!app.roll_selected[static_cast<std::size_t>(index)]) {
            continue;
        }
        MidiNote& note = notes[static_cast<std::size_t>(index)];
        clear_fingering(note);
        note.on_tab = false;
    }
    if (app.tab_fingering_mode) {
        tab_exit_fingering_mode(app);
    }
    finish_note_gesture(app, stem);
    tab_score_build(app, stem);
    return true;
}

bool tab_enter_fingering_for_notes(App& app, int stem, const std::vector<int>& note_indices) {
    if (stem < 0 || stem >= bt::kStemCount || note_indices.empty()) {
        return false;
    }
    auto& notes = app.notes[static_cast<std::size_t>(stem)];
    std::vector<int> selected;
    selected.reserve(note_indices.size());
    for (int index : note_indices) {
        if (index < 0 || index >= static_cast<int>(notes.size())) {
            continue;
        }
        notes[static_cast<std::size_t>(index)].on_tab = true;
        selected.push_back(index);
    }
    if (selected.empty()) {
        return false;
    }
    auto groups = build_fingering_groups(app, stem, selected);
    bool any_combo = false;
    for (const TabOnsetGroup& group : groups) {
        if (!group.combos.empty()) {
            any_combo = true;
            break;
        }
    }
    if (!any_combo) {
        return false;
    }
    app.roll_selected.assign(notes.size(), 0);
    for (int index : selected) {
        app.roll_selected[static_cast<std::size_t>(index)] = 1;
    }
    app.tab_fingering_mode = true;
    app.tab_mode_notes = std::move(selected);
    app.tab_focus_group = 0;
    app.tab_combo_index = 0;
    for (int g = 0; g < static_cast<int>(groups.size()); ++g) {
        const TabOnsetGroup& group = groups[static_cast<std::size_t>(g)];
        if (group.combos.empty()) {
            continue;
        }
        int best = 0;
        int best_dist = std::numeric_limits<int>::max();
        for (int c = 0; c < static_cast<int>(group.combos.size()); ++c) {
            int dist = 0;
            const TabCombo& combo = group.combos[static_cast<std::size_t>(c)];
            for (std::size_t i = 0; i < group.note_indices.size(); ++i) {
                const MidiNote& note = notes[static_cast<std::size_t>(group.note_indices[i])];
                if (note_has_fingering(note) && fingering_matches_pitch(note)) {
                    dist += std::abs(note.string - combo.spots[i].string) * 10 + std::abs(note.fret - combo.spots[i].fret);
                } else {
                    dist += 50;
                }
            }
            if (dist < best_dist) {
                best_dist = dist;
                best = c;
            }
        }
        tab_apply_combo(app, stem, g, best);
    }
    app.tab_focus_group = 0;
    return true;
}

bool tab_enter_fingering_mode(App& app, int stem) {
    if (stem < 0 || stem >= bt::kStemCount) {
        return false;
    }
    const auto& notes = app.notes[static_cast<std::size_t>(stem)];
    if (app.roll_selected.size() != notes.size()) {
        return false;
    }
    std::vector<int> selected;
    for (int index = 0; index < static_cast<int>(notes.size()); ++index) {
        if (app.roll_selected[static_cast<std::size_t>(index)]) {
            selected.push_back(index);
        }
    }
    return tab_enter_fingering_for_notes(app, stem, selected);
}

void tab_exit_fingering_mode(App& app) {
    app.tab_fingering_mode = false;
    app.tab_mode_notes.clear();
    app.tab_focus_group = 0;
    app.tab_combo_index = 0;
}

bool tab_cycle_combo(App& app, int stem, int delta) {
    if (!app.tab_fingering_mode || delta == 0) {
        return false;
    }
    const auto groups = build_fingering_groups(app, stem, app.tab_mode_notes);
    if (groups.empty()) {
        return false;
    }
    int group = std::clamp(app.tab_focus_group, 0, static_cast<int>(groups.size()) - 1);
    for (int guard = 0; guard < static_cast<int>(groups.size()); ++guard) {
        if (!groups[static_cast<std::size_t>(group)].combos.empty()) {
            break;
        }
        group = (group + (delta > 0 ? 1 : -1) + static_cast<int>(groups.size())) % static_cast<int>(groups.size());
    }
    const int count = static_cast<int>(groups[static_cast<std::size_t>(group)].combos.size());
    if (count <= 0) {
        return false;
    }
    const int next = app.tab_combo_index + delta;
    return tab_apply_combo(app, stem, group, next);
}

int tab_combo_count(const App& app, int stem) {
    if (!app.tab_fingering_mode) {
        return 0;
    }
    const auto groups = build_fingering_groups(app, stem, app.tab_mode_notes);
    if (app.tab_focus_group < 0 || app.tab_focus_group >= static_cast<int>(groups.size())) {
        return 0;
    }
    return static_cast<int>(groups[static_cast<std::size_t>(app.tab_focus_group)].combos.size());
}

bool tab_selection_locked(const App& app, int note_index) {
    if (!app.tab_fingering_mode) {
        return false;
    }
    for (int index : app.tab_mode_notes) {
        if (index == note_index) {
            return true;
        }
    }
    return false;
}

bool handle_tab_fingering_keys(App& app) {
    if (!app.tab_fingering_mode || app.note_stage != NoteStage::Midi) {
        return false;
    }
    if (ImGui::GetIO().WantTextInput || ImGui::IsAnyItemActive()) {
        return false;
    }
    if (ImGui::IsKeyPressed(ImGuiKey_Escape, false) || ImGui::IsKeyPressed(ImGuiKey_Enter, false)) {
        tab_exit_fingering_mode(app);
        return true;
    }
    return false;
}

void tab_score_clear(App& app, int stem) {
    if (stem < 0 || stem >= bt::kStemCount) {
        return;
    }
    app.tab_scores[static_cast<std::size_t>(stem)] = {};
    app.tab_score_measure = 0;
    app.tab_score_beat = 0;
}

bool tab_score_build(App& app, int stem, bool on_tab_only) {
    if (stem < 0 || stem >= bt::kStemCount) {
        return false;
    }
    auto workspace = tab_workspace(app);
    const bool ok = bt::core::build_tab_score(workspace, stem, on_tab_only);
    tab_workspace_pull(app, workspace);
    if (ok) {
        park_score_caret_on_first_note(app, stem);
    } else {
        clamp_score_caret(app, stem);
    }
    return ok;
}

bool tab_score_ensure(App& app, int stem) {
    if (stem < 0 || stem >= bt::kStemCount) {
        return false;
    }
    if (!tab_score_empty(app.tab_scores[static_cast<std::size_t>(stem)])) {
        return true;
    }
    return tab_score_build(app, stem, false);
}

bool tab_score_ensure_layout(App& app, int stem) {
    if (stem < 0 || stem >= bt::kStemCount) {
        return false;
    }
    const auto& notes = app.notes[static_cast<std::size_t>(stem)];
    bool needs_finger = false;
    for (const MidiNote& note : notes) {
        if (!note_has_fingering(note)) {
            needs_finger = true;
            break;
        }
    }
    if (needs_finger) {
        tab_resolve_fingerings(app, stem, false);
    }
    if (!tab_score_empty(app.tab_scores[static_cast<std::size_t>(stem)])) {
        return true;
    }
    return tab_score_build(app, stem, false);
}

bool tab_group_snapped(const App& app, int stem, const std::vector<int>& note_indices) {
    auto workspace = tab_workspace(const_cast<App&>(app));
    return bt::core::tab_group_snapped(workspace, stem, note_indices);
}

bool tab_score_set_duration(App& app, int stem, int measure, int beat, TabDuration duration, bool dotted) {
    if (stem < 0 || stem >= bt::kStemCount) {
        return false;
    }
    tab_score_ensure(app, stem);
    const TabScore& score = app.tab_scores[static_cast<std::size_t>(stem)];
    if (!bt::core::tab_slot_valid(score, measure, beat)) {
        return false;
    }
    const TabBeat& current = score.measures[static_cast<std::size_t>(measure)].beats[static_cast<std::size_t>(beat)];
    const bool writes_notes = !current.rest && !current.note_indices.empty();
    const bool nested = app.note_gesture;
    if (writes_notes && !nested) {
        begin_note_gesture(app, stem);
    }
    auto workspace = tab_workspace(app);
    bt::core::TabEditCaret caret;
    const bool ok = bt::core::set_tab_duration(workspace, stem, measure, beat, duration, dotted, &caret);
    tab_workspace_pull(app, workspace);
    if (writes_notes && !nested) {
        finish_note_gesture(app, stem);
    }
    if (!ok) {
        return false;
    }
    app.tab_score_measure = caret.measure;
    app.tab_score_beat = caret.beat;
    app.tab_duration = caret.duration;
    app.tab_duration_dotted = caret.dotted;
    clamp_score_caret(app, stem);
    return true;
}

bool tab_score_snap_group(App& app, int stem, const std::vector<int>& note_indices, int measure, int beat) {
    if (stem < 0 || stem >= bt::kStemCount || note_indices.empty()) {
        return false;
    }
    tab_score_ensure(app, stem);
    if (!bt::core::tab_slot_valid(app.tab_scores[static_cast<std::size_t>(stem)], measure, beat)) {
        return false;
    }
    const bool nested = app.note_gesture;
    if (!nested) {
        begin_note_gesture(app, stem);
    }
    auto workspace = tab_workspace(app);
    const bool ok = bt::core::snap_tab_group(workspace, stem, note_indices, measure, beat);
    tab_workspace_pull(app, workspace);
    if (!nested) {
        finish_note_gesture(app, stem);
    }
    if (!ok) {
        return false;
    }
    const TabBeat& dest =
        app.tab_scores[static_cast<std::size_t>(stem)].measures[static_cast<std::size_t>(measure)].beats[static_cast<std::size_t>(beat)];
    app.tab_score_measure = measure;
    app.tab_score_beat = beat;
    app.tab_duration = dest.duration;
    app.tab_duration_dotted = dest.dotted;
    clamp_score_caret(app, stem);
    return true;
}

bool tab_score_sync_from_midi(App& app, int stem) {
    if (stem < 0 || stem >= bt::kStemCount) {
        return false;
    }
    auto workspace = tab_workspace(app);
    const bool ok = bt::core::sync_tab_score_from_midi(workspace, stem);
    if (ok) {
        clamp_score_caret(app, stem);
    }
    return ok;
}

bool tab_score_nudge_duration(App& app, int stem, int delta) {
    if (stem < 0 || stem >= bt::kStemCount || delta == 0) {
        return false;
    }
    tab_score_ensure(app, stem);
    clamp_score_caret(app, stem);
    const TabScore& score = app.tab_scores[static_cast<std::size_t>(stem)];
    if (!bt::core::tab_slot_valid(score, app.tab_score_measure, app.tab_score_beat)) {
        return false;
    }
    const TabBeat& beat =
        score.measures[static_cast<std::size_t>(app.tab_score_measure)].beats[static_cast<std::size_t>(app.tab_score_beat)];
    const TabDuration next = tab_duration_step(beat.duration, -delta);
    return tab_score_set_duration(app, stem, app.tab_score_measure, app.tab_score_beat, next, beat.dotted);
}

bool tab_score_insert_rest(App& app, int stem) {
    if (stem < 0 || stem >= bt::kStemCount) {
        return false;
    }
    tab_score_ensure(app, stem);
    clamp_score_caret(app, stem);
    TabScore& score = app.tab_scores[static_cast<std::size_t>(stem)];
    if (!bt::core::tab_slot_valid(score, app.tab_score_measure, app.tab_score_beat)) {
        return false;
    }
    TabMeasure& bar = score.measures[static_cast<std::size_t>(app.tab_score_measure)];
    const TabBeat at = bar.beats[static_cast<std::size_t>(app.tab_score_beat)];
    auto workspace = tab_workspace(app);
    TabBeat rest;
    rest.rest = true;
    rest.duration = app.tab_duration;
    rest.dotted = app.tab_duration_dotted;
    rest.time = at.time + tab_duration_quarters(at.duration, at.dotted) * bt::core::tab_pulse_seconds(workspace);
    bar.beats.insert(bar.beats.begin() + app.tab_score_beat + 1, rest);
    return tab_score_set_duration(app, stem, app.tab_score_measure, app.tab_score_beat, at.duration, at.dotted);
}

bool tab_score_delete_beat(App& app, int stem) {
    if (stem < 0 || stem >= bt::kStemCount) {
        return false;
    }
    tab_score_ensure(app, stem);
    clamp_score_caret(app, stem);
    TabScore& score = app.tab_scores[static_cast<std::size_t>(stem)];
    if (!bt::core::tab_slot_valid(score, app.tab_score_measure, app.tab_score_beat)) {
        return false;
    }
    TabMeasure& bar = score.measures[static_cast<std::size_t>(app.tab_score_measure)];
    TabBeat& target = bar.beats[static_cast<std::size_t>(app.tab_score_beat)];
    if (!target.rest) {
        target.rest = true;
        target.note_indices.clear();
    } else {
        bar.beats.erase(bar.beats.begin() + app.tab_score_beat);
    }
    if (bar.beats.empty()) {
        TabBeat rest;
        rest.rest = true;
        rest.duration = TabDuration::Whole;
        rest.time = bar.start;
        bar.beats.push_back(rest);
    }
    auto workspace = tab_workspace(app);
    bt::core::pad_measure_rests(bar, score.beats_per_bar, bt::core::tab_pulse_seconds(workspace));
    clamp_score_caret(app, stem);
    return true;
}

bool tab_score_move_caret(App& app, int stem, int beat_delta, int string_delta) {
    if (stem < 0 || stem >= bt::kStemCount) {
        return false;
    }
    if (!tab_score_ensure(app, stem)) {
        return false;
    }
    clamp_score_caret(app, stem);
    app.tab_score_string = std::clamp(app.tab_score_string + string_delta, 0, kGuitarStringCount - 1);
    if (beat_delta == 0) {
        return string_delta != 0;
    }
    TabScore& score = app.tab_scores[static_cast<std::size_t>(stem)];
    int measure = app.tab_score_measure;
    int beat = app.tab_score_beat + beat_delta;
    while (true) {
        if (measure < 0 || measure >= static_cast<int>(score.measures.size())) {
            clamp_score_caret(app, stem);
            return true;
        }
        const int count = static_cast<int>(score.measures[static_cast<std::size_t>(measure)].beats.size());
        if (beat >= 0 && beat < count) {
            app.tab_score_measure = measure;
            app.tab_score_beat = beat;
            const TabBeat& cur = score.measures[static_cast<std::size_t>(measure)].beats[static_cast<std::size_t>(beat)];
            app.tab_duration = cur.duration;
            app.tab_duration_dotted = cur.dotted;
            return true;
        }
        if (beat_delta > 0) {
            ++measure;
            beat = 0;
        } else {
            --measure;
            if (measure >= 0 && measure < static_cast<int>(score.measures.size())) {
                beat = static_cast<int>(score.measures[static_cast<std::size_t>(measure)].beats.size()) - 1;
            }
        }
    }
}

bool handle_tab_score_keys(App& app) {
    if (app.note_stage != NoteStage::Midi || app.midi_view != MidiView::Tab || pane_is_roll(app.pane_focus)) {
        return false;
    }
    if (ImGui::GetIO().WantTextInput || ImGui::IsAnyItemActive()) {
        return false;
    }
    const int stem = std::clamp(app.selected, 0, bt::kStemCount - 1);
    if (!tab_score_ensure(app, stem)) {
        return false;
    }
    if (ImGui::IsKeyPressed(ImGuiKey_LeftArrow, true)) {
        return tab_score_move_caret(app, stem, -1, 0);
    }
    if (ImGui::IsKeyPressed(ImGuiKey_RightArrow, true)) {
        return tab_score_move_caret(app, stem, 1, 0);
    }
    if (ImGui::IsKeyPressed(ImGuiKey_UpArrow, true)) {
        return tab_score_move_caret(app, stem, 0, -1);
    }
    if (ImGui::IsKeyPressed(ImGuiKey_DownArrow, true)) {
        return tab_score_move_caret(app, stem, 0, 1);
    }
    if (ImGui::IsKeyPressed(ImGuiKey_Equal, false) || ImGui::IsKeyPressed(ImGuiKey_KeypadAdd, false)) {
        return tab_score_nudge_duration(app, stem, 1);
    }
    if (ImGui::IsKeyPressed(ImGuiKey_Minus, false) || ImGui::IsKeyPressed(ImGuiKey_KeypadSubtract, false)) {
        return tab_score_nudge_duration(app, stem, -1);
    }
    if (ImGui::IsKeyPressed(ImGuiKey_Insert, false)) {
        return tab_score_insert_rest(app, stem);
    }
    if (ImGui::IsKeyPressed(ImGuiKey_Delete, false) && !app.tab_fingering_mode) {
        clamp_score_caret(app, stem);
        const TabScore& score = app.tab_scores[static_cast<std::size_t>(stem)];
        if (bt::core::tab_slot_valid(score, app.tab_score_measure, app.tab_score_beat) &&
            score.measures[static_cast<std::size_t>(app.tab_score_measure)]
                .beats[static_cast<std::size_t>(app.tab_score_beat)]
                .rest) {
            return tab_score_delete_beat(app, stem);
        }
    }
    if (ImGui::IsKeyPressed(ImGuiKey_Period, false)) {
        clamp_score_caret(app, stem);
        const TabScore& score = app.tab_scores[static_cast<std::size_t>(stem)];
        if (!bt::core::tab_slot_valid(score, app.tab_score_measure, app.tab_score_beat)) {
            return false;
        }
        const TabBeat& beat =
            score.measures[static_cast<std::size_t>(app.tab_score_measure)].beats[static_cast<std::size_t>(app.tab_score_beat)];
        return tab_score_set_duration(app, stem, app.tab_score_measure, app.tab_score_beat, beat.duration, !beat.dotted);
    }
    return false;
}

}  // namespace btapp
