#include "ui.hpp"

#include "i18n.hpp"
#include "icons.hpp"
#include "transport.hpp"

#include "beats.hpp"
#include "keys.hpp"
#include "midi_view.hpp"
#include "monitor.hpp"
#include "notes.hpp"
#include "separate.hpp"

#include "bt/project.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <exception>
#include <fstream>
#include <utility>

#include "imgui.h"
#include "imgui_internal.h"

namespace ImGui {
IMGUI_API void BeginDisabledOverrideReenable();
IMGUI_API void EndDisabledOverrideReenable();
}

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <shobjidl.h>

namespace btapp {

void load_output(App& app);
void save_output(const App& app);
void save_language(const App& app);

void set_language(App& app, int language) {
    app.language = language < 0 ? 0 : (language > 2 ? 2 : language);
    i18n_set_language(app.language);
}

void load_language(App& app) {
    if (app.language_loaded) {
        return;
    }
    app.language_loaded = true;
    std::ifstream input(app.cache_directory / "ui-language.txt");
    std::string token;
    input >> token;
    if (token == "zh-Hans") {
        set_language(app, 1);
    } else if (token == "en") {
        set_language(app, 2);
    } else {
        set_language(app, 0);
    }
}

void save_language(const App& app) {
    if (app.cache_directory.empty()) {
        return;
    }
    std::error_code error;
    std::filesystem::create_directories(app.cache_directory, error);
    std::ofstream output(app.cache_directory / "ui-language.txt", std::ios::trunc);
    const char* name = app.language == 1 ? "zh-Hans" : app.language == 2 ? "en" : "zh-Hant";
    output << name << '\n';
}

void save_performance(const App& app) {
    if (app.cache_directory.empty()) {
        return;
    }
    std::error_code error;
    std::filesystem::create_directories(app.cache_directory, error);
    std::ofstream output(app.cache_directory / "performance.txt", std::ios::trunc);
    if (!output) {
        return;
    }
    output << "note_workers=" << app.note_workers << '\n';
    output << "demucs_threads=" << app.demucs_threads << '\n';
    output << "seek_step=" << static_cast<int>(std::lround(app.seek_step)) << '\n';
    const std::string model = app.separation_model.empty() ? std::string(bt::kModelKey) : app.separation_model;
    const char* device = app.separation_preference == 1 ? "cpu" : app.separation_preference == 2 ? "cuda" : "auto";
    output << "separation_model=" << model << '\n';
    output << "separation_device=" << device << '\n';
}

void load_performance(App& app) {
    if (app.cache_directory.empty()) {
        return;
    }
    std::ifstream input(app.cache_directory / "performance.txt");
    std::string line;
    while (std::getline(input, line)) {
        const auto eq = line.find('=');
        if (eq == std::string::npos) {
            continue;
        }
        const std::string key = line.substr(0, eq);
        std::string raw = line.substr(eq + 1);
        if (!raw.empty() && raw.back() == '\r') {
            raw.pop_back();
        }
        const int value = std::atoi(raw.c_str());
        if (key == "note_workers") {
            app.note_workers = std::clamp(value, 1, 8);
        } else if (key == "demucs_threads") {
            app.demucs_threads = std::clamp(value, 1, 16);
        } else if (key == "seek_step") {
            app.seek_step = static_cast<float>(std::clamp(value, 1, 20));
        } else if (key == "separation_model" && bt::is_separation_key(raw)) {
            app.separation_model = raw;
        } else if (key == "separation_device") {
            app.separation_preference = raw == "cpu" ? 1 : raw == "cuda" ? 2 : 0;
        }
    }
}

namespace {

constexpr int kMixDrag = 6;
constexpr int kMeterSegments = 24;

constexpr ImU32 kStemColor[bt::kStemCount] = {
    IM_COL32(196, 104, 71, 255),
    IM_COL32(88, 121, 198, 255),
    IM_COL32(142, 148, 156, 255),
    IM_COL32(200, 168, 90, 255),
    IM_COL32(80, 157, 115, 255),
    IM_COL32(95, 179, 190, 255),
};
constexpr ImU32 kStemInk[bt::kStemCount] = {
    IM_COL32(26, 18, 14, 255),
    IM_COL32(244, 247, 255, 255),
    IM_COL32(28, 30, 34, 255),
    IM_COL32(28, 22, 8, 255),
    IM_COL32(7, 20, 13, 255),
    IM_COL32(6, 32, 36, 255),
};

constexpr ImU32 kHi = IM_COL32(242, 242, 242, 255);
constexpr ImU32 kHiInk = IM_COL32(26, 26, 26, 255);
constexpr ImU32 kWaveColor = IM_COL32(106, 173, 212, 255);

float ui_scale() {
    const float scale = ImGui::GetIO().FontGlobalScale;
    return scale > 0.f ? scale : 1.f;
}

float gain_to_db(float gain) {
    if (!(gain > 1.0e-4f)) {
        return -60.f;
    }
    return std::clamp(20.f * std::log10(gain), -60.f, 6.f);
}

float db_to_gain(float db) {
    return std::pow(10.f, db / 20.f);
}

float snap_value(float value, float min_value, float max_value, float step) {
    const float steps = std::round((value - min_value) / step);
    return std::clamp(min_value + steps * step, min_value, max_value);
}

void pan_gains(float pan, float& left, float& right) {
    const float theta = (std::clamp(pan, -1.f, 1.f) + 1.f) * 0.78539816339f;
    left = std::cos(theta) * 1.41421356237f;
    right = std::sin(theta) * 1.41421356237f;
}

void format_time(double seconds, char* text, std::size_t size) {
    if (!(seconds > 0)) {
        seconds = 0;
    }
    const int rounded = static_cast<int>(seconds + 0.5);
    const int hours = rounded / 3600;
    const int minutes = (rounded / 60) % 60;
    const int secs = rounded % 60;
    if (hours > 0) {
        std::snprintf(text, size, "%d:%02d:%02d", hours, minutes, secs);
    } else {
        std::snprintf(text, size, "%d:%02d", minutes, secs);
    }
}

void format_db(float db, char* text, std::size_t size) {
    const int rounded = static_cast<int>(std::lround(db));
    std::snprintf(text, size, "%s%d dB", rounded > 0 ? "+" : "", rounded);
}

void format_pan(float pan, char* text, std::size_t size) {
    const int rounded = static_cast<int>(std::lround(pan * 100.f));
    if (rounded == 0) {
        std::snprintf(text, size, "C");
        return;
    }
    std::snprintf(text, size, "%s %d", rounded < 0 ? "L" : "R", std::abs(rounded));
}

const char* phase_text(bt::SongPhase phase, int /*language*/) {
    switch (phase) {
    case bt::SongPhase::Empty:
        return "";
    case bt::SongPhase::Decoding:
        return t("phase.decoding");
    case bt::SongPhase::Separating:
        return t("phase.separating");
    case bt::SongPhase::Ready:
        return "";
    case bt::SongPhase::Cancelled:
        return t("phase.cancelled");
    case bt::SongPhase::Failed:
        return t("phase.failed");
    }
    return "";
}

void refresh_outputs(App& app) {
    const double now = ImGui::GetTime();
    const bool due = app.outputs_at < 0 || now - app.outputs_at >= 1.0;
    if (due && app.player != nullptr) {
        app.outputs_at = now;
        std::vector<OutputChoice> next;
        next.push_back(OutputChoice{"", t("device_default")});
        for (const bt::PlaybackDevice& device : app.player->playback_devices()) {
            OutputChoice choice;
            choice.id = device.id;
            choice.name = device.name.empty() ? device.id : device.name;
            next.push_back(std::move(choice));
        }
        app.outputs = std::move(next);
        if (!app.output_id.empty()) {
            bool found = false;
            for (const OutputChoice& choice : app.outputs) {
                if (choice.id == app.output_id) {
                    found = true;
                    break;
                }
            }
            if (!found) {
                app.output_id.clear();
                if (auto switched = app.player->use_device(""); !switched) {
                    app.device_note = switched.error();
                }
            }
        }
    } else if (app.outputs.empty()) {
        app.outputs.push_back(OutputChoice{"", t("device_default")});
    } else {
        app.outputs.front().name = t("device_default");
    }
    app.output_device = 0;
    for (int index = 0; index < static_cast<int>(app.outputs.size()); ++index) {
        if (app.outputs[static_cast<std::size_t>(index)].id == app.output_id) {
            app.output_device = index;
            break;
        }
    }
}

void build_peaks(const bt::Audio& mix, std::vector<Peak>& peaks, float& peak_scale) {
    constexpr int kBuckets = 1024;
    peaks.assign(kBuckets, {});
    peak_scale = 0.05f;
    if (mix.frames <= 0 || mix.channels <= 0) {
        return;
    }
    for (int bucket = 0; bucket < kBuckets; ++bucket) {
        const auto begin = mix.frames * bucket / kBuckets;
        const auto end = std::max(begin + 1, mix.frames * (bucket + 1) / kBuckets);
        float low = 0.f;
        float high = 0.f;
        for (std::int64_t frame = begin; frame < end && frame < mix.frames; ++frame) {
            float value = 0.f;
            for (int channel = 0; channel < mix.channels; ++channel) {
                value += mix.samples[static_cast<std::size_t>(channel * mix.frames + frame)];
            }
            value /= static_cast<float>(mix.channels);
            low = std::min(low, value);
            high = std::max(high, value);
        }
        peaks[static_cast<std::size_t>(bucket)] = {low, high};
        peak_scale = std::max(peak_scale, std::max(std::fabs(low), std::fabs(high)));
    }
}

void build_stem_peaks(const bt::Stems& stems, int stem, std::vector<Peak>& peaks, float& peak_scale) {
    constexpr int kBuckets = 512;
    peaks.assign(kBuckets, {});
    peak_scale = 0.05f;
    const auto& audio = stems.audio[static_cast<std::size_t>(stem)];
    if (stems.frames <= 0 || audio.size() < static_cast<std::size_t>(stems.frames * 2)) {
        return;
    }
    for (int bucket = 0; bucket < kBuckets; ++bucket) {
        const auto begin = stems.frames * bucket / kBuckets;
        const auto end = std::max(begin + 1, stems.frames * (bucket + 1) / kBuckets);
        float low = 0.f;
        float high = 0.f;
        for (std::int64_t frame = begin; frame < end && frame < stems.frames; ++frame) {
            const float left = audio[static_cast<std::size_t>(frame)];
            const float right = audio[static_cast<std::size_t>(stems.frames + frame)];
            const float value = (left + right) * 0.5f;
            low = std::min(low, value);
            high = std::max(high, value);
        }
        peaks[static_cast<std::size_t>(bucket)] = {low, high};
        peak_scale = std::max(peak_scale, std::max(std::fabs(low), std::fabs(high)));
    }
}

float song_wave_scale(const App& app) {
    float shared = std::max(app.peak_scale, 0.05f);
    for (float scale : app.stem_peak_scale) {
        shared = std::max(shared, scale);
    }
    return shared;
}

float channel_peak(const std::vector<float>& audio, std::int64_t frames, int channel, std::int64_t center) {
    if (frames <= 0 || audio.size() < static_cast<std::size_t>(frames * 2)) {
        return 0.f;
    }
    const std::int64_t begin = std::max<std::int64_t>(0, center - 256);
    const std::int64_t end = std::min(frames, center + 256);
    const float* samples = audio.data() + static_cast<std::size_t>(channel) * static_cast<std::size_t>(frames);
    float peak = 0.f;
    for (std::int64_t frame = begin; frame < end; ++frame) {
        peak = std::max(peak, std::fabs(samples[frame]));
    }
    return peak;
}

bool stem_audible(const bt::SongSnapshot& song, int stem) {
    bool any_solo = false;
    for (bool solo : song.solo) {
        any_solo = any_solo || solo;
    }
    return !song.mute[static_cast<std::size_t>(stem)] &&
           (!any_solo || song.solo[static_cast<std::size_t>(stem)]);
}

void update_meters(App& app, const bt::SongSnapshot& song, const bt::Player::Snapshot& transport) {
    const float master = db_to_gain(transport.master_db);
    const auto center = song.stems == nullptr
                            ? 0
                            : static_cast<std::int64_t>(transport.position * static_cast<double>(bt::kSampleRate));
    for (int stem = 0; stem < bt::kStemCount; ++stem) {
        float left = 0.f;
        float right = 0.f;
        if (transport.playing && song.stems && stem_audible(song, stem)) {
            const auto& audio = song.stems->audio[static_cast<std::size_t>(stem)];
            float pan_l = 1.f;
            float pan_r = 1.f;
            pan_gains(song.pan[static_cast<std::size_t>(stem)], pan_l, pan_r);
            const float gain = song.gain[static_cast<std::size_t>(stem)] * master;
            left = channel_peak(audio, song.stems->frames, 0, center) * gain * pan_l;
            right = channel_peak(audio, song.stems->frames, 1, center) * gain * pan_r;
        }
        auto follow = [](float current, float target) {
            return current + (target - current) * (target > current ? 0.45f : 0.12f);
        };
        app.meter_l[static_cast<std::size_t>(stem)] =
            follow(app.meter_l[static_cast<std::size_t>(stem)], std::clamp(left, 0.f, 1.f));
        app.meter_r[static_cast<std::size_t>(stem)] =
            follow(app.meter_r[static_cast<std::size_t>(stem)], std::clamp(right, 0.f, 1.f));
    }
    float mix_left = 0.f;
    float mix_right = 0.f;
    if (transport.playing && song.stems != nullptr && song.stems->frames > 0) {
        const auto frames = song.stems->frames;
        const std::int64_t begin = std::max<std::int64_t>(0, center - 256);
        const std::int64_t end = std::min(frames, center + 256);
        for (std::int64_t frame = begin; frame < end; ++frame) {
            float left = 0.f;
            float right = 0.f;
            for (int stem = 0; stem < bt::kStemCount; ++stem) {
                if (!stem_audible(song, stem)) {
                    continue;
                }
                const auto& audio = song.stems->audio[static_cast<std::size_t>(stem)];
                if (audio.size() < static_cast<std::size_t>(frames * 2)) {
                    continue;
                }
                float pan_l = 1.f;
                float pan_r = 1.f;
                pan_gains(song.pan[static_cast<std::size_t>(stem)], pan_l, pan_r);
                const float gain = song.gain[static_cast<std::size_t>(stem)] * master;
                left += audio[static_cast<std::size_t>(frame)] * gain * pan_l;
                right += audio[static_cast<std::size_t>(frames + frame)] * gain * pan_r;
            }
            mix_left = std::max(mix_left, std::fabs(left));
            mix_right = std::max(mix_right, std::fabs(right));
        }
    }
    auto follow = [](float current, float target) {
        return current + (target - current) * (target > current ? 0.45f : 0.12f);
    };
    app.master_meter_l = follow(app.master_meter_l, std::clamp(mix_left, 0.f, 1.f));
    app.master_meter_r = follow(app.master_meter_r, std::clamp(mix_right, 0.f, 1.f));
}

void draw_icon(ImDrawList* draw, Icon icon, ImVec2 min, ImVec2 max, ImU32 color) {
    // Atlas glyphs are drawn at 2/3 of the button inset (shrink by 1/3 vs full fill).
    const float cx = (min.x + max.x) * 0.5f;
    const float cy = (min.y + max.y) * 0.5f;
    const float hw = (max.x - min.x) * (1.f / 3.f);
    const float hh = (max.y - min.y) * (1.f / 3.f);
    if (draw_icon_atlas(draw, icon, ImVec2(cx - hw, cy - hh), ImVec2(cx + hw, cy + hh), color)) {
        return;
    }
    const ImVec2 center((min.x + max.x) * 0.5f, (min.y + max.y) * 0.5f);
    const float s = (max.y - min.y) * 0.28f;
    switch (icon) {
    case Icon::Play:
        draw->AddTriangleFilled(
            ImVec2(center.x - s * 0.55f, center.y - s), ImVec2(center.x - s * 0.55f, center.y + s),
            ImVec2(center.x + s * 0.9f, center.y), color);
        break;
    case Icon::Pause:
        draw->AddRectFilled(ImVec2(center.x - s * 0.7f, center.y - s), ImVec2(center.x - s * 0.15f, center.y + s), color);
        draw->AddRectFilled(ImVec2(center.x + s * 0.15f, center.y - s), ImVec2(center.x + s * 0.7f, center.y + s), color);
        break;
    case Icon::Stop:
        draw->AddRectFilled(ImVec2(center.x - s * 0.7f, center.y - s * 0.7f), ImVec2(center.x + s * 0.7f, center.y + s * 0.7f), color);
        break;
    case Icon::Loop: {
        // Chamfered square repeat path (matches atlas).
        const float thick = std::max(1.6f, s * 0.2f);
        const float box_w = s * 1.05f;
        const float box_h = s * 0.78f;
        const float ch = s * 0.35f;
        const float l = center.x - box_w;
        const float r = center.x + box_w;
        const float t = center.y - box_h;
        const float btm = center.y + box_h;
        const ImVec2 path[] = {
            {center.x - s * 0.2f, t}, {l + ch, t}, {l, t + ch}, {l, btm - ch}, {l + ch, btm},
            {r - ch, btm}, {r, btm - ch}, {r, t + ch}, {r - ch, t}, {center.x + s * 0.55f, t},
        };
        for (int i = 1; i < 10; ++i) {
            draw->AddLine(path[i - 1], path[i], color, thick);
        }
        draw->AddTriangleFilled(
            ImVec2(center.x - s * 0.35f, t), ImVec2(center.x + s * 0.45f, t - s * 0.5f),
            ImVec2(center.x + s * 0.45f, t + s * 0.5f), color);
        break;
    }
    case Icon::Clear:
        draw->AddLine(ImVec2(center.x - s, center.y - s), ImVec2(center.x + s, center.y + s), color, std::max(1.6f, s * 0.18f));
        draw->AddLine(ImVec2(center.x + s, center.y - s), ImVec2(center.x - s, center.y + s), color, std::max(1.6f, s * 0.18f));
        break;
    case Icon::Speaker:
        draw->AddRectFilled(ImVec2(center.x - s, center.y - s * 0.35f), ImVec2(center.x - s * 0.25f, center.y + s * 0.35f), color);
        draw->AddTriangleFilled(
            ImVec2(center.x - s * 0.25f, center.y - s * 0.35f), ImVec2(center.x - s * 0.25f, center.y + s * 0.35f),
            ImVec2(center.x + s * 0.45f, center.y + s), color);
        draw->PathArcTo(ImVec2(center.x + s * 0.2f, center.y), s * 0.55f, -1.f, 1.f, 6);
        draw->PathStroke(color, 0, std::max(1.3f, s * 0.14f));
        break;
    case Icon::SpeakerOff:
        draw_icon(draw, Icon::Speaker, min, max, color);
        draw->AddLine(ImVec2(center.x + s * 0.15f, center.y - s * 0.7f), ImVec2(center.x + s, center.y + s * 0.55f), color, std::max(1.4f, s * 0.16f));
        break;
    case Icon::Solo:
        draw->PathArcTo(ImVec2(center.x, center.y - s * 0.15f), s * 0.85f, 3.4f, 6.0f, 8);
        draw->PathStroke(color, 0, std::max(1.4f, s * 0.16f));
        draw->AddRectFilled(ImVec2(center.x - s, center.y - s * 0.15f), ImVec2(center.x - s * 0.45f, center.y + s * 0.55f), color);
        draw->AddRectFilled(ImVec2(center.x + s * 0.45f, center.y - s * 0.15f), ImVec2(center.x + s, center.y + s * 0.55f), color);
        break;
    case Icon::Reset:
        draw->PathArcTo(center, s * 0.8f, -2.4f, 2.6f, 10);
        draw->PathStroke(color, 0, std::max(1.4f, s * 0.16f));
        draw->AddTriangleFilled(
            ImVec2(center.x - s * 0.15f, center.y - s * 1.2f), ImVec2(center.x - s * 0.95f, center.y - s * 0.45f),
            ImVec2(center.x - s * 0.05f, center.y - s * 0.25f), color);
        break;
    case Icon::Pencil: {
        const float thick = std::max(1.4f, s * 0.16f);
        draw->AddLine(ImVec2(center.x - s * 0.95f, center.y + s * 0.95f), ImVec2(center.x + s * 0.55f, center.y - s * 0.55f), color, thick);
        draw->AddLine(ImVec2(center.x - s * 0.55f, center.y + s * 0.55f), ImVec2(center.x - s * 0.95f, center.y + s * 0.15f), color, thick);
        draw->AddLine(ImVec2(center.x - s * 0.55f, center.y + s * 0.55f), ImVec2(center.x - s * 0.15f, center.y + s * 0.95f), color, thick);
        draw->AddLine(ImVec2(center.x + s * 0.28f, center.y - s * 0.28f), ImVec2(center.x + s * 0.95f, center.y - s * 0.95f), color, std::max(2.2f, s * 0.32f));
        break;
    }
    case Icon::Trash:
        draw->AddLine(ImVec2(center.x - s, center.y - s * 0.55f), ImVec2(center.x + s, center.y - s * 0.55f), color, std::max(1.4f, s * 0.16f));
        draw->AddLine(ImVec2(center.x - s * 0.45f, center.y - s * 0.55f), ImVec2(center.x - s * 0.45f, center.y - s), color, std::max(1.4f, s * 0.16f));
        draw->AddLine(ImVec2(center.x + s * 0.45f, center.y - s * 0.55f), ImVec2(center.x + s * 0.45f, center.y - s), color, std::max(1.4f, s * 0.16f));
        draw->AddLine(ImVec2(center.x - s * 0.45f, center.y - s), ImVec2(center.x + s * 0.45f, center.y - s), color, std::max(1.4f, s * 0.16f));
        draw->AddLine(ImVec2(center.x - s * 0.75f, center.y - s * 0.35f), ImVec2(center.x - s * 0.55f, center.y + s), color, std::max(1.4f, s * 0.16f));
        draw->AddLine(ImVec2(center.x + s * 0.75f, center.y - s * 0.35f), ImVec2(center.x + s * 0.55f, center.y + s), color, std::max(1.4f, s * 0.16f));
        draw->AddLine(ImVec2(center.x - s * 0.55f, center.y + s), ImVec2(center.x + s * 0.55f, center.y + s), color, std::max(1.4f, s * 0.16f));
        break;
    case Icon::Gear: {
        draw->AddCircle(center, s * 0.38f, color, 12, std::max(1.4f, s * 0.16f));
        for (int tooth = 0; tooth < 8; ++tooth) {
            const float angle = static_cast<float>(tooth) / 8.f * 6.2831853f;
            draw->AddLine(
                ImVec2(center.x + std::cos(angle) * s * 0.5f, center.y + std::sin(angle) * s * 0.5f),
                ImVec2(center.x + std::cos(angle) * s * 0.95f, center.y + std::sin(angle) * s * 0.95f), color,
                std::max(1.5f, s * 0.18f));
        }
        break;
    }
    case Icon::Metro:
        draw->AddQuadFilled(
            ImVec2(center.x - s * 0.45f, center.y - s), ImVec2(center.x + s * 0.45f, center.y - s),
            ImVec2(center.x + s, center.y + s), ImVec2(center.x - s, center.y + s), color);
        draw->AddLine(ImVec2(center.x, center.y + s * 0.7f), ImVec2(center.x + s * 0.55f, center.y - s * 0.85f), IM_COL32(20, 20, 20, 255), std::max(1.4f, s * 0.16f));
        draw->AddCircleFilled(ImVec2(center.x + s * 0.55f, center.y - s * 0.85f), s * 0.22f, color);
        break;
    case Icon::Import: {
        const float thick = std::max(2.f, s * 0.28f);
        draw->AddLine(ImVec2(center.x - s, center.y), ImVec2(center.x + s, center.y), color, thick);
        draw->AddLine(ImVec2(center.x, center.y - s), ImVec2(center.x, center.y + s), color, thick);
        break;
    }
    case Icon::Cancel: {
        const float thick = std::max(1.5f, s * 0.16f);
        draw->AddCircle(center, s * 0.95f, color, 16, thick);
        draw->AddLine(ImVec2(center.x - s * 0.42f, center.y - s * 0.42f), ImVec2(center.x + s * 0.42f, center.y + s * 0.42f), color, thick);
        draw->AddLine(ImVec2(center.x + s * 0.42f, center.y - s * 0.42f), ImVec2(center.x - s * 0.42f, center.y + s * 0.42f), color, thick);
        break;
    }
    case Icon::Monitor: {
        const float thick = std::max(1.4f, s * 0.16f);
        draw->AddRect(ImVec2(center.x - s, center.y - s * 0.72f), ImVec2(center.x + s, center.y + s * 0.72f), color, s * 0.12f, 0, thick);
        draw->AddLine(ImVec2(center.x - s * 0.7f, center.y), ImVec2(center.x - s * 0.3f, center.y), color, thick);
        draw->AddLine(ImVec2(center.x - s * 0.3f, center.y), ImVec2(center.x - s * 0.05f, center.y - s * 0.42f), color, thick);
        draw->AddLine(ImVec2(center.x - s * 0.05f, center.y - s * 0.42f), ImVec2(center.x + s * 0.2f, center.y + s * 0.42f), color, thick);
        draw->AddLine(ImVec2(center.x + s * 0.2f, center.y + s * 0.42f), ImVec2(center.x + s * 0.42f, center.y), color, thick);
        draw->AddLine(ImVec2(center.x + s * 0.42f, center.y), ImVec2(center.x + s * 0.7f, center.y), color, thick);
        break;
    }
    case Icon::Guitar: {
        const float thick = std::max(1.4f, s * 0.14f);
        draw->AddCircle(ImVec2(center.x, center.y + s * 0.38f), s * 0.58f, color, 16, thick);
        draw->AddCircle(ImVec2(center.x, center.y + s * 0.42f), s * 0.16f, color, 10, thick);
        draw->AddLine(ImVec2(center.x, center.y - s * 0.1f), ImVec2(center.x, center.y - s), color, thick);
        draw->AddLine(ImVec2(center.x - s * 0.32f, center.y - s), ImVec2(center.x + s * 0.32f, center.y - s), color, thick);
        break;
    }
    case Icon::Bass: {
        const float thick = std::max(1.4f, s * 0.14f);
        draw->AddCircle(ImVec2(center.x, center.y + s * 0.55f), s * 0.42f, color, 16, thick);
        draw->AddLine(ImVec2(center.x, center.y + s * 0.15f), ImVec2(center.x, center.y - s * 0.72f), color, std::max(2.f, s * 0.22f));
        draw->AddLine(ImVec2(center.x - s * 0.42f, center.y - s * 0.85f), ImVec2(center.x + s * 0.42f, center.y - s * 0.85f), color, thick);
        draw->AddCircleFilled(ImVec2(center.x - s * 0.22f, center.y - s * 0.85f), s * 0.1f, color);
        draw->AddCircleFilled(ImVec2(center.x + s * 0.22f, center.y - s * 0.85f), s * 0.1f, color);
        break;
    }
    case Icon::Positions:
        for (int row = 0; row < 2; ++row) {
            for (int col = 0; col < 3; ++col) {
                const float x = center.x - s * 0.8f + static_cast<float>(col) * s * 0.62f;
                const float y = center.y - s * 0.62f + static_cast<float>(row) * s * 0.68f;
                draw->AddRectFilled(ImVec2(x, y), ImVec2(x + s * 0.4f, y + s * 0.4f), color);
            }
        }
        break;
    case Icon::Suggest: {
        const float thick = std::max(1.3f, s * 0.14f);
        for (int col = 0; col < 4; ++col) {
            const float x = center.x - s + static_cast<float>(col) * s * 0.52f;
            const ImVec2 box_min(x, center.y - s * 0.28f);
            const ImVec2 box_max(x + s * 0.36f, center.y + s * 0.28f);
            if (col == 1) {
                draw->AddRectFilled(box_min, box_max, color);
            } else {
                draw->AddRect(box_min, box_max, color, 0.f, 0, thick);
            }
        }
        break;
    }
    case Icon::Grid: {
        const float thick = std::max(1.4f, s * 0.16f);
        draw->AddLine(ImVec2(center.x - s, center.y + s * 0.65f), ImVec2(center.x + s, center.y + s * 0.65f), color, thick);
        for (int tick = 0; tick < 4; ++tick) {
            const float x = center.x - s * 0.75f + static_cast<float>(tick) * s * 0.5f;
            const float top = tick == 0 ? center.y - s : center.y - s * 0.25f;
            draw->AddLine(ImVec2(x, top), ImVec2(x, center.y + s * 0.65f), color, thick);
        }
        break;
    }
    case Icon::Detect: {
        const float thick = std::max(1.4f, s * 0.16f);
        draw->AddLine(ImVec2(center.x - s, center.y + s * 0.65f), ImVec2(center.x + s, center.y + s * 0.65f), color, thick);
        const float marks[] = {-0.85f, -0.25f, 0.15f, 0.7f};
        for (int tick = 0; tick < 4; ++tick) {
            const float x = center.x + s * marks[tick];
            const float top = tick == 0 ? center.y - s : center.y - s * 0.25f;
            draw->AddLine(ImVec2(x, top), ImVec2(x, center.y + s * 0.65f), color, thick);
        }
        break;
    }
    case Icon::Again: {
        const float thick = std::max(1.4f, s * 0.16f);
        draw->PathArcTo(center, s * 0.72f, 0.6f, 5.4f, 12);
        draw->PathStroke(color, 0, thick);
        draw->AddTriangleFilled(
            ImVec2(center.x + s * 0.15f, center.y - s * 1.05f), ImVec2(center.x + s * 0.95f, center.y - s * 0.35f),
            ImVec2(center.x + s * 0.1f, center.y - s * 0.15f), color);
        break;
    }
    case Icon::Rerun: {
        const float thick = std::max(1.4f, s * 0.16f);
        draw->PathArcTo(center, s * 0.88f, -2.3f, 2.2f, 12);
        draw->PathStroke(color, 0, thick);
        draw->AddTriangleFilled(
            ImVec2(center.x - s * 0.32f, center.y - s * 0.42f), ImVec2(center.x - s * 0.32f, center.y + s * 0.42f),
            ImVec2(center.x + s * 0.42f, center.y), color);
        break;
    }
    case Icon::Magnet: {
        const float thick = std::max(1.8f, s * 0.28f);
        const float radius = s * 0.58f;
        const ImVec2 arc(center.x, center.y - s * 0.18f);
        draw->PathArcTo(arc, radius, 3.14159265f, 6.2831853f, 14);
        draw->PathStroke(color, 0, thick);
        const float leg_bot = arc.y + s * 0.78f;
        const float left = arc.x - radius;
        const float right = arc.x + radius;
        draw->AddLine(ImVec2(left, arc.y), ImVec2(left, leg_bot), color, thick);
        draw->AddLine(ImVec2(right, arc.y), ImVec2(right, leg_bot), color, thick);
        const float pole_h = std::max(2.2f, s * 0.28f);
        const float pole_w = thick * 1.45f;
        draw->AddRectFilled(ImVec2(left - pole_w * 0.5f, leg_bot - pole_h), ImVec2(left + pole_w * 0.5f, leg_bot), color);
        draw->AddRectFilled(ImVec2(right - pole_w * 0.5f, leg_bot - pole_h), ImVec2(right + pole_w * 0.5f, leg_bot), color);
        break;
    }
    case Icon::Count: {
        const float thick = std::max(1.6f, s * 0.16f);
        draw->AddCircle(center, s * 0.82f, color, 20, thick);
        draw->AddLine(center, ImVec2(center.x, center.y - s * 0.46f), color, thick);
        draw->AddLine(center, ImVec2(center.x + s * 0.36f, center.y + s * 0.14f), color, thick);
        draw->AddCircleFilled(center, std::max(1.4f, s * 0.12f), color);
        break;
    }
    }
}

bool item_locked() {
    return (ImGui::GetCurrentContext()->CurrentItemFlags & ImGuiItemFlags_Disabled) != 0;
}

bool control_disabled(bool enabled) {
    return !enabled || item_locked();
}

ImU32 paint(ImU32 color, bool disabled) {
    if (!disabled) {
        return color;
    }
    ImVec4 value = ImGui::ColorConvertU32ToFloat4(color);
    value.w *= ImGui::GetStyle().DisabledAlpha;
    return ImGui::ColorConvertFloat4ToU32(value);
}

float drag_sensitivity(float min_value, float max_value) {
    return std::max(max_value - min_value, 0.0001f) / 150.f;
}

// Map 0..1 onto a 270° sweep with the gap at the bottom (FL-style).
float knob_angle(float norm) {
    return -1.5707963f + (std::clamp(norm, 0.f, 1.f) - 0.5f) * 4.712389f;
}

float knob_norm(float value, float min_value, float max_value, float visual_center) {
    const float span = std::max(max_value - min_value, 0.0001f);
    float norm = std::clamp((value - min_value) / span, 0.f, 1.f);
    if (visual_center > min_value && visual_center < max_value) {
        if (value <= visual_center) {
            norm = 0.5f * std::clamp((value - min_value) / (visual_center - min_value), 0.f, 1.f);
        } else {
            norm = 0.5f + 0.5f * std::clamp((value - visual_center) / (max_value - visual_center), 0.f, 1.f);
        }
    }
    return norm;
}

bool knob(
    App& app, const char* id, float* value, float min_value, float max_value, float reset, float sensitivity, const char* tip,
    float visual_center = NAN, float diameter = 0.f, bool enabled = true) {
    ImGui::PushID(id);
    const float size = diameter > 0.f ? diameter : 36.f * ui_scale();
    const ImVec2 origin = ImGui::GetCursorScreenPos();
    ImGui::InvisibleButton("knob", ImVec2(size, size));
    const bool disabled = control_disabled(enabled);
    const bool hovered = !disabled && ImGui::IsItemHovered();
    if (hovered && tip != nullptr) {
        ImGui::SetTooltip("%s", tip);
    }
    bool changed = false;
    if (!disabled && ImGui::IsItemActivated()) {
        app.widget_drag = ImGui::GetItemID();
        app.widget_origin = *value;
        app.widget_anchor = ImGui::GetIO().MousePos.y;
    }
    if (!disabled && ImGui::IsItemActive() && app.widget_drag == ImGui::GetItemID()) {
        const float next = std::clamp(app.widget_origin + (app.widget_anchor - ImGui::GetIO().MousePos.y) * sensitivity / ui_scale(), min_value, max_value);
        if (next != *value) {
            *value = next;
            changed = true;
        }
    }
    if (!disabled && hovered && (ImGui::IsMouseDoubleClicked(ImGuiMouseButton_Left) || ImGui::IsMouseClicked(ImGuiMouseButton_Right))) {
        if (*value != reset) {
            *value = reset;
            changed = true;
        }
    }

    ImDrawList* draw = ImGui::GetWindowDrawList();
    const ImVec2 center(origin.x + size * 0.5f, origin.y + size * 0.5f);
    const bool bipolar = visual_center > min_value && visual_center < max_value;
    const float norm = knob_norm(*value, min_value, max_value, visual_center);
    const float reset_norm = knob_norm(reset, min_value, max_value, visual_center);
    const float a0 = knob_angle(0.f);
    const float a1 = knob_angle(1.f);
    const float a_val = knob_angle(norm);
    const float a_reset = knob_angle(reset_norm);
    const float a_mid = knob_angle(0.5f);

    // Outer ring track + value arc (not a linear bar under the knob).
    const float track_r = size * 0.44f;
    const float face_r = size * 0.30f;
    const float track_thick = std::max(2.2f * ui_scale(), size * 0.075f);
    const int arc_segments = std::max(24, static_cast<int>(size * 0.9f));
    const ImU32 track_col = paint(IM_COL32(36, 36, 36, 255), disabled);
    const ImU32 fill_col = paint(hovered ? IM_COL32(230, 230, 230, 255) : IM_COL32(200, 200, 200, 255), disabled);
    const ImU32 face_col = paint(IM_COL32(52, 52, 52, 255), disabled);
    const ImU32 face_edge = paint(IM_COL32(18, 18, 18, 255), disabled);
    const ImU32 needle_col = paint(IM_COL32(242, 242, 242, 255), disabled);
    const ImU32 reset_col = paint(IM_COL32(160, 160, 160, 255), disabled);

    draw->PathClear();
    draw->PathArcTo(center, track_r, a0, a1, arc_segments);
    draw->PathStroke(track_col, ImDrawFlags_None, track_thick);

    if (bipolar) {
        if (std::fabs(norm - 0.5f) > 0.002f) {
            const float lo = std::min(a_mid, a_val);
            const float hi = std::max(a_mid, a_val);
            draw->PathClear();
            draw->PathArcTo(center, track_r, lo, hi, arc_segments);
            draw->PathStroke(fill_col, ImDrawFlags_None, track_thick);
        }
    } else if (norm > 0.002f) {
        draw->PathClear();
        draw->PathArcTo(center, track_r, a0, a_val, arc_segments);
        draw->PathStroke(fill_col, ImDrawFlags_None, track_thick);
    }

    // Reset tick overlaps the scale ring (radial mark through the track).
    const float tick_half = track_thick * 0.85f;
    const float tick_w = std::max(1.4f * ui_scale(), size * 0.035f);
    const float cos_r = std::cos(a_reset);
    const float sin_r = std::sin(a_reset);
    draw->AddLine(
        ImVec2(center.x + cos_r * (track_r - tick_half), center.y + sin_r * (track_r - tick_half)),
        ImVec2(center.x + cos_r * (track_r + tick_half), center.y + sin_r * (track_r + tick_half)),
        reset_col, tick_w);

    // Cap body + needle.
    draw->AddCircleFilled(center, face_r, face_col, 28);
    draw->AddCircle(center, face_r, face_edge, 28, 1.5f * ui_scale());
    const float needle_len = face_r * 0.78f;
    draw->AddLine(
        center,
        ImVec2(center.x + std::cos(a_val) * needle_len, center.y + std::sin(a_val) * needle_len), needle_col,
        std::max(1.5f * ui_scale(), size * 0.045f));
    draw->AddCircleFilled(center, std::max(1.4f * ui_scale(), size * 0.04f), needle_col, 10);

    ImGui::PopID();
    return changed;
}

void draw_wave(
    App& app, const char* id, int drag_id, const std::vector<Peak>& peaks, float peak_scale, ImU32 color, ImVec2 size,
    const bt::Player::Snapshot& transport, double length, bool ready, bool scrub) {
    ImGui::PushID(id);
    const ImVec2 origin = ImGui::GetCursorScreenPos();
    ImGui::InvisibleButton("wave", size);
    const bool hovered = !item_locked() && ImGui::IsItemHovered();
    ImDrawList* draw = ImGui::GetWindowDrawList();
    const bool disabled = item_locked();
    const ImVec2 corner(origin.x + size.x, origin.y + size.y);
    draw->AddRectFilled(origin, corner, paint(IM_COL32(20, 20, 20, 255), disabled));
    const float mid = origin.y + size.y * 0.5f;
    if (!peaks.empty() && peak_scale > 0.f) {
        const int buckets = static_cast<int>(peaks.size());
        const int columns = std::max(static_cast<int>(size.x), 1);
        for (int column = 0; column < columns; ++column) {
            const Peak peak = peaks[static_cast<std::size_t>(column * buckets / columns)];
            const float amp = size.y * 0.42f / peak_scale;
            draw->AddLine(
                ImVec2(origin.x + static_cast<float>(column), mid - peak.high * amp),
                ImVec2(origin.x + static_cast<float>(column), mid - peak.low * amp), paint(color, disabled));
        }
    }
    auto fraction_at = [&](float mouse_x) {
        return std::clamp((mouse_x - origin.x) / std::max(size.x, 1.f), 0.f, 1.f);
    };
    auto x_of = [&](double seconds) {
        return origin.x + static_cast<float>(seconds / length) * size.x;
    };
    if (!disabled && scrub && ready && length > 0 && hovered && ImGui::IsMouseClicked(ImGuiMouseButton_Left)) {
        app.wave_drag = drag_id;
        app.loop_drag = -1;
    }
    if (!disabled && app.wave_drag == drag_id) {
        if (!ImGui::IsMouseDown(ImGuiMouseButton_Left)) {
            app.wave_drag = -1;
        } else {
            app.player->seek(static_cast<double>(fraction_at(ImGui::GetIO().MousePos.x)) * length);
        }
    }
    if (!disabled && scrub && ready && length > 0 && hovered && ImGui::IsMouseClicked(ImGuiMouseButton_Right)) {
        const double seconds = static_cast<double>(fraction_at(ImGui::GetIO().MousePos.x)) * length;
        if (edit_loop_begin(
                app, transport, seconds, length, ImGui::GetIO().MousePos.x, x_of(std::max(0.0, transport.loop_a)),
                x_of(std::max(0.0, transport.loop_b)), 36.f * ui_scale())) {
            app.loop_drag = drag_id;
            app.wave_drag = -1;
        }
    }
    if (!disabled && app.loop_drag == drag_id) {
        if (!ImGui::IsMouseDown(ImGuiMouseButton_Right)) {
            app.loop_drag = -1;
        } else if (length > 0) {
            edit_loop_drag(app, static_cast<double>(fraction_at(ImGui::GetIO().MousePos.x)) * length, length);
        }
    }
    if (app.metro && length > 0) {
        each_metro_beat(app, 0, length + 1e-4, [&](double time, bool downbeat) {
            if (time > length) {
                return;
            }
            const float x = x_of(time);
            draw->AddLine(
                ImVec2(x, origin.y), ImVec2(x, corner.y),
                paint(downbeat ? IM_COL32(255, 255, 255, 90) : IM_COL32(255, 255, 255, 32), disabled), downbeat ? 2.f : 1.f);
        });
    }
    if (length > 0) {
        draw_ab_loop(
            draw, ui_scale(), origin, corner, transport.loop_a >= 0, x_of(std::max(0.0, transport.loop_a)), transport.loop_b >= 0,
            x_of(std::max(0.0, transport.loop_b)), false, paint(IM_COL32(242, 242, 242, 40), disabled), paint(kHi, disabled), paint(kHi, disabled),
            paint(kHiInk, disabled), paint(IM_COL32(26, 26, 26, 180), disabled));
    }
    if (ready && length > 0) {
        const float head = x_of(std::clamp(transport.position, 0.0, length));
        draw->AddLine(ImVec2(head, origin.y), ImVec2(head, corner.y), paint(kHi, disabled), 2.f);
    }
    ImGui::PopID();
}

void draw_meter_bars(ImDrawList* draw, ImVec2 origin, float width, float height, float level) {
    const int lit = static_cast<int>(std::lround(std::clamp(level, 0.f, 1.f) * static_cast<float>(kMeterSegments)));
    const float label_h = ImGui::GetTextLineHeight();
    const float meter_h = std::max(8.f, height - label_h);
    const float gap = 1.f;
    const float seg_h = std::max(1.f, (meter_h - gap * static_cast<float>(kMeterSegments - 1)) / static_cast<float>(kMeterSegments));
    for (int segment = 0; segment < kMeterSegments; ++segment) {
        const float y = origin.y + meter_h - (static_cast<float>(segment) + 1.f) * seg_h - static_cast<float>(segment) * gap;
        ImU32 color = paint(IM_COL32(42, 42, 42, 255), item_locked());
        if (segment < lit) {
            const ImU32 lit_color = segment >= 21 ? IM_COL32(197, 92, 85, 255) : segment >= 17 ? IM_COL32(205, 185, 99, 255) : IM_COL32(84, 181, 89, 255);
            color = paint(lit_color, item_locked());
        }
        draw->AddRectFilled(ImVec2(origin.x, y), ImVec2(origin.x + width, y + seg_h), color);
    }
}

float verify_label_width() {
    // Fixed plate width for 原音 / MIDI — not sized from glyph width.
    return 40.f * ui_scale();
}

float verify_submix_width() {
    const float scale = ui_scale();
    const float label_w = verify_label_width();
    const float knob = SmallKnob::diameter();
    const float gap = 4.f * scale;
    const float margin = 2.f * scale; // small pad after solo
    // label | vol + pan + mute + solo | margin
    return label_w + gap + knob * 2.f + SmallButton::width() * 2.f + gap * 3.f + margin;
}

void draw_verify_lane(App& app, App::VerifyLaneUi& lane, App::VerifyLaneUi& other, const char* id, const char* label, float width, float height) {
    ImGui::PushID(id);
    const float scale = ui_scale();
    const ImVec2 origin = ImGui::GetCursorScreenPos();
    const bool disabled = item_locked();
    ImDrawList* draw = ImGui::GetWindowDrawList();
    const ImVec2 corner(origin.x + width, origin.y + height);
    draw->AddRectFilled(origin, corner, paint(IM_COL32(28, 28, 28, 255), disabled));

    const float label_w = verify_label_width();
    draw->AddRectFilled(origin, ImVec2(origin.x + label_w, corner.y), paint(IM_COL32(48, 48, 48, 255), disabled));
    const ImVec2 name_size = ImGui::CalcTextSize(label);
    draw->AddText(
        ImVec2(origin.x + (label_w - name_size.x) * 0.5f, origin.y + (height - name_size.y) * 0.5f),
        paint(IM_COL32(220, 220, 220, 255), disabled), label);

    const float knob_size = std::min(SmallKnob::diameter(), std::max(18.f * scale, height - 4.f * scale));
    const float button_h = SmallButton::height();
    const float gap = 4.f * scale;
    const float cluster_h = std::max(knob_size, button_h);
    const float cluster_x = origin.x + label_w + gap;
    const float cluster_y = origin.y + (height - cluster_h) * 0.5f;
    const float knob_y = cluster_y + (cluster_h - knob_size) * 0.5f;
    const float button_y = cluster_y + (cluster_h - button_h) * 0.5f;

    ImGui::SetCursorScreenPos(ImVec2(cluster_x, knob_y));
    float db = lane.db;
    if (knob(app, "vol", &db, -60.f, 6.f, 0.f, 0.45f, t("master"), NAN, knob_size)) {
        lane.db = db;
    }
    float x = cluster_x + knob_size + gap;
    ImGui::SetCursorScreenPos(ImVec2(x, knob_y));
    float pan = lane.pan;
    if (knob(app, "pan", &pan, -1.f, 1.f, 0.f, 0.012f, "Pan", 0.f, knob_size)) {
        lane.pan = pan;
    }
    x += knob_size + gap;
    ImGui::SetCursorScreenPos(ImVec2(x, button_y));
    if (SmallButton("mute", lane.mute ? Icon::SpeakerOff : Icon::Speaker).engaged(lane.mute).tip(t("mute")).draw()) {
        lane.mute = !lane.mute;
    }
    x += SmallButton::width() + gap;
    ImGui::SetCursorScreenPos(ImVec2(x, button_y));
    if (SmallButton("solo", Icon::Solo).engaged(lane.solo).tip(t("solo")).draw()) {
        lane.solo = !lane.solo;
        if (lane.solo) {
            other.solo = false;
        }
    }
    const bool audible = !lane.mute && (!(lane.solo || other.solo) || lane.solo);
    if (!audible) {
        draw->AddRectFilled(origin, corner, paint(IM_COL32(0, 0, 0, 150), disabled));
    }
    draw->AddRect(origin, corner, paint(IM_COL32(22, 22, 22, 255), disabled));
    ImGui::PopID();
}

void verify_pitch_range(const App& app, int stem, int& lo, int& hi) {
    // Bass / guitar stems use their fret ranges; others use the piano/roll key range.
    if (stem == 1) {
        lo = app.bass_pitch_min;
        hi = app.bass_pitch_max;
    } else if (stem == 4) {
        lo = app.guitar_pitch_min;
        hi = app.guitar_pitch_max;
    } else {
        lo = app.key_min;
        hi = app.key_max;
    }
    if (lo > hi) {
        std::swap(lo, hi);
    }
}

// Sub mixer only (two stacked lanes). Waveform is drawn by the stem strip to the right.
void draw_verify_submix(App& app, int stem, float width, float height) {
    App::VerifyStemUi& verify = app.verify[static_cast<std::size_t>(stem)];
    const float scale = ui_scale();
    const float row_gap = 2.f * scale;
    const float row_h = std::max(1.f, (height - row_gap) * 0.5f);
    const ImVec2 origin = ImGui::GetCursorScreenPos();
    ImGui::PushID(stem);
    ImGui::SetCursorScreenPos(origin);
    draw_verify_lane(app, verify.original, verify.midi, "verify-orig", t("verify_original"), width, row_h);
    ImGui::SetCursorScreenPos(ImVec2(origin.x, origin.y + row_h + row_gap));
    draw_verify_lane(app, verify.midi, verify.original, "verify-midi", t("verify_midi"), width, row_h);
    ImGui::PopID();
}

void select_strip(App& app, int stem) {
    app.selected = stem;
}

void draw_strip(
    App& app, const bt::SongSnapshot& song, const bt::Player::Snapshot& transport, double length, bool ready, int stem,
    float width, float height) {
    ImGui::PushID(stem);
    const float scale = ui_scale();
    const ImVec2 origin = ImGui::GetCursorScreenPos();
    ImGui::SetNextItemAllowOverlap();
    ImGui::InvisibleButton("row", ImVec2(width, height));
    const bool row_clicked = ImGui::IsItemClicked();
    const bool audible = stem_audible(song, stem);
    const bool disabled = item_locked();
    ImDrawList* draw = ImGui::GetWindowDrawList();
    const ImVec2 corner(origin.x + width, origin.y + height);
    draw->AddRectFilled(origin, corner, paint(IM_COL32(36, 36, 36, 255), disabled));

    const float plate_w = 72.f * scale;
    const bool selected = app.selected == stem;
    draw->AddRectFilled(origin, ImVec2(origin.x + plate_w, corner.y), paint(kStemColor[stem], disabled));
    const char* name = stem_label(stem);
    const ImVec2 name_size = ImGui::CalcTextSize(name);
    draw->AddText(
        ImVec2(origin.x + (plate_w - name_size.x) * 0.5f, origin.y + (height - name_size.y) * 0.5f),
        paint(kStemInk[stem], disabled), name);
    ImGui::SetCursorScreenPos(origin);
    ImGui::InvisibleButton("label", ImVec2(plate_w, height));
    const bool label_clicked = ImGui::IsItemClicked();

    const float knob_size = MediumKnob::diameter();
    const float button = MediumButton::width();
    const float meter_w = LevelMeter::bar_width();
    const float right_w = knob_size * 2.f + button + meter_w * 2.f + 36.f * scale;
    const float sub_gap = 6.f * scale;
    const float sub_w = verify_submix_width();
    // name | submix (原音/MIDI) | single wave | main strip controls
    const float content_x = origin.x + plate_w + 8.f * scale;
    const float wave_x = content_x + sub_w + sub_gap;
    const float wave_w = std::max(48.f, width - (wave_x - origin.x) - right_w - 8.f * scale);
    const float wave_h = std::max(24.f, height - 12.f * scale);
    const float wave_y = origin.y + (height - wave_h) * 0.5f;

    ImGui::SetCursorScreenPos(ImVec2(content_x, wave_y));
    draw_verify_submix(app, stem, sub_w, wave_h);

    ImGui::SetCursorScreenPos(ImVec2(wave_x, wave_y));
    draw_wave(
        app, "stem", stem, app.stem_peaks[static_cast<std::size_t>(stem)], song_wave_scale(app), kStemColor[stem],
        ImVec2(wave_w, wave_h), transport, length, ready, false);
    const bool wave_clicked = ImGui::IsItemClicked();
    if (label_clicked || wave_clicked || row_clicked) {
        select_strip(app, stem);
    }

    float x = wave_x + wave_w + 8.f * scale;
    const float control_y = origin.y + 6.f * scale;
    ImGui::SetCursorScreenPos(ImVec2(x, control_y));
    float db = app.gain_db[static_cast<std::size_t>(stem)];
    if (MediumKnob("vol", &db)
            .range(-60.f, 6.f)
            .reset(0.f)
            .sensitivity(0.45f)
            .tip(t("master"))
            .caption(format_db)
            .draw(app)) {
        app.gain_db[static_cast<std::size_t>(stem)] = db;
        app.session->set_gain(stem, db_to_gain(db));
        select_strip(app, stem);
    }

    x += knob_size + 6.f * scale;
    ImGui::SetCursorScreenPos(ImVec2(x, control_y));
    float pan = app.pan[static_cast<std::size_t>(stem)];
    if (MediumKnob("pan", &pan).range(-1.f, 1.f).reset(0.f).sensitivity(0.012f).tip("Pan").caption(format_pan).draw(app)) {
        app.pan[static_cast<std::size_t>(stem)] = pan;
        app.session->set_pan(stem, pan);
        select_strip(app, stem);
    }

    x += knob_size + 6.f * scale;
    const float button_h = MediumButton::height();
    ImGui::SetCursorScreenPos(ImVec2(x, control_y));
    const bool mute = song.mute[static_cast<std::size_t>(stem)];
    if (MediumButton("mute", mute ? Icon::SpeakerOff : Icon::Speaker).engaged(mute).tip(t("mute")).draw()) {
        app.session->set_mute(stem, !mute);
        select_strip(app, stem);
    }
    ImGui::SetCursorScreenPos(ImVec2(x, control_y + button_h + 2.f));
    const bool solo = song.solo[static_cast<std::size_t>(stem)];
    if (MediumButton("solo", Icon::Solo).engaged(solo).tip(t("solo")).draw()) {
        app.session->set_solo(stem, !solo);
        select_strip(app, stem);
    }

    x += button + 8.f * scale;
    const float meter_h = height - 8.f * scale;
    LevelMeter::draw(x, origin.y + 2.f * scale, meter_h, app.meter_l[static_cast<std::size_t>(stem)], app.meter_r[static_cast<std::size_t>(stem)]);

    if (!audible) {
        draw->AddRectFilled(origin, corner, paint(IM_COL32(0, 0, 0, 150), disabled));
    }
    draw->AddRect(origin, corner, paint(selected ? kHi : IM_COL32(22, 22, 22, 255), disabled));
    ImGui::SetCursorScreenPos(ImVec2(origin.x, origin.y + height + 2.f));
    ImGui::PopID();
}

void note_beats(App& app, double from, double to) {
    bool hit = false;
    bool downbeat = false;
    each_metro_beat(app, from, to, [&](double, bool down) {
        hit = true;
        downbeat = down;
    });
    if (hit) {
        app.beat_flash_at = ImGui::GetTime();
        app.beat_flash_down = downbeat;
    }
}

void push_metronome(App& app) {
    if (app.player == nullptr) {
        return;
    }
    app.player->set_metronome(app.metro, app.bpm, app.click_db, app.beat_offset, true, app.beat_times);
}

PitchDraft pitch_preset(int stem) {
    const bt::PitchParameters parameters = bt::pitch_parameters_for_stem(stem);
    PitchDraft draft;
    draft.onset = parameters.onset_threshold;
    draft.frame = parameters.frame_threshold;
    draft.min_ms = static_cast<int>(std::lround(parameters.minimum_note_ms));
    draft.min_hz = static_cast<int>(std::lround(parameters.minimum_frequency));
    draft.max_hz = static_cast<int>(std::lround(parameters.maximum_frequency));
    draft.melodia = parameters.melodia;
    return draft;
}

void seed_shell(App& app) {
    if (!app.pitch_seeded) {
        for (int stem = 0; stem < bt::kStemCount; ++stem) {
            app.pitch_draft[static_cast<std::size_t>(stem)] = pitch_preset(stem);
            app.pitch_applied[static_cast<std::size_t>(stem)] = app.pitch_draft[static_cast<std::size_t>(stem)];
        }
        app.pitch_seeded = true;
    }
}

const char* project_name(const App& app) {
    if (app.project_index < 0 || app.project_index >= static_cast<int>(app.projects.size())) {
        return "";
    }
    return app.projects[static_cast<std::size_t>(app.project_index)].name.c_str();
}


void caption_hundredths(float value, char* text, std::size_t size) {
    std::snprintf(text, size, "%.2f", snap_value(value, 0.f, 1.f, 0.01f));
}

void caption_ms(float value, char* text, std::size_t size) {
    std::snprintf(text, size, "%d ms", static_cast<int>(std::lround(value)));
}

void caption_hz(float value, char* text, std::size_t size) {
    const int hz = static_cast<int>(std::lround(value));
    if (hz <= 0) {
        std::snprintf(text, size, "%s", t("unlimited"));
        return;
    }
    std::snprintf(text, size, "%d", hz);
}

void caption_int(float value, char* text, std::size_t size) {
    std::snprintf(text, size, "%d", static_cast<int>(std::lround(value)));
}

void caption_note(float value, char* text, std::size_t size) {
    midi_note_name(static_cast<int>(std::lround(value)), text, size);
}

struct FoldItem {
    float width = 0.f;
    float height = 0.f;
    std::function<void()> draw;
};

struct FoldFlow {
    float origin_x = 0.f;
    float y = 0.f;
    float limit = 0.f;
    float gap_x = 0.f;
    float gap_y = 0.f;
    std::vector<FoldItem> row;

