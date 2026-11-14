#pragma once

#include "ui.hpp"

#include "imgui.h"

#include <filesystem>

struct ID3D11Device;

namespace btapp {

inline constexpr int kIconCount = static_cast<int>(Icon::Count) + 1;

// Load assets/icons/atlas.png + atlas.json. Safe to call once after D3D device exists.
bool load_icon_atlas(ID3D11Device* device, const std::filesystem::path& atlas_png);
void unload_icon_atlas();
bool icon_atlas_ready();

// Draws a tinted atlas glyph into [min,max]. Returns false if that icon is missing
// from the atlas (caller may fall back to vector drawing).
bool draw_icon_atlas(ImDrawList* draw, Icon icon, ImVec2 min, ImVec2 max, ImU32 tint);

}  // namespace btapp
