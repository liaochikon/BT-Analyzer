#pragma once

#include "bt/basic_pitch.hpp"
#include "bt/beat_track.hpp"
#include "bt/player.hpp"
#include "bt/project.hpp"
#include "bt/session.hpp"
#include "keys.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <memory>
#include <string>
#include <string_view>
#include <vector>

struct HWND__;

namespace btapp {

struct OutputChoice {
    std::string id;
    std::string name;
};

struct Peak {
    float low = 0.f;
    float high = 0.f;
};

enum class MidiView { Guitar, Bass, Fall };

enum class NoteStage { View, Midi, Tab };

// Which main pane the mouse is over. Used for context shortcut hints.
enum class HoverRegion { None, Projects, Midi, Mixer, Player };

struct ScreenRect {
    float x0 = 0.f;
    float y0 = 0.f;
    float x1 = 0.f;
    float y1 = 0.f;

    bool valid() const { return x1 > x0 && y1 > y0; }
    bool contains(float x, float y) const { return valid() && x >= x0 && x < x1 && y >= y0 && y < y1; }
};

using MidiNote = bt::Note;
using ProjectItem = bt::ProjectEntry;

enum class NoteStatus { Idle, Running, Ready, Failed };

// Division of the beat pulse. Bar is four beats, Beat is the detected pulse, and the
// rest divide that pulse. The metronome and the grid each pick their own.
enum class BeatDivision { Bar = 0, Beat = 1, Half = 2, Triplet = 3, Quarter = 4 };

inline const char* division_token(BeatDivision division) {
    switch (division) {
    case BeatDivision::Bar:
        return "bar";
    case BeatDivision::Half:
        return "half";
    case BeatDivision::Triplet:
        return "triplet";
    case BeatDivision::Quarter:
        return "quarter";
    case BeatDivision::Beat:
        return "beat";
    }
    return "beat";
}

inline BeatDivision division_from_token(std::string_view token) {
    if (token == "bar") {
        return BeatDivision::Bar;
    }
    if (token == "half") {
        return BeatDivision::Half;
    }
    if (token == "triplet") {
        return BeatDivision::Triplet;
    }
    if (token == "quarter") {
        return BeatDivision::Quarter;
    }
    return BeatDivision::Beat;
}

enum class GridMark { Bar, Beat, Sub };

// The fretboard is always 24 frets. Note brightness on the board uses a fixed full scale.
inline constexpr int kFretCount = 24;
inline constexpr int kFretBright = 100;

// One undoable change to the open project. Each step keeps both sides, so undo applies
// `before` and redo applies `after`. Separation swaps folders in both directions.
struct ProjectStep {
    enum class Kind { Notes, Playback, Rename, Separation };
    Kind kind = Kind::Notes;
    int stem = -1;
    std::vector<MidiNote> notes_before;
    std::vector<MidiNote> notes_after;
    bt::ProjectPlayback playback_before;
    bt::ProjectPlayback playback_after;
    std::filesystem::path folder;
    std::string name_before;
    std::string name_after;
    // Separation: the folder that holds the other side of the swap.
    std::filesystem::path held;
};

// UI-facing pitch knobs; convert to bt::PitchParameters at request time.
struct PitchDraft {
    float onset = 0.2f;
    float frame = 0.2f;
    int min_ms = 128;
    int min_hz = 0;
    int max_hz = 0;
    bool melodia = false;
};

class NoteService;
class BeatService;
class StemEngine;

struct App {
    bt::Session* session = nullptr;
    bt::Player* player = nullptr;
    HWND__* hwnd = nullptr;
    std::filesystem::path cache_directory;
    std::filesystem::path saves_directory;
    std::string device_note;
    // May be an i18n key (error.*) or a raw technical message.
    std::string model_note;
    int separation_chunk = 0;
    std::string separation_device;
    std::string separation_model = std::string(bt::kModelKey);
    int separation_preference = 0;
    StemEngine* engine = nullptr;
    std::shared_ptr<const bt::Stems> loaded;
    std::shared_ptr<const bt::Audio> peaks_source;
    std::shared_ptr<const bt::Stems> peak_stems;
    std::vector<Peak> peaks;
    float peak_scale = 1.f;
    std::array<std::vector<Peak>, bt::kStemCount> stem_peaks{};
    std::array<float, bt::kStemCount> stem_peak_scale{};
    std::array<float, bt::kStemCount> gain_db{};
    std::array<float, bt::kStemCount> pan{};
    std::array<bool, bt::kStemCount> mute{};
    std::array<bool, bt::kStemCount> solo{};
    std::array<float, bt::kStemCount> meter_l{};
    std::array<float, bt::kStemCount> meter_r{};
    float master_meter_l = 0.f;
    float master_meter_r = 0.f;
    float speed = 1.f;
    float applied_speed = 1.f;
    float seek_step = 10.f;
    float master_db = 0.f;
    bool metro = true;
    BeatDivision metro_division = BeatDivision::Beat;
    // Grid lines in the views, and note placement snapping. Its own switch and division.
    bool grid = true;
    BeatDivision grid_division = BeatDivision::Beat;
    float bpm = 120.f;
    double beat_offset = 0;
    std::shared_ptr<const std::vector<double>> beat_times;
    std::vector<bt::BeatSpan> beat_spans;
    BeatService* beat_service = nullptr;
    std::uint64_t beat_song = 0;
    std::uint64_t beat_ticket = 0;
    bool beat_ready = false;
    bool beat_pending = false;
    std::string beat_detail;
    float click_db = 0.f;
    double metro_seen = -1;
    double beat_flash_at = -1;
    int beat_flash_level = 1;
    std::uint64_t seen_revision = 0;
    int selected = 4;
    MidiView midi_view = MidiView::Guitar;
    NoteStage note_stage = NoteStage::View;
    // Piano roll viewport. Vertical span is a whole number of octaves.
    double roll_time = 0;
    double roll_seconds = 16;
    int roll_bottom = 48;
    int roll_semitones = 24;
    std::uint64_t roll_song = 0;
    int roll_stem = -1;
    bool roll_seen_notes = false;
    double roll_fit_length = -1;
    float roll_grab_x = 0.f;
    float roll_grab_y = 0.f;
    double roll_grab_time = 0;
    int roll_grab_bottom = 48;
    int roll_note = -1;
    int roll_note_pitch = 0;
    double roll_note_start = 0;
    double roll_note_end = 0;
    // 0 move, 1 resize start, 2 resize end, 3 place.
    int roll_edit = 0;
    bool roll_erase = false;
    // Piano-roll selection. Parallel to notes[roll_stem] when non-empty; cleared on stem change.
    std::vector<char> roll_selected;
    // Snapshot of selected notes at the start of a multi-note move.
    struct RollDragNote {
        int index = 0;
        int pitch = 0;
        double start = 0;
        double end = 0;
    };
    std::vector<RollDragNote> roll_drag_group;
    // Piano-roll clipboard. Starts are relative to the earliest copied note (0 = paste anchor).
    std::vector<MidiNote> roll_clipboard;
    // True while paste preview follows the mouse; left click / Enter commits, Esc / right cancels.
    bool roll_paste = false;
    // Undo and redo for the open project. Cleared when the user opens or closes a project.
    std::vector<ProjectStep> project_undo;
    std::vector<ProjectStep> project_redo;
    // Session job the timeline is attached to, and the job the app started itself (refresh, undo swap).
    std::uint64_t timeline_job = 0;
    std::uint64_t timeline_expected_job = 0;
    // Refresh job whose replaced folder should become an undo step once it is Ready.
    std::uint64_t separation_job = 0;
    std::array<std::vector<MidiNote>, bt::kStemCount> rerun_before{};
    std::array<bool, bt::kStemCount> rerun_pending{};
    // Last playback state that was recorded as a step (or applied from the project file).
    bt::ProjectPlayback playback_committed;
    bool note_gesture = false;
    int note_gesture_stem = -1;
    std::vector<MidiNote> note_gesture_before;
    int guitar_pitch_min = 40;
    int guitar_pitch_max = 86;
    int bass_pitch_min = 28;
    int bass_pitch_max = 65;
    int fret_prefer = 0;
    bool fret_suggest = false;
    bool fret_allow_open = false;
    bool fret_show_alt = true;
    bool fret_harmonic = false;
    int key_min = 21;
    int key_max = 108;
    using VerifyLaneUi = bt::ProjectVerifyLane;
    using VerifyStemUi = bt::ProjectVerifyStem;
    std::array<VerifyStemUi, bt::kStemCount> verify{};
    float hand_center = 5.f;
    double midi_drag_origin = 0;
    float midi_drag_y = 0.f;
    std::array<std::vector<MidiNote>, bt::kStemCount> notes{};
    NoteService* note_service = nullptr;
    std::array<NoteStatus, bt::kStemCount> note_status{};
    std::array<std::string, bt::kStemCount> note_detail{};
    std::uint64_t note_song = 0;
    std::array<std::uint64_t, bt::kStemCount> note_ticket{};
    std::array<std::uint32_t, bt::kStemCount> note_edits{};
    std::array<bool, bt::kStemCount> note_refresh{};
    float note_progress = 0.f;
    bool pitch_ready = false;
    // May be an i18n key (error.*) or a raw technical message.
    std::string pitch_note;
    std::filesystem::path pitch_model_path;
    int note_workers = 4;
    int demucs_threads = 4;
    Keymap keymap = default_keymap();
    int keymap_capture = -1;
    KeySequence keymap_draft{};
    double keymap_draft_at = 0;
    bool shortcut_hints = false;
    HoverRegion hover_region = HoverRegion::None;
    ScreenRect hover_projects{};
    ScreenRect hover_midi{};
    ScreenRect hover_mixer{};
    ScreenRect hover_player{};
    int language = 0;
    bool language_loaded = false;
    int wave_drag = -1;
    int loop_drag = -1;
    bool loop_is_a = true;
    bool loop_paint = false;
    bool loop_snap = false;
    bool count_in = false;
    // Song folder whose mixer and player settings are loaded. Empty means no project owns them.
    std::uint64_t playback_job = 0;
    std::filesystem::path playback_directory;
    bool playback_bound = false;
    bool playback_dirty = false;
    bool playback_loop_pending = false;
    bool playback_loop_on = false;
    double playback_loop_a = -1;
    double playback_loop_b = -1;
    double pivot = 0;
    std::uint32_t widget_drag = 0;
    float widget_origin = 0.f;
    float widget_anchor = 0.f;
    std::vector<ProjectItem> projects;
    int project_index = -1;
    bool settings_panel = false;
    bool projects_seeded = false;
    double projects_scanned = -1;
    std::uint64_t project_watch_job = 0;
    int project_watch_phase = -1;
    std::string project_watch_hash;
    std::string project_watch_name;
    std::uint64_t closed_job = 0;
    int rename_index = -1;
    int rename_pending = -1;
    float rename_arm = -1.f;
    bool rename_focus = false;
    char rename_text[160] = {};
    float stage_share = -1.f;
    float mixer_restored = -1.f;  // last non-zero mixer height share of inner (0..1)
    // Design pixels (pre ui_scale). 0 = collapsed to pull-strip.
    float projects_width = 232.f;
    float projects_restored = 232.f;
    bool layout_loaded = false;
    int mixer_scroll = 0;
    int output_device = 0;
    std::string output_id;
    double outputs_at = -1;
    std::vector<OutputChoice> outputs;
    bool monitor_open = false;
    bool midi_menu = false;
    bool pitch_seeded = false;
    std::array<PitchDraft, bt::kStemCount> pitch_draft{};
    std::array<PitchDraft, bt::kStemCount> pitch_applied{};
};

template <class Fn>
void each_metro_beat(const App& app, double from, double to, Fn&& visit) {
    if (!(to > from) || app.beat_times == nullptr) {
        return;
    }
    const auto& times = *app.beat_times;
    const auto begin = times.begin();
    for (auto it = std::lower_bound(begin, times.end(), from - 1e-4); it != times.end() && *it < to - 1e-9; ++it) {
        if (*it >= from - 1e-4) {
            visit(*it, (it - begin) % 4 == 0);
        }
    }
}

inline int division_parts(BeatDivision division) {
    switch (division) {
    case BeatDivision::Half:
        return 2;
    case BeatDivision::Triplet:
        return 3;
    case BeatDivision::Quarter:
        return 4;
    case BeatDivision::Bar:
    case BeatDivision::Beat:
        return 1;
    }
    return 1;
}

inline double division_step(double beat, BeatDivision division) {
    switch (division) {
    case BeatDivision::Bar:
        return beat * 4.0;
    case BeatDivision::Half:
        return beat / 2.0;
    case BeatDivision::Triplet:
        return beat / 3.0;
    case BeatDivision::Quarter:
        return beat / 4.0;
    case BeatDivision::Beat:
        return beat;
    }
    return beat;
}

inline double beat_gap(const std::vector<double>& times, std::size_t index) {
    if (index + 1 < times.size()) {
        const double gap = times[index + 1] - times[index];
        if (gap > 1e-6) {
            return gap;
        }
    }
    if (index > 0) {
        const double gap = times[index] - times[index - 1];
        if (gap > 1e-6) {
            return gap;
        }
    }
    return 0.5;
}

inline double minimum_gap(const std::vector<double>& times, double from, double to) {
    if (times.size() < 2) {
        return 0;
    }
    auto it = std::lower_bound(times.begin(), times.end(), from);
    if (it != times.begin()) {
        --it;
    }
    double gap = 0;
    bool any = false;
    for (; it != times.end() && *it < to; ++it) {
        const auto next = it + 1;
        if (next == times.end()) {
            break;
        }
        const double step = *next - *it;
        if (step > 1e-6 && (!any || step < gap)) {
            gap = step;
            any = true;
        }
    }
    return any ? gap : 0;
}

// Walk the beat lattice. Bar is every fourth beat. Finer divisions split the gap to the next beat.
template <class Fn>
void each_grid_tick(const std::vector<double>& times, BeatDivision division, double from, double to, Fn&& visit) {
    if (!(to > from) || times.empty()) {
        return;
    }
    const int parts = division_parts(division);
    const bool bars_only = division == BeatDivision::Bar;
    auto cursor = std::lower_bound(times.begin(), times.end(), from - 1e-4);
    std::size_t index = static_cast<std::size_t>(cursor - times.begin());
    if (index > 0) {
        --index;
    }
    const double limit = to - 1e-9;
    for (; index < times.size(); ++index) {
        const double beat = times[index];
        if (beat >= limit) {
            break;
        }
        const double gap = beat_gap(times, index);
        const bool bar = index % 4 == 0;
        if (beat >= from - 1e-4) {
            if (bars_only) {
                if (bar) {
                    visit(beat, GridMark::Bar);
                }
            } else {
                visit(beat, bar ? GridMark::Bar : GridMark::Beat);
            }
        }
        if (!bars_only && parts > 1) {
            const double next = index + 1 < times.size() ? times[index + 1] : beat + gap;
            for (int part = 1; part < parts; ++part) {
                const double tick = beat + gap * static_cast<double>(part) / static_cast<double>(parts);
                if (tick >= next - 1e-6) {
                    break;
                }
                if (tick >= from - 1e-4 && tick < limit) {
                    visit(tick, GridMark::Sub);
                }
            }
        }
    }
}

template <class Fn>
void each_bpm_tick(double bpm, double offset, BeatDivision division, double from, double to, Fn&& visit) {
    if (!(to > from)) {
        return;
    }
    const double beat = bpm > 1.0 ? 60.0 / bpm : 0.5;
    const int parts = division_parts(division);
    const double step = division == BeatDivision::Bar ? beat * 4.0 : beat / static_cast<double>(std::max(1, parts));
    if (!(step > 1e-6)) {
        return;
    }
    const double phase = std::fmod(std::max(0.0, offset), step);
    long long index = static_cast<long long>(std::ceil((from - phase - 1e-6) / step));
    if (index < 0) {
        index = 0;
    }
    const double limit = to - 1e-9;
    for (int guard = 0; guard < 100000; ++guard, ++index) {
        const double time = phase + static_cast<double>(index) * step;
        if (time >= limit) {
            break;
        }
        if (time < from - 1e-4) {
            continue;
        }
        if (division == BeatDivision::Bar) {
            visit(time, GridMark::Bar);
            continue;
        }
        const double beat_pos = (time - offset) / beat;
        const double nearest = std::round(beat_pos);
        if (std::abs(beat_pos - nearest) >= 1e-3) {
            visit(time, GridMark::Sub);
            continue;
        }
        const auto beat_index = static_cast<long long>(std::llround(nearest));
        const int wrapped = static_cast<int>(beat_index % 4);
        visit(time, (wrapped < 0 ? wrapped + 4 : wrapped) == 0 ? GridMark::Bar : GridMark::Beat);
    }
}

inline double snap_grid_time(const App& app, double time, bool snap) {
    time = std::max(0.0, time);
    // With the grid off, positions are free. Ctrl turns snapping off per gesture.
    if (!snap || !app.grid) {
        return time;
    }
    double best = time;
    double best_distance = 1e9;
    auto consider = [&](double tick, GridMark) {
        const double distance = std::abs(tick - time);
        if (distance < best_distance) {
            best_distance = distance;
            best = tick;
        }
    };
    if (app.beat_times != nullptr && !app.beat_times->empty()) {
        each_grid_tick(*app.beat_times, app.grid_division, time - 8.0, time + 8.0, consider);
    } else {
        each_bpm_tick(app.bpm, app.beat_offset, app.grid_division, time - 8.0, time + 8.0, consider);
    }
    if (!(best_distance < 1e8)) {
        return time;
    }
    return std::max(0.0, best);
}

inline double grid_cell(const App& app, double time) {
    time = std::max(0.0, time);
    double next = 0;
    bool found = false;
    auto take = [&](double tick, GridMark) {
        if (!found) {
            next = tick;
            found = true;
        }
    };
    if (app.beat_times != nullptr && !app.beat_times->empty()) {
        each_grid_tick(*app.beat_times, app.grid_division, time + 1e-4, time + 16.0, take);
    } else {
        each_bpm_tick(app.bpm, app.beat_offset, app.grid_division, time + 1e-4, time + 16.0, take);
    }
    if (!found) {
        const double beat = app.bpm > 1.f ? 60.0 / static_cast<double>(app.bpm) : 0.5;
        return std::max(0.05, division_step(beat, app.grid_division));
    }
    return std::max(0.05, next - time);
}

inline float gain_to_db(float gain) {
    if (!(gain > 1.0e-4f)) {
        return -60.f;
    }
    return std::clamp(20.f * std::log10(gain), -60.f, 6.f);
}

inline float db_to_gain(float db) {
    return std::pow(10.f, db / 20.f);
}

inline void apply_metronome(App& app) {
    if (app.player == nullptr) {
        return;
    }
    app.player->set_metronome(
        app.metro, app.bpm, app.click_db, app.beat_offset, true, app.beat_times, static_cast<int>(app.metro_division));
}

bool edit_loop_begin(
    App& app, const bt::Player::Snapshot& transport, double seconds, double length, float pointer, float a_at, float b_at,
    float grab);
void edit_loop_drag(App& app, double seconds, double length);

}  // namespace btapp