    static FoldFlow begin() {
        FoldFlow flow;
        flow.origin_x = ImGui::GetCursorPosX();
        flow.y = ImGui::GetCursorPosY();
        flow.limit = flow.origin_x + ImGui::GetContentRegionAvail().x;
        flow.gap_x = 14.f * ui_scale();
        flow.gap_y = 10.f * ui_scale();
        return flow;
    }

    float row_width() const {
        float width = 0.f;
        for (std::size_t index = 0; index < row.size(); ++index) {
            if (index > 0) {
                width += gap_x;
            }
            width += row[index].width;
        }
        return width;
    }

    void commit_row(bool more_below) {
        if (row.empty()) {
            return;
        }
        float row_h = 0.f;
        for (const FoldItem& item : row) {
            row_h = std::max(row_h, item.height);
        }
        float x = origin_x;
        for (const FoldItem& item : row) {
            ImGui::SetCursorPos(ImVec2(x, y + (row_h - item.height) * 0.5f));
            item.draw();
            x += item.width + gap_x;
        }
        y += row_h;
        if (more_below) {
            y += gap_y;
        }
        row.clear();
    }

    template <class Draw>
    void item(float width, float height, Draw&& draw) {
        if (!row.empty() && origin_x + row_width() + gap_x + width > limit + 0.5f) {
            commit_row(true);
        }
        row.push_back(FoldItem{width, height, std::function<void()>(std::forward<Draw>(draw))});
    }

