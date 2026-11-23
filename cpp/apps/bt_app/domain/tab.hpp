#pragma once

#include "app_state.hpp"

#include "bt/core/tab.hpp"

#include <filesystem>
#include <string>
#include <vector>

namespace btapp {

using bt::core::kGuitarOpenPitch;
using bt::core::kGuitarStem;
using bt::core::kGuitarStringCount;
inline constexpr int kTabMaxFret = 29;

using bt::core::apply_fingering;
using bt::core::clear_fingering;
using bt::core::collect_guitar_spots;
using bt::core::cycle_note_fingering;
using bt::core::fingering_matches_pitch;
using bt::core::note_has_fingering;
using bt::core::revalidate_stem_fingerings;
using bt::core::set_note_string;
using bt::core::tab_display_end;
using bt::core::tab_duration_from_quarters;
using bt::core::tab_duration_label_key;
using bt::core::tab_duration_quarters;
using bt::core::tab_duration_step;
using bt::core::tab_score_empty;

using TabSpot = bt::core::TabSpot;
using TabGate = bt::core::TabGate;
using TabExportResult = bt::core::TabExportResult;
using TabCombo = bt::core::TabCombo;
using TabOnsetGroup = bt::core::TabOnsetGroup;
using TabGroupConflict = bt::core::TabGroupConflict;

inline int tab_hand_span_frets(const App& app) {
    return std::clamp(app.tab_hand_span, 1, 12);
}

inline double tab_onset_window_seconds(const App& app) {
    return std::max(0.0, static_cast<double>(std::clamp(app.tab_onset_ms, 0, 200)) * 0.001);
}

bt::core::TabWorkspace tab_workspace(App& app);
void tab_workspace_pull(App& app, const bt::core::TabWorkspace& workspace);

bool tab_note_on_staff(const App& app, int stem, int note_index);
bool stem_fully_fingered(const App& app, int stem);

TabGate evaluate_tab_gate(const App& app, int stem);
bool convert_stem_to_tab(App& app, int stem);
TabExportResult export_stem_tab_text(const App& app, int stem, const std::filesystem::path& path);

std::vector<TabCombo> enumerate_fingering_combos(const App& app, int stem, const std::vector<int>& note_indices);
std::vector<TabOnsetGroup> build_fingering_groups(const App& app, int stem, const std::vector<int>& note_indices);
TabGroupConflict tab_group_conflict(const App& app, int stem, const std::vector<int>& note_indices);
bool tab_resolve_fingerings(App& app, int stem, bool record_history = true);

bool tab_publish_selection(App& app, int stem);
bool tab_unpublish_selection(App& app, int stem);
bool tab_unpublish_note(App& app, int stem, int note_index);
bool tab_enter_fingering_mode(App& app, int stem);
bool tab_enter_fingering_for_notes(App& app, int stem, const std::vector<int>& note_indices);
bool tab_focus_group_notes(App& app, int stem, const std::vector<int>& note_indices);
void tab_exit_fingering_mode(App& app);
bool tab_cycle_combo(App& app, int stem, int delta);
bool tab_cycle_notes_combo(App& app, int stem, const std::vector<int>& note_indices, int delta);
bool tab_apply_combo(App& app, int stem, int group_index, int combo_index);
int tab_combo_count(const App& app, int stem);
bool tab_selection_locked(const App& app, int note_index);
bool handle_tab_fingering_keys(App& app);

void tab_score_clear(App& app, int stem);
bool tab_score_build(App& app, int stem, bool on_tab_only = false);
bool tab_score_ensure(App& app, int stem);
bool tab_score_ensure_layout(App& app, int stem);
bool tab_group_snapped(const App& app, int stem, const std::vector<int>& note_indices);
bool tab_score_set_duration(App& app, int stem, int measure, int beat, TabDuration duration, bool dotted);
bool tab_score_nudge_duration(App& app, int stem, int delta);
bool tab_score_insert_rest(App& app, int stem);
bool tab_score_delete_beat(App& app, int stem);
bool tab_score_move_caret(App& app, int stem, int beat_delta, int string_delta);
bool tab_score_snap_group(App& app, int stem, const std::vector<int>& note_indices, int measure, int beat);
bool tab_score_sync_from_midi(App& app, int stem);
bool handle_tab_score_keys(App& app);

}  // namespace btapp
