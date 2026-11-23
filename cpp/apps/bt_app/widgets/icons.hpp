#pragma once

#include "ui.hpp"

#include "imgui.h"

namespace btapp {

inline constexpr int kIconCount = static_cast<int>(Icon::Count) + 1;
// Private-use codepoints in fonts/bt-icons.ttf (tools/build_icon_font.py).
inline constexpr ImWchar kIconGlyphBase = 0xE000;

void set_icon_font(ImFont* font);
ImFont* icon_font();
bool icon_font_ready();

// Draw via fonts/bt-icons.ttf glyph (ImGui font atlas). Returns false if missing.
bool draw_icon_font(ImDrawList* draw, Icon icon, ImVec2 min, ImVec2 max, ImU32 tint);

}  // namespace btapp