    void end() {
        commit_row(false);
        ImGui::SetCursorPos(ImVec2(origin_x, y));
    }
};

struct LabeledKnobSize {
    float width = 0.f;
    float height = 0.f;
};

LabeledKnobSize labeled_knob_size(const char* label, const char* shown) {
    const float column = std::max(
        MediumKnob::column_width(shown), std::max(ImGui::CalcTextSize(label).x, ImGui::CalcTextSize(shown).x));
    const float height = ImGui::GetTextLineHeight() + 2.f * ui_scale() + MediumKnob::height();
    return {column, height};
}

bool labeled_medium_knob(
    App& app, const char* id, const char* label, float column, float* value, float min_value, float max_value, float reset, float sensitivity,
    MediumKnob::Caption caption, bool enabled = true) {
    const float label_h = ImGui::GetTextLineHeight();
    const float gap = 2.f * ui_scale();
    const ImVec2 start = ImGui::GetCursorScreenPos();
    ImGui::Dummy(ImVec2(column, label_h + gap + MediumKnob::height()));
    const bool disabled = control_disabled(enabled);
    const ImVec2 label_size = ImGui::CalcTextSize(label);
    ImGui::GetWindowDrawList()->AddText(
        ImVec2(start.x + (column - label_size.x) * 0.5f, start.y), paint(IM_COL32(141, 141, 141, 255), disabled), label);
    ImGui::SetCursorScreenPos(ImVec2(start.x, start.y + label_h + gap));
    return MediumKnob(id, value)
        .range(min_value, max_value)
        .reset(reset)
        .sensitivity(sensitivity)
        .tip(label)
        .enabled(enabled)
        .column(column)
        .caption(caption)
        .draw(app);
}

void flow_float_knob(
    FoldFlow& flow, App& app, const char* id, const char* label, float* value, float min_value, float max_value, float step, float reset,
    MediumKnob::Caption caption) {
    char shown[32];
    caption(*value, shown, sizeof shown);
    const LabeledKnobSize size = labeled_knob_size(label, shown);
    const float column = size.width;
    flow.item(column, size.height, [&app, id, label, column, value, min_value, max_value, step, reset, caption] {
        float current = *value;
        if (labeled_medium_knob(app, id, label, column, &current, min_value, max_value, reset, drag_sensitivity(min_value, max_value), caption)) {
            *value = snap_value(current, min_value, max_value, step);
        }
    });
}

void flow_int_knob(
    FoldFlow& flow, App& app, const char* id, const char* label, int* value, int min_value, int max_value, int reset, MediumKnob::Caption caption,
    bool enabled = true) {
    char shown[32];
    caption(static_cast<float>(*value), shown, sizeof shown);
    const LabeledKnobSize size = labeled_knob_size(label, shown);
    const float column = size.width;
    flow.item(column, size.height, [&app, id, label, column, value, min_value, max_value, reset, caption, enabled] {
        float current = static_cast<float>(*value);
        if (labeled_medium_knob(
                app, id, label, column, &current, static_cast<float>(min_value), static_cast<float>(max_value), static_cast<float>(reset),
                drag_sensitivity(static_cast<float>(min_value), static_cast<float>(max_value)), caption, enabled)) {
            *value = std::clamp(static_cast<int>(std::lround(current)), min_value, max_value);
        }
    });
}

float text_button_width(const char* label, float min_width, float pad_x) {
    return std::max(min_width, ImGui::CalcTextSize(label).x + pad_x * 2.f);
}

bool text_button(const char* id, const char* label, bool engaged, bool enabled, float width, float height, float radius) {
    ImGui::PushID(id);
    const ImVec2 origin = ImGui::GetCursorScreenPos();
    const bool pressed = ImGui::InvisibleButton("text", ImVec2(width, height));
    const bool disabled = control_disabled(enabled);
    const bool hovered = !disabled && ImGui::IsItemHovered();
    const bool hot = engaged || hovered;
    ImDrawList* draw = ImGui::GetWindowDrawList();
    const ImVec2 corner(origin.x + width, origin.y + height);
    const ImVec2 text_size = ImGui::CalcTextSize(label);
    draw->AddRectFilled(origin, corner, paint(hot ? kHi : IM_COL32(51, 51, 51, 255), disabled), radius);
    draw->AddRect(origin, corner, paint(hot ? kHi : IM_COL32(74, 74, 74, 255), disabled), radius);
    draw->AddText(
        ImVec2(origin.x + (width - text_size.x) * 0.5f, origin.y + (height - text_size.y) * 0.5f),
        paint(hot ? kHiInk : IM_COL32(210, 210, 210, 255), disabled), label);
    ImGui::PopID();
    return !disabled && pressed;
}

bool medium_toggle(const char* id, const char* label, bool engaged, bool enabled) {
    const float pad_x = 8.f * ui_scale();
    const float width = text_button_width(label, MediumButton::width(), pad_x);
    return text_button(id, label, engaged, enabled, width, MediumButton::height(), 6.f * ui_scale());
}

void flow_toggle(FoldFlow& flow, const char* id, const char* label, bool* value, bool enabled = true) {
    const float pad_x = 8.f * ui_scale();
    const float width = text_button_width(label, MediumButton::width(), pad_x);
    flow.item(width, MediumButton::height(), [id, label, value, enabled] {
        if (medium_toggle(id, label, *value, enabled)) {
            *value = !*value;
        }
    });
}

void draw_fold_body(App& app, int kind, int stem) {
    (void)stem;
    if (kind == 1) {
        FoldFlow look = FoldFlow::begin();
        flow_int_knob(look, app, "frets", t("frets"), &app.fret_count, 12, 24, 22, caption_int);
        flow_int_knob(look, app, "bright", t("bright"), &app.fret_bright, 1, 127, 100, caption_int);
        look.end();
        app.fret_prefer = std::clamp(app.fret_prefer, 0, app.fret_count);
        return;
    }
    if (kind == 2) {
        FoldFlow range = FoldFlow::begin();
        if (app.midi_view == MidiView::Guitar || app.midi_view == MidiView::Bass) {
            const bool bass = app.midi_view == MidiView::Bass;
            int low = 0;
            int high = 0;
            instrument_pitch_bounds(bass, app.fret_count, low, high);
            int* pitch_min = bass ? &app.bass_pitch_min : &app.guitar_pitch_min;
            int* pitch_max = bass ? &app.bass_pitch_max : &app.guitar_pitch_max;
            *pitch_min = std::clamp(*pitch_min, low, high);
            *pitch_max = std::clamp(*pitch_max, low, high);
            const char* min_id = bass ? "bass-min" : "guitar-min";
            const char* max_id = bass ? "bass-max" : "guitar-max";
            flow_int_knob(range, app, min_id, t("pitch_min"), pitch_min, low, high, low, caption_note);
            flow_int_knob(range, app, max_id, t("pitch_max"), pitch_max, low, high, high, caption_note);
        } else {
            flow_int_knob(range, app, "key-min", t("pitch_min"), &app.key_min, 21, 108, 21, caption_note);
            flow_int_knob(range, app, "key-max", t("pitch_max"), &app.key_max, 21, 108, 108, caption_note);
        }
        range.end();
        return;
    }
    app.fret_prefer = std::clamp(app.fret_prefer, 0, std::max(1, app.fret_count));
    FoldFlow hand = FoldFlow::begin();
    const bool suggested = app.fret_suggest;
    flow_toggle(hand, "suggest", t("mode_suggest"), &app.fret_suggest);
    flow_int_knob(hand, app, "prefer", t("prefer"), &app.fret_prefer, 0, std::max(1, app.fret_count), 0, caption_int, suggested);
    flow_toggle(hand, "open", t("allow_open"), &app.fret_allow_open, suggested);
    flow_toggle(hand, "alt", t("show_alt"), &app.fret_show_alt, suggested);
    flow_toggle(hand, "harmonic", t("harmonic"), &app.fret_harmonic);
    hand.end();
}

void draw_midi_stage(App& app, const bt::SongSnapshot& song, const bt::Player::Snapshot& transport, double length, bool ready, float stage_h) {
    const float scale = ui_scale();
    const int stem = std::clamp(app.selected, 0, bt::kStemCount - 1);
    const char* views[] = {t("guitar_view"), t("bass_view"), t("fall")};
    const MidiView modes[] = {MidiView::Guitar, MidiView::Bass, MidiView::Fall};
    const char* view_ids[] = {"view-guitar", "view-bass", "view-fall"};
    const float row_y = 6.f * scale;
    const float view_h = PlayerButton::height();
    const float view_pad = 10.f * scale;
    SideRow views_row(SideRow::Dock::Right);
    views_row.margin(8.f * scale);
    for (int index = 0; index < 3; ++index) {
        const float view_w = text_button_width(views[index], PlayerButton::width(), view_pad);
        views_row.item(view_w, view_h, index == 0 ? -1.f : 4.f * scale, [&, index, view_w] {
            if (text_button(view_ids[index], views[index], app.midi_view == modes[index], true, view_w, view_h, 8.f * scale)) {
                app.midi_view = modes[index];
            }
        });
    }
    const float row_h = views_row.commit(row_y);
    ImGui::SetCursorPos(ImVec2(10.f * scale, row_y + (row_h - ImGui::GetTextLineHeight()) * 0.5f));
    const char* opened = project_name(app);
    if (opened[0] != '\0') {
        ImGui::TextUnformatted(opened);
    }
    ImGui::SetCursorPos(ImVec2(10.f * scale, row_y + row_h + 6.f * scale));
    if (!app.model_note.empty() && song.phase != bt::SongPhase::Ready) {
        ImGui::NewLine();
        ImGui::SetCursorPosX(10.f * scale);
        ImGui::TextDisabled("%s", stored(app.model_note));
    }
    const auto note_state = app.note_status[static_cast<std::size_t>(stem)];
    if (note_state == NoteStatus::Failed) {
        ImGui::NewLine();
        ImGui::SetCursorPosX(10.f * scale);
        if (app.note_detail[static_cast<std::size_t>(stem)].empty()) {
            ImGui::TextDisabled("%s", t("pitch_missing"));
        } else {
            ImGui::TextDisabled("%s", app.note_detail[static_cast<std::size_t>(stem)].c_str());
        }
    }

    const bool fret = app.midi_view == MidiView::Guitar || app.midi_view == MidiView::Bass;
    struct Column {
        const char* id;
        const char* title;
        bool* open;
        int kind;
    };
    // Pitch/BP knobs removed: decode uses pitch_parameters_for_stem only.
    Column columns[4] = {
        {"pitch", t("pitch_fold"), &app.fold_pitch, 0},
        {"look", t("look"), &app.fold_look, 1},
        {"range", t("range"), &app.fold_range, 2},
        {"hand", t("hand"), &app.fold_hand, 3},
    };
    const int shown_kinds[] = {fret ? 1 : -1, 2, fret ? 3 : -1};
    int shown = 0;
    int visible[4];
    for (int kind : shown_kinds) {
        if (kind >= 0) {
            visible[shown++] = kind;
        }
    }
    const float gap = 3.f * scale;
    const float header_h = std::max(28.f * scale, MediumButton::height() + 4.f * scale);
    const float inner_w = std::max(1.f, ImGui::GetWindowWidth() - 20.f * scale);
    const float col_w =
        shown > 0 ? (inner_w - gap * static_cast<float>(shown - 1)) / static_cast<float>(shown) : inner_w;
    const ImVec2 row = ImGui::GetCursorScreenPos();
    ImGui::Dummy(ImVec2(inner_w, header_h));
    const ImVec2 after_headers = ImGui::GetCursorScreenPos();
    struct Head {
        ImVec2 origin;
        int kind;
        bool open;
        ImVec2 body_min;
        ImVec2 body_max;
    };
    Head heads[4];
    for (int slot = 0; slot < shown; ++slot) {
        const int kind = visible[slot];
        Column& column = columns[kind];
        const ImVec2 origin(row.x + static_cast<float>(slot) * (col_w + gap), row.y);
        ImGui::SetCursorScreenPos(origin);
        ImGui::PushID(column.id);
        ImGui::InvisibleButton("head", ImVec2(col_w, header_h));
        if (ImGui::IsItemClicked()) {
            *column.open = !*column.open;
        }
        ImDrawList* draw = ImGui::GetWindowDrawList();
        draw->AddRectFilled(origin, ImVec2(origin.x + col_w, origin.y + header_h), paint(IM_COL32(36, 36, 36, 255), item_locked()));
        draw->AddRect(origin, ImVec2(origin.x + col_w, origin.y + header_h), paint(IM_COL32(22, 22, 22, 255), item_locked()));
        const char* chevron = *column.open ? "v" : ">";
        const bool header_off = item_locked();
        draw->AddText(ImVec2(origin.x + 8.f * scale, origin.y + (header_h - ImGui::GetTextLineHeight()) * 0.5f), paint(*column.open ? kHi : IM_COL32(141, 141, 141, 255), header_off), chevron);
        draw->AddText(ImVec2(origin.x + 22.f * scale, origin.y + (header_h - ImGui::GetTextLineHeight()) * 0.5f), paint(kHi, header_off), column.title);
        heads[slot] = {origin, kind, *column.open, ImVec2(0.f, 0.f), ImVec2(0.f, 0.f)};
        ImGui::PopID();
    }
    ImGui::SetCursorScreenPos(after_headers);

    const float canvas_top = ImGui::GetCursorPosY() + 4.f * scale;
    const float canvas_h = std::max(1.f, stage_h - canvas_top - 8.f * scale);
    const float canvas_w = inner_w;
    ImGui::SetCursorPos(ImVec2(10.f * scale, canvas_top));
    draw_midi_canvas(app, transport, length, ready, ImVec2(canvas_w, canvas_h));

    ImGui::PushStyleColor(ImGuiCol_ChildBg, ImVec4(0.141f, 0.141f, 0.141f, 1.f));
    ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2(12.f * scale, 10.f * scale));
    for (int slot = 0; slot < shown; ++slot) {
        if (!heads[slot].open) {
            continue;
        }
        const Column& column = columns[heads[slot].kind];
        const ImVec2 origin(heads[slot].origin.x, heads[slot].origin.y + header_h);
        const ImVec2 saved = ImGui::GetCursorScreenPos();
        ImGui::SetCursorScreenPos(origin);
        ImGui::PushID(column.id);
        ImGui::BeginChild(
            "body", ImVec2(col_w, 0.f),
            ImGuiChildFlags_AlwaysUseWindowPadding | ImGuiChildFlags_AutoResizeY,
            ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoScrollWithMouse);
        draw_fold_body(app, column.kind, stem);
        ImGui::EndChild();
        const ImVec2 body_min = ImGui::GetItemRectMin();
        const ImVec2 body_max = ImGui::GetItemRectMax();
        ImGui::PopID();
        ImGui::GetWindowDrawList()->AddRect(body_min, body_max, IM_COL32(22, 22, 22, 255));
        heads[slot].body_min = body_min;
        heads[slot].body_max = body_max;
        ImGui::SetCursorScreenPos(saved);
    }
    if (ImGui::IsMouseClicked(ImGuiMouseButton_Left)) {
        const ImVec2 mouse = ImGui::GetIO().MouseClickedPos[0];
        const auto contains = [](ImVec2 min, ImVec2 max, ImVec2 point) {
            return point.x >= min.x && point.x < max.x && point.y >= min.y && point.y < max.y;
        };
        for (int slot = 0; slot < shown; ++slot) {
            const ImVec2 header_max(heads[slot].origin.x + col_w, heads[slot].origin.y + header_h);
            const bool on_header = contains(heads[slot].origin, header_max, mouse);
            const bool on_body = heads[slot].open && contains(heads[slot].body_min, heads[slot].body_max, mouse);
            if (!on_header && !on_body) {
                *columns[heads[slot].kind].open = false;
            }
        }
    }
    ImGui::PopStyleVar();
    ImGui::PopStyleColor();
}

void apply_separation(App& app) {
    if (app.engine == nullptr) {
        return;
    }
    const auto device = app.separation_preference == 1
        ? InferenceDevice::Cpu
        : app.separation_preference == 2 ? InferenceDevice::Cuda : InferenceDevice::Auto;
    app.engine->apply(app.separation_model, device, app.demucs_threads);
    app.separation_model = app.engine->model_key();
    app.separation_device = app.engine->device();
    app.separation_chunk = app.engine->chunk();
    app.model_note = app.engine->error();
    save_performance(app);
}

std::string separation_caption(const ProjectItem& item) {
    std::string label = model_label(item.model_key);
    if (label.empty()) {
        return item.device;
    }
    if (!item.device.empty()) {
        label += " · ";
        label += item.device;
    }
    return label;
}

void copy_project(ProjectItem& item, const bt::ProjectEntry& entry) {
    item = entry;
}

void open_project(App& app, const ProjectItem& item) {
    if (app.session == nullptr) {
        return;
    }
    std::error_code error;
    if (std::filesystem::is_directory(item.directory, error)) {
        app.session->open_cache(item.directory);
        return;
    }
    if (!item.source.empty() && std::filesystem::exists(item.source, error)) {
        app.session->open_song(item.source);
    }
}

void sync_projects(App& app, const bt::SongSnapshot& song, double length) {
    std::string rename_hash;
    if (app.rename_index >= 0 && app.rename_index < static_cast<int>(app.projects.size())) {
        rename_hash = app.projects[static_cast<std::size_t>(app.rename_index)].hash;
    }
    const int phase = static_cast<int>(song.phase);
    const double now = ImGui::GetTime();
    const bool scan = !app.projects_seeded || song.job != app.project_watch_job || phase != app.project_watch_phase ||
        song.project_hash != app.project_watch_hash || song.project_name != app.project_watch_name ||
        app.projects_scanned < 0 || now - app.projects_scanned >= 1.0;
    std::vector<ProjectItem> next = app.projects;
    if (scan) {
        next.clear();
        try {
            const auto append_projects = [&](const std::filesystem::path& root, std::string_view model_key) {
                if (root.empty()) {
                    return;
                }
                for (const bt::ProjectEntry& entry : bt::list_projects(root, model_key)) {
                    bool seen = false;
                    for (const ProjectItem& item : next) {
                        if (item.directory == entry.directory) {
                            seen = true;
                            break;
                        }
                    }
                    if (seen) {
                        continue;
                    }
                    ProjectItem item;
                    copy_project(item, entry);
                    next.push_back(std::move(item));
                }
            };
            for (const std::string_view key : bt::kSeparationKeys) {
                append_projects(app.saves_directory, key);
            }
        } catch (const std::exception&) {
            next = app.projects;
        }
        app.projects_scanned = now;
        app.project_watch_job = song.job;
        app.project_watch_phase = phase;
        app.project_watch_hash = song.project_hash;
        app.project_watch_name = song.project_name;
    }
    const bool pending = song.phase == bt::SongPhase::Decoding || song.phase == bt::SongPhase::Separating;
    const bool live = (pending || song.phase == bt::SongPhase::Ready) && !song.project_hash.empty() && song.job != app.closed_job;
    int found = -1;
    if (!song.cache_directory.empty()) {
        for (int index = 0; index < static_cast<int>(next.size()); ++index) {
            if (next[static_cast<std::size_t>(index)].directory == song.cache_directory) {
                found = index;
                break;
            }
        }
    } else if (!song.project_hash.empty()) {
        for (int index = 0; index < static_cast<int>(next.size()); ++index) {
            if (next[static_cast<std::size_t>(index)].hash == song.project_hash) {
                found = index;
                break;
            }
        }
    }
    if (found < 0 && live && !song.cache_directory.empty()) {
        ProjectItem item;
        item.name = song.project_name.empty() ? bt::display_name_from_path(song.source) : song.project_name;
        item.hash = song.project_hash;
        item.directory = song.cache_directory;
        item.source = song.source;
        try {
            const std::string parent = bt::display_name_from_path(song.cache_directory.parent_path());
            if (bt::is_separation_key(parent)) {
                item.model_key = parent;
            }
        } catch (const std::exception&) {
        }
        if (song.phase == bt::SongPhase::Separating || song.phase == bt::SongPhase::Decoding) {
            item.device = app.separation_device;
        }
        next.insert(next.begin(), std::move(item));
        found = 0;
    }
    if (found >= 0) {
        ProjectItem& item = next[static_cast<std::size_t>(found)];
        std::error_code error;
        if (!song.cache_directory.empty() && std::filesystem::exists(song.cache_directory, error)) {
            item.directory = song.cache_directory;
            if (!song.project_name.empty()) {
                item.name = song.project_name;
            }
        }
        if (!song.source.empty() && std::filesystem::exists(song.source, error)) {
            item.source = song.source;
        }
        if (song.phase == bt::SongPhase::Ready && length > 0 && item.seconds <= 0 && std::filesystem::is_directory(item.directory, error)) {
            if (bt::write_project_seconds(item.directory, length)) {
                item.seconds = length;
            }
        }
    }
    app.projects = std::move(next);
    app.projects_seeded = true;
    app.rename_index = -1;
    if (!rename_hash.empty()) {
        for (int index = 0; index < static_cast<int>(app.projects.size()); ++index) {
            if (app.projects[static_cast<std::size_t>(index)].hash == rename_hash) {
                app.rename_index = index;
                break;
            }
        }
    }
    if (found >= 0) {
        app.project_index = found;
    } else if (app.project_index >= static_cast<int>(app.projects.size())) {
        app.project_index = static_cast<int>(app.projects.size()) - 1;
    }
}

void fill_item_width() {
    ImGui::SetNextItemWidth(std::max(1.f, ImGui::GetContentRegionAvail().x));
}

void draw_settings_panel(App& app, bool busy, float height) {
    const float scale = ui_scale();
    ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2(12.f * scale, 8.f * scale));
    ImGui::BeginChild(
        "side-settings", ImVec2(0.f, std::max(1.f, height)), ImGuiChildFlags_AlwaysUseWindowPadding, ImGuiWindowFlags_None);
    if (busy) {
        ImGui::BeginDisabled();
    }
    ImGui::TextUnformatted(t("language"));
    const char* languages[] = {t("settings.lang_hant"), t("settings.lang_hans"), t("settings.lang_en")};
    fill_item_width();
    if (ImGui::BeginCombo("##language", languages[app.language])) {
        for (int index = 0; index < 3; ++index) {
            if (ImGui::Selectable(languages[index], app.language == index)) {
                set_language(app, index);
                save_language(app);
            }
        }
        ImGui::EndCombo();
    }
    ImGui::Spacing();
    ImGui::TextDisabled("%s", t("audio"));
    ImGui::TextUnformatted(t("output"));
    refresh_outputs(app);
    const int shown = std::clamp(app.output_device, 0, static_cast<int>(app.outputs.size()) - 1);
    fill_item_width();
    if (ImGui::BeginCombo("##output", app.outputs[static_cast<std::size_t>(shown)].name.c_str())) {
        for (int index = 0; index < static_cast<int>(app.outputs.size()); ++index) {
            const OutputChoice& choice = app.outputs[static_cast<std::size_t>(index)];
            const std::string label = choice.name + "##" + std::to_string(index);
            if (ImGui::Selectable(label.c_str(), app.output_device == index)) {
                if (choice.id == app.output_id || app.player == nullptr) {
                    app.output_device = index;
                    app.output_id = choice.id;
                } else if (auto switched = app.player->use_device(choice.id); switched) {
                    app.output_device = index;
                    app.output_id = choice.id;
                    app.device_note.clear();
                    save_output(app);
                } else {
                    app.output_device = 0;
                    app.output_id.clear();
                    app.device_note = switched.error();
                    save_output(app);
                }
            }
        }
        ImGui::EndCombo();
    }
    ImGui::Spacing();
    ImGui::TextDisabled("%s", t("settings.performance"));
    ImGui::TextUnformatted(t("settings.note_workers"));
    fill_item_width();
    if (ImGui::SliderInt("##note-workers", &app.note_workers, 1, 8)) {
        app.note_workers = std::clamp(app.note_workers, 1, 8);
        if (app.note_service != nullptr) {
            app.note_service->set_workers(app.note_workers);
        }
        save_performance(app);
    }
    const bool cpu_threads = app.separation_device != "CUDA";
    if (!cpu_threads) {
        ImGui::BeginDisabled();
    }
    ImGui::TextUnformatted(t("settings.separation_threads"));
    fill_item_width();
    if (ImGui::SliderInt("##separation-threads", &app.demucs_threads, 1, 16)) {
        app.demucs_threads = std::clamp(app.demucs_threads, 1, 16);
        save_performance(app);
    }
    if (cpu_threads && ImGui::IsItemDeactivatedAfterEdit()) {
        apply_separation(app);
    }
    if (!cpu_threads) {
        ImGui::EndDisabled();
    }
    ImGui::Spacing();
    ImGui::TextDisabled("%s", t("separation"));
    ImGui::TextUnformatted(t("separation_model"));
    const std::vector<BackendStatus> backends = app.engine == nullptr ? std::vector<BackendStatus>{} : app.engine->backends();
    const std::string model_preview_owned = model_label(app.separation_model);
    const char* model_preview = model_preview_owned.c_str();
    fill_item_width();
    if (ImGui::BeginCombo("##separation-model", model_preview)) {
        for (const BackendStatus& backend : backends) {
            if (!backend.available) {
                ImGui::BeginDisabled();
            }
            const bool selected = backend.key == app.separation_model;
            if (ImGui::Selectable(backend.label.c_str(), selected) && backend.available && !selected) {
                app.separation_model = backend.key;
                apply_separation(app);
            }
            if (!backend.available) {
                ImGui::EndDisabled();
            }
        }
        if (backends.empty()) {
            ImGui::TextDisabled("%s", model_preview);
        }
        ImGui::EndCombo();
    }
    ImGui::TextUnformatted(t("inference_device"));
    const bool cuda_backend = app.separation_model != "htdemucs_6s";
    const char* device_labels[] = {t("device_auto"), t("device_cpu"), t("device_cuda")};
    const int preference = std::clamp(app.separation_preference, 0, 2);
    const char* device_preview = cuda_backend ? device_labels[preference] : device_labels[1];
    if (!cuda_backend) {
        ImGui::BeginDisabled();
    }
    fill_item_width();
    if (ImGui::BeginCombo("##separation-device", device_preview)) {
        for (int index = 0; index < 3; ++index) {
            if (ImGui::Selectable(device_labels[index], preference == index) && preference != index) {
                app.separation_preference = index;
                apply_separation(app);
            }
        }
        ImGui::EndCombo();
    }
    if (!cuda_backend) {
        ImGui::EndDisabled();
    }
    if (!app.model_note.empty()) {
        ImGui::PushTextWrapPos(ImGui::GetCursorPos().x + ImGui::GetContentRegionAvail().x);
        ImGui::TextWrapped("%s", stored(app.model_note));
        ImGui::PopTextWrapPos();
    } else if (!app.separation_device.empty()) {
        ImGui::TextDisabled("%s", app.separation_device.c_str());
    }
    ImGui::Spacing();
    draw_keymap_settings(app);
    ImGui::Spacing();
    if (busy) {
        ImGui::BeginDisabledOverrideReenable();
    }
    if (MediumButton("monitor", Icon::Monitor).engaged(app.monitor_open).tip(t("monitor")).draw()) {
        app.monitor_open = true;
    }
    if (busy) {
        ImGui::EndDisabledOverrideReenable();
        ImGui::EndDisabled();
    }
    ImGui::EndChild();
    ImGui::PopStyleVar();
}

