#include "keys.hpp"

#include "i18n.hpp"
#include "history.hpp"
#include "playback.hpp"
#include "prefs.hpp"

#include "app_state.hpp"
#include "midi_view.hpp"
#include "notes.hpp"
#include "tab.hpp"
#include "transport.hpp"

#include "imgui.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <imm.h>

namespace btapp {
namespace {

constexpr double kSequenceTimeout = 0.75;
constexpr int kActionCount = static_cast<int>(Action::Count);
constexpr int kLayoutVersion = 4;

bool ime_owns_keyboard(const App& app) {
    if (ImGui::GetIO().WantTextInput) {
        return true;
    }
    const HWND hwnd = reinterpret_cast<HWND>(app.hwnd);
    if (hwnd == nullptr) {
        return false;
    }
    const HIMC context = ImmGetContext(hwnd);
    if (context == nullptr) {
        return false;
    }
    const LONG composing = ImmGetCompositionStringW(context, GCS_COMPSTR, nullptr, 0);
    ImmReleaseContext(hwnd, context);
    return composing > 0;
}

const char* action_id(Action action) {
    switch (action) {
    case Action::Undo:
        return "undo";
    case Action::Redo:
        return "redo";
    case Action::PlayPause:
        return "play";
    case Action::Stop:
        return "stop";
    case Action::SeekBack:
        return "seek_back";
    case Action::SeekForward:
        return "seek_forward";
    case Action::Loop:
        return "loop";
    case Action::ClearLoop:
        return "clear_loop";
    case Action::Metro:
        return "metro";
    case Action::Grid:
        return "grid";
    case Action::CountIn:
        return "count_in";
    case Action::Snap:
        return "snap";
    case Action::Mute:
        return "mute";
    case Action::Solo:
        return "solo";
    case Action::PrevTrack:
        return "prev_track";
    case Action::NextTrack:
        return "next_track";
    case Action::AdjustSpeed:
        return "adjust_speed";
    case Action::AdjustSeek:
        return "adjust_seek";
    case Action::AdjustMaster:
        return "adjust_master";
    case Action::AdjustClick:
        return "adjust_click";
    case Action::AdjustTrackGain:
        return "adjust_track_gain";
    case Action::AdjustTrackPan:
        return "adjust_track_pan";
    case Action::AdjustDown:
        return "adjust_down";
    case Action::AdjustUp:
        return "adjust_up";
    case Action::MidiCopy:
        return "midi_copy";
    case Action::MidiPaste:
        return "midi_paste";
    case Action::MidiDuplicate:
        return "midi_duplicate";
    case Action::MidiDelete:
        return "midi_delete";
    case Action::TabFingering:
        return "tab_fingering";
    case Action::TabComboPrev:
        return "tab_combo_prev";
    case Action::TabComboNext:
        return "tab_combo_next";
    case Action::Count:
        break;
    }
    return "unknown";
}

Action action_from_id(std::string_view id) {
    for (int index = 0; index < kActionCount; ++index) {
        const auto action = static_cast<Action>(index);
        if (id == action_id(action)) {
            return action;
        }
    }
    return Action::Count;
}

KeyChord make_chord(int key, bool shift = false, bool ctrl = false, bool alt = false) {
    return KeyChord{ctrl, shift, alt, key};
}

KeySequence make_sequence(KeyChord chord) {
    KeySequence sequence{};
    if (chord.key == 0) {
        return sequence;
    }
    sequence.steps[0] = chord;
    sequence.length = 1;
    return sequence;
}

bool keyboard_key(int key) {
    return key >= ImGuiKey_Tab && key <= ImGuiKey_Oem102;
}

bool bindable_key(int key) {
    if (!keyboard_key(key)) {
        return false;
    }
    switch (key) {
    case ImGuiKey_Escape:
    case ImGuiKey_LeftCtrl:
    case ImGuiKey_RightCtrl:
    case ImGuiKey_LeftShift:
    case ImGuiKey_RightShift:
    case ImGuiKey_LeftAlt:
    case ImGuiKey_RightAlt:
    case ImGuiKey_LeftSuper:
    case ImGuiKey_RightSuper:
    case ImGuiKey_Menu:
        return false;
    default:
        return true;
    }
}

int imgui_key_from_name(std::string_view name) {
    if (name.empty() || name == "None") {
        return 0;
    }
    for (int key = ImGuiKey_NamedKey_BEGIN; key < ImGuiKey_NamedKey_END; ++key) {
        if (name == ImGui::GetKeyName(static_cast<ImGuiKey>(key))) {
            return key;
        }
    }
    return 0;
}

const char* key_name(int key) {
    if (!keyboard_key(key)) {
        return nullptr;
    }
    return ImGui::GetKeyName(static_cast<ImGuiKey>(key));
}

bool same_chord(const KeyChord& left, const KeyChord& right) {
    return left.key == right.key && left.ctrl == right.ctrl && left.shift == right.shift && left.alt == right.alt;
}

bool same_sequence(const KeySequence& left, const KeySequence& right) {
    if (left.length != right.length) {
        return false;
    }
    for (int index = 0; index < left.length; ++index) {
        if (!same_chord(left.steps[static_cast<std::size_t>(index)], right.steps[static_cast<std::size_t>(index)])) {
            return false;
        }
    }
    return true;
}

bool sequence_is_prefix(const KeySequence& prefix, const KeySequence& full) {
    if (prefix.empty() || prefix.length >= full.length) {
        return false;
    }
    for (int index = 0; index < prefix.length; ++index) {
        if (!same_chord(prefix.steps[static_cast<std::size_t>(index)], full.steps[static_cast<std::size_t>(index)])) {
            return false;
        }
    }
    return true;
}

bool adjust_layer_action(Action action) {
    switch (action) {
    case Action::AdjustSpeed:
    case Action::AdjustSeek:
    case Action::AdjustMaster:
    case Action::AdjustClick:
    case Action::AdjustTrackGain:
    case Action::AdjustTrackPan:
        return true;
    default:
        return false;
    }
}

bool direction_pair(Action left, Action right) {
    return (left == Action::SeekBack && right == Action::AdjustDown) ||
           (left == Action::AdjustDown && right == Action::SeekBack) ||
           (left == Action::SeekForward && right == Action::AdjustUp) ||
           (left == Action::AdjustUp && right == Action::SeekForward);
}

bool compatible_overlap(Action left, Action right) {
    if (left == right) {
        return false;
    }
    if (adjust_layer_action(left) != adjust_layer_action(right)) {
        return true;
    }
    return direction_pair(left, right);
}

int conflict_of(const Keymap& map, Action self, int slot, const KeySequence& sequence) {
    if (sequence.empty()) {
        return -1;
    }
    for (int index = 0; index < kActionCount; ++index) {
        const auto action = static_cast<Action>(index);
        if (compatible_overlap(self, action)) {
            continue;
        }
        for (int other_slot = 0; other_slot < 2; ++other_slot) {
            if (action == self && other_slot == slot) {
                continue;
            }
            const KeySequence& other =
                other_slot == 0 ? map.bindings[static_cast<std::size_t>(index)]
                                : map.alternate[static_cast<std::size_t>(index)];
            if (other.empty()) {
                continue;
            }
            if (same_sequence(sequence, other) || sequence_is_prefix(sequence, other) ||
                sequence_is_prefix(other, sequence)) {
                return index;
            }
        }
    }
    return -1;
}

void apply_mute_or_solo(App& app, bool solo) {
    if (app.session == nullptr || app.selected < 0 || app.selected >= bt::kStemCount) {
        return;
    }
    const auto index = static_cast<std::size_t>(app.selected);
    if (solo) {
        app.solo[index] = !app.solo[index];
        app.session->set_solo(app.selected, app.solo[index]);
    } else {
        app.mute[index] = !app.mute[index];
        app.session->set_mute(app.selected, app.mute[index]);
    }
    touch_project_playback(app);
}

KeyChord parse_chord(std::string value) {
    KeyChord parsed{};
    while (!value.empty()) {
        const auto plus = value.find('+');
        const std::string token = plus == std::string::npos ? value : value.substr(0, plus);
        if (token == "Ctrl") {
            parsed.ctrl = true;
        } else if (token == "Shift") {
            parsed.shift = true;
        } else if (token == "Alt") {
            parsed.alt = true;
        } else if (token == "None") {
            return {};
        } else {
            parsed.key = imgui_key_from_name(token);
        }
        if (plus == std::string::npos) {
            break;
        }
        value.erase(0, plus + 1);
    }
    return parsed;
}

KeySequence parse_sequence(std::string value) {
    KeySequence sequence{};
    if (value.empty() || value == "None") {
        return sequence;
    }
    while (!value.empty() && sequence.length < kMaxSequenceSteps) {
        const auto comma = value.find(',');
        const std::string step = comma == std::string::npos ? value : value.substr(0, comma);
        const KeyChord chord = parse_chord(step);
        if (chord.key == 0) {
            return {};
        }
        sequence.steps[static_cast<std::size_t>(sequence.length++)] = chord;
        if (comma == std::string::npos) {
            break;
        }
        value.erase(0, comma + 1);
    }
    return sequence;
}

void drop_reserved_alternates(Keymap& map) {
    for (int index = 0; index < kActionCount; ++index) {
        KeySequence& extra = map.alternate[static_cast<std::size_t>(index)];
        if (!extra.empty() && reserved_chord(extra.steps[0])) {
            extra = {};
        }
    }
}

bool layer_held(const KeySequence& sequence) {
    if (sequence.length != 1) {
        return false;
    }
    const KeyChord& chord = sequence.steps[0];
    if (chord.key == 0 || chord.ctrl || chord.shift || chord.alt || reserved_chord(chord)) {
        return false;
    }
    if (ImGui::IsKeyDown(ImGuiKey_LeftSuper) || ImGui::IsKeyDown(ImGuiKey_RightSuper) || ImGui::IsKeyDown(ImGuiKey_Menu) ||
        ImGui::IsKeyDown(ImGuiKey_LeftShift) || ImGui::IsKeyDown(ImGuiKey_RightShift) ||
        ImGui::IsKeyDown(ImGuiKey_LeftCtrl) || ImGui::IsKeyDown(ImGuiKey_RightCtrl) ||
        ImGui::IsKeyDown(ImGuiKey_LeftAlt) || ImGui::IsKeyDown(ImGuiKey_RightAlt)) {
        return false;
    }
    return ImGui::IsKeyDown(static_cast<ImGuiKey>(chord.key));
}

bool single_chord_pressed(const KeySequence& sequence, bool repeat) {
    if (sequence.length != 1) {
        return false;
    }
    return chord_pressed(sequence.steps[0], repeat);
}

float snap_step(float value, float min_value, float max_value, float step) {
    const float steps = std::round((value - min_value) / step);
    return std::clamp(min_value + steps * step, min_value, max_value);
}

void nudge_layer(App& app, Action layer, int direction) {
    switch (layer) {
    case Action::AdjustSpeed:
        app.speed = snap_step(app.speed + 0.1f * static_cast<float>(direction), 0.1f, 2.f, 0.1f);
        if (app.speed != app.applied_speed) {
            app.applied_speed = app.speed;
            app.player->set_speed(app.speed);
        }
        touch_project_playback(app);
        break;
    case Action::AdjustSeek:
        app.seek_step = snap_step(app.seek_step + static_cast<float>(direction), 1.f, 20.f, 1.f);
        save_performance(app);
        break;
    case Action::AdjustMaster:
        app.master_db = snap_step(app.master_db + static_cast<float>(direction), -60.f, 6.f, 1.f);
        app.player->set_master_db(app.master_db);
        touch_project_playback(app);
        break;
    case Action::AdjustClick:
        app.click_db = snap_step(app.click_db + static_cast<float>(direction), -60.f, 6.f, 1.f);
        apply_metronome(app);
        touch_project_playback(app);
        break;
    case Action::AdjustTrackGain:
        if (app.session != nullptr && app.selected >= 0 && app.selected < bt::kStemCount) {
            float& db = app.gain_db[static_cast<std::size_t>(app.selected)];
            db = snap_step(db + static_cast<float>(direction), -60.f, 6.f, 1.f);
            app.session->set_gain(app.selected, std::pow(10.f, db / 20.f));
            touch_project_playback(app);
        }
        break;
    case Action::AdjustTrackPan:
        if (app.session != nullptr && app.selected >= 0 && app.selected < bt::kStemCount) {
            float& pan = app.pan[static_cast<std::size_t>(app.selected)];
            pan = snap_step(pan + 0.05f * static_cast<float>(direction), -1.f, 1.f, 0.05f);
            app.session->set_pan(app.selected, pan);
            touch_project_playback(app);
        }
        break;
    default:
        break;
    }
}

std::string sequence_hint(const Keymap& map, Action action, int slot) {
    const KeySequence& sequence =
        slot == 0 ? map.bindings[static_cast<std::size_t>(action)] : map.alternate[static_cast<std::size_t>(action)];
    if (sequence.empty()) {
        return {};
    }
    if (!adjust_layer_action(action) || sequence.length != 1) {
        return sequence_text(sequence);
    }
    const KeySequence& down =
        slot == 0 ? map.bindings[static_cast<std::size_t>(Action::AdjustDown)]
                  : map.alternate[static_cast<std::size_t>(Action::AdjustDown)];
    const KeySequence& up =
        slot == 0 ? map.bindings[static_cast<std::size_t>(Action::AdjustUp)]
                  : map.alternate[static_cast<std::size_t>(Action::AdjustUp)];
    const KeyChord& back = down.empty() ? map.bindings[static_cast<std::size_t>(Action::SeekBack)].steps[0]
                                        : down.steps[0];
    const KeyChord& forward = up.empty() ? map.bindings[static_cast<std::size_t>(Action::SeekForward)].steps[0]
                                         : up.steps[0];
    std::string shown = sequence_text(sequence);
    shown += " + ";
    shown += key_name(back.key) != nullptr ? key_name(back.key) : "?";
    shown += "/";
    shown += key_name(forward.key) != nullptr ? key_name(forward.key) : "?";
    return shown;
}

void draw_hint_panel(const ScreenRect& anchor, const std::vector<std::pair<std::string, std::string>>& rows) {
    if (!anchor.valid() || rows.empty()) {
        return;
    }
    const float scale = ImGui::GetIO().FontGlobalScale > 0.f ? ImGui::GetIO().FontGlobalScale : 1.f;
    const float pad = 8.f * scale;
    const float gap = 10.f * scale;
    const float line_gap = 2.f * scale;
    const float margin = 8.f * scale;
    float key_w = 0.f;
    float action_w = 0.f;
    float line_h = ImGui::GetTextLineHeight();
    for (const auto& [key, action] : rows) {
        key_w = std::max(key_w, ImGui::CalcTextSize(key.c_str()).x);
        action_w = std::max(action_w, ImGui::CalcTextSize(action.c_str()).x);
    }
    const float box_w = pad * 2.f + key_w + gap + action_w;
    const float box_h = pad * 2.f + static_cast<float>(rows.size()) * line_h +
                        static_cast<float>(rows.size() - 1) * line_gap;
    const float x1 = std::min(anchor.x1 - margin, anchor.x0 + std::max(box_w + margin, 1.f));
    const float y1 = std::min(anchor.y1 - margin, anchor.y0 + std::max(box_h + margin, 1.f));
    const float x0 = x1 - box_w;
    const float y0 = y1 - box_h;
    ImDrawList* draw = ImGui::GetForegroundDrawList();
    draw->AddRectFilled(ImVec2(x0, y0), ImVec2(x1, y1), IM_COL32(0, 0, 0, 160), 4.f * scale);
    float y = y0 + pad;
    for (const auto& [key, action] : rows) {
        draw->AddText(ImVec2(x0 + pad, y), IM_COL32(242, 242, 242, 255), key.c_str());
        draw->AddText(ImVec2(x0 + pad + key_w + gap, y), IM_COL32(242, 242, 242, 255), action.c_str());
        y += line_h + line_gap;
    }
}

void append_action_hints(
    App& app, std::vector<std::pair<std::string, std::string>>& rows, const Action* order, std::size_t count) {
    for (std::size_t index = 0; index < count; ++index) {
        const Action action = order[index];
        const auto at = static_cast<std::size_t>(action);
        const char* label = action_label(action, i18n_language());
        for (int slot = 0; slot < 2; ++slot) {
            const std::string key = sequence_hint(app.keymap, action, slot);
            if (key.empty() || key == "None") {
                continue;
            }
            rows.emplace_back(key, label);
        }
        (void)at;
    }
}

struct SeqRuntime {
    int depth = 0;
    std::array<KeyChord, kMaxSequenceSteps> steps{};
    double deadline = 0;
};

SeqRuntime& seq_runtime() {
    static SeqRuntime state;
    return state;
}

void clear_seq_runtime() {
    seq_runtime() = {};
}

bool matches_armed_prefix(const KeySequence& sequence, const SeqRuntime& armed) {
    if (armed.depth <= 0 || armed.depth > sequence.length) {
        return false;
    }
    for (int index = 0; index < armed.depth; ++index) {
        if (!same_chord(sequence.steps[static_cast<std::size_t>(index)], armed.steps[static_cast<std::size_t>(index)])) {
            return false;
        }
    }
    return true;
}

bool poll_chord_edge(KeyChord& out) {
    return capture_chord(out);
}

// Returns matched action or Count. Advances / clears sequence state.
Action match_sequence_edge(const Keymap& map, bool allow_repeat_single) {
    SeqRuntime& armed = seq_runtime();
    const double now = ImGui::GetTime();
    if (armed.depth > 0 && now > armed.deadline) {
        clear_seq_runtime();
    }

    KeyChord edge{};
    if (!poll_chord_edge(edge)) {
        return Action::Count;
    }
    if (reserved_chord(edge)) {
        clear_seq_runtime();
        return Action::Count;
    }

    KeySequence candidate = make_sequence(edge);
    if (armed.depth > 0 && armed.depth < kMaxSequenceSteps) {
        candidate.length = armed.depth + 1;
        for (int index = 0; index < armed.depth; ++index) {
            candidate.steps[static_cast<std::size_t>(index)] = armed.steps[static_cast<std::size_t>(index)];
        }
        candidate.steps[static_cast<std::size_t>(armed.depth)] = edge;
    }

    bool has_longer = false;
    Action exact = Action::Count;
    for (int index = 0; index < kActionCount; ++index) {
        const auto action = static_cast<Action>(index);
        for (int slot = 0; slot < 2; ++slot) {
            const KeySequence& sequence =
                slot == 0 ? map.bindings[static_cast<std::size_t>(index)]
                          : map.alternate[static_cast<std::size_t>(index)];
            if (sequence.empty()) {
                continue;
            }
            if (same_sequence(sequence, candidate)) {
                exact = action;
            } else if (sequence_is_prefix(candidate, sequence)) {
                has_longer = true;
            }
        }
    }

    if (exact != Action::Count && !has_longer) {
        clear_seq_runtime();
        (void)allow_repeat_single;
        return exact;
    }
    if (has_longer) {
        armed.depth = candidate.length;
        for (int index = 0; index < candidate.length; ++index) {
            armed.steps[static_cast<std::size_t>(index)] = candidate.steps[static_cast<std::size_t>(index)];
        }
        armed.deadline = now + kSequenceTimeout;
        return Action::Count;
    }
    if (exact != Action::Count) {
        clear_seq_runtime();
        return exact;
    }
    clear_seq_runtime();
    return Action::Count;
}

bool action_fired(const Keymap& map, Action action, bool repeat) {
    // Length-1 with repeat uses ImGui repeat; multi-step never repeats.
    const KeySequence& primary = map.bindings[static_cast<std::size_t>(action)];
    const KeySequence& alternate = map.alternate[static_cast<std::size_t>(action)];
    if (repeat) {
        return single_chord_pressed(primary, true) || single_chord_pressed(alternate, true);
    }
    // Edge matching is done once per frame via match_sequence_edge; callers use that for tap actions.
    return single_chord_pressed(primary, false) || single_chord_pressed(alternate, false);
}

void fire_action(App& app, Action action, bool ready, const bt::Player::Snapshot& transport) {
    switch (action) {
    case Action::Undo:
        undo_project(app);
        break;
    case Action::Redo:
        redo_project(app);
        break;
    case Action::PlayPause:
        if (ready) {
            start_or_pause(app, transport);
        }
        break;
    case Action::Stop:
        if (ready) {
            app.player->stop();
        }
        break;
    case Action::SeekBack:
        if (ready) {
            const double step = static_cast<double>(std::clamp(app.seek_step, 1.f, 20.f));
            app.player->seek(transport.position - step * static_cast<double>(transport.speed));
        }
        break;
    case Action::SeekForward:
        if (ready) {
            const double step = static_cast<double>(std::clamp(app.seek_step, 1.f, 20.f));
            app.player->seek(transport.position + step * static_cast<double>(transport.speed));
        }
        break;
    case Action::Loop:
        if (ready) {
            app.player->set_loop_enabled(!transport.loop);
            touch_project_playback(app);
        }
        break;
    case Action::ClearLoop:
        app.player->clear_loop();
        touch_project_playback(app);
        break;
    case Action::Metro:
        app.metro = !app.metro;
        apply_metronome(app);
        touch_project_playback(app);
        break;
    case Action::Grid:
        app.grid = !app.grid;
        touch_project_playback(app);
        break;
    case Action::CountIn:
        app.count_in = !app.count_in;
        touch_project_playback(app);
        break;
    case Action::Snap:
        app.loop_snap = !app.loop_snap;
        touch_project_playback(app);
        break;
    case Action::Mute:
        if (ready) {
            apply_mute_or_solo(app, false);
        }
        break;
    case Action::Solo:
        if (ready) {
            apply_mute_or_solo(app, true);
        }
        break;
    case Action::PrevTrack:
        tab_exit_fingering_mode(app);
        app.selected = (app.selected + bt::kStemCount - 1) % bt::kStemCount;
        break;
    case Action::NextTrack:
        tab_exit_fingering_mode(app);
        app.selected = (app.selected + 1) % bt::kStemCount;
        break;
    case Action::MidiCopy:
        if (app.note_stage == NoteStage::Midi && !app.tab_fingering_mode) {
            midi_copy_selection(app);
        }
        break;
    case Action::MidiPaste:
        if (app.note_stage == NoteStage::Midi && !app.tab_fingering_mode) {
            midi_begin_paste_preview(app);
        }
        break;
    case Action::MidiDuplicate:
        if (app.note_stage == NoteStage::Midi && !app.tab_fingering_mode) {
            midi_duplicate_selection(app);
        }
        break;
    case Action::MidiDelete:
        if (app.note_stage == NoteStage::Midi) {
            midi_delete_selection(app);
        }
        break;
    case Action::TabFingering:
        if (app.note_stage == NoteStage::Midi) {
            const int stem = std::clamp(app.selected, 0, bt::kStemCount - 1);
            if (app.tab_fingering_mode) {
                tab_exit_fingering_mode(app);
            } else {
                tab_enter_fingering_mode(app, stem);
            }
        }
        break;
    case Action::TabComboPrev:
        if (app.note_stage == NoteStage::Midi) {
            tab_cycle_combo(app, std::clamp(app.selected, 0, bt::kStemCount - 1), -1);
        }
        break;
    case Action::TabComboNext:
        if (app.note_stage == NoteStage::Midi) {
            tab_cycle_combo(app, std::clamp(app.selected, 0, bt::kStemCount - 1), 1);
        }
        break;
    default:
        break;
    }
}

}  // namespace

ActionScope action_scope(Action action) {
    switch (action) {
    case Action::Undo:
    case Action::Redo:
        return ActionScope::Project;
    case Action::Mute:
    case Action::Solo:
    case Action::PrevTrack:
    case Action::NextTrack:
    case Action::AdjustTrackGain:
    case Action::AdjustTrackPan:
        return ActionScope::Track;
    case Action::MidiCopy:
    case Action::MidiPaste:
    case Action::MidiDuplicate:
    case Action::MidiDelete:
    case Action::TabFingering:
    case Action::TabComboPrev:
    case Action::TabComboNext:
        return ActionScope::Midi;
    default:
        return ActionScope::Player;
    }
}

bool reserved_chord(const KeyChord& chord) {
    if (chord.key == 0) {
        return false;
    }
    if (chord.key == ImGuiKey_LeftSuper || chord.key == ImGuiKey_RightSuper || chord.key == ImGuiKey_Menu ||
        chord.key == ImGuiKey_F10) {
        return true;
    }
    if (chord.key == ImGuiKey_Space && (chord.ctrl || chord.shift || chord.alt)) {
        return true;
    }
    if (chord.alt && (chord.key == ImGuiKey_F4 || chord.key == ImGuiKey_Tab || chord.key == ImGuiKey_Escape ||
                      chord.key == ImGuiKey_Enter || chord.key == ImGuiKey_F10)) {
        return true;
    }
    if (chord.ctrl && !chord.shift && !chord.alt &&
        (chord.key == ImGuiKey_Escape || chord.key == ImGuiKey_F4)) {
        return true;
    }
    if (chord.alt && !chord.ctrl) {
        return true;
    }
    return false;
}

const char* action_label(Action action, int language) {
    const char* key = "";
    switch (action) {
    case Action::Undo:
        key = "undo";
        break;
    case Action::Redo:
        key = "redo";
        break;
    case Action::PlayPause:
        key = "action.play_pause";
        break;
    case Action::Stop:
        key = "action.stop";
        break;
    case Action::SeekBack:
        key = "action.seek_back";
        break;
    case Action::SeekForward:
        key = "action.seek_forward";
        break;
    case Action::Loop:
        key = "action.loop";
        break;
    case Action::ClearLoop:
        key = "action.clear_loop";
        break;
    case Action::Metro:
        key = "action.metro";
        break;
    case Action::Grid:
        key = "action.grid";
        break;
    case Action::CountIn:
        key = "action.count_in";
        break;
    case Action::Snap:
        key = "action.snap";
        break;
    case Action::Mute:
        key = "action.mute";
        break;
    case Action::Solo:
        key = "action.solo";
        break;
    case Action::PrevTrack:
        key = "action.prev_track";
        break;
    case Action::NextTrack:
        key = "action.next_track";
        break;
    case Action::AdjustSpeed:
        key = "action.adjust_speed";
        break;
    case Action::AdjustSeek:
        key = "action.adjust_seek";
        break;
    case Action::AdjustMaster:
        key = "action.adjust_master";
        break;
    case Action::AdjustClick:
        key = "action.adjust_click";
        break;
    case Action::AdjustTrackGain:
        key = "action.adjust_track_gain";
        break;
    case Action::AdjustTrackPan:
        key = "action.adjust_track_pan";
        break;
    case Action::AdjustDown:
        key = "action.adjust_down";
        break;
    case Action::AdjustUp:
        key = "action.adjust_up";
        break;
    case Action::MidiCopy:
        key = "action.midi_copy";
        break;
    case Action::MidiPaste:
        key = "action.midi_paste";
        break;
    case Action::MidiDuplicate:
        key = "action.midi_duplicate";
        break;
    case Action::MidiDelete:
        key = "action.midi_delete";
        break;
    case Action::TabFingering:
        key = "action.tab_fingering";
        break;
    case Action::TabComboPrev:
        key = "action.tab_combo_prev";
        break;
    case Action::TabComboNext:
        key = "action.tab_combo_next";
        break;
    case Action::Count:
        break;
    }
    return t(key, language);
}

Keymap default_keymap() {
    Keymap map;
    auto set = [&](Action action, KeyChord primary, KeyChord extra = {}) {
        map.bindings[static_cast<std::size_t>(action)] = make_sequence(primary);
        if (extra.key != 0) {
            map.alternate[static_cast<std::size_t>(action)] = make_sequence(extra);
        }
    };
    set(Action::Undo, make_chord(ImGuiKey_Z, false, true));
    set(Action::Redo, make_chord(ImGuiKey_Z, true, true));
    set(Action::PlayPause, make_chord(ImGuiKey_Z), make_chord(ImGuiKey_Space));
    set(Action::Stop, make_chord(ImGuiKey_C));
    set(Action::SeekBack, make_chord(ImGuiKey_A), make_chord(ImGuiKey_LeftArrow));
    set(Action::SeekForward, make_chord(ImGuiKey_D), make_chord(ImGuiKey_RightArrow));
    set(Action::Loop, make_chord(ImGuiKey_V));
    set(Action::ClearLoop, make_chord(ImGuiKey_B));
    set(Action::Metro, make_chord(ImGuiKey_R));
    set(Action::Grid, make_chord(ImGuiKey_G));
    set(Action::CountIn, make_chord(ImGuiKey_X));
    set(Action::Snap, make_chord(ImGuiKey_N));
    set(Action::Mute, make_chord(ImGuiKey_Q));
    set(Action::Solo, make_chord(ImGuiKey_E));
    set(Action::PrevTrack, make_chord(ImGuiKey_W), make_chord(ImGuiKey_UpArrow));
    set(Action::NextTrack, make_chord(ImGuiKey_S), make_chord(ImGuiKey_DownArrow));
    set(Action::AdjustSpeed, make_chord(ImGuiKey_1));
    set(Action::AdjustSeek, make_chord(ImGuiKey_2));
    set(Action::AdjustMaster, make_chord(ImGuiKey_3));
    set(Action::AdjustClick, make_chord(ImGuiKey_4));
    set(Action::AdjustTrackGain, make_chord(ImGuiKey_5));
    set(Action::AdjustTrackPan, make_chord(ImGuiKey_6));
    set(Action::AdjustDown, make_chord(ImGuiKey_A), make_chord(ImGuiKey_LeftArrow));
    set(Action::AdjustUp, make_chord(ImGuiKey_D), make_chord(ImGuiKey_RightArrow));
    set(Action::MidiCopy, make_chord(ImGuiKey_C, false, true));
    set(Action::MidiPaste, make_chord(ImGuiKey_V, false, true));
    set(Action::MidiDuplicate, make_chord(ImGuiKey_D, false, true));
    set(Action::MidiDelete, make_chord(ImGuiKey_Delete), make_chord(ImGuiKey_Backspace));
    set(Action::TabFingering, make_chord(ImGuiKey_F));
    set(Action::TabComboPrev, make_chord(ImGuiKey_LeftBracket));
    set(Action::TabComboNext, make_chord(ImGuiKey_RightBracket), make_chord(ImGuiKey_Tab));
    return map;
}

void load_keymap(App& app) {
    app.keymap = default_keymap();
    if (app.cache_directory.empty()) {
        return;
    }
    std::ifstream input(app.cache_directory / "keybindings.txt");
    std::string line;
    int layout = 0;
    while (std::getline(input, line)) {
        if (line.empty() || line[0] == '#') {
            continue;
        }
        const auto eq = line.find('=');
        if (eq == std::string::npos) {
            continue;
        }
        if (line.substr(0, eq) == "layout") {
            layout = std::atoi(line.substr(eq + 1).c_str());
            continue;
        }
        if (layout < 2 || layout > kLayoutVersion) {
            continue;
        }
        const Action action = action_from_id(line.substr(0, eq));
        if (action == Action::Count) {
            continue;
        }
        const std::string value = line.substr(eq + 1);
        const auto bar = value.find('|');
        const std::string primary_text = bar == std::string::npos ? value : value.substr(0, bar);
        KeySequence primary = layout >= 4 ? parse_sequence(primary_text) : make_sequence(parse_chord(primary_text));
        if (!primary.empty() && !reserved_chord(primary.steps[0])) {
            app.keymap.bindings[static_cast<std::size_t>(action)] = primary;
        }
        if ((layout == 3 || layout == 4) && bar != std::string::npos) {
            const std::string extra_text = value.substr(bar + 1);
            KeySequence extra = layout >= 4 ? parse_sequence(extra_text) : make_sequence(parse_chord(extra_text));
            if (!extra.empty() && !reserved_chord(extra.steps[0])) {
                app.keymap.alternate[static_cast<std::size_t>(action)] = extra;
            }
        }
    }
    if (layout == 2) {
        drop_reserved_alternates(app.keymap);
        save_keymap(app);
    } else if (layout == 3) {
        save_keymap(app);
    } else if (layout != kLayoutVersion) {
        app.keymap = default_keymap();
        save_keymap(app);
    }
}

void save_keymap(const App& app) {
    if (app.cache_directory.empty()) {
        return;
    }
    std::error_code error;
    std::filesystem::create_directories(app.cache_directory, error);
    std::ofstream output(app.cache_directory / "keybindings.txt", std::ios::trunc);
    if (!output) {
        return;
    }
    output << "layout=" << kLayoutVersion << '\n';
    for (int index = 0; index < kActionCount; ++index) {
        const auto action = static_cast<Action>(index);
        const auto at = static_cast<std::size_t>(index);
        output << action_id(action) << '=' << sequence_text(app.keymap.bindings[at]) << '|'
               << sequence_text(app.keymap.alternate[at]) << '\n';
    }
}

bool chord_pressed(const KeyChord& chord, bool repeat) {
    if (chord.key == 0 || reserved_chord(chord)) {
        return false;
    }
    if (ImGui::IsKeyDown(ImGuiKey_LeftSuper) || ImGui::IsKeyDown(ImGuiKey_RightSuper) ||
        ImGui::IsKeyDown(ImGuiKey_Menu)) {
        return false;
    }
    const bool shift = ImGui::IsKeyDown(ImGuiKey_LeftShift) || ImGui::IsKeyDown(ImGuiKey_RightShift);
    const bool ctrl = ImGui::IsKeyDown(ImGuiKey_LeftCtrl) || ImGui::IsKeyDown(ImGuiKey_RightCtrl);
    const bool alt = ImGui::IsKeyDown(ImGuiKey_LeftAlt) || ImGui::IsKeyDown(ImGuiKey_RightAlt);
    if (shift != chord.shift || ctrl != chord.ctrl || alt != chord.alt) {
        return false;
    }
    if (chord.key == ImGuiKey_Space && (shift || ctrl || alt)) {
        return false;
    }
    return ImGui::IsKeyPressed(static_cast<ImGuiKey>(chord.key), repeat);
}

std::string chord_text(const KeyChord& chord) {
    if (chord.key == 0) {
        return "None";
    }
    std::string text;
    if (chord.ctrl) {
        text += "Ctrl+";
    }
    if (chord.shift) {
        text += "Shift+";
    }
    if (chord.alt) {
        text += "Alt+";
    }
    if (const char* name = key_name(chord.key)) {
        text += name;
    } else {
        text += '?';
    }
    return text;
}

std::string sequence_text(const KeySequence& sequence) {
    if (sequence.empty()) {
        return "None";
    }
    std::string text = chord_text(sequence.steps[0]);
    for (int index = 1; index < sequence.length; ++index) {
        text += ',';
        text += chord_text(sequence.steps[static_cast<std::size_t>(index)]);
    }
    return text;
}

bool capture_chord(KeyChord& out) {
    if (ImGui::IsKeyDown(ImGuiKey_LeftSuper) || ImGui::IsKeyDown(ImGuiKey_RightSuper) ||
        ImGui::IsKeyDown(ImGuiKey_Menu)) {
        return false;
    }
    const bool shift = ImGui::IsKeyDown(ImGuiKey_LeftShift) || ImGui::IsKeyDown(ImGuiKey_RightShift);
    const bool ctrl = ImGui::IsKeyDown(ImGuiKey_LeftCtrl) || ImGui::IsKeyDown(ImGuiKey_RightCtrl);
    const bool alt = ImGui::IsKeyDown(ImGuiKey_LeftAlt) || ImGui::IsKeyDown(ImGuiKey_RightAlt);
    for (int key = ImGuiKey_NamedKey_BEGIN; key < ImGuiKey_NamedKey_END; ++key) {
        if (key == ImGuiKey_LeftShift || key == ImGuiKey_RightShift || key == ImGuiKey_LeftCtrl ||
            key == ImGuiKey_RightCtrl || key == ImGuiKey_LeftAlt || key == ImGuiKey_RightAlt ||
            key == ImGuiKey_LeftSuper || key == ImGuiKey_RightSuper || key == ImGuiKey_Menu ||
            key == ImGuiKey_Escape) {
            continue;
        }
        if (!ImGui::IsKeyPressed(static_cast<ImGuiKey>(key), false)) {
            continue;
        }
        if (!bindable_key(key)) {
            continue;
        }
        out = make_chord(key, shift, ctrl, alt);
        return true;
    }
    return false;
}

void draw_shortcut_hints(App& app) {
    if (!app.shortcut_hints || app.keymap_capture >= 0 || ime_owns_keyboard(app)) {
        return;
    }

    const Action project_order[] = {Action::Undo, Action::Redo};
    const Action player_order[] = {
        Action::PlayPause, Action::CountIn, Action::Stop, Action::Loop, Action::ClearLoop, Action::Snap,
        Action::SeekBack, Action::SeekForward, Action::Metro, Action::Grid,
        Action::AdjustSpeed, Action::AdjustSeek, Action::AdjustMaster, Action::AdjustClick,
        Action::AdjustDown, Action::AdjustUp,
    };
    const Action track_order[] = {
        Action::PrevTrack, Action::NextTrack, Action::Mute, Action::Solo,
        Action::AdjustTrackGain, Action::AdjustTrackPan,
    };
    const Action midi_order[] = {
        Action::MidiCopy, Action::MidiPaste, Action::MidiDuplicate, Action::MidiDelete, Action::TabFingering,
        Action::TabComboPrev, Action::TabComboNext,
    };

    if (app.hover_region == HoverRegion::Projects || app.hover_region == HoverRegion::Player) {
        std::vector<std::pair<std::string, std::string>> rows;
        if (app.hover_region == HoverRegion::Projects) {
            append_action_hints(app, rows, project_order, sizeof project_order / sizeof project_order[0]);
            draw_hint_panel(app.hover_projects, rows);
        } else {
            append_action_hints(app, rows, project_order, sizeof project_order / sizeof project_order[0]);
            append_action_hints(app, rows, player_order, sizeof player_order / sizeof player_order[0]);
            draw_hint_panel(app.hover_player, rows);
        }
    }
    if (app.hover_region == HoverRegion::Mixer) {
        std::vector<std::pair<std::string, std::string>> rows;
        append_action_hints(app, rows, track_order, sizeof track_order / sizeof track_order[0]);
        draw_hint_panel(app.hover_mixer, rows);
    }
    if (app.hover_region == HoverRegion::Midi) {
        std::vector<std::pair<std::string, std::string>> midi_rows;
        if (app.note_stage == NoteStage::Midi) {
            append_action_hints(app, midi_rows, midi_order, sizeof midi_order / sizeof midi_order[0]);
            midi_rows.emplace_back(t("hint.middle_drag"), t("hint.midi_pan"));
            midi_rows.emplace_back(t("hint.ctrl_wheel"), t("hint.midi_zoom"));
            midi_rows.emplace_back(t("hint.ctrl_shift_wheel"), t("hint.midi_zoom_pitch"));
            midi_rows.emplace_back(t("hint.shift_left"), t("hint.midi_box"));
            midi_rows.emplace_back(t("hint.ctrl"), t("hint.midi_nosnap"));
            if (app.roll_paste) {
                midi_rows.emplace_back(t("hint.paste_confirm_key"), t("hint.midi_paste_commit"));
                midi_rows.emplace_back(t("hint.paste_cancel_key"), t("hint.midi_paste_cancel"));
            }
            midi_rows.emplace_back(t("hint.right_drag"), t("hint.midi_erase"));
            midi_rows.emplace_back(t("hint.left_empty"), t("hint.midi_place"));
            midi_rows.emplace_back(t("hint.left_note"), t("hint.midi_edit"));
            if (app.midi_view == MidiView::Fall) {
                midi_rows.emplace_back(t("hint.left_drag"), t("hint.fall_scrub"));
                midi_rows.emplace_back(t("hint.right_drag"), t("hint.fall_loop"));
            }
        }
        draw_hint_panel(app.hover_midi, midi_rows);
    }
}

void draw_keymap_settings(App& app) {
    const float scale = ImGui::GetIO().FontGlobalScale > 0.f ? ImGui::GetIO().FontGlobalScale : 1.f;
    if (ImGui::Checkbox(t("shortcut.hints"), &app.shortcut_hints)) {
        save_performance(app);
    }
    ImGui::Spacing();
    ImGui::TextDisabled("%s", t("shortcut.sequence_help"));
    ImGui::Spacing();
    const char* reset_label = t("shortcut.reset");
    const float gap = 4.f * scale;
    const float reset_w = ImGui::CalcTextSize(reset_label).x + ImGui::GetStyle().FramePadding.x * 2.f;
    struct Reject {
        int index = -1;
        int kind = 0;
    };
    static Reject reject;
    const Keymap defaults = default_keymap();

    const Action project_order[] = {Action::Undo, Action::Redo};
    const Action player_order[] = {
        Action::PlayPause, Action::CountIn, Action::Stop, Action::Loop, Action::ClearLoop, Action::Snap,
        Action::SeekBack, Action::SeekForward, Action::Metro, Action::Grid,
        Action::AdjustSpeed, Action::AdjustSeek, Action::AdjustMaster, Action::AdjustClick,
        Action::AdjustDown, Action::AdjustUp,
    };
    const Action track_order[] = {
        Action::PrevTrack, Action::NextTrack, Action::AdjustTrackGain, Action::AdjustTrackPan, Action::Mute,
        Action::Solo,
    };
    const Action midi_order[] = {
        Action::MidiCopy, Action::MidiPaste, Action::MidiDuplicate, Action::MidiDelete, Action::TabFingering,
        Action::TabComboPrev, Action::TabComboNext,
    };

    auto draw_group = [&](const char* heading, const Action* order, std::size_t count) {
        ImGui::Spacing();
        ImGui::TextUnformatted(heading);
        for (std::size_t order_i = 0; order_i < count; ++order_i) {
            const Action action = order[order_i];
            const auto at = static_cast<std::size_t>(action);
            const int index = static_cast<int>(action);
            const bool adjust = adjust_layer_action(action);
            ImGui::PushID(index);
            ImGui::TextUnformatted(action_label(action, i18n_language()));
            ImGui::SameLine(0.f, gap);
            const float spare = ImGui::GetContentRegionAvail().x;
            if (spare > reset_w) {
                ImGui::SetCursorPosX(ImGui::GetCursorPosX() + spare - reset_w);
            }
            if (ImGui::SmallButton(reset_label)) {
                app.keymap.bindings[at] = defaults.bindings[at];
                app.keymap.alternate[at] = defaults.alternate[at];
                save_keymap(app);
                app.keymap_capture = -1;
                app.keymap_draft = {};
                reject = {};
            }
            const float key_w = std::max(1.f, (ImGui::GetContentRegionAvail().x - gap) * 0.5f);
            for (int slot = 0; slot < 2; ++slot) {
                KeySequence& binding = slot == 0 ? app.keymap.bindings[at] : app.keymap.alternate[at];
                const int capture_id = index * 2 + slot;
                const bool capturing = app.keymap_capture == capture_id;
                ImGui::PushID(slot);
                if (slot == 1) {
                    ImGui::SameLine(0.f, gap);
                }
                const std::string shown = capturing
                    ? (app.keymap_draft.empty() ? t("shortcut.press") : sequence_text(app.keymap_draft) + " …")
                    : (adjust ? sequence_hint(app.keymap, action, slot) : sequence_text(binding));
                const bool clash =
                    !capturing && !binding.empty() && conflict_of(app.keymap, action, slot, binding) >= 0;
                if (clash) {
                    ImGui::PushStyleColor(ImGuiCol_Button, ImVec4(0.62f, 0.14f, 0.14f, 1.f));
                    ImGui::PushStyleColor(ImGuiCol_ButtonHovered, ImVec4(0.78f, 0.20f, 0.20f, 1.f));
                    ImGui::PushStyleColor(ImGuiCol_ButtonActive, ImVec4(0.48f, 0.10f, 0.10f, 1.f));
                }
                if (ImGui::Button(shown.c_str(), ImVec2(key_w, 0.f))) {
                    app.keymap_capture = capturing ? -1 : capture_id;
                    app.keymap_draft = {};
                    app.keymap_draft_at = 0;
                    reject = {};
                }
                if (ImGui::IsItemClicked(ImGuiMouseButton_Right) && !binding.empty()) {
                    if (app.keymap_capture == capture_id) {
                        app.keymap_capture = -1;
                        app.keymap_draft = {};
                    }
                    binding = {};
                    reject = {};
                    save_keymap(app);
                }
                if (clash) {
                    ImGui::PopStyleColor(3);
                }
                if (capturing) {
                    if (ImGui::IsKeyPressed(ImGuiKey_Escape)) {
                        app.keymap_capture = -1;
                        app.keymap_draft = {};
                        reject = {};
                    } else if (ImGui::IsKeyPressed(ImGuiKey_Backspace) && app.keymap_draft.empty()) {
                        binding = {};
                        app.keymap_capture = -1;
                        reject = {};
                        save_keymap(app);
                    } else if (ImGui::IsKeyPressed(ImGuiKey_Enter) && !app.keymap_draft.empty()) {
                        const int clash_with = conflict_of(app.keymap, action, slot, app.keymap_draft);
                        const bool plain = app.keymap_draft.length == 1 && !app.keymap_draft.steps[0].ctrl &&
                                           !app.keymap_draft.steps[0].shift && !app.keymap_draft.steps[0].alt;
                        if (reserved_chord(app.keymap_draft.steps[0])) {
                            reject = {capture_id, 2};
                        } else if (adjust && !plain) {
                            reject = {capture_id, 3};
                        } else if (clash_with >= 0) {
                            reject = {capture_id, 4};
                        } else {
                            binding = app.keymap_draft;
                            save_keymap(app);
                            app.keymap_capture = -1;
                            app.keymap_draft = {};
                            reject = {};
                        }
                    } else if (!ime_owns_keyboard(app)) {
                        KeyChord next{};
                        if (capture_chord(next)) {
                            if (next.key == ImGuiKey_Backspace && app.keymap_draft.empty()) {
                                // handled above
                            } else if (app.keymap_draft.length < kMaxSequenceSteps) {
                                app.keymap_draft.steps[static_cast<std::size_t>(app.keymap_draft.length++)] = next;
                                app.keymap_draft_at = ImGui::GetTime();
                            }
                        } else if (
                            !app.keymap_draft.empty() && app.keymap_draft_at > 0 &&
                            ImGui::GetTime() - app.keymap_draft_at >= kSequenceTimeout) {
                            const int clash_with = conflict_of(app.keymap, action, slot, app.keymap_draft);
                            const bool plain = app.keymap_draft.length == 1 && !app.keymap_draft.steps[0].ctrl &&
                                               !app.keymap_draft.steps[0].shift && !app.keymap_draft.steps[0].alt;
                            if (reserved_chord(app.keymap_draft.steps[0])) {
                                reject = {capture_id, 2};
                                app.keymap_draft = {};
                            } else if (adjust && !plain) {
                                reject = {capture_id, 3};
                                app.keymap_draft = {};
                            } else if (clash_with >= 0) {
                                reject = {capture_id, 4};
                                app.keymap_draft = {};
                            } else {
                                binding = app.keymap_draft;
                                save_keymap(app);
                                app.keymap_capture = -1;
                                app.keymap_draft = {};
                                reject = {};
                            }
                        }
                    }
                }
                ImGui::PopID();
            }
            if (reject.index / 2 == index) {
                const char* note = nullptr;
                if (reject.kind == 2) {
                    note = t("shortcut.reserved");
                } else if (reject.kind == 3) {
                    note = t("shortcut.no_modifiers");
                } else if (reject.kind == 4) {
                    note = t("shortcut.conflict");
                }
                if (note != nullptr) {
                    ImGui::PushTextWrapPos(ImGui::GetCursorPos().x + ImGui::GetContentRegionAvail().x);
                    ImGui::TextDisabled("%s", note);
                    ImGui::PopTextWrapPos();
                }
            }
            ImGui::PopID();
        }
    };

    draw_group(t("shortcut.scope_project"), project_order, sizeof project_order / sizeof project_order[0]);
    draw_group(t("shortcut.scope_player"), player_order, sizeof player_order / sizeof player_order[0]);
    draw_group(t("shortcut.scope_track"), track_order, sizeof track_order / sizeof track_order[0]);
    draw_group(t("shortcut.scope_midi"), midi_order, sizeof midi_order / sizeof midi_order[0]);

    if (ImGui::Button(
            t("shortcut.reset_all"),
            ImVec2(std::max(1.f, ImGui::GetContentRegionAvail().x), 0.f))) {
        app.keymap = default_keymap();
        save_keymap(app);
        app.keymap_capture = -1;
        app.keymap_draft = {};
    }
}

namespace {

// Mode-local keys that are not part of the remappable keymap. Runs before Action matching.
bool handle_mode_keys(App& app) {
    if (app.note_stage != NoteStage::Midi) {
        return false;
    }
    // Order matters: paste owns Esc/Enter while active; then fingering; then TAB score caret.
    if (midi_handle_paste_keys(app)) {
        return true;
    }
    if (handle_tab_fingering_keys(app)) {
        return true;
    }
    if (handle_tab_score_keys(app)) {
        return true;
    }
    return false;
}

}  // namespace

void handle_actions(App& app, bool ready) {
    // --- 1) gates ---
    if (app.keymap_capture >= 0 || ime_owns_keyboard(app) || ImGui::GetIO().WantTextInput) {
        clear_seq_runtime();
        return;
    }
    if (app.player == nullptr || ImGui::IsAnyItemActive()) {
        clear_seq_runtime();
        return;
    }
    if (app.settings_panel) {
        clear_seq_runtime();
        return;
    }

    // --- 2) mode-local ---
    const auto transport = app.player->snapshot();
    if (handle_mode_keys(app)) {
        clear_seq_runtime();
        return;
    }

    // --- 3) keymap Actions ---
    const auto& map = app.keymap;
    const auto layer_down = [&](Action action) {
        return layer_held(map.bindings[static_cast<std::size_t>(action)]) ||
               layer_held(map.alternate[static_cast<std::size_t>(action)]);
    };

    Action held = Action::Count;
    int holds = 0;
    for (Action layer :
         {Action::AdjustSpeed, Action::AdjustSeek, Action::AdjustMaster, Action::AdjustClick, Action::AdjustTrackGain,
          Action::AdjustTrackPan}) {
        if (layer_down(layer)) {
            held = layer;
            ++holds;
        }
    }
    if (holds > 1) {
        clear_seq_runtime();
        return;
    }
    if (holds == 1) {
        const auto is_layer_key = [&](const KeySequence& sequence) {
            return sequence.length == 1 &&
                   (same_chord(sequence.steps[0], map.bindings[static_cast<std::size_t>(held)].steps[0]) ||
                    (!map.alternate[static_cast<std::size_t>(held)].empty() &&
                     same_chord(sequence.steps[0], map.alternate[static_cast<std::size_t>(held)].steps[0])));
        };
        bool nudged = false;
        const bool down = action_fired(map, Action::AdjustDown, true) || action_fired(map, Action::SeekBack, true);
        const bool up = action_fired(map, Action::AdjustUp, true) || action_fired(map, Action::SeekForward, true);
        if (down) {
            nudge_layer(app, held, -1);
            nudged = true;
        }
        if (up) {
            nudge_layer(app, held, 1);
            nudged = true;
        }
        const bool layer_just_pressed = action_fired(map, held, false);
        if (nudged || !layer_just_pressed) {
            clear_seq_runtime();
            return;
        }
        (void)is_layer_key;
    }

    // Repeatable seeks (length-1 only).
    if (ready && holds == 0) {
        if (action_fired(map, Action::SeekBack, true) &&
            map.bindings[static_cast<std::size_t>(Action::SeekBack)].length <= 1) {
            fire_action(app, Action::SeekBack, ready, transport);
        }
        if (action_fired(map, Action::SeekForward, true) &&
            map.bindings[static_cast<std::size_t>(Action::SeekForward)].length <= 1) {
            fire_action(app, Action::SeekForward, ready, transport);
        }
    }

    const Action matched = match_sequence_edge(map, false);
    if (matched == Action::Count) {
        return;
    }
    if (matched == Action::SeekBack || matched == Action::SeekForward) {
        // Already handled via repeat path for length-1; multi-step seek still fires here.
        if (map.bindings[static_cast<std::size_t>(matched)].length > 1 ||
            map.alternate[static_cast<std::size_t>(matched)].length > 1) {
            fire_action(app, matched, ready, transport);
        }
        return;
    }
    if (matched == Action::AdjustDown || matched == Action::AdjustUp || adjust_layer_action(matched)) {
        return;
    }
    fire_action(app, matched, ready, transport);
}

}  // namespace btapp
