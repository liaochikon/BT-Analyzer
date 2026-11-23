#pragma once

#include <cstdint>

namespace btapp {

struct App;

// Shared pointer / mouse gesture state for the shell + MIDI dual-view.
// Wave scrub for stems uses raw stem index 0..N-1 (see wave_stem_drag).

enum class DragKind : int {
    None = -1,
    // 0 .. stemCount-1 = per-stem waveform scrub (passed as stem index).
    WaveMix = 6,
    Fall = 8,
    RollNote = 10,
    RollOverview = 11,
    RollPan = 12,
    RollSeek = 13,
    RollBox = 14,
    TabSplit = 16,
    TabGroup = 17,
};

enum class PaneFocus : std::uint8_t {
    Roll = 0,  // piano-roll owns shortcuts / erase continue
    Tab = 1,   // TAB strip owns shortcuts / erase continue
};

enum class RollEdit : int {
    Move = 0,
    Start = 1,
    End = 2,
    Place = 3,
};

inline constexpr bool roll_note_dragging(DragKind kind) {
    return kind == DragKind::RollNote;
}

inline constexpr DragKind wave_stem_drag(int stem) {
    return static_cast<DragKind>(stem);
}

inline constexpr bool drag_active(DragKind kind) {
    return kind != DragKind::None;
}

inline constexpr bool pane_is_tab(PaneFocus focus) {
    return focus == PaneFocus::Tab;
}

inline constexpr bool pane_is_roll(PaneFocus focus) {
    return focus == PaneFocus::Roll;
}

// After InvisibleButton("midi-view"): true when the dual-view should take pointer input.
bool midi_canvas_hot(float origin_x, float origin_y, float width, float height);

// Abort in-progress pointer gestures (drag / erase / tab-group). Finishes note_gesture if open.
void cancel_pointer_gestures(App& app);

}  // namespace btapp