void draw_projects(App& app, const bt::SongSnapshot& song, double length, float height) {
    const float scale = ui_scale();
    const bool busy = interface_locked(app);
    sync_projects(app, song, length);
    ImGui::PushStyleColor(ImGuiCol_ChildBg, ImVec4(0.102f, 0.102f, 0.102f, 1.f));
    ImGui::BeginChild("projects", ImVec2(232.f * scale, height), ImGuiChildFlags_None, ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoScrollWithMouse);
    const float head_h = 32.f * scale;
    const float side = 8.f * scale;
    const float gap = 4.f * scale;
    const float import_w = MediumButton::width();
    const float btn_h = MediumButton::height();
    const float btn_y = (head_h - btn_h) * 0.5f;
    const float switches = std::max(1.f, ImGui::GetWindowWidth() - side * 2.f - gap * 2.f - import_w);
    const float switch_w = switches * 0.5f;
    ImGui::SetCursorPos(ImVec2(side, btn_y));
    const ImVec2 projects_origin = ImGui::GetCursorScreenPos();
    ImGui::PushClipRect(projects_origin, ImVec2(projects_origin.x + switch_w, projects_origin.y + btn_h), true);
    if (text_button("side-projects", t("projects"), !app.settings_panel, true, switch_w, btn_h, 6.f * scale) &&
        app.settings_panel) {
        app.settings_panel = false;
        app.keymap_capture = -1;
    }
    ImGui::PopClipRect();
    ImGui::SetCursorPos(ImVec2(side + switch_w + gap, btn_y));
    const ImVec2 settings_origin = ImGui::GetCursorScreenPos();
    ImGui::PushClipRect(settings_origin, ImVec2(settings_origin.x + switch_w, settings_origin.y + btn_h), true);
    if (text_button("side-settings", t("settings"), app.settings_panel, true, switch_w, btn_h, 6.f * scale) &&
        !app.settings_panel) {
        app.settings_panel = true;
    }
    ImGui::PopClipRect();
    ImGui::SetCursorPos(ImVec2(ImGui::GetWindowWidth() - side - import_w, btn_y));
    if (MediumButton("import", Icon::Import).enabled(!busy).tip(t("import")).draw()) {
        if (auto path = pick_audio(app.hwnd)) {
            app.rename_index = -1;
            app.rename_arm = -1.f;
            app.session->open_song(*path);
        }
    }
    ImGui::SetCursorPos(ImVec2(0.f, head_h));
    if (app.settings_panel) {
        draw_settings_panel(app, busy, std::max(1.f, height - head_h));
        ImGui::EndChild();
        ImGui::PopStyleColor();
        return;
    }
    if (busy) {
        ImGui::BeginDisabled();
    }
    ImGui::PushStyleColor(ImGuiCol_ChildBg, ImVec4(0.102f, 0.102f, 0.102f, 1.f));
    ImGui::BeginChild("project-list", ImVec2(0, std::max(1.f, height - head_h)), ImGuiChildFlags_None, ImGuiWindowFlags_None);
    ImGui::SetCursorPos(ImVec2(6.f * scale, 6.f * scale));
    int remove = -1;
    const float text_h = ImGui::GetTextLineHeight();
    const float icon_w = MediumButton::width();
    const float icon_h = MediumButton::height();
    for (int index = 0; index < static_cast<int>(app.projects.size()); ++index) {
        ProjectItem& item = app.projects[static_cast<std::size_t>(index)];
        ImGui::PushID(index);
        const float row_w = std::max(1.f, ImGui::GetContentRegionAvail().x);
        const ImVec2 origin = ImGui::GetCursorScreenPos();
        const bool open = index == app.project_index;
        const bool editing = app.rename_index == index;
        const std::string method = separation_caption(item);
        const float row_h = method.empty()
            ? std::max(26.f * scale, MediumButton::height() + 6.f * scale)
            : std::max(text_h * 2.f + 8.f * scale, MediumButton::height() + 6.f * scale);
        char clock[16];
        const char* duration = "—";
        if (item.seconds > 0) {
            format_time(item.seconds, clock, sizeof clock);
            duration = clock;
        }
        const ImVec2 duration_size = ImGui::CalcTextSize(duration);
        const float trash_x = origin.x + row_w - icon_w - 4.f * scale;
        const float pencil_x = trash_x - 4.f * scale - icon_w;
        const float duration_x = pencil_x - 8.f * scale - duration_size.x;
        const float row_hit = std::max(1.f, pencil_x - origin.x - 4.f * scale);
        bool row_clicked = false;
        if (!editing) {
            ImGui::InvisibleButton("row", ImVec2(row_hit, row_h));
            row_clicked = ImGui::IsItemClicked() && !ImGui::IsMouseDoubleClicked(ImGuiMouseButton_Left);
        }
        ImDrawList* draw = ImGui::GetWindowDrawList();
        const bool row_off = item_locked();
        draw->AddRectFilled(origin, ImVec2(origin.x + row_w, origin.y + row_h), paint(open ? kHi : IM_COL32(36, 36, 36, 255), row_off));
        draw->AddRect(origin, ImVec2(origin.x + row_w, origin.y + row_h), paint(open ? kHi : IM_COL32(22, 22, 22, 255), row_off));
        const float text_y = method.empty() ? origin.y + (row_h - text_h) * 0.5f : origin.y + 3.f * scale;
        const float method_y = text_y + text_h;
        const ImU32 ink = paint(open ? kHiInk : kHi, row_off);
        const ImU32 dim = paint(open ? kHiInk : IM_COL32(141, 141, 141, 255), row_off);
        if (app.rename_index == index) {
            ImGui::SetCursorScreenPos(ImVec2(origin.x + 6.f * scale, origin.y + 4.f * scale));
            ImGui::SetNextItemWidth(std::max(40.f, duration_x - origin.x - 10.f * scale));
            ImGui::PushStyleColor(ImGuiCol_FrameBg, ImVec4(0.08f, 0.08f, 0.08f, 1.f));
            ImGui::PushStyleColor(ImGuiCol_Border, ImVec4(0.949f, 0.949f, 0.949f, 1.f));
            if (app.rename_focus) {
                ImGui::SetKeyboardFocusHere();
                app.rename_focus = false;
            }
            const bool entered = ImGui::InputText("##rename", app.rename_text, sizeof app.rename_text, ImGuiInputTextFlags_EnterReturnsTrue | ImGuiInputTextFlags_AutoSelectAll);
            const bool escape = ImGui::IsItemFocused() && ImGui::IsKeyPressed(ImGuiKey_Escape);
            if (escape) {
                app.rename_index = -1;
            } else if (entered || ImGui::IsItemDeactivatedAfterEdit()) {
                app.rename_index = -1;
                if (app.rename_text[0] != '\0' && app.rename_text != item.name) {
                    const auto old_directory = item.directory;
                    if (auto renamed = bt::rename_project(old_directory, app.rename_text)) {
                        item.name = renamed->name;
                        item.directory = renamed->directory;
                        item.hash = renamed->hash;
                        item.seconds = renamed->seconds;
                        item.source = renamed->source;
                        item.model_key = renamed->model_key;
                        item.device = renamed->device;
                        if (song.cache_directory == old_directory && app.session != nullptr) {
                            app.session->relocate_cache(item.directory, item.name);
                        }
                    }
                }
            }
            ImGui::PopStyleColor(2);
        } else {
            const float name_r = duration_x - 6.f * scale;
            const float name_bottom = method.empty() ? origin.y + row_h : method_y;
            draw->PushClipRect(ImVec2(origin.x + 8.f * scale, origin.y), ImVec2(name_r, name_bottom), true);
            draw->AddText(ImVec2(origin.x + 8.f * scale, text_y), ink, item.name.c_str());
            draw->PopClipRect();
        }
        if (!method.empty()) {
            draw->PushClipRect(ImVec2(origin.x + 8.f * scale, method_y), ImVec2(pencil_x - 4.f * scale, origin.y + row_h), true);
            draw->AddText(ImVec2(origin.x + 8.f * scale, method_y), dim, method.c_str());
            draw->PopClipRect();
        }
        draw->AddText(ImVec2(duration_x, text_y), dim, duration);
        const float icon_y = origin.y + (row_h - icon_h) * 0.5f;
        ImGui::SetCursorScreenPos(ImVec2(pencil_x, icon_y));
        const bool pencil_pressed = MediumButton("pencil", Icon::Pencil).tip(t("rename")).draw();
        ImGui::SetCursorScreenPos(ImVec2(trash_x, icon_y));
        const bool trash_pressed = MediumButton("trash", Icon::Trash).tip(t("delete")).draw();
        if (!busy && trash_pressed) {
            remove = index;
        } else if (!busy && pencil_pressed) {
            std::snprintf(app.rename_text, sizeof app.rename_text, "%s", item.name.c_str());
            app.rename_index = index;
            app.rename_focus = true;
            app.rename_arm = -1.f;
        } else if (!busy && row_clicked && !open) {
            app.rename_arm = -1.f;
            app.project_index = index;
            open_project(app, item);
        }
        ImGui::SetCursorScreenPos(ImVec2(origin.x, origin.y + row_h + 2.f * scale));
        ImGui::PopID();
    }
    if (remove >= 0 && remove < static_cast<int>(app.projects.size())) {
        const ProjectItem removed = app.projects[static_cast<std::size_t>(remove)];
        if (bt::delete_project(removed.directory)) {
            const bool playing = !song.cache_directory.empty() && removed.directory == song.cache_directory;
            if (app.rename_index == remove) {
                app.rename_index = -1;
            } else if (app.rename_index > remove) {
                --app.rename_index;
            }
            if (playing) {
                app.closed_job = song.job;
                app.player->stop();
                app.player->clear_song();
                app.loaded = nullptr;
                if (app.session != nullptr) {
                    app.session->close_song();
                }
            }
            app.projects.erase(app.projects.begin() + remove);
            if (playing || remove == app.project_index) {
                app.project_index = -1;
            } else if (remove < app.project_index) {
                --app.project_index;
            }
        }
    }
    ImGui::EndChild();
    ImGui::PopStyleColor();
    if (busy) {
        ImGui::EndDisabled();
    }
    ImGui::EndChild();
    ImGui::PopStyleColor();
}

}  // namespace

