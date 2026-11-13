#pragma once

#include "bt/player.hpp"
#include "bt/session.hpp"
#include "keys.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <functional>
#include <memory>
#include <optional>
#include <string>
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

enum class MidiView { Guitar, Bass, Roll, Fall };

enum class Icon {
    Play,
    Pause,
    Stop,
    Loop,
    Clear,
    Speaker,
    SpeakerOff,
    Solo,
    Reset,
    Gear,
    Metro,
    Pencil,
    Trash,
    Import,
    Cancel,
    Monitor,
    Guitar,
    Bass,
    Roll,
    Fall,
    Positions,
    Suggest,
    Grid,
    Detect,
    Again,
    Rerun,
    Magnet,
    Count,
};

struct App;

class PlayerButton {
public:
    static float width();
    static float height();

    PlayerButton(const char* id, Icon icon) : id_(id), icon_(icon) {}
    PlayerButton& engaged(bool on) {
        engaged_ = on;
        return *this;
    }
    PlayerButton& tip(const char* text) {
        tip_ = text == nullptr ? "" : text;
        return *this;
    }
    PlayerButton& enabled(bool on) {
        enabled_ = on;
        return *this;
    }
    bool draw() const;

private:
    const char* id_;
    Icon icon_;
    bool engaged_ = false;
    bool enabled_ = true;
    const char* tip_ = "";
};

class SmallButton {
public:
    static float width();
    static float height();

    SmallButton(const char* id, Icon icon) : id_(id), icon_(icon) {}
    SmallButton& engaged(bool on) {
        engaged_ = on;
        return *this;
    }
    SmallButton& tip(const char* text) {
        tip_ = text == nullptr ? "" : text;
        return *this;
    }
    SmallButton& enabled(bool on) {
        enabled_ = on;
        return *this;
    }
    bool draw() const;

private:
    const char* id_;
    Icon icon_;
    bool engaged_ = false;
    bool enabled_ = true;
    const char* tip_ = "";
};

class MediumButton {
public:
    static float width();
    static float height();

    MediumButton(const char* id, Icon icon) : id_(id), icon_(icon) {}
    MediumButton& engaged(bool on) {
        engaged_ = on;
        return *this;
    }
    MediumButton& tip(const char* text) {
        tip_ = text == nullptr ? "" : text;
        return *this;
    }
    MediumButton& enabled(bool on) {
        enabled_ = on;
        return *this;
    }
    bool draw() const;

private:
    const char* id_;
    Icon icon_;
    bool engaged_ = false;
    bool enabled_ = true;
    const char* tip_ = "";
};

class SmallKnob {
public:
    using Caption = void (*)(float value, char* text, std::size_t size);

    static float diameter();
    static float height();
    static float column_width(const char* sample);

    SmallKnob(const char* id, float* value) : id_(id), value_(value) {}
    SmallKnob& range(float min_value, float max_value) {
        min_ = min_value;
        max_ = max_value;
        return *this;
    }
    SmallKnob& reset(float value) {
        reset_ = value;
        return *this;
    }
    SmallKnob& sensitivity(float value) {
        sensitivity_ = value;
        return *this;
    }
    SmallKnob& visual_center(float value) {
        centered_ = true;
        visual_center_ = value;
        return *this;
    }
    SmallKnob& tip(const char* text) {
        tip_ = text;
        return *this;
    }
    SmallKnob& enabled(bool on) {
        enabled_ = on;
        return *this;
    }
    SmallKnob& column(float width) {
        column_ = width;
        return *this;
    }
    SmallKnob& caption(Caption write) {
        caption_ = write;
        return *this;
    }
    bool draw(App& app) const;

private:
    const char* id_;
    float* value_;
    float min_ = 0.f;
    float max_ = 1.f;
    float reset_ = 0.f;
    float sensitivity_ = 0.45f;
    bool centered_ = false;
    float visual_center_ = 0.f;
    const char* tip_ = nullptr;
    bool enabled_ = true;
    float column_ = 0.f;
    Caption caption_ = nullptr;
};

class MediumKnob {
public:
    using Caption = void (*)(float value, char* text, std::size_t size);

    static float diameter();
    static float height();
    static float column_width(const char* sample);

    MediumKnob(const char* id, float* value) : id_(id), value_(value) {}
    MediumKnob& range(float min_value, float max_value) {
        min_ = min_value;
        max_ = max_value;
        return *this;
    }
    MediumKnob& reset(float value) {
        reset_ = value;
        return *this;
    }
    MediumKnob& sensitivity(float value) {
        sensitivity_ = value;
        return *this;
    }
    MediumKnob& visual_center(float value) {
        centered_ = true;
        visual_center_ = value;
        return *this;
    }
    MediumKnob& tip(const char* text) {
        tip_ = text;
        return *this;
    }
    MediumKnob& enabled(bool on) {
        enabled_ = on;
        return *this;
    }
    MediumKnob& column(float width) {
        column_ = width;
        return *this;
    }
    MediumKnob& caption(Caption write) {
        caption_ = write;
        return *this;
    }
    bool draw(App& app) const;

private:
    const char* id_;
    float* value_;
    float min_ = 0.f;
    float max_ = 1.f;
    float reset_ = 0.f;
    float sensitivity_ = 0.45f;
    bool centered_ = false;
    float visual_center_ = 0.f;
    const char* tip_ = nullptr;
    bool enabled_ = true;
    float column_ = 0.f;
    Caption caption_ = nullptr;
};

