#pragma once

#include <array>
#include <cstdint>
#include <filesystem>
#include <string>

namespace btapp {

struct App;

enum class Action : int {
    PlayPause = 0,
    Stop,
    SeekBack,
    SeekForward,
    Loop,
    ClearLoop,
    Metro,
    CountIn,
    Snap,
    Mute,
    Solo,
    PrevTrack,
    NextTrack,
    ViewGuitar,
    ViewBass,
    ViewFall,
    AdjustSpeed,
    AdjustSeek,
    AdjustMaster,
    AdjustClick,
    AdjustTrackGain,
    AdjustTrackPan,
    Count
};

struct KeyChord {
    bool ctrl = false;
    bool shift = false;
    bool alt = false;
    int key = 0;  // ImGuiKey as int; 0 = unbound
};

struct Keymap {
    std::array<KeyChord, static_cast<std::size_t>(Action::Count)> bindings{};
    std::array<KeyChord, static_cast<std::size_t>(Action::Count)> alternate{};
};

const char* action_label(Action action, int language);
Keymap default_keymap();
void load_keymap(App& app);
void save_keymap(const App& app);
bool reserved_chord(const KeyChord& chord);
bool chord_pressed(const KeyChord& chord, bool repeat);
std::string chord_text(const KeyChord& chord);
bool capture_chord(KeyChord& out);
void draw_keymap_settings(App& app);
void handle_actions(App& app, bool ready);

}  // namespace btapp