double snap_loop_time(const App& app, double seconds, double length) {
    seconds = std::clamp(seconds, 0.0, length);
    if (!app.loop_snap || app.beat_times == nullptr || app.beat_times->empty()) {
        return seconds;
    }
    const auto& times = *app.beat_times;
    const auto it = std::lower_bound(times.begin(), times.end(), seconds);
    double best = seconds;
    double best_dist = 1e300;
    if (it != times.end()) {
        best = *it;
        best_dist = std::abs(*it - seconds);
    }
    if (it != times.begin()) {
        const double previous = *(it - 1);
        const double dist = std::abs(previous - seconds);
        if (dist < best_dist) {
            best = previous;
        }
    }
    return std::clamp(best, 0.0, length);
}

double adjacent_beat(const App& app, double beat, int direction, double length) {
    if (direction == 0 || app.beat_times == nullptr || app.beat_times->empty()) {
        return beat;
    }
    const auto& times = *app.beat_times;
    auto it = std::lower_bound(times.begin(), times.end(), beat - 1e-3);
    if (direction > 0) {
        if (it != times.end() && std::abs(*it - beat) <= 1e-3) {
            ++it;
        }
        if (it == times.end()) {
            return beat;
        }
        return std::min(*it, length);
    }
    if (it == times.begin()) {
        return beat;
    }
    --it;
    if (std::abs(*it - beat) <= 1e-3) {
        if (it == times.begin()) {
            return beat;
        }
        --it;
    }
    return std::max(*it, 0.0);
}