class LevelMeter {
public:
    static float bar_width();
    static float width();
    static void draw(float x, float y, float height, float left, float right);
};

class ProgressBar {
public:
    static float default_height();

    ProgressBar(const char* id, float fraction) : id_(id), fraction_(fraction) {}
    ProgressBar& size(float width, float height) {
        width_ = width;
        height_ = height;
        return *this;
    }
    ProgressBar& overlay(const char* text) {
        overlay_ = text == nullptr ? "" : text;
        return *this;
    }
    void draw() const;

private:
    const char* id_;
    float fraction_ = 0.f;
    float width_ = -1.f;
    float height_ = 0.f;
    const char* overlay_ = "";
};

class SideRow {
public:
    enum class Dock { Left, Right };

    explicit SideRow(Dock dock) : dock_(dock) {}
    SideRow& margin(float value) {
        margin_ = value;
        return *this;
    }
    template <class Draw>
    SideRow& item(float width, float height, Draw&& draw) {
        return item(width, height, -1.f, std::forward<Draw>(draw));
    }
    template <class Draw>
    SideRow& item(float width, float height, float gap, Draw&& draw) {
        entries_.push_back(Entry{width, height, gap, std::function<void()>(std::forward<Draw>(draw))});
        return *this;
    }
    float commit(float y);

private:
    struct Entry {
        float width = 0.f;
        float height = 0.f;
        float gap = -1.f;
        std::function<void()> draw;
    };
    Dock dock_;
    float margin_ = 0.f;
    std::vector<Entry> entries_;
};

struct MidiNote {
    int pitch = 0;
    double start = 0;
    double end = 0;
    int velocity = 100;
};

enum class NoteStatus { Idle, Running, Ready, Failed };

struct PitchDraft {
    float onset = 0.2f;
    float frame = 0.2f;
    int min_ms = 128;
    int min_hz = 0;
    int max_hz = 0;
    bool melodia = false;
};

struct ProjectItem {
    std::string name;
    double seconds = 0;
    std::string hash;
    std::filesystem::path directory;
    std::filesystem::path source;
    std::string model_key;
    std::string device;
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
    std::array<float, bt::kStemCount> meter_l{};
    std::array<float, bt::kStemCount> meter_r{};
    float master_meter_l = 0.f;
    float master_meter_r = 0.f;
    float speed = 1.f;
    float applied_speed = 1.f;
    float seek_step = 5.f;
    float master_db = 0.f;
    bool metro = true;
    float bpm = 120.f;
    double beat_offset = 0;
    bool beat_refresh = false;
    std::shared_ptr<const std::vector<double>> beat_times;
    BeatService* beat_service = nullptr;
    std::uint64_t beat_song = 0;
    std::uint64_t beat_ticket = 0;
    bool beat_ready = false;
    bool beat_pending = false;
    std::string beat_detail;
    float click_db = 0.f;
    double metro_seen = -1;
    double beat_flash_at = -1;
    bool beat_flash_down = false;
    std::uint64_t seen_revision = 0;
    int selected = 4;
    MidiView midi_view = MidiView::Guitar;
    int fret_count = 22;
    int fret_bright = 100;
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
    bool pitch_mask = false;
    float hand_center = 5.f;
    double midi_drag_origin = 0;
    float midi_drag_y = 0.f;
    std::array<std::vector<MidiNote>, bt::kStemCount> notes{};
    NoteService* note_service = nullptr;
    std::array<NoteStatus, bt::kStemCount> note_status{};
    std::array<std::string, bt::kStemCount> note_detail{};
    std::uint64_t note_song = 0;
    std::array<std::uint64_t, bt::kStemCount> note_ticket{};
    std::array<bool, bt::kStemCount> note_refresh{};
    float note_progress = 0.f;
    bool pitch_ready = false;
    std::string pitch_note;
    std::filesystem::path pitch_model_path;
    int note_workers = 4;
    int demucs_threads = 4;
    Keymap keymap = default_keymap();
    int keymap_capture = -1;
    int language = 0;
    bool language_loaded = false;
    int wave_drag = -1;
    int loop_drag = -1;
    bool loop_is_a = true;
    bool loop_paint = false;
    bool loop_snap = false;
    bool count_in = false;
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
    int mixer_scroll = 0;
    int output_device = 0;
    std::string output_id;
    double outputs_at = -1;
    std::vector<OutputChoice> outputs;
    bool monitor_open = false;
    bool fold_pitch = false;
    bool fold_look = false;
    bool fold_range = false;
    bool fold_hand = false;
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

bool edit_loop_begin(
    App& app, const bt::Player::Snapshot& transport, double seconds, double length, float pointer, float a_at, float b_at, float grab);
void edit_loop_drag(App& app, double seconds, double length);

void draw(App& app);
void load_output(App& app);
void load_performance(App& app);
void save_performance(const App& app);
bool interface_locked(const App& app);
std::optional<std::filesystem::path> pick_audio(HWND__* hwnd);
std::vector<char> read_binary(const std::filesystem::path& path);

}  // namespace btapp
