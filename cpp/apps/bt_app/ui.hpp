#pragma once

#include "app_state.hpp"

#include "imgui.h"

#include <functional>
#include <optional>
#include <string>
#include <vector>

namespace btapp {

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
    Positions,
    Suggest,
    Grid,
    Detect,
    Again,
    Rerun,
    Magnet,
    Count,
};

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

class LargeKnob {
public:
    using Caption = void (*)(float value, char* text, std::size_t size);

    static float diameter();
    static float height();
    static float column_width(const char* sample);

    LargeKnob(const char* id, float* value) : id_(id), value_(value) {}
    LargeKnob& range(float min_value, float max_value) {
        min_ = min_value;
        max_ = max_value;
        return *this;
    }
    LargeKnob& reset(float value) {
        reset_ = value;
        return *this;
    }
    LargeKnob& sensitivity(float value) {
        sensitivity_ = value;
        return *this;
    }
    LargeKnob& visual_center(float value) {
        centered_ = true;
        visual_center_ = value;
        return *this;
    }
    LargeKnob& tip(const char* text) {
        tip_ = text;
        return *this;
    }
    LargeKnob& enabled(bool on) {
        enabled_ = on;
        return *this;
    }
    LargeKnob& column(float width) {
        column_ = width;
        return *this;
    }
    LargeKnob& caption(Caption write) {
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
    ProgressBar& fill(ImU32 color) {
        fill_ = color;
        return *this;
    }
    void draw() const;

private:
    const char* id_;
    float fraction_ = 0.f;
    float width_ = -1.f;
    float height_ = 0.f;
    const char* overlay_ = "";
    ImU32 fill_ = IM_COL32(242, 242, 242, 255);
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

void draw(App& app);
void load_language(App& app);
void load_output(App& app);
bool interface_locked(const App& app);
std::optional<std::filesystem::path> pick_audio(HWND__* hwnd);
std::vector<char> read_binary(const std::filesystem::path& path);

}  // namespace btapp