void commit_loop_span(App& app, double start, double end, double length) {
    start = std::clamp(start, 0.0, length);
    end = std::clamp(end, 0.0, length);
    if (start > end) {
        std::swap(start, end);
    }
    app.player->set_loop_a(start);
    app.player->set_loop_b(end);
    if (end > start + 0.01) {
        app.player->set_loop_enabled(true);
    }
}

bool edit_loop_begin(
    App& app, const bt::Player::Snapshot& transport, double seconds, double length, float pointer, float a_at, float b_at, float grab) {
    if (app.player == nullptr || !(length > 0)) {
        return false;
    }
    const bool has_a = transport.loop_a >= 0;
    const bool has_b = transport.loop_b >= 0;
    const float da = has_a ? std::fabs(pointer - a_at) : 1.0e9f;
    const float db = has_b ? std::fabs(pointer - b_at) : 1.0e9f;
    const bool grab_a = has_a && da <= grab && da <= db;
    const bool grab_b = has_b && db <= grab && db < da;
    if (grab_a || grab_b) {
        app.loop_paint = false;
        app.loop_is_a = grab_a;
        app.pivot = grab_a ? (has_b ? transport.loop_b : -1.0) : (has_a ? transport.loop_a : -1.0);
        edit_loop_drag(app, seconds, length);
        return true;
    }
    if (has_a && has_b) {
        return false;
    }
    if (has_a || has_b) {
        app.loop_paint = false;
        app.loop_is_a = !has_a;
        app.pivot = has_a ? transport.loop_a : transport.loop_b;
        edit_loop_drag(app, seconds, length);
        return true;
    }
    app.loop_paint = true;
    app.pivot = snap_loop_time(app, std::clamp(seconds, 0.0, length), length);
    edit_loop_drag(app, seconds, length);
    return true;
}

void edit_loop_drag(App& app, double seconds, double length) {
    if (app.player == nullptr || !(length > 0)) {
        return;
    }
    const double raw = std::clamp(seconds, 0.0, length);
    double moving = snap_loop_time(app, raw, length);
    const bool anchored = app.loop_paint || app.pivot >= 0;
    if (anchored && app.loop_snap && std::abs(moving - app.pivot) <= 1e-3) {
        const int direction = raw >= app.pivot ? 1 : -1;
        const double stepped = adjacent_beat(app, app.pivot, direction, length);
        if (std::abs(stepped - app.pivot) > 1e-3) {
            moving = stepped;
        }
    }
    if (anchored) {
        commit_loop_span(app, app.pivot, moving, length);
        return;
    }
    if (app.loop_is_a) {
        app.player->set_loop_a(moving);
    } else {
        app.player->set_loop_b(moving);
    }
}

bool rounded_icon_button(const char* id, ImVec2 size, float radius, Icon icon, bool engaged, const char* tip, bool enabled) {
    ImGui::PushID(id);
    const ImVec2 origin = ImGui::GetCursorScreenPos();
    const bool pressed = ImGui::InvisibleButton("icon", size);
    const bool disabled = control_disabled(enabled);
    const bool hovered = !disabled && ImGui::IsItemHovered();
    if (hovered && tip != nullptr && tip[0] != '\0') {
        ImGui::SetTooltip("%s", tip);
    }
    const bool hot = engaged || hovered;
    ImDrawList* draw = ImGui::GetWindowDrawList();
    const ImVec2 corner(origin.x + size.x, origin.y + size.y);
    draw->AddRectFilled(origin, corner, paint(hot ? kHi : IM_COL32(51, 51, 51, 255), disabled), radius);
    draw->AddRect(origin, corner, paint(hot ? kHi : IM_COL32(74, 74, 74, 255), disabled), radius);
    const float pad = std::min(size.x, size.y) * 0.16f;
    draw_icon(draw, icon, ImVec2(origin.x + pad, origin.y + pad), ImVec2(corner.x - pad, corner.y - pad), paint(hot ? kHiInk : IM_COL32(210, 210, 210, 255), disabled));
    ImGui::PopID();
    return !disabled && pressed;
}

float PlayerButton::width() {
    return 44.f * ui_scale();
}

float PlayerButton::height() {
    return 38.f * ui_scale();
}

bool PlayerButton::draw() const {
    return rounded_icon_button(id_, ImVec2(width(), height()), 8.f * ui_scale(), icon_, engaged_, tip_, enabled_);
}

float SmallButton::width() {
    return PlayerButton::width() * 0.5f;
}

float SmallButton::height() {
    return PlayerButton::height() * 0.5f;
}

bool SmallButton::draw() const {
    return rounded_icon_button(id_, ImVec2(width(), height()), 4.f * ui_scale(), icon_, engaged_, tip_, enabled_);
}

float MediumButton::width() {
    return PlayerButton::width() * 0.75f;
}

float MediumButton::height() {
    return PlayerButton::height() * 0.75f;
}

bool MediumButton::draw() const {
    return rounded_icon_button(id_, ImVec2(width(), height()), 6.f * ui_scale(), icon_, engaged_, tip_, enabled_);
}

bool draw_value_knob(
    App& app, const char* id, float* value, float size, float gap, float stack, float column, float min_value, float max_value,
    float reset, float sensitivity, bool centered, float visual_center, const char* tip,
    void (*caption)(float, char*, std::size_t), bool enabled) {
    ImGui::BeginGroup();
    const ImVec2 start = ImGui::GetCursorScreenPos();
    const float pad = std::max(3.f * ui_scale(), size * 0.08f);
    ImGui::Dummy(ImVec2(column, stack));
    ImGui::SetCursorScreenPos(ImVec2(start.x + (column - size) * 0.5f, start.y + pad * 0.25f));
    const bool changed =
        knob(app, id, value, min_value, max_value, reset, sensitivity, tip, centered ? visual_center : NAN, size, enabled);
    if (caption != nullptr && value != nullptr) {
        char text[32];
        caption(*value, text, sizeof text);
        const ImVec2 caption_size = ImGui::CalcTextSize(text);
        ImGui::GetWindowDrawList()->AddText(
            ImVec2(start.x + (column - caption_size.x) * 0.5f, start.y + size + pad * 0.25f + gap),
            paint(IM_COL32(141, 141, 141, 255), control_disabled(enabled)), text);
    }
    ImGui::EndGroup();
    return changed;
}

float SmallKnob::diameter() {
    return 30.f * ui_scale();
}

float SmallKnob::height() {
    const float pad = std::max(3.f * ui_scale(), diameter() * 0.08f);
    return diameter() + pad * 0.25f + ui_scale() + ImGui::GetTextLineHeight();
}

