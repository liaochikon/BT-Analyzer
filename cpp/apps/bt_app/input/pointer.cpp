#include "pointer.hpp"

#include "app_state.hpp"
#include "notes.hpp"

#include "imgui.h"

namespace btapp {

bool midi_canvas_hot(float origin_x, float origin_y, float width, float height) {
    const ImGuiIO& io = ImGui::GetIO();
    const ImVec2 mouse = io.MousePos;
    const bool in_rect = mouse.x >= origin_x && mouse.x < origin_x + width && mouse.y >= origin_y &&
                         mouse.y < origin_y + height;
    if (!in_rect) {
        return false;
    }
    constexpr ImGuiHoveredFlags kHoverFlags = ImGuiHoveredFlags_AllowWhenBlockedByActiveItem;
    if (ImGui::IsItemHovered(kHoverFlags)) {
        return true;
    }
    if (ImGui::IsItemActive()) {
        return true;
    }
    // First click after alt-tab / clicking back into the app: item hover can lag one frame.
    if (ImGui::IsMouseClicked(ImGuiMouseButton_Left) || ImGui::IsMouseClicked(ImGuiMouseButton_Right) ||
        ImGui::IsMouseClicked(ImGuiMouseButton_Middle)) {
        if (ImGui::IsWindowHovered(kHoverFlags | ImGuiHoveredFlags_AllowWhenBlockedByPopup)) {
            return true;
        }
    }
    return false;
}

void cancel_pointer_gestures(App& app) {
    if (app.note_gesture) {
        finish_note_gesture(app, app.note_gesture_stem);
    }
    app.wave_drag = DragKind::None;
    app.loop_drag = DragKind::None;
    app.roll_note = -1;
    app.roll_erase = false;
    app.roll_paste = false;
    app.roll_drag_group.clear();
    app.tab_group_dragging = false;
    app.tab_drag_notes.clear();
    app.tab_drag_measure = -1;
    app.tab_drag_beat = -1;
}

}  // namespace btapp
