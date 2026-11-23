#include "notation.hpp"

#include "imgui_internal.h"

#include <algorithm>
#include <cfloat>

namespace btapp {
namespace {

ImFont* g_notation_font = nullptr;

constexpr ImWchar kRestByDuration[] = {
    smufl::restWhole,
    smufl::restHalf,
    smufl::restQuarter,
    smufl::rest8th,
    smufl::rest16th,
    smufl::rest32nd,
    smufl::rest64th,
};

}  // namespace

void set_notation_font(ImFont* font) {
    g_notation_font = font;
}

ImFont* notation_font() {
    return g_notation_font;
}

bool notation_font_ready() {
    return g_notation_font != nullptr;
}

ImWchar rest_for_tab_duration(int duration_index) {
    if (duration_index < 0 ||
        duration_index >= static_cast<int>(sizeof(kRestByDuration) / sizeof(kRestByDuration[0]))) {
        return 0;
    }
    return kRestByDuration[duration_index];
}

bool draw_notation_glyph(ImDrawList* draw, ImWchar code, ImVec2 min, ImVec2 max, ImU32 tint) {
    if (draw == nullptr || g_notation_font == nullptr || code == 0) {
        return false;
    }
    if (g_notation_font->FindGlyphNoFallback(code) == nullptr) {
        return false;
    }
    char utf8[8]{};
    ImTextCharToUtf8(utf8, code);
    const float side = std::min(max.x - min.x, max.y - min.y);
    if (side <= 1.f) {
        return false;
    }
    const ImVec2 size = g_notation_font->CalcTextSizeA(side, FLT_MAX, 0.f, utf8);
    const ImVec2 pos((min.x + max.x - size.x) * 0.5f, (min.y + max.y - size.y) * 0.5f);
    draw->AddText(g_notation_font, side, pos, tint, utf8);
    return true;
}

}  // namespace btapp