float SmallKnob::column_width(const char* sample) {
    const float pad = std::max(3.f * ui_scale(), diameter() * 0.08f);
    const float text = sample == nullptr ? 0.f : ImGui::CalcTextSize(sample).x;
    return std::max(diameter() + pad, text);
}

bool SmallKnob::draw(App& app) const {
    const float size = diameter();
    const float column = column_ > 0.f ? column_ : column_width(nullptr);
    return draw_value_knob(
        app, id_, value_, size, ui_scale(), height(), column, min_, max_, reset_, sensitivity_, centered_, visual_center_, tip_,
        caption_, enabled_);
}

float MediumKnob::diameter() {
    return 36.f * ui_scale();
}

float MediumKnob::height() {
    const float pad = std::max(3.f * ui_scale(), diameter() * 0.08f);
    return diameter() + pad * 0.25f + 1.f + ImGui::GetTextLineHeight();
}

float MediumKnob::column_width(const char* sample) {
    const float pad = std::max(3.f * ui_scale(), diameter() * 0.08f);
    const float text = sample == nullptr ? 0.f : ImGui::CalcTextSize(sample).x;
    return std::max(diameter() + pad, text);
}

bool MediumKnob::draw(App& app) const {
    const float size = diameter();
    const float column = column_ > 0.f ? column_ : column_width(nullptr);
    return draw_value_knob(
        app, id_, value_, size, 1.f, height(), column, min_, max_, reset_, sensitivity_, centered_, visual_center_, tip_, caption_,
        enabled_);
}

float LargeKnob::diameter() {
    return 48.f * ui_scale();
}

float LargeKnob::height() {
    const float pad = std::max(4.f * ui_scale(), diameter() * 0.08f);
    return diameter() + pad * 0.25f + 2.f * ui_scale() + ImGui::GetTextLineHeight();
}

float LargeKnob::column_width(const char* sample) {
    const float pad = std::max(4.f * ui_scale(), diameter() * 0.08f);
    const float text = sample == nullptr ? 0.f : ImGui::CalcTextSize(sample).x;
    return std::max(diameter() + pad, text);
}

bool LargeKnob::draw(App& app) const {
    const float size = diameter();
    const float column = column_ > 0.f ? column_ : column_width(nullptr);
    return draw_value_knob(
        app, id_, value_, size, 2.f * ui_scale(), height(), column, min_, max_, reset_, sensitivity_, centered_, visual_center_, tip_,
        caption_, enabled_);
}

float LevelMeter::bar_width() {
    return 8.f * ui_scale();
}

float LevelMeter::width() {
    return bar_width() * 2.f + 4.f * ui_scale();
}

void LevelMeter::draw(float x, float y, float height, float left, float right) {
    const float bar = bar_width();
    const float gap = 4.f * ui_scale();
    const float label_h = ImGui::GetTextLineHeight();
    ImDrawList* draw = ImGui::GetWindowDrawList();
    draw_meter_bars(draw, ImVec2(x, y), bar, height, left);
    draw_meter_bars(draw, ImVec2(x + bar + gap, y), bar, height, right);
    draw->AddText(ImVec2(x, y + height - label_h + 2.f), paint(IM_COL32(141, 141, 141, 255), item_locked()), "L");
    draw->AddText(ImVec2(x + bar + gap, y + height - label_h + 2.f), paint(IM_COL32(141, 141, 141, 255), item_locked()), "R");
}

float ProgressBar::default_height() {
    return ImGui::GetFrameHeight();
}

void ProgressBar::draw() const {
    float width = width_;
    float height = height_;
    const float fraction = std::clamp(fraction_, 0.f, 1.f);
    if (height <= 0.f) {
        height = default_height();
    }
    if (width < 0.f) {
        width = std::max(1.f, ImGui::GetContentRegionAvail().x);
    }
    ImGui::PushID(id_);
    const ImVec2 origin = ImGui::GetCursorScreenPos();
    // Dummy avoids InvisibleButton frame/nav chrome that looked like a nested outer box.
    ImGui::Dummy(ImVec2(width, height));
    ImDrawList* draw = ImGui::GetWindowDrawList();
    const ImVec2 corner(origin.x + width, origin.y + height);
    const float rounding = std::min(4.f, height * 0.35f);
    draw->AddRectFilled(origin, corner, IM_COL32(17, 17, 17, 255), rounding);
    const float fill_w = std::clamp(width * fraction, 0.f, width);
    if (fill_w > 1.f) {
        const float inset = 1.f;
        const float right = fraction >= 1.f ? corner.x - inset : origin.x + fill_w;
        draw->AddRectFilled(
            ImVec2(origin.x + inset, origin.y + inset), ImVec2(std::max(origin.x + inset + 1.f, right), corner.y - inset),
            fill_, std::max(0.f, rounding - 1.f));
    }
    draw->AddRect(origin, corner, fill_, rounding);
    if (overlay_ != nullptr && overlay_[0] != '\0') {
        const ImVec2 text = ImGui::CalcTextSize(overlay_);
        const ImVec2 pos(origin.x + (width - text.x) * 0.5f, origin.y + (height - text.y) * 0.5f);
        draw->PushClipRect(origin, ImVec2(origin.x + fill_w, corner.y), true);
        draw->AddText(pos, IM_COL32(26, 26, 26, 255), overlay_);
        draw->PopClipRect();
        draw->PushClipRect(ImVec2(origin.x + fill_w, origin.y), corner, true);
        draw->AddText(pos, fill_, overlay_);
        draw->PopClipRect();
    }
    ImGui::PopID();
}

float SideRow::commit(float y) {
    float row_h = 0.f;
    float width = 0.f;
    const float spacing = ImGui::GetStyle().ItemSpacing.x;
    for (std::size_t index = 0; index < entries_.size(); ++index) {
        row_h = std::max(row_h, entries_[index].height);
        if (index > 0) {
            width += entries_[index].gap >= 0.f ? entries_[index].gap : spacing;
        }
        width += entries_[index].width;
    }
    float x = dock_ == Dock::Right ? ImGui::GetWindowWidth() - margin_ - width : ImGui::GetCursorPosX();
    for (std::size_t index = 0; index < entries_.size(); ++index) {
        if (index > 0) {
            x += entries_[index].gap >= 0.f ? entries_[index].gap : spacing;
        }
        const Entry& entry = entries_[index];
        ImGui::SetCursorPos(ImVec2(x, y + (row_h - entry.height) * 0.5f));
        entry.draw();
        x += entry.width;
    }
    return row_h;
}

std::vector<char> read_binary(const std::filesystem::path& path) {
    std::ifstream input(path, std::ios::binary);
    if (!input) {
        return {};
    }
    input.seekg(0, std::ios::end);
    const auto size = input.tellg();
    if (size <= 0) {
        return {};
    }
    input.seekg(0, std::ios::beg);
    std::vector<char> bytes(static_cast<std::size_t>(size));
    input.read(bytes.data(), size);
    if (!input) {
        return {};
    }
    return bytes;
}

std::optional<std::filesystem::path> pick_audio(HWND__* hwnd) {
    IFileOpenDialog* dialog = nullptr;
    if (FAILED(CoCreateInstance(CLSID_FileOpenDialog, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&dialog)))) {
        return std::nullopt;
    }
    DWORD options = 0;
    dialog->GetOptions(&options);
    dialog->SetOptions(options | FOS_FILEMUSTEXIST | FOS_FORCEFILESYSTEM);
    const std::wstring filter_audio = tw("dialog.audio_filter");
    const std::wstring filter_all = tw("dialog.all_files");
    const std::wstring open_title = tw("dialog.open_song");
    const COMDLG_FILTERSPEC filters[] = {
        {filter_audio.c_str(), L"*.wav;*.mp3;*.flac"},
        {filter_all.c_str(), L"*.*"},
    };
    dialog->SetFileTypes(2, filters);
    dialog->SetTitle(open_title.c_str());
    std::optional<std::filesystem::path> result;
    if (SUCCEEDED(dialog->Show(reinterpret_cast<HWND>(hwnd)))) {
        IShellItem* item = nullptr;
        if (SUCCEEDED(dialog->GetResult(&item))) {
            PWSTR path = nullptr;
            if (SUCCEEDED(item->GetDisplayName(SIGDN_FILESYSPATH, &path))) {
                result = std::filesystem::path(path);
                CoTaskMemFree(path);
            }
            item->Release();
        }
    }
    dialog->Release();
    return result;
}

void dismiss_popups_on_click_away() {
    ImGuiContext& context = *GImGui;
    if (context.OpenPopupStack.Size <= 0) {
        return;
    }
    if (ImGui::GetIO().AppFocusLost) {
        ImGui::ClosePopupToLevel(0, false);
        return;
    }
    const int button = ImGui::IsMouseClicked(ImGuiMouseButton_Left) ? 0 : ImGui::IsMouseClicked(ImGuiMouseButton_Right) ? 1 : -1;
    if (button < 0) {
        return;
    }
    const ImVec2 mouse = context.IO.MouseClickedPos[button];
    int keep = 0;
    for (int index = 0; index < context.OpenPopupStack.Size; ++index) {
        const ImGuiPopupData& popup = context.OpenPopupStack[index];
        if (popup.OpenFrameCount == context.FrameCount) {
            keep = index + 1;
            continue;
        }
        ImGuiWindow* window = popup.Window;
        if (window != nullptr && window->Active && window->Rect().Contains(mouse)) {
            keep = index + 1;
        }
    }
    if (keep < context.OpenPopupStack.Size) {
        ImGui::ClosePopupToLevel(keep, true);
    }
}

