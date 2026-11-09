#include "keys.hpp"

#include "ui.hpp"

#include "imgui.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <fstream>
#include <string_view>

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

constexpr int kActionCount = static_cast<int>(Action::Count);

const char* action_id(Action action) {
    switch (action) {
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
    case Action::Metro:
        return "metro";
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
    case Action::ViewGuitar:
        return "view_guitar";
    case Action::ViewBass:
        return "view_bass";
    case Action::ViewRoll:
        return "view_roll";
    case Action::ViewFall:
        return "view_fall";
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

KeyChord chord(int key, bool shift = false, bool ctrl = false, bool alt = false) {
    return KeyChord{ctrl, shift, alt, key};
}

int imgui_key_from_name(std::string_view name) {
    if (name == "Space") return ImGuiKey_Space;
    if (name == "A") return ImGuiKey_A;
    if (name == "B") return ImGuiKey_B;
    if (name == "C") return ImGuiKey_C;
    if (name == "D") return ImGuiKey_D;
    if (name == "E") return ImGuiKey_E;
    if (name == "F") return ImGuiKey_F;
    if (name == "G") return ImGuiKey_G;
    if (name == "H") return ImGuiKey_H;
    if (name == "N") return ImGuiKey_N;
    if (name == "Q") return ImGuiKey_Q;
    if (name == "T") return ImGuiKey_T;
    if (name == "R") return ImGuiKey_R;
    if (name == "S") return ImGuiKey_S;
    if (name == "V") return ImGuiKey_V;
    if (name == "W") return ImGuiKey_W;
    if (name == "X") return ImGuiKey_X;
    if (name == "Z") return ImGuiKey_Z;
    if (name == "1") return ImGuiKey_1;
    if (name == "2") return ImGuiKey_2;
    if (name == "3") return ImGuiKey_3;
    if (name == "4") return ImGuiKey_4;
    if (name == "LeftArrow") return ImGuiKey_LeftArrow;
    if (name == "RightArrow") return ImGuiKey_RightArrow;
    return 0;
}

const char* key_name(int key) {
    switch (key) {
    case ImGuiKey_Space:
        return "Space";
    case ImGuiKey_A:
        return "A";
    case ImGuiKey_B:
        return "B";
    case ImGuiKey_C:
        return "C";
    case ImGuiKey_D:
        return "D";
    case ImGuiKey_E:
        return "E";
    case ImGuiKey_F:
        return "F";
    case ImGuiKey_G:
        return "G";
    case ImGuiKey_H:
        return "H";
    case ImGuiKey_N:
        return "N";
    case ImGuiKey_Q:
        return "Q";
    case ImGuiKey_T:
        return "T";
    case ImGuiKey_R:
        return "R";
    case ImGuiKey_S:
        return "S";
    case ImGuiKey_V:
        return "V";
    case ImGuiKey_W:
        return "W";
    case ImGuiKey_X:
        return "X";
    case ImGuiKey_Z:
        return "Z";
    case ImGuiKey_1:
        return "1";
    case ImGuiKey_2:
        return "2";
    case ImGuiKey_3:
        return "3";
    case ImGuiKey_4:
        return "4";
    case ImGuiKey_LeftArrow:
        return "LeftArrow";
    case ImGuiKey_RightArrow:
        return "RightArrow";
    default:
        return nullptr;
    }
}

bool same_chord(const KeyChord& left, const KeyChord& right) {
    return left.key == right.key && left.ctrl == right.ctrl && left.shift == right.shift && left.alt == right.alt;
}

int conflict_of(const Keymap& map, Action self, const KeyChord& chord) {
    if (chord.key == 0) {
        return -1;
    }
    for (int index = 0; index < kActionCount; ++index) {
        if (index == static_cast<int>(self)) {
            continue;
        }
        if (same_chord(map.bindings[static_cast<std::size_t>(index)], chord)) {
            return index;
        }
    }
    return -1;
}

void apply_mute_or_solo(App& app, bool solo) {
    if (app.session == nullptr || app.selected < 0 || app.selected >= bt::kStemCount) {
        return;
    }
    const auto song = app.session->snapshot();
    if (song == nullptr) {
        return;
    }
    const auto index = static_cast<std::size_t>(app.selected);
    if (solo) {
        const bool next = !song->solo[index];
        app.session->set_solo(app.selected, next);
    } else {
        const bool next = !song->mute[index];
        app.session->set_mute(app.selected, next);
    }
}

void start_or_pause_transport(App& app, const bt::Player::Snapshot& transport) {
    if (transport.playing) {
        app.player->pause();
        return;
    }
    if (transport.counting) {
        app.player->play();
        return;
    }
    if (app.count_in) {
        app.player->count_in(4);
        return;
    }
    app.player->play();
}

}  // namespace

bool reserved_chord(const KeyChord& chord) {
    if (chord.key == 0) {
        return false;
    }
    // Leave Windows shell / system menu chords alone.
    if (chord.key == ImGuiKey_LeftSuper || chord.key == ImGuiKey_RightSuper || chord.key == ImGuiKey_Menu ||
        chord.key == ImGuiKey_F10) {
        return true;
    }
    // Ctrl/Shift/Alt+Space are common IME / layout switch chords.
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
    // Alt alone turns on menu mnemonics; do not let app bindings own Alt+letter.
    if (chord.alt && !chord.ctrl) {
        return true;
    }
    return false;
}

const char* action_label(Action action, int language) {
    const char* hant = "";
    const char* hans = "";
    const char* en = "";
    switch (action) {
    case Action::PlayPause:
        hant = "播放／暫停";
        hans = "播放／暂停";
        en = "Play / Pause";
        break;
    case Action::Stop:
        hant = "停止";
        hans = "停止";
        en = "Stop";
        break;
    case Action::SeekBack:
        hant = "後退";
        hans = "后退";
        en = "Seek back";
        break;
    case Action::SeekForward:
        hant = "前進";
        hans = "前进";
        en = "Seek forward";
        break;
    case Action::Loop:
        hant = "循環";
        hans = "循环";
        en = "Loop";
        break;
    case Action::Metro:
        hant = "節拍器";
        hans = "节拍器";
        en = "Metronome";
        break;
    case Action::CountIn:
        hant = "數四拍";
        hans = "数四拍";
        en = "Count in";
        break;
    case Action::Snap:
        hant = "對拍";
        hans = "对拍";
        en = "Snap";
        break;
    case Action::Mute:
        hant = "靜音選取軌";
        hans = "静音选取轨";
        en = "Mute selected";
        break;
    case Action::Solo:
        hant = "獨奏選取軌";
        hans = "独奏选取轨";
        en = "Solo selected";
        break;
    case Action::PrevTrack:
        hant = "上一軌";
        hans = "上一轨";
        en = "Previous track";
        break;
    case Action::NextTrack:
        hant = "下一軌";
        hans = "下一轨";
        en = "Next track";
        break;
    case Action::ViewGuitar:
        hant = "吉他視圖";
        hans = "吉他视图";
        en = "Guitar view";
        break;
    case Action::ViewBass:
        hant = "貝斯視圖";
        hans = "贝斯视图";
        en = "Bass view";
        break;
    case Action::ViewRoll:
        hant = "鋼琴卷簾";
        hans = "钢琴卷帘";
        en = "Piano roll";
        break;
    case Action::ViewFall:
        hant = "下落視圖";
        hans = "下落视图";
        en = "Fall view";
        break;
    case Action::AdjustSpeed:
        hant = "速度 −／+";
        hans = "速度 −／+";
        en = "Speed − / +";
        break;
    case Action::AdjustSeek:
        hant = "步進 −／+";
        hans = "步进 −／+";
        en = "Seek step − / +";
        break;
    case Action::AdjustMaster:
        hant = "主音量 −／+";
        hans = "主音量 −／+";
        en = "Master − / +";
        break;
    case Action::AdjustClick:
        hant = "節拍器音量 −／+";
        hans = "节拍器音量 −／+";
        en = "Click − / +";
        break;
    case Action::AdjustTrackGain:
        hant = "選取軌音量 −／+";
        hans = "选取轨音量 −／+";
        en = "Track volume − / +";
        break;
    case Action::AdjustTrackPan:
        hant = "選取軌聲像 左／右";
        hans = "选取轨声像 左／右";
        en = "Track pan L / R";
        break;
    case Action::Count:
        break;
    }
    if (language == 1) {
        return hans;
    }
    if (language == 2) {
        return en;
    }
    return hant;
}

Keymap default_keymap() {
    Keymap map;
    map.bindings[static_cast<std::size_t>(Action::PlayPause)] = chord(ImGuiKey_Space);
    map.bindings[static_cast<std::size_t>(Action::Stop)] = chord(ImGuiKey_X);
    map.bindings[static_cast<std::size_t>(Action::SeekBack)] = chord(ImGuiKey_A);
    map.bindings[static_cast<std::size_t>(Action::SeekForward)] = chord(ImGuiKey_D);
    map.bindings[static_cast<std::size_t>(Action::Loop)] = chord(ImGuiKey_W);
    map.bindings[static_cast<std::size_t>(Action::Metro)] = chord(ImGuiKey_S);
    map.bindings[static_cast<std::size_t>(Action::CountIn)] = chord(ImGuiKey_T);
    map.bindings[static_cast<std::size_t>(Action::Snap)] = chord(ImGuiKey_G);
    map.bindings[static_cast<std::size_t>(Action::Mute)] = chord(ImGuiKey_B);
    map.bindings[static_cast<std::size_t>(Action::Solo)] = chord(ImGuiKey_V);
    map.bindings[static_cast<std::size_t>(Action::PrevTrack)] = chord(ImGuiKey_C);
    map.bindings[static_cast<std::size_t>(Action::NextTrack)] = chord(ImGuiKey_N);
    map.bindings[static_cast<std::size_t>(Action::ViewGuitar)] = chord(ImGuiKey_1);
    map.bindings[static_cast<std::size_t>(Action::ViewBass)] = chord(ImGuiKey_2);
    map.bindings[static_cast<std::size_t>(Action::ViewRoll)] = chord(ImGuiKey_3);
    map.bindings[static_cast<std::size_t>(Action::ViewFall)] = chord(ImGuiKey_4);
    map.bindings[static_cast<std::size_t>(Action::AdjustSpeed)] = chord(ImGuiKey_Q);
    map.bindings[static_cast<std::size_t>(Action::AdjustSeek)] = chord(ImGuiKey_E);
    map.bindings[static_cast<std::size_t>(Action::AdjustMaster)] = chord(ImGuiKey_Z);
    map.bindings[static_cast<std::size_t>(Action::AdjustClick)] = chord(ImGuiKey_H);
    map.bindings[static_cast<std::size_t>(Action::AdjustTrackGain)] = chord(ImGuiKey_F);
    map.bindings[static_cast<std::size_t>(Action::AdjustTrackPan)] = chord(ImGuiKey_R);
    return map;
}

void load_keymap(App& app) {
    app.keymap = default_keymap();
    if (app.cache_directory.empty()) {
        return;
    }
    std::ifstream input(app.cache_directory / "keybindings.txt");
    std::string line;
    bool layout_ok = false;
    while (std::getline(input, line)) {
        if (line.empty() || line[0] == '#') {
            continue;
        }
        const auto eq = line.find('=');
        if (eq == std::string::npos) {
            continue;
        }
        if (line.substr(0, eq) == "layout") {
            layout_ok = line.substr(eq + 1) == "2";
            continue;
        }
        if (!layout_ok) {
            continue;
        }
        const Action action = action_from_id(line.substr(0, eq));
        if (action == Action::Count) {
            continue;
        }
        KeyChord chord{};
        std::string value = line.substr(eq + 1);
        while (!value.empty()) {
            const auto plus = value.find('+');
            const std::string token = plus == std::string::npos ? value : value.substr(0, plus);
            if (token == "Ctrl") {
                chord.ctrl = true;
            } else if (token == "Shift") {
                chord.shift = true;
            } else if (token == "Alt") {
                chord.alt = true;
            } else if (token == "None") {
                chord = {};
                break;
            } else {
                chord.key = imgui_key_from_name(token);
            }
            if (plus == std::string::npos) {
                break;
            }
            value.erase(0, plus + 1);
        }
        if (reserved_chord(chord)) {
            continue;
        }
        app.keymap.bindings[static_cast<std::size_t>(action)] = chord;
    }
    if (!layout_ok) {
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
    output << "layout=2\n";
    for (int index = 0; index < kActionCount; ++index) {
        const auto action = static_cast<Action>(index);
        output << action_id(action) << '=' << chord_text(app.keymap.bindings[static_cast<std::size_t>(index)]) << '\n';
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
    // Space with any modifier is reserved for IME / layout switching.
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
        if (key_name(key) == nullptr) {
            continue;
        }
        KeyChord next = chord(key, shift, ctrl, alt);
        if (reserved_chord(next)) {
            return false;
        }
        out = next;
        return true;
    }
    return false;
}

void draw_keymap_settings(App& app) {
    const float scale = ImGui::GetIO().FontGlobalScale;
    ImGui::TextUnformatted(app.language == 2 ? "Shortcuts" : app.language == 1 ? "快捷键" : "快捷鍵");
    if (ImGui::BeginTable("keymap", 3, ImGuiTableFlags_SizingStretchProp)) {
        for (int index = 0; index < kActionCount; ++index) {
            const auto action = static_cast<Action>(index);
            KeyChord& binding = app.keymap.bindings[static_cast<std::size_t>(index)];
            ImGui::PushID(index);
            ImGui::TableNextRow();
            ImGui::TableSetColumnIndex(0);
            ImGui::TextUnformatted(action_label(action, app.language));
            ImGui::TableSetColumnIndex(1);
            const bool capturing = app.keymap_capture == index;
            const bool adjust = action >= Action::AdjustSpeed && action <= Action::AdjustTrackPan;
            std::string shown = chord_text(binding);
            if (adjust && binding.key != 0) {
                const char* down = key_name(app.keymap.bindings[static_cast<std::size_t>(Action::SeekBack)].key);
                const char* up = key_name(app.keymap.bindings[static_cast<std::size_t>(Action::SeekForward)].key);
                shown += " + ";
                shown += down != nullptr ? down : "?";
                shown += "/";
                shown += up != nullptr ? up : "?";
            }
            const std::string label = capturing
                                          ? (app.language == 2 ? "Press key..." : app.language == 1 ? "请按键..." : "請按鍵…")
                                          : shown;
            if (ImGui::Button(label.c_str(), ImVec2(140.f * scale, 0.f))) {
                app.keymap_capture = capturing ? -1 : index;
            }
            if (capturing) {
                if (ImGui::IsKeyPressed(ImGuiKey_Escape)) {
                    app.keymap_capture = -1;
                } else if (ImGui::IsKeyPressed(ImGuiKey_Backspace)) {
                    binding = {};
                    app.keymap_capture = -1;
                    save_keymap(app);
                } else if (!ime_owns_keyboard(app)) {
                    KeyChord next{};
                    if (capture_chord(next)) {
                        const bool layered = action >= Action::AdjustSpeed && action <= Action::AdjustTrackPan;
                        const bool plain = !next.ctrl && !next.shift && !next.alt;
                        const bool direction = next.key != 0 &&
                                               (next.key == app.keymap.bindings[static_cast<std::size_t>(Action::SeekBack)].key ||
                                                next.key == app.keymap.bindings[static_cast<std::size_t>(Action::SeekForward)].key);
                        if (conflict_of(app.keymap, action, next) < 0 && !reserved_chord(next) &&
                            (!layered || (plain && !direction))) {
                            binding = next;
                            save_keymap(app);
                            app.keymap_capture = -1;
                        }
                    }
                }
            }
            ImGui::TableSetColumnIndex(2);
            if (ImGui::SmallButton(app.language == 2 ? "Reset" : "還原")) {
                binding = default_keymap().bindings[static_cast<std::size_t>(index)];
                save_keymap(app);
                app.keymap_capture = -1;
            }
            ImGui::PopID();
        }
        ImGui::EndTable();
    }
    if (ImGui::Button(app.language == 2 ? "Reset all shortcuts" : app.language == 1 ? "还原全部快捷键" : "還原全部快捷鍵")) {
        app.keymap = default_keymap();
        save_keymap(app);
        app.keymap_capture = -1;
    }
}

bool layer_held(const KeyChord& chord) {
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
        break;
    case Action::AdjustSeek:
        app.seek_step = snap_step(app.seek_step + static_cast<float>(direction), 1.f, 20.f, 1.f);
        save_performance(app);
        break;
    case Action::AdjustMaster:
        app.master_db = snap_step(app.master_db + static_cast<float>(direction), -60.f, 6.f, 1.f);
        app.player->set_master_db(app.master_db);
        break;
    case Action::AdjustClick:
        app.click_db = snap_step(app.click_db + static_cast<float>(direction), -60.f, 6.f, 1.f);
        app.player->set_metronome(app.metro, app.bpm, app.click_db, app.beat_offset, true, app.beat_times);
        break;
    case Action::AdjustTrackGain:
        if (app.session != nullptr && app.selected >= 0 && app.selected < bt::kStemCount) {
            float& db = app.gain_db[static_cast<std::size_t>(app.selected)];
            db = snap_step(db + static_cast<float>(direction), -60.f, 6.f, 1.f);
            app.session->set_gain(app.selected, std::pow(10.f, db / 20.f));
        }
        break;
    case Action::AdjustTrackPan:
        if (app.session != nullptr && app.selected >= 0 && app.selected < bt::kStemCount) {
            float& pan = app.pan[static_cast<std::size_t>(app.selected)];
            pan = snap_step(pan + 0.05f * static_cast<float>(direction), -1.f, 1.f, 0.05f);
            app.session->set_pan(app.selected, pan);
        }
        break;
    default:
        break;
    }
}

void handle_actions(App& app, bool ready) {
    if (app.player == nullptr || ImGui::IsAnyItemActive() || app.keymap_capture >= 0) {
        return;
    }
    if (ImGui::IsPopupOpen("settings", ImGuiPopupFlags_AnyPopupId)) {
        return;
    }
    if (ime_owns_keyboard(app)) {
        return;
    }
    const auto transport = app.player->snapshot();
    const auto& map = app.keymap;

    if (ready && chord_pressed(map.bindings[static_cast<std::size_t>(Action::PlayPause)], false)) {
        start_or_pause_transport(app, transport);
    }
    if (ready && chord_pressed(map.bindings[static_cast<std::size_t>(Action::Stop)], false)) {
        app.player->stop();
    }

    Action held = Action::Count;
    int holds = 0;
    for (int index = static_cast<int>(Action::AdjustSpeed); index <= static_cast<int>(Action::AdjustTrackPan); ++index) {
        if (layer_held(map.bindings[static_cast<std::size_t>(index)])) {
            held = static_cast<Action>(index);
            ++holds;
        }
    }
    if (holds > 0) {
        if (holds == 1) {
            const KeyChord& down = map.bindings[static_cast<std::size_t>(Action::SeekBack)];
            const KeyChord& up = map.bindings[static_cast<std::size_t>(Action::SeekForward)];
            if (down.key != 0 && ImGui::IsKeyPressed(static_cast<ImGuiKey>(down.key), true)) {
                nudge_layer(app, held, -1);
            }
            if (up.key != 0 && ImGui::IsKeyPressed(static_cast<ImGuiKey>(up.key), true)) {
                nudge_layer(app, held, 1);
            }
        }
        return;
    }

    const bool back = ready && chord_pressed(map.bindings[static_cast<std::size_t>(Action::SeekBack)], true);
    const bool forward = ready && chord_pressed(map.bindings[static_cast<std::size_t>(Action::SeekForward)], true);
    if (back || forward) {
        double step = static_cast<double>(std::clamp(app.seek_step, 1.f, 20.f));
        if (back) {
            step = -step;
        }
        app.player->seek(transport.position + step * static_cast<double>(transport.speed));
    }
    if (ready && chord_pressed(map.bindings[static_cast<std::size_t>(Action::Loop)], false)) {
        app.player->set_loop_enabled(!transport.loop);
    }
    if (chord_pressed(map.bindings[static_cast<std::size_t>(Action::Metro)], false)) {
        app.metro = !app.metro;
        app.player->set_metronome(app.metro, app.bpm, app.click_db, app.beat_offset, true, app.beat_times);
    }
    if (chord_pressed(map.bindings[static_cast<std::size_t>(Action::CountIn)], false)) {
        app.count_in = !app.count_in;
    }
    if (chord_pressed(map.bindings[static_cast<std::size_t>(Action::Snap)], false)) {
        app.loop_snap = !app.loop_snap;
    }
    if (ready && chord_pressed(map.bindings[static_cast<std::size_t>(Action::Mute)], false)) {
        apply_mute_or_solo(app, false);
    }
    if (ready && chord_pressed(map.bindings[static_cast<std::size_t>(Action::Solo)], false)) {
        apply_mute_or_solo(app, true);
    }
    if (chord_pressed(map.bindings[static_cast<std::size_t>(Action::PrevTrack)], false)) {
        app.selected = (app.selected + bt::kStemCount - 1) % bt::kStemCount;
    }
    if (chord_pressed(map.bindings[static_cast<std::size_t>(Action::NextTrack)], false)) {
        app.selected = (app.selected + 1) % bt::kStemCount;
    }
    if (chord_pressed(map.bindings[static_cast<std::size_t>(Action::ViewGuitar)], false)) {
        app.midi_view = MidiView::Guitar;
    }
    if (chord_pressed(map.bindings[static_cast<std::size_t>(Action::ViewBass)], false)) {
        app.midi_view = MidiView::Bass;
    }
    if (chord_pressed(map.bindings[static_cast<std::size_t>(Action::ViewRoll)], false)) {
        app.midi_view = MidiView::Roll;
    }
    if (chord_pressed(map.bindings[static_cast<std::size_t>(Action::ViewFall)], false)) {
        app.midi_view = MidiView::Fall;
    }
}

}  // namespace btapp
