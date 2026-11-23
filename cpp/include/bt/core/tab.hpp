#pragma once

#include "bt/core/grid.hpp"
#include "bt/core/types.hpp"

#include <filesystem>
#include <vector>

namespace bt::core {

struct TabEditCaret {
    int measure = 0;
    int beat = 0;
    TabDuration duration = TabDuration::Quarter;
    bool dotted = false;
};

bool note_has_fingering(const bt::Note& note);
bool fingering_matches_pitch(const bt::Note& note);
void clear_fingering(bt::Note& note);
void apply_fingering(bt::Note& note, TabSpot spot);
void revalidate_stem_fingerings(std::vector<bt::Note>& notes);
int collect_guitar_spots(int pitch, bool allow_open, TabSpot* out, int cap);
bool cycle_note_fingering(bt::Note& note, bool allow_open);
bool set_note_string(bt::Note& note, int string, bool allow_open);

double tab_duration_quarters(TabDuration duration, bool dotted);
const char* tab_duration_label_key(TabDuration duration);
TabDuration tab_duration_step(TabDuration duration, int delta);
TabDuration tab_duration_from_quarters(double quarters);

bool tab_score_empty(const TabScore& score);
double tab_display_end(
    const std::vector<bt::Note>& notes, const std::vector<int>& group, int note_index, double onset_eps);

TabGate evaluate_tab_gate(const TabWorkspace& workspace, int stem);
bool resolve_fingerings(TabWorkspace& workspace, int stem);

std::vector<TabCombo> enumerate_fingering_combos(
    const TabWorkspace& workspace, int stem, const std::vector<int>& note_indices);
std::vector<TabOnsetGroup> build_fingering_groups(
    const TabWorkspace& workspace, int stem, const std::vector<int>& note_indices);
TabGroupConflict tab_group_conflict(const TabWorkspace& workspace, int stem, const std::vector<int>& note_indices);
int matching_fingering_combo(const TabWorkspace& workspace, int stem, const TabOnsetGroup& group);
int preferred_fingering_combo(const TabWorkspace& workspace, const TabOnsetGroup& group);
bool write_fingering_combo(TabWorkspace& workspace, int stem, const TabOnsetGroup& group, int combo_index);

// Writes workspace.scores[stem]. Also resolves fingerings (no history).
bool build_tab_score(TabWorkspace& workspace, int stem, bool on_tab_only = false);
bool sync_tab_score_from_midi(TabWorkspace& workspace, int stem);
bool set_tab_duration(
    TabWorkspace& workspace, int stem, int measure, int beat, TabDuration duration, bool dotted,
    TabEditCaret* caret = nullptr);
bool snap_tab_group(TabWorkspace& workspace, int stem, const std::vector<int>& note_indices, int measure, int beat);
bool tab_group_snapped(const TabWorkspace& workspace, int stem, const std::vector<int>& note_indices);

// Empty `selected` quantizes every note. A mask must match the note count.
bool quantize_notes(TabWorkspace& workspace, int stem, const std::vector<char>& selected = {});
// Empty `selected` converts every note: clear fingering, mark on_tab, resolve.
bool convert_notes_to_tab(TabWorkspace& workspace, int stem, const std::vector<char>& selected = {});
TabExportResult export_tab_text(const TabWorkspace& workspace, int stem, const std::filesystem::path& path);

double tab_pulse_seconds(const TabWorkspace& workspace);
bool tab_slot_valid(const TabScore& score, int measure, int beat);
void pad_measure_rests(TabMeasure& measure, int beats_per_bar, double beat_seconds);

}  // namespace bt::core