void draw(App& app) {
    load_language(app);
    static bool settings_seeded = false;
    if (!settings_seeded) {
        load_performance(app);
        load_keymap(app);
        settings_seeded = true;
    }
    seed_shell(app);
    const auto song = app.session->snapshot();
    const auto transport = app.player->snapshot();
    sync_beats(app, song);
    push_metronome(app);
    if (app.metro && transport.playing && app.metro_seen >= 0) {
        const double from = app.metro_seen;
        const double to = transport.position;
        if (transport.loop && to + 0.05 < from) {
            note_beats(app, from, transport.loop_b);
            note_beats(app, transport.loop_a, to);
        } else if (to >= from) {
            note_beats(app, from, to);
        }
    }
    app.metro_seen = transport.position;
    sync_notes(app, *song);
    const bool ready = song->phase == bt::SongPhase::Ready && song->stems != nullptr && song->job != app.closed_job;
    const bool locked = interface_locked(app);
    const bool hold = !ready || locked;
    if (locked) {
        app.wave_drag = -1;
        app.loop_drag = -1;
    }
    if (ready && song->stems != app.loaded) {
        app.player->set_song(song->stems);
        app.loaded = song->stems;
    } else if (!ready && app.loaded) {
        app.player->clear_song();
        app.loaded = nullptr;
    }
    if (song->mix != app.peaks_source || song->stems != app.peak_stems) {
        app.peaks_source = song->mix;
        app.peak_stems = song->stems;
        if (song->mix) {
            build_peaks(*song->mix, app.peaks, app.peak_scale);
        } else if (song->stems) {
            bt::Audio summed;
            summed.sample_rate = bt::kSampleRate;
            summed.channels = 2;
            summed.frames = song->stems->frames;
            summed.samples.assign(static_cast<std::size_t>(2 * summed.frames), 0.f);
            for (int stem = 0; stem < bt::kStemCount; ++stem) {
                const auto& audio = song->stems->audio[static_cast<std::size_t>(stem)];
                if (static_cast<std::int64_t>(audio.size()) < summed.frames * 2) {
                    continue;
                }
                for (std::int64_t frame = 0; frame < summed.frames; ++frame) {
                    summed.samples[static_cast<std::size_t>(frame)] += audio[static_cast<std::size_t>(frame)];
                    summed.samples[static_cast<std::size_t>(summed.frames + frame)] +=
                        audio[static_cast<std::size_t>(summed.frames + frame)];
                }
            }
            build_peaks(summed, app.peaks, app.peak_scale);
        } else {
            app.peaks.clear();
            app.peak_scale = 1.f;
        }
        if (song->stems) {
            for (int stem = 0; stem < bt::kStemCount; ++stem) {
                build_stem_peaks(
                    *song->stems, stem, app.stem_peaks[static_cast<std::size_t>(stem)],
                    app.stem_peak_scale[static_cast<std::size_t>(stem)]);
            }
        } else {
            for (auto& peaks : app.stem_peaks) {
                peaks.clear();
            }
        }
    }
    if (song->revision != app.seen_revision) {
        app.player->set_mix(song->gain, song->mute, song->solo, song->pan);
        if (!ImGui::IsAnyItemActive()) {
            for (int stem = 0; stem < bt::kStemCount; ++stem) {
                app.gain_db[static_cast<std::size_t>(stem)] = gain_to_db(song->gain[static_cast<std::size_t>(stem)]);
                app.pan[static_cast<std::size_t>(stem)] = song->pan[static_cast<std::size_t>(stem)];
            }
            app.seen_revision = song->revision;
        }
    }
    if (app.player != nullptr) {
        static std::array<std::uint64_t, bt::kStemCount> cached_rev{};
        static std::array<std::shared_ptr<const std::vector<bt::MidiSynthNote>>, bt::kStemCount> cached_notes{};
        for (int stem = 0; stem < bt::kStemCount; ++stem) {
            const auto index = static_cast<std::size_t>(stem);
            const App::VerifyStemUi& verify = app.verify[index];
            bt::Player::VerifyLane original;
            original.gain = db_to_gain(verify.original.db);
            original.pan = verify.original.pan;
            original.mute = verify.original.mute;
            original.solo = verify.original.solo;
            bt::Player::VerifyLane midi;
            midi.gain = db_to_gain(verify.midi.db);
            midi.pan = verify.midi.pan;
            midi.mute = verify.midi.mute;
            midi.solo = verify.midi.solo;
            int lo = 0;
            int hi = 0;
            verify_pitch_range(app, stem, lo, hi);
            const std::uint64_t revision =
                (app.note_song << 16) ^ (app.note_ticket[index] << 8) ^
                static_cast<std::uint64_t>(app.notes[index].size());
            if (cached_rev[index] != revision || !cached_notes[index]) {
                auto notes = std::make_shared<std::vector<bt::MidiSynthNote>>();
                notes->reserve(app.notes[index].size());
                for (const MidiNote& note : app.notes[index]) {
                    notes->push_back(bt::MidiSynthNote{note.pitch, note.start, note.end, note.velocity});
                }
                cached_notes[index] = std::move(notes);
                cached_rev[index] = revision;
            }
            app.player->set_verify_mix(stem, original, midi, lo, hi, cached_notes[index], revision);
        }
    }
    if (!ImGui::IsAnyItemActive()) {
        app.speed = transport.speed;
        app.master_db = transport.master_db;
    }
    update_meters(app, *song, transport);

    const ImGuiViewport* viewport = ImGui::GetMainViewport();
    ImGui::SetNextWindowPos(viewport->WorkPos);
    ImGui::SetNextWindowSize(viewport->WorkSize);
    ImGui::PushStyleColor(ImGuiCol_WindowBg, ImVec4(0.110f, 0.110f, 0.110f, 1.f));
    ImGui::PushStyleColor(ImGuiCol_ChildBg, ImVec4(0.110f, 0.110f, 0.110f, 1.f));
    ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2(0.f, 0.f));
    ImGui::PushStyleVar(ImGuiStyleVar_ItemSpacing, ImVec2(6.f, 4.f));
    ImGui::Begin(
        "bt", nullptr,
        ImGuiWindowFlags_NoTitleBar | ImGuiWindowFlags_NoResize | ImGuiWindowFlags_NoMove | ImGuiWindowFlags_NoCollapse |
            ImGuiWindowFlags_NoBringToFrontOnFocus | ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoScrollWithMouse);

    const float scale = ui_scale();
    const float top_h = 32.f * scale;
    const float transport_bottom = 12.f * scale;
    const float transport_h = 164.f * scale + transport_bottom;
    const float split_h = 8.f * scale;
    const float column_gap = ImGui::GetStyle().ItemSpacing.y;
    const float avail = ImGui::GetContentRegionAvail().y;
    const float body = std::max(0.f, avail - top_h - transport_h - column_gap * 2.f);
    const float inner = std::max(1.f, body - split_h);
    const float strip_natural = std::max(74.f * scale, 14.f * scale + MediumButton::height() * 2.f + 2.f);
    const float mixer_pad = 8.f * scale;
    const float strip_gap = 2.f * scale;
    const float row_body = strip_natural - strip_gap;
    const auto tracks_height = [&](int count) {
        if (count <= 0) {
            return 0.f;
        }
        return mixer_pad * 2.f + row_body * static_cast<float>(count) +
               strip_gap * static_cast<float>(count - 1);
    };
    const auto fit_tracks = [&](float height) {
        int count = 0;
        for (int n = 1; n <= bt::kStemCount; ++n) {
            if (tracks_height(n) <= height + 0.5f) {
                count = n;
            }
        }
        return count;
    };
    const float mixer_floor = tracks_height(bt::kStemCount);
    if (app.stage_share < 0.f) {
        const float mixer_init = tracks_height(fit_tracks(inner));
        app.stage_share = inner > 1.f ? (inner - mixer_init) / inner : 0.f;
    }
    const float requested_mixer = std::clamp(inner - app.stage_share * inner, 0.f, std::min(inner, mixer_floor));
    float mixer_h = 0.f;
    const float row_h = row_body;
    int visible = 0;
    if (requested_mixer >= mixer_floor - 0.5f) {
        mixer_h = std::min(mixer_floor, inner);
        visible = bt::kStemCount;
        app.mixer_scroll = 0;
        if (inner > mixer_floor) {
            app.stage_share = (inner - mixer_floor) / inner;
        }
    } else {
        visible = fit_tracks(requested_mixer);
        mixer_h = tracks_height(visible);
        app.stage_share = inner > 1.f ? (inner - mixer_h) / inner : 0.f;
        app.mixer_scroll = std::clamp(app.mixer_scroll, 0, std::max(0, bt::kStemCount - visible));
    }
    const float stage_h = std::max(0.f, inner - mixer_h);
    const double length = [&] {
        if (transport.length > 0) {
            return transport.length;
        }
        if (song->stems && song->stems->frames > 0) {
            return static_cast<double>(song->stems->frames) / static_cast<double>(bt::kSampleRate);
        }
        return 0.0;
    }();

    ImGui::PushStyleColor(ImGuiCol_ChildBg, ImVec4(0.102f, 0.102f, 0.102f, 1.f));
    ImGui::BeginChild("topbar", ImVec2(0, top_h), ImGuiChildFlags_None, ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoScrollWithMouse);
    const bool importing = song->phase == bt::SongPhase::Decoding || song->phase == bt::SongPhase::Separating;
    const bool can_cancel = importing && app.session != nullptr;
    bool show_note = false;
    for (NoteStatus status : app.note_status) {
        if (status == NoteStatus::Running) {
            show_note = true;
            break;
        }
    }
    const float pad = 8.f * scale;
    const float edge = 12.f * scale;
    const float cancel_w = MediumButton::width();
    const float cancel_h = MediumButton::height();
    const float bar_h = std::max(8.f * scale, top_h - 16.f * scale);
    const float gap = 6.f * scale;
    const ImVec2 origin = ImGui::GetCursorScreenPos();
    const float row_w = ImGui::GetContentRegionAvail().x;
    float x = edge;
    float bar_right = row_w - edge;
    if (can_cancel) {
        bar_right -= cancel_w + pad;
    }
    // Status text lives only inside the bar overlay — no duplicate label on the left.
    if (!(importing || show_note)) {
        const char* phase = phase_text(song->phase, app.language);
        if (phase[0] != '\0') {
            ImGui::SetCursorScreenPos(ImVec2(origin.x + x, origin.y + (top_h - ImGui::GetTextLineHeight()) * 0.5f));
            ImGui::TextDisabled("%s", phase);
        }
    } else if (bar_right - x >= 48.f * scale) {
        const int bars = (importing ? 1 : 0) + (show_note ? 1 : 0);
        const float each = (bar_right - x - gap * static_cast<float>(bars - 1)) / static_cast<float>(bars);
        const float bar_y = origin.y + (top_h - bar_h) * 0.5f;
        auto place_bar = [&](const char* id, float fraction, const char* overlay) {
            ImGui::SetCursorScreenPos(ImVec2(origin.x + x, bar_y));
            ProgressBar(id, fraction).size(each, bar_h).overlay(overlay).draw();
            x += each + gap;
        };
        if (importing) {
            const float fraction = song->phase == bt::SongPhase::Separating ? song->progress : 0.f;
            char overlay[64];
            std::snprintf(
                overlay, sizeof overlay, "%s  %.0f%%", phase_text(song->phase, app.language),
                std::clamp(fraction, 0.f, 1.f) * 100.f);
            place_bar("top-import", fraction, overlay);
        }
        if (show_note) {
            const float fraction = std::clamp(app.note_progress, 0.f, 1.f);
            char overlay[64];
            std::snprintf(overlay, sizeof overlay, "%s  %.0f%%", t("transcribe"), fraction * 100.f);
            place_bar("top-notes", fraction, overlay);
        }
    }
    if (can_cancel) {
        ImGui::SetCursorScreenPos(
            ImVec2(origin.x + row_w - edge - cancel_w, origin.y + (top_h - cancel_h) * 0.5f));
        if (MediumButton("cancel", Icon::Cancel).tip(t("cancel")).draw()) {
            app.session->cancel();
        }
    }
    ImGui::EndChild();
    ImGui::PopStyleColor();

    draw_projects(app, *song, length, body);
    ImGui::SameLine(0.f, 0.f);
    ImGui::BeginChild("work", ImVec2(0, body), ImGuiChildFlags_None, ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoScrollWithMouse);
    const ImVec2 work_origin = ImGui::GetCursorScreenPos();

    const bool show_stage = stage_h >= 1.f;
    if (show_stage) {
    ImGui::BeginChild("stage", ImVec2(0, stage_h), ImGuiChildFlags_None, ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoScrollWithMouse);
    if (song->phase == bt::SongPhase::Failed && !song->detail.empty()) {
        ImGui::SetCursorPos(ImVec2(16.f * scale, 16.f * scale));
        ImGui::PushTextWrapPos(ImGui::GetCursorPosX() + ImGui::GetContentRegionAvail().x - 16.f * scale);
        ImGui::TextWrapped("%s", song->detail.c_str());
        ImGui::PopTextWrapPos();
    } else {
        if (hold) {
            ImGui::BeginDisabled();
        }
        draw_midi_stage(app, *song, transport, length, ready, stage_h);
        if (hold) {
            ImGui::EndDisabled();
        }
    }
    ImGui::EndChild();
    }

    if (locked) {
        ImGui::BeginDisabled();
    }
    ImGui::InvisibleButton("split", ImVec2(std::max(1.f, ImGui::GetContentRegionAvail().x), split_h));
    if (!locked && (ImGui::IsItemHovered() || ImGui::IsItemActive())) {
        ImGui::SetMouseCursor(ImGuiMouseCursor_ResizeNS);
    }
    if (!locked && ImGui::IsItemActive()) {
        const float y = std::clamp(ImGui::GetIO().MousePos.y - work_origin.y, 0.f, inner);
        const float requested = std::clamp(inner - y, 0.f, mixer_floor);
        const float snapped = requested >= mixer_floor - 0.5f ? mixer_floor : tracks_height(fit_tracks(requested));
        app.stage_share = inner > 1.f ? (inner - snapped) / inner : 0.f;
    }
    if (ImGui::IsItemHovered() && ImGui::GetIO().MouseWheel != 0.f) {
        const int delta = ImGui::GetIO().MouseWheel > 0.f ? 1 : -1;
        if (delta < 0 || visible < bt::kStemCount) {
            const int count = std::clamp(visible + delta, 0, std::min(bt::kStemCount, fit_tracks(inner)));
            const float snapped = count >= bt::kStemCount ? mixer_floor : tracks_height(count);
            app.stage_share = inner > 1.f ? (inner - snapped) / inner : 0.f;
            if (count >= bt::kStemCount) {
                app.mixer_scroll = 0;
            }
        }
    }
    if (locked) {
        ImGui::EndDisabled();
    }
    {
        const ImVec2 split_min = ImGui::GetItemRectMin();
        const ImVec2 split_max = ImGui::GetItemRectMax();
        const bool hot = ImGui::IsItemHovered() || ImGui::IsItemActive();
        const ImU32 grip = paint(hot ? kHi : IM_COL32(106, 106, 106, 255), locked);
        const float mid_x = (split_min.x + split_max.x) * 0.5f;
        const float mid_y = (split_min.y + split_max.y) * 0.5f;
        ImDrawList* draw = ImGui::GetWindowDrawList();
        draw->AddLine(ImVec2(mid_x - 18.f * scale, mid_y - 3.f), ImVec2(mid_x + 18.f * scale, mid_y - 3.f), grip, 1.f);
        draw->AddLine(ImVec2(mid_x - 18.f * scale, mid_y), ImVec2(mid_x + 18.f * scale, mid_y), grip, 1.f);
        draw->AddLine(ImVec2(mid_x - 18.f * scale, mid_y + 3.f), ImVec2(mid_x + 18.f * scale, mid_y + 3.f), grip, 1.f);
    }

    if (mixer_h >= 1.f) {
        ImGui::PushStyleColor(ImGuiCol_ChildBg, ImVec4(0.165f, 0.165f, 0.165f, 1.f));
        ImGui::BeginChild("mixer", ImVec2(0, mixer_h), ImGuiChildFlags_None, ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoScrollWithMouse);
        if (!hold && visible < bt::kStemCount && ImGui::IsWindowHovered() && ImGui::GetIO().MouseWheel != 0.f) {
            const int delta = ImGui::GetIO().MouseWheel > 0.f ? -1 : 1;
            app.mixer_scroll = std::clamp(app.mixer_scroll + delta, 0, bt::kStemCount - visible);
        }
        if (hold) {
            ImGui::BeginDisabled();
        }
        const float strip_w = std::max(1.f, ImGui::GetContentRegionAvail().x - 8.f * scale);
        float strip_y = mixer_pad;
        for (int slot = 0; slot < visible; ++slot) {
            const int stem = app.mixer_scroll + slot;
            ImGui::SetCursorPos(ImVec2(8.f * scale, strip_y));
            draw_strip(app, *song, transport, length, ready, stem, strip_w, row_h);
            strip_y += row_h + strip_gap;
        }
        if (hold) {
            ImGui::EndDisabled();
        }
        ImGui::EndChild();
        ImGui::PopStyleColor();
    }
    ImGui::EndChild();

    ImGui::PushStyleColor(ImGuiCol_ChildBg, ImVec4(0.125f, 0.125f, 0.125f, 1.f));
    ImGui::BeginChild("transport", ImVec2(0, transport_h), ImGuiChildFlags_None, ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoScrollWithMouse);
    ImGui::SetCursorPos(ImVec2(10.f * scale, 8.f * scale));
    if (hold) {
        ImGui::BeginDisabled();
    }
    const float block_gap = 16.f * scale;
    const float speed_col = MediumKnob::column_width("2.0 x");
    const float seek_col = MediumKnob::column_width("20 s");
    const float db_col = MediumKnob::column_width("-60 dB");
    const float row_y = ImGui::GetCursorPosY();
    SideRow left(SideRow::Dock::Left);
    left.item(PlayerButton::width(), PlayerButton::height(), [&] {
        if (PlayerButton("play", transport.playing ? Icon::Pause : Icon::Play).engaged(transport.playing).tip(transport.playing ? t("pause") : t("play")).draw()) {
            start_or_pause(app, transport);
        }
    });
    left.item(PlayerButton::width(), PlayerButton::height(), [&] {
        if (PlayerButton("count", Icon::Count).engaged(app.count_in).tip(t("count_in")).draw()) {
            app.count_in = !app.count_in;
        }
    });
    left.item(PlayerButton::width(), PlayerButton::height(), [&] {
        if (PlayerButton("stop", Icon::Stop).tip(t("stop")).draw()) {
            app.player->stop();
        }
    });
    left.item(PlayerButton::width(), PlayerButton::height(), [&] {
        if (PlayerButton("loop", Icon::Loop).engaged(transport.loop).tip(transport.loop ? t("loop") : t("loop_off")).draw()) {
            app.player->set_loop_enabled(!transport.loop);
        }
    });
    left.item(PlayerButton::width(), PlayerButton::height(), [&] {
        if (PlayerButton("clear", Icon::Clear).tip(t("clear")).draw()) {
            app.player->clear_loop();
        }
    });
    left.item(PlayerButton::width(), PlayerButton::height(), [&] {
        if (PlayerButton("snap", Icon::Magnet).engaged(app.loop_snap).tip(t("snap")).draw()) {
            app.loop_snap = !app.loop_snap;
        }
    });
    left.item(speed_col, MediumKnob::height(), block_gap, [&] {
        if (MediumKnob("speed", &app.speed)
                .range(0.1f, 2.f)
                .reset(1.f)
                .sensitivity(0.012f)
                .visual_center(1.f)
                .tip(t("speed"))
                .column(speed_col)
                .caption([](float value, char* text, std::size_t size) { std::snprintf(text, size, "%.1f x", value); })
                .draw(app)) {
            app.speed = snap_value(app.speed, 0.1f, 2.f, 0.1f);
            if (app.speed != app.applied_speed) {
                app.applied_speed = app.speed;
                app.player->set_speed(app.speed);
            }
        }
    });
    left.item(seek_col, MediumKnob::height(), block_gap, [&] {
        if (MediumKnob("seek-step", &app.seek_step)
                .range(1.f, 20.f)
                .reset(10.f)
                .sensitivity(0.12f)
                .tip(t("seek_step"))
                .column(seek_col)
                .caption([](float value, char* text, std::size_t size) {
                    std::snprintf(text, size, "%d s", static_cast<int>(std::lround(value)));
                })
                .draw(app)) {
            app.seek_step = snap_value(app.seek_step, 1.f, 20.f, 1.f);
            save_performance(app);
        }
    });
    left.item(db_col, MediumKnob::height(), block_gap, [&] {
        if (MediumKnob("master", &app.master_db)
                .range(-60.f, 6.f)
                .reset(0.f)
                .sensitivity(0.45f)
                .tip(t("master"))
                .column(db_col)
                .caption(format_db)
                .draw(app)) {
            app.player->set_master_db(app.master_db);
        }
    });
    if (!app.device_note.empty()) {
        const ImVec2 note_size = ImGui::CalcTextSize(app.device_note.c_str());
        left.item(note_size.x, note_size.y, [&] { ImGui::TextDisabled("%s", stored(app.device_note)); });
    }
    SideRow right(SideRow::Dock::Right);
    right.margin(10.f * scale);
    right.item(db_col, MediumKnob::height(), [&] {
        if (MediumKnob("click", &app.click_db)
                .range(-60.f, 6.f)
                .reset(0.f)
                .sensitivity(0.45f)
                .tip(t("click"))
                .column(db_col)
                .caption(format_db)
                .draw(app)) {
            push_metronome(app);
        }
    });
    right.item(PlayerButton::width(), PlayerButton::height(), block_gap, [&] {
        if (PlayerButton("metro", Icon::Metro).engaged(app.metro).tip(t("metronome")).draw()) {
            app.metro = !app.metro;
            push_metronome(app);
        }
    });
    right.item(PlayerButton::width(), PlayerButton::height(), [&] {
        if (PlayerButton("beat-again", Icon::Again).tip(t("beat_again")).draw()) {
            rerun_beats(app);
        }
    });
    const float left_h = left.commit(row_y);
    const float controls_h = std::max(left_h, right.commit(row_y));

    char now[16];
    char total[16];
    format_time(transport.position, now, sizeof now);
    format_time(length, total, sizeof total);
    ImGui::SetCursorPos(ImVec2(10.f * scale, row_y + controls_h + 4.f * scale));
    ImGui::TextDisabled("%s  /  %s", now, total);
    ImGui::SetCursorPosX(10.f * scale);
    const ImVec2 wave_cursor = ImGui::GetCursorScreenPos();
    const float window_bottom = ImGui::GetMainViewport()->WorkPos.y + ImGui::GetMainViewport()->WorkSize.y;
    const float wave_h = std::max(1.f, window_bottom - transport_bottom - wave_cursor.y);
    const float meter_gap = 8.f * scale;
    const float meters_w = LevelMeter::width();
    const float wave_w = std::max(1.f, ImGui::GetContentRegionAvail().x - 10.f * scale - meters_w - meter_gap);
    draw_wave(app, "mix", kMixDrag, app.peaks, song_wave_scale(app), kWaveColor, ImVec2(wave_w, wave_h), transport, length, ready, true);
    LevelMeter::draw(wave_cursor.x + wave_w + meter_gap, wave_cursor.y, wave_h, app.master_meter_l, app.master_meter_r);
    if (hold) {
        ImGui::EndDisabled();
    }
    handle_actions(app, ready && !locked);
    ImGui::EndChild();
    ImGui::PopStyleColor();

    ImGui::End();
    ImGui::PopStyleVar(2);
    ImGui::PopStyleColor(2);
    draw_monitor(app);
    dismiss_popups_on_click_away();
}

bool interface_locked(const App& app) {
    if (app.session == nullptr) {
        return false;
    }
    const auto song = app.session->snapshot();
    if (song == nullptr) {
        return false;
    }
    if (song->phase == bt::SongPhase::Decoding || song->phase == bt::SongPhase::Separating) {
        return true;
    }
    for (int stem = 0; stem < bt::kStemCount; ++stem) {
        const auto index = static_cast<std::size_t>(stem);
        if (app.note_status[index] == NoteStatus::Running && app.note_refresh[index]) {
            return true;
        }
    }
    return false;
}

void load_output(App& app) {
    if (app.player == nullptr || app.cache_directory.empty()) {
        return;
    }
    std::ifstream input(app.cache_directory / "output-device.txt");
    std::string token;
    std::getline(input, token);
    while (!token.empty() && (token.back() == '\r' || token.back() == ' ')) {
        token.pop_back();
    }
    if (token.empty() || token == "default") {
        return;
    }
    if (auto switched = app.player->use_device(token); !switched) {
        app.device_note = switched.error();
        return;
    }
    app.output_id = token;
    app.device_note.clear();
}

void save_output(const App& app) {
    if (app.cache_directory.empty()) {
        return;
    }
    std::error_code error;
    std::filesystem::create_directories(app.cache_directory, error);
    std::ofstream output(app.cache_directory / "output-device.txt", std::ios::trunc);
    output << (app.output_id.empty() ? "default" : app.output_id) << '\n';
}

}  // namespace btapp
