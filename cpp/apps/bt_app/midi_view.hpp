#pragma once

#include "ui.hpp"

#include "imgui.h"

namespace btapp {

void draw_ab_loop(
    ImDrawList* draw, float scale, ImVec2 area_min, ImVec2 area_max, bool has_a, float a_pos, bool has_b, float b_pos, bool time_down,
    ImU32 wash, ImU32 line, ImU32 tag, ImU32 ink, ImU32 outline);
void midi_note_name(int pitch, char* text, std::size_t size);
void instrument_pitch_bounds(bool bass, int fret_count, int& low, int& high);
void draw_midi_canvas(App& app, const bt::Player::Snapshot& transport, double length, bool ready, ImVec2 size);

}  // namespace btapp
