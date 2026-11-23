#include "icons.hpp"

#include "imgui_internal.h"

#include <algorithm>
#include <cfloat>

namespace btapp {
namespace {

ImFont* g_icon_font = nullptr;

}  // namespace

void set_icon_font(ImFont* font) {
    g_icon_font = font;
}

ImFont* icon_font() {
    return g_icon_font;
}

bool icon_font_ready() {
    return g_icon_font != nullptr;
}

bool draw_icon_font(ImDrawList* draw, Icon icon, ImVec2 min, ImVec2 max, ImU32 tint) {
    if (draw == nullptr || g_icon_font == nullptr) {
        return false;
    }
    if (static_cast<int>(icon) < 0 || static_cast<int>(icon) >= kIconCount) {
        return false;
    }
    const ImWchar code = static_cast<ImWchar>(kIconGlyphBase + static_cast<int>(icon));
    if (g_icon_font->FindGlyphNoFallback(code) == nullptr) {
        return false;
    }
    char utf8[8]{};
    ImTextCharToUtf8(utf8, code);
    const float side = std::min(max.x - min.x, max.y - min.y);
    if (side <= 1.f) {
        return false;
    }
    const ImVec2 size = g_icon_font->CalcTextSizeA(side, FLT_MAX, 0.f, utf8);
    const ImVec2 pos((min.x + max.x - size.x) * 0.5f, (min.y + max.y - size.y) * 0.5f);
    draw->AddText(g_icon_font, side, pos, tint, utf8);
    return true;
}

}  // namespace btapp
