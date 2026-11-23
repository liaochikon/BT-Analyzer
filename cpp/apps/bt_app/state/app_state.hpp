#pragma once

#include "bt/basic_pitch.hpp"
#include "bt/beat_track.hpp"
#include "bt/core/grid.hpp"
#include "bt/core/types.hpp"
#include "bt/core/engine.hpp"
#include "bt/player.hpp"
#include "bt/project.hpp"
#include "bt/session.hpp"
#include "keys.hpp"
#include "pointer.hpp"

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

enum class MidiView { Tab, Guitar, Bass, Fall };

// View = not in the MIDI/TAB editor; Midi = dual-view editor (TAB strip + roll/fall/fret).
enum class NoteStage { View, Midi };

using TabDuration = bt::core::TabDuration;
using TabBeat = bt::core::TabBeat;
using TabMeasure = bt::core::TabMeasure;
using TabScore = bt::core::TabScore;
inline constexpr int kTabDurationCount = bt::core::kTabDurationCount;

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

using BeatDivision = bt::core::BeatDivision;
using GridMark = bt::core::GridMark;
using bt::core::division_from_token;
using bt::core::division_parts;
using bt::core::division_step;
using bt::core::division_token;
using bt::core::each_bpm_tick;
using bt::core::each_grid_tick;

// The fretboard is always 24 frets. Note brightness on the board uses a fixed full scale.
inline constexpr int kFretCount = bt::core::kFretCount;
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
    // Same notes storage as session when wired — GUI edits and headless Engine ops share one array.
    bt::core::Engine* core = nullptr;
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
    // Dirty keys for peak rebuild — avoid holding stems shared_ptr (pins all audio in RAM).
    std::uint64_t peaks_job = 0;
    std::uintptr_t peaks_stems_id = 0;
    // Last stem-residency mask pushed to Session (bit i set ⇒ stem i kept in RAM).
    std::uint64_t stem_residency_mask = ~0ull;
    std::uint64_t stem_residency_job = 0;
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
    MidiView midi_view = MidiView::Tab;
    NoteStage note_stage = NoteStage::Midi;
    // Dual-view: TAB strip height as fraction of Midi edit canvas (above piano roll).
    float tab_pane_ratio = 0.28f;
    // TAB fingering options.
    int tab_hand_span = 4;   // max frets a single hand shape may cover
    int tab_onset_ms = 2;    // notes within this window share one onset group
    // Fingering-only mode: selection locked; cycle playable string/fret combos per onset.
    bool tab_fingering_mode = false;
    std::vector<int> tab_mode_notes;  // note indices in selected stem
    int tab_focus_group = 0;
    int tab_combo_index = 0;
    // Which MIDI dual-view pane last claimed pointer focus (shortcuts / erase).
    PaneFocus pane_focus = PaneFocus::Roll;
    // Rhythm tree per stem (Score layout). TAB group edits can write MIDI starts/ends.
    std::array<TabScore, bt::kStemCount> tab_scores{};
    // TAB group drag onto existing rhythm slots (Score mode).
    bool tab_group_dragging = false;
    std::vector<int> tab_drag_notes;
    int tab_drag_measure = -1;
    int tab_drag_beat = -1;
    TabDuration tab_duration = TabDuration::Quarter;
    bool tab_duration_dotted = false;
    int tab_score_measure = 0;
    int tab_score_beat = 0;
    int tab_score_string = 0;
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
    // Locked at middle-pan start so TAB + piano roll (different grid_w) stay in sync.
    float roll_pan_sec_per_px = 0.f;
    float roll_pan_row_h = 0.f;
    int roll_note = -1;
    int roll_note_pitch = 0;
    double roll_note_start = 0;
    double roll_note_end = 0;
    RollEdit roll_edit = RollEdit::Move;
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
    // Last paste-preview anchor (updated while drawing the roll); Enter commits via this.
    double roll_paste_time = 0;
    int roll_paste_pitch = 60;
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
    DragKind wave_drag = DragKind::None;
    DragKind loop_drag = DragKind::None;
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
