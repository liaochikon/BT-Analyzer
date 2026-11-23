#pragma once

#include <array>
#include <cstdint>
#include <filesystem>
#include <string>

namespace btapp {

struct App;

enum class Action : int {
    // Project
    Undo = 0,
    Redo,
    // Player
    PlayPause,
    Stop,
    SeekBack,
    SeekForward,
    Loop,
    ClearLoop,
    Metro,
    Grid,
    CountIn,
    Snap,
    AdjustSpeed,
    AdjustSeek,
    AdjustMaster,
    AdjustClick,
    AdjustDown,
    AdjustUp,
    // Track / mixer
    Mute,
    Solo,
    PrevTrack,
    NextTrack,
    AdjustTrackGain,
    AdjustTrackPan,
    // MIDI edit
    MidiCopy,
    MidiPaste,
    MidiDuplicate,
    MidiDelete,
    // TAB fingering (Midi dual-view)
    TabFingering,
    TabComboPrev,
    TabComboNext,
    Count
};

enum class ActionScope : int {
    Project = 0,
    Player,
    Track,
    Midi,
};

constexpr int kMaxSequenceSteps = 4;

struct KeyChord {
    bool ctrl = false;
    bool shift = false;
    bool alt = false;
    int key = 0;  // ImGuiKey as int; 0 = unbound
};

struct KeySequence {
    std::array<KeyChord, kMaxSequenceSteps> steps{};
    int length = 0;

    bool empty() const { return length <= 0 || steps[0].key == 0; }
};

struct Keymap {
    std::array<KeySequence, static_cast<std::size_t>(Action::Count)> bindings{};
    std::array<KeySequence, static_cast<std::size_t>(Action::Count)> alternate{};
};

ActionScope action_scope(Action action);
const char* action_label(Action action, int language);
Keymap default_keymap();
void load_keymap(App& app);
void save_keymap(const App& app);
bool reserved_chord(const KeyChord& chord);
bool chord_pressed(const KeyChord& chord, bool repeat);
std::string chord_text(const KeyChord& chord);
std::string sequence_text(const KeySequence& sequence);
bool capture_chord(KeyChord& out);
void draw_keymap_settings(App& app);
void draw_shortcut_hints(App& app);

// Single keyboard entry (called once per frame from ui::draw).
// Pipeline:
//   1) gates — keymap capture, IME, WantTextInput, settings, active widgets
//   2) mode-local — paste Esc/Enter, TAB fingering Esc/Enter, TAB score caret keys
//   3) keymap Actions — transport / mixer / MIDI edit bindings
// UI widgets (settings capture, focused text fields) keep their own Esc locally.
void handle_actions(App& app, bool ready);

}  // namespace btapp
