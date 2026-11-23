#pragma once

#include "app_state.hpp"

#include "imgui.h"

namespace btapp {

void draw_ab_loop(
    ImDrawList* draw, float scale, ImVec2 area_min, ImVec2 area_max, bool has_a, float a_pos, bool has_b, float b_pos, bool time_down,
    ImU32 wash, ImU32 line, ImU32 tag, ImU32 ink, ImU32 outline);
void midi_note_name(int pitch, char* text, std::size_t size);
void instrument_pitch_bounds(bool bass, int& low, int& high);
void draw_midi_canvas(App& app, const bt::Player::Snapshot& transport, double length, bool ready, ImVec2 size);

// Global MIDI edit actions (no-op when not in MIDI edit or selection is empty).
void midi_copy_selection(App& app);
void midi_begin_paste_preview(App& app);
void midi_cancel_paste(App& app);
// Commit paste at roll_paste_time / roll_paste_pitch (set while the roll draws preview).
bool midi_commit_paste_preview(App& app);
// Mode-local keys for paste preview: Esc cancel, Enter commit. Called from handle_actions.
bool midi_handle_paste_keys(App& app);
void midi_duplicate_selection(App& app);
void midi_delete_selection(App& app);

}  // namespace btapp
