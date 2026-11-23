#pragma once

#include "imgui.h"

namespace btapp {

// SMuFL codepoints used by TAB rhythm / guitar marks (Bravura).
namespace smufl {
inline constexpr ImWchar restWhole = 0xE4E3;
inline constexpr ImWchar restHalf = 0xE4E4;
inline constexpr ImWchar restQuarter = 0xE4E5;
inline constexpr ImWchar rest8th = 0xE4E6;
inline constexpr ImWchar rest16th = 0xE4E7;
inline constexpr ImWchar rest32nd = 0xE4E8;
inline constexpr ImWchar rest64th = 0xE4E9;
inline constexpr ImWchar noteheadWhole = 0xE0A2;
inline constexpr ImWchar noteheadHalf = 0xE0A3;
inline constexpr ImWchar noteheadBlack = 0xE0A4;
inline constexpr ImWchar noteheadXBlack = 0xE0A9;
inline constexpr ImWchar noteheadDiamondBlack = 0xE0D8;
inline constexpr ImWchar flag8thUp = 0xE240;
inline constexpr ImWchar flag16thUp = 0xE242;
inline constexpr ImWchar flag32ndUp = 0xE244;
inline constexpr ImWchar flag64thUp = 0xE246;
inline constexpr ImWchar augmentationDot = 0xE1E7;
inline constexpr ImWchar guitarLeftHandTapping = 0xE840;
inline constexpr ImWchar guitarRightHandTapping = 0xE841;
inline constexpr ImWchar guitarGolpe = 0xE842;
inline constexpr ImWchar guitarStrumUp = 0xE846;
inline constexpr ImWchar guitarStrumDown = 0xE847;
inline constexpr ImWchar guitarBarreFull = 0xE848;
inline constexpr ImWchar guitarShake = 0xE832;
inline constexpr ImWchar wiggleVibrato = 0xEAB2;
inline constexpr ImWchar tabClef6 = 0xE06D;
}  // namespace smufl

void set_notation_font(ImFont* font);
ImFont* notation_font();
bool notation_font_ready();

// Map TabDuration index 0..6 to rest glyph. Returns 0 if out of range.
ImWchar rest_for_tab_duration(int duration_index);

// Draw one SMuFL glyph centered in [min,max]. Returns false if font/glyph missing.
bool draw_notation_glyph(ImDrawList* draw, ImWchar code, ImVec2 min, ImVec2 max, ImU32 tint);

}  // namespace btapp
