#include "midi_view.hpp"

#include "i18n.hpp"
#include "notes.hpp"

#include <algorithm>
#include <cmath>
#include <cstdio>

namespace btapp {
namespace {

constexpr int kFallDrag = 8;
constexpr int kRollNoteDrag = 10;
constexpr int kRollOverviewDrag = 11;
constexpr int kRollPanDrag = 12;
constexpr int kRollSeekDrag = 13;
constexpr int kRollBoxDrag = 14;
constexpr int kEditMove = 0;
constexpr int kEditStart = 1;
constexpr int kEditEnd = 2;
constexpr int kEditPlace = 3;
constexpr int kHandSpan = 4;
constexpr double kFallLook = 4.0;
constexpr float kOutOfRangeNoteAlpha = 0.22f;
constexpr int kPianoLo = 21;
constexpr int kPianoHi = 108;
constexpr int kGuitarOpen[6] = {64, 59, 55, 50, 45, 40};
constexpr int kBassOpen[4] = {43, 38, 33, 28};
constexpr int kInlaySingle[] = {3, 5, 7, 9, 15, 17, 19, 21};
constexpr int kInlayDouble[] = {12, 24};
constexpr float kGuitarGauge[6] = {0.010f, 0.013f, 0.017f, 0.026f, 0.036f, 0.046f};
constexpr float kBassGauge[4] = {0.045f, 0.065f, 0.085f, 0.105f};

constexpr ImU32 kPitchColor[12] = {
    IM_COL32(224, 91, 91, 255),  IM_COL32(221, 136, 74, 255), IM_COL32(221, 183, 74, 255), IM_COL32(181, 201, 79, 255),
    IM_COL32(108, 196, 94, 255), IM_COL32(86, 194, 147, 255), IM_COL32(86, 188, 194, 255), IM_COL32(97, 152, 213, 255),
    IM_COL32(129, 122, 218, 255), IM_COL32(168, 114, 216, 255), IM_COL32(206, 104, 199, 255), IM_COL32(213, 97, 152, 255),
};

struct Spot {
    int string = 0;
    int fret = 0;
    int pitch = 0;
};

float ui_scale() {
    const float scale = ImGui::GetIO().FontGlobalScale;
    return scale > 0.f ? scale : 1.f;
}

bool black_key(int pitch) {
    const int degree = pitch % 12;
    return degree == 1 || degree == 3 || degree == 6 || degree == 8 || degree == 10;
}

bool inlay_single(int fret) {
    for (const int mark : kInlaySingle) {
        if (mark == fret) {
            return true;
        }
    }
    return false;
}

bool inlay_double(int fret) {
    for (const int mark : kInlayDouble) {
        if (mark == fret) {
            return true;
        }
    }
    return false;
}

ImU32 with_alpha(ImU32 color, float alpha) {
    const int channel = std::clamp(static_cast<int>(std::lround(alpha * 255.f)), 0, 255);
    return (color & 0x00FFFFFFu) | (static_cast<ImU32>(channel) << 24);
}

void text_at(ImDrawList* draw, ImVec2 top_left, float size, ImU32 color, const char* text) {
    draw->AddText(ImGui::GetFont(), size, top_left, color, text);
}

ImVec2 text_size(float size, const char* text) {
    return ImGui::GetFont()->CalcTextSizeA(size, 10000.f, 0.f, text);
}

int key_span(const App& app, int& lo, int& hi) {
    lo = std::min(app.key_min, app.key_max);
    hi = std::max(app.key_min, app.key_max);
    return std::max(1, hi - lo + 1);
}

int pick_window(const App& app, int num_frets) {
    const int minimum = app.fret_allow_open ? 0 : 1;
    const int maximum = std::max(minimum, num_frets - kHandSpan);
    if (app.fret_prefer > 0) {
        return std::clamp(app.fret_prefer - 1, minimum, maximum);
    }
    return std::clamp(static_cast<int>(std::lround(app.hand_center)), minimum, maximum);
}

int window_distance(int fret, int low, int high) {
    if (fret >= low && fret <= high) {
        return 0;
    }
    return std::min(std::abs(fret - low), std::abs(fret - high));
}

int collect_press(int pitch, const int* opens, int strings, int num_frets, bool allow_open, Spot* out, int cap) {
    const int minimum = allow_open ? 0 : 1;
    int count = 0;
    for (int string = 0; string < strings && count < cap; ++string) {
        const int fret = pitch - opens[string];
        if (fret >= minimum && fret <= num_frets) {
            out[count++] = Spot{string, fret, pitch};
        }
    }
    return count;
}

void draw_beat_frame(App& app, ImDrawList* draw, ImVec2 origin, ImVec2 size) {
    const int level = app.beat_flash_level;
    const double age = ImGui::GetTime() - app.beat_flash_at;
    const double duration = level >= 2 ? 0.17 : level == 1 ? 0.12 : 0.08;
    if (!app.metro || age < 0 || age >= duration) {
        return;
    }
    const float fade = 1.f - static_cast<float>(age / duration);
    const float peak = level >= 2 ? 242.f : level == 1 ? 158.f : 90.f;
    const int alpha = static_cast<int>(fade * fade * peak);
    const float thick = (level >= 2 ? 4.f : level == 1 ? 3.f : 2.f) * ui_scale();
    const ImU32 frame = IM_COL32(255, 255, 255, alpha);
    const ImVec2 corner(origin.x + size.x, origin.y + size.y);
    draw->AddRectFilled(origin, ImVec2(corner.x, origin.y + thick), frame);
    draw->AddRectFilled(ImVec2(origin.x, corner.y - thick), corner, frame);
    draw->AddRectFilled(origin, ImVec2(origin.x + thick, corner.y), frame);
    draw->AddRectFilled(ImVec2(corner.x - thick, origin.y), corner, frame);
}

void draw_fretboard(App& app, ImDrawList* draw, ImVec2 origin, ImVec2 size, int stem, double position) {
    const float scale_px = ui_scale();
    const bool bass = app.midi_view == MidiView::Bass;
    const int* opens = bass ? kBassOpen : kGuitarOpen;
    const int strings = bass ? 4 : 6;
    const int num_frets = kFretCount;
    constexpr float string_gap_units = 0.74f;
    constexpr float edge_units = 0.46f;
    constexpr float open_lane = 0.9f;
    const float name_w = 48.f * scale_px;
    const float pad_t = 8.f * scale_px;
    const float pad_b = 20.f * scale_px;
    const float pad_r = 12.f * scale_px;
    const float board_w = open_lane + static_cast<float>(num_frets);
    const float board_h = edge_units * 2.f + static_cast<float>(strings - 1) * string_gap_units;
    const float avail_w = std::max(1.f, size.x - name_w - pad_r);
    const float avail_h = std::max(1.f, size.y - pad_t - pad_b);
    const float scale = std::min(avail_w / board_w, avail_h / board_h);
    const float origin_x = origin.x + name_w + (avail_w - board_w * scale) * 0.5f;
    const float origin_y = origin.y + pad_t + (avail_h - board_h * scale) * 0.5f;
    float fret_x[25];
    for (int fret = 0; fret <= num_frets; ++fret) {
        fret_x[fret] = origin_x + (open_lane + static_cast<float>(fret)) * scale;
    }
    float string_y[6];
    for (int index = 0; index < strings; ++index) {
        string_y[index] = origin_y + (edge_units + static_cast<float>(index) * string_gap_units) * scale;
    }
    const float board_top = origin_y;
    const float board_bot = origin_y + board_h * scale;
    const float mid_y = (string_y[0] + string_y[strings - 1]) * 0.5f;
    const float string_gap = string_gap_units * scale;
    auto fret_center = [&](int fret) {
        if (fret <= 0) {
            return fret_x[0] - scale * 0.42f;
        }
        return (fret_x[fret - 1] + fret_x[fret]) * 0.5f;
    };

    draw->AddRectFilled(ImVec2(fret_x[0], board_top), ImVec2(fret_x[num_frets], board_bot), IM_COL32(46, 34, 26, 255));
    draw->AddRect(ImVec2(fret_x[0], board_top), ImVec2(fret_x[num_frets], board_bot), IM_COL32(60, 45, 35, 255));

    const float number_size = std::max(11.f * scale_px, string_gap * 0.22f);
    for (int fret = 0; fret <= num_frets; ++fret) {
        const float thick = fret == 0 ? std::max(3.f * scale_px, scale * 0.07f) : std::max(1.f, scale_px);
        draw->AddLine(ImVec2(fret_x[fret], board_top), ImVec2(fret_x[fret], board_bot), fret == 0 ? IM_COL32(200, 200, 210, 255) : IM_COL32(120, 120, 130, 255), thick);
        if (inlay_single(fret) || inlay_double(fret)) {
            char label[8];
            std::snprintf(label, sizeof label, "%d", fret);
            const ImVec2 ts = text_size(number_size, label);
            text_at(draw, ImVec2(fret_center(fret) - ts.x * 0.5f, board_bot + 4.f * scale_px), number_size, IM_COL32(150, 150, 160, 255), label);
        }
    }

    const float name_size = std::max(11.f * scale_px, string_gap * 0.38f);
    for (int index = 0; index < strings; ++index) {
        char name[8];
        midi_note_name(opens[index], name, sizeof name);
        const ImVec2 ts = text_size(name_size, name);
        text_at(draw, ImVec2(origin_x - 6.f * scale_px - ts.x, string_y[index] - ts.y * 0.5f), name_size, IM_COL32(200, 200, 210, 255), name);
    }

    int fret_xi[25];
    for (int fret = 0; fret <= num_frets; ++fret) {
        fret_xi[fret] = static_cast<int>(std::lround(fret_x[fret]));
    }
    int lane_y[7];
    lane_y[0] = static_cast<int>(std::floor(board_top));
    for (int index = 1; index < strings; ++index) {
        lane_y[index] = static_cast<int>(std::lround((string_y[index - 1] + string_y[index]) * 0.5f));
    }
    lane_y[strings] = static_cast<int>(std::ceil(board_bot));
    const int stored_min = bass ? app.bass_pitch_min : app.guitar_pitch_min;
    const int stored_max = bass ? app.bass_pitch_max : app.guitar_pitch_max;
    int span_low = 0;
    int span_high = 0;
    instrument_pitch_bounds(bass, span_low, span_high);
    const int pitch_min = std::clamp(std::min(stored_min, stored_max), span_low, span_high);
    const int pitch_max = std::clamp(std::max(stored_min, stored_max), span_low, span_high);
    for (int index = 0; index < strings; ++index) {
        const int y1 = lane_y[index];
        const int y2 = lane_y[index + 1];
        if (y2 <= y1) {
            continue;
        }
        for (int fret = 1; fret <= num_frets; ++fret) {
            if (pitch_min <= opens[index] + fret && opens[index] + fret <= pitch_max) {
                continue;
            }
            draw->AddRectFilled(
                ImVec2(static_cast<float>(fret_xi[fret - 1]), static_cast<float>(y1)),
                ImVec2(static_cast<float>(fret_xi[fret]), static_cast<float>(y2)), IM_COL32(0, 0, 0, 145));
        }
    }

    const float dot = std::max(3.2f * scale_px, string_gap * 0.2f);
    const ImU32 inlay = IM_COL32(214, 208, 196, 235);
    for (const int fret : kInlaySingle) {
        if (fret > num_frets) {
            continue;
        }
        draw->AddCircleFilled(ImVec2(fret_center(fret), mid_y), dot, inlay, 16);
    }
    for (const int fret : kInlayDouble) {
        if (fret > num_frets) {
            continue;
        }
        const float cx = fret_center(fret);
        const float split = (string_y[strings - 1] - string_y[0]) * 0.22f;
        draw->AddCircleFilled(ImVec2(cx, mid_y - split), dot, inlay, 16);
        draw->AddCircleFilled(ImVec2(cx, mid_y + split), dot, inlay, 16);
    }
    const float gauge_unit = std::max(1.1f * scale_px, string_gap * 0.042f);
    const ImU32 string_color = IM_COL32(198, 200, 208, 255);
    for (int index = 0; index < strings; ++index) {
        const float gauge = bass ? kBassGauge[index] : kGuitarGauge[index];
        const float thick = std::min(string_gap * 0.36f, gauge / 0.010f * gauge_unit);
        draw->AddLine(ImVec2(fret_x[0], string_y[index]), ImVec2(fret_x[num_frets], string_y[index]), string_color, thick);
    }
    for (int index = 0; index < strings; ++index) {
        const int y1 = lane_y[index];
        const int y2 = lane_y[index + 1];
        if (y2 <= y1) {
            continue;
        }
        auto edge = [&](int pitch, bool upper) {
            const int fret = pitch - opens[index];
            if (fret <= 0) {
                return static_cast<float>(fret_xi[0]);
            }
            if (fret > num_frets) {
                return static_cast<float>(fret_xi[num_frets]);
            }
            return static_cast<float>(fret_xi[upper ? fret : fret - 1]);
        };
        draw->AddLine(ImVec2(edge(pitch_min, false), static_cast<float>(y1)), ImVec2(edge(pitch_min, false), static_cast<float>(y2)), IM_COL32(100, 195, 225, 255), 3.f * scale_px);
        draw->AddLine(ImVec2(edge(pitch_max, true), static_cast<float>(y1)), ImVec2(edge(pitch_max, true), static_cast<float>(y2)), IM_COL32(225, 168, 100, 255), 3.f * scale_px);
    }

    const float radius = std::min(scale, string_gap) * 0.34f;
    auto in_mask = [&](int pitch) {
        return pitch_min <= pitch && pitch <= pitch_max;
    };
    auto draw_press = [&](const Spot& spot, float alpha, bool label) {
        const bool masked = in_mask(spot.pitch);
        const float draw_alpha = alpha * (masked ? 1.f : kOutOfRangeNoteAlpha);
        const ImVec2 center(fret_center(spot.fret), string_y[spot.string]);
        draw->AddCircleFilled(center, radius, with_alpha(kPitchColor[spot.pitch % 12], draw_alpha), 20);
        if (!label || !masked) {
            return;
        }
        char name[8];
        midi_note_name(spot.pitch, name, sizeof name);
        const float font = std::max(9.f * scale_px, radius * 0.92f);
        const ImVec2 ts = text_size(font, name);
        text_at(draw, ImVec2(center.x - ts.x * 0.5f, center.y - ts.y * 0.5f), font, with_alpha(IM_COL32(255, 255, 255, 255), draw_alpha), name);
    };
    auto draw_ring = [&](const Spot& spot) {
        if (!in_mask(spot.pitch)) {
            return;
        }
        draw->AddCircle(ImVec2(fret_center(spot.fret), string_y[spot.string]), radius, with_alpha(kPitchColor[spot.pitch % 12], 55.f / 255.f), 20, 2.f * scale_px);
    };
    auto draw_harmonic = [&](int pitch, float alpha) {
        if (!app.fret_harmonic || !in_mask(pitch)) {
            return;
        }
        for (int string = 0; string < strings; ++string) {
            const int interval = pitch - opens[string];
            int touch = 0;
            if (interval == 12) {
                touch = 12;
            } else if (interval == 19) {
                touch = 7;
            } else if (interval == 24) {
                touch = 5;
            } else {
                continue;
            }
            if (touch > num_frets) {
                continue;
            }
            const float cx = fret_center(touch);
            const float cy = string_y[string];
            const float hr = radius * 1.6f;
            const ImVec2 diamond[4] = {ImVec2(cx, cy - hr), ImVec2(cx + hr, cy), ImVec2(cx, cy + hr), ImVec2(cx - hr, cy)};
            draw->AddPolyline(diamond, 4, with_alpha(kPitchColor[pitch % 12], alpha), ImDrawFlags_Closed, 2.f * scale_px);
        }
    };
    auto draw_hand = [&](const Spot& spot) {
        if (!in_mask(spot.pitch)) {
            return;
        }
        const float cx = fret_center(spot.fret);
        const float cy = string_y[spot.string];
        const float r = radius * 1.5f;
        const ImVec2 shape[6] = {
            ImVec2(cx - r, cy - r), ImVec2(cx - r, cy + r * 0.4f), ImVec2(cx - r * 0.5f, cy + r),
            ImVec2(cx + r * 0.5f, cy + r), ImVec2(cx + r, cy + r * 0.4f), ImVec2(cx + r, cy - r)};
        draw->AddPolyline(shape, 6, IM_COL32(255, 255, 255, 255), ImDrawFlags_None, 2.f * scale_px);
    };
    auto alpha_for = [&](int velocity) {
        const float norm = std::clamp(static_cast<float>(velocity) / static_cast<float>(kFretBright), 0.f, 1.f);
        return (12.f + norm * 243.f) / 255.f;
    };

    int pitches[32];
    int velocities[32];
    int pitch_count = 0;
    for (const MidiNote& note : app.notes[static_cast<std::size_t>(stem)]) {
        if (note.pitch < span_low || note.pitch > span_high || note.start > position || position >= note.end) {
            continue;
        }
        int slot = -1;
        for (int index = 0; index < pitch_count; ++index) {
            if (pitches[index] == note.pitch) {
                slot = index;
                break;
            }
        }
        if (slot < 0) {
            if (pitch_count >= 32) {
                continue;
            }
            pitches[pitch_count] = note.pitch;
            velocities[pitch_count] = note.velocity;
            ++pitch_count;
        } else {
            velocities[slot] = std::max(velocities[slot], note.velocity);
        }
    }

    if (app.fret_suggest) {
        const int anchor = pick_window(app, num_frets);
        const int low = anchor;
        const int high = anchor + kHandSpan;
        Spot rings[128];
        int ring_count = 0;
        Spot emphasized[32];
        int emphasized_velocity[32];
        int emphasized_count = 0;
        for (int index = 0; index < pitch_count; ++index) {
            Spot found[8];
            const int all_count = collect_press(pitches[index], opens, strings, num_frets, true, found, 8);
            for (int spot = 0; spot < all_count && ring_count < 128; ++spot) {
                rings[ring_count++] = found[spot];
            }
            Spot pool[8];
            int pool_count = collect_press(pitches[index], opens, strings, num_frets, app.fret_allow_open, pool, 8);
            if (pool_count == 0) {
                pool_count = collect_press(pitches[index], opens, strings, num_frets, true, pool, 8);
            }
            if (pool_count == 0) {
                continue;
            }
            Spot windowed[8];
            int window_count = 0;
            for (int spot = 0; spot < pool_count; ++spot) {
                if (pool[spot].fret >= low && pool[spot].fret <= high) {
                    windowed[window_count++] = pool[spot];
                }
            }
            const Spot* source = window_count > 0 ? windowed : pool;
            const int source_count = window_count > 0 ? window_count : pool_count;
            Spot chosen = source[0];
            int best = 1000000;
            for (int spot = 0; spot < source_count; ++spot) {
                const int distance = window_count > 0 ? std::abs(source[spot].fret - anchor) : window_distance(source[spot].fret, low, high);
                const int score = distance * 10 + source[spot].string;
                if (score < best) {
                    best = score;
                    chosen = source[spot];
                }
            }
            emphasized_velocity[emphasized_count] = velocities[index];
            emphasized[emphasized_count++] = chosen;
        }
        Spot hand[8];
        int hand_count = 0;
        for (int index = 0; index < emphasized_count; ++index) {
            if (emphasized[index].fret < low || emphasized[index].fret > high) {
                continue;
            }
            int slot = -1;
            for (int hand_index = 0; hand_index < hand_count; ++hand_index) {
                if (hand[hand_index].string == emphasized[index].string) {
                    slot = hand_index;
                    break;
                }
            }
            if (slot < 0 && hand_count < 8) {
                hand[hand_count++] = emphasized[index];
            } else if (slot >= 0 && emphasized[index].fret < hand[slot].fret) {
                hand[slot] = emphasized[index];
            }
        }
        std::sort(hand, hand + hand_count, [](const Spot& left, const Spot& right) { return left.string < right.string; });
        app.hand_center += 0.3f * (static_cast<float>(anchor) - app.hand_center);
        if (app.fret_show_alt) {
            for (int index = 0; index < ring_count; ++index) {
                draw_ring(rings[index]);
            }
        }
        for (int index = 0; index < emphasized_count; ++index) {
            const float alpha = alpha_for(emphasized_velocity[index]);
            draw_press(emphasized[index], alpha, true);
            draw_harmonic(emphasized[index].pitch, alpha);
        }
        for (int index = 0; index < hand_count; ++index) {
            draw_hand(hand[index]);
        }
    } else {
        for (int index = 0; index < pitch_count; ++index) {
            const float alpha = alpha_for(velocities[index]);
            Spot found[8];
            const int count = collect_press(pitches[index], opens, strings, num_frets, true, found, 8);
            for (int spot = 0; spot < count; ++spot) {
                draw_press(found[spot], alpha, true);
            }
            draw_harmonic(pitches[index], alpha);
        }
    }
}

struct PianoKey {
    int pitch = 0;
    bool black = false;
    bool sounding = false;
    bool in_range = true;
    ImVec2 min{};
    ImVec2 max{};
};

PianoKey make_piano_key(int pitch, float x, float slot, float gap, float top, float bottom, bool sounding, bool in_range) {
    PianoKey key;
    key.pitch = pitch;
    key.black = black_key(pitch);
    key.sounding = sounding;
    key.in_range = in_range;
    key.min = ImVec2(x + gap, top);
    key.max = ImVec2(x + std::max(gap + 1.f, slot), bottom);
    return key;
}

ImU32 piano_key_fill(const PianoKey& key) {
    if (key.sounding) {
        return kPitchColor[key.pitch % 12];
    }
    return key.black ? IM_COL32(22, 22, 22, 255) : IM_COL32(228, 228, 228, 255);
}

ImU32 piano_key_ink(const PianoKey& key) {
    if (!key.in_range) {
        return IM_COL32(120, 120, 120, 180);
    }
    // Dark ink only on idle white keys; black / lit keys use light ink.
    return key.black || key.sounding ? IM_COL32(245, 245, 245, 230) : IM_COL32(28, 28, 28, 230);
}

void draw_piano_key(ImDrawList* draw, const PianoKey& key, float scale, float font) {
    draw->AddRectFilled(key.min, key.max, piano_key_fill(key));
    if (!key.in_range) {
        draw->AddRectFilled(key.min, key.max, IM_COL32(0, 0, 0, 145));
    }
    char name[8];
    midi_note_name(key.pitch, name, sizeof name);
    float used = font;
    ImVec2 ts = text_size(used, name);
    const float limit_w = std::max(1.f, key.max.x - key.min.x - 2.f * scale);
    const float limit_h = std::max(1.f, key.max.y - key.min.y - 2.f * scale);
    if (ts.x > limit_w || ts.y > limit_h) {
        const float by_w = ts.x > 0.f ? limit_w / ts.x : 1.f;
        const float by_h = ts.y > 0.f ? limit_h / ts.y : 1.f;
        used = std::max(5.f * scale, used * std::min(by_w, by_h));
        ts = text_size(used, name);
    }
    const float text_x = (key.min.x + key.max.x - ts.x) * 0.5f;
    const float text_y = key.max.y - ts.y - 3.f * scale;
    draw->PushClipRect(key.min, key.max, true);
    text_at(draw, ImVec2(text_x, text_y), used, piano_key_ink(key), name);
    draw->PopClipRect();
}

void draw_fall(App& app, ImDrawList* draw, ImVec2 origin, ImVec2 size, int stem, double position, double length, bool ready) {
    const float scale = ui_scale();
    const bt::Player::Snapshot transport = app.player != nullptr ? app.player->snapshot() : bt::Player::Snapshot{};
    const float keyboard_h = std::min(72.f * scale, size.y * 0.28f);
    const float lane_h = std::max(1.f, size.y - keyboard_h);
    const int lo = kPianoLo;
    const int hi = kPianoHi;
    const int span = hi - lo + 1;
    int range_lo = 0;
    int range_hi = 0;
    key_span(app, range_lo, range_hi);
    range_lo = std::clamp(range_lo, lo, hi);
    range_hi = std::clamp(range_hi, lo, hi);
    if (range_lo > range_hi) {
        std::swap(range_lo, range_hi);
    }
    const float slot = size.x / static_cast<float>(span);
    auto x_of_pitch = [&](int pitch) {
        return origin.x + (static_cast<float>(pitch - lo) / static_cast<float>(span)) * size.x;
    };
    auto y_of = [&](double time) {
        return origin.y + lane_h - static_cast<float>((time - position) / kFallLook) * lane_h;
    };
    if (app.grid && app.beat_times != nullptr && !app.beat_times->empty()) {
        const int parts = division_parts(app.grid_division);
        const double gap = minimum_gap(*app.beat_times, position, position + kFallLook);
        const bool show_sub =
            parts > 1 && gap > 1e-6 && static_cast<float>(gap / static_cast<double>(parts) / kFallLook) * lane_h >= 3.f * scale;
        each_grid_tick(*app.beat_times, app.grid_division, position, position + kFallLook, [&](double time, GridMark mark) {
            if (mark == GridMark::Sub && !show_sub) {
                return;
            }
            const float thick = mark == GridMark::Bar ? std::max(1.f, scale) : 1.f;
            const ImU32 color = mark == GridMark::Bar ? IM_COL32(255, 255, 255, 71)
                                : mark == GridMark::Beat ? IM_COL32(255, 255, 255, 26)
                                                         : IM_COL32(255, 255, 255, 16);
            draw->AddRectFilled(ImVec2(origin.x, y_of(time)), ImVec2(origin.x + size.x, y_of(time) + thick), color);
        });
    }
    for (int pitch = lo; pitch <= hi; ++pitch) {
        if (pitch >= range_lo && pitch <= range_hi) {
            continue;
        }
        const float x = x_of_pitch(pitch);
        draw->AddRectFilled(
            ImVec2(x, origin.y), ImVec2(x + slot, origin.y + lane_h), IM_COL32(0, 0, 0, 145));
    }
    bool live[128] = {};
    for (const MidiNote& note : app.notes[static_cast<std::size_t>(stem)]) {
        if (note.pitch < lo || note.pitch > hi) {
            continue;
        }
        const float top = y_of(note.end);
        const float bottom = y_of(note.start);
        if (bottom < origin.y || top > origin.y + lane_h) {
            continue;
        }
        const bool masked = note.pitch >= range_lo && note.pitch <= range_hi;
        const bool sounding = note.start <= position && position < note.end;
        if (masked && sounding && note.pitch >= 0 && note.pitch < 128) {
            live[note.pitch] = true;
        }
        const float y1 = std::max(origin.y, top);
        const float y2 = std::min(origin.y + lane_h, bottom);
        const float width = std::max(3.f * scale, slot * 0.72f);
        const float x = x_of_pitch(note.pitch) + slot * 0.5f;
        const float bar_h = std::max(y1 + 2.f * scale, y2) - y1;
        const float alpha = (sounding ? 1.f : 0.82f) * (masked ? 1.f : kOutOfRangeNoteAlpha);
        draw->AddRectFilled(ImVec2(x - width * 0.5f, y1), ImVec2(x + width * 0.5f, y1 + bar_h), with_alpha(kPitchColor[note.pitch % 12], alpha));
    }
    if (length > 0) {
        draw_ab_loop(
            draw, scale, origin, ImVec2(origin.x + size.x, origin.y + lane_h), transport.loop_a >= 0, y_of(std::max(0.0, transport.loop_a)),
            transport.loop_b >= 0, y_of(std::max(0.0, transport.loop_b)), true, IM_COL32(242, 242, 242, 40), IM_COL32(242, 242, 242, 255),
            IM_COL32(242, 242, 242, 255), IM_COL32(26, 26, 26, 255), IM_COL32(26, 26, 26, 180));
    }
    draw->AddRectFilled(ImVec2(origin.x, origin.y + lane_h - scale), ImVec2(origin.x + size.x, origin.y + lane_h + scale), IM_COL32(242, 242, 242, 255));
    const float key_top = origin.y + lane_h;
    const float key_bottom = origin.y + size.y;
    const float key_gap = scale;
    const float key_font = std::min(12.f * scale, std::max(8.f * scale, slot * 0.38f));
    for (int pitch = lo; pitch <= hi; ++pitch) {
        const bool in_range = pitch >= range_lo && pitch <= range_hi;
        const bool sounding = in_range && pitch >= 0 && pitch < 128 && live[pitch];
        draw_piano_key(
            draw, make_piano_key(pitch, x_of_pitch(pitch), slot, key_gap, key_top, key_bottom, sounding, in_range), scale, key_font);
    }

    if (!ready || !(length > 0)) {
        return;
    }
    const bool hovered = ImGui::IsItemHovered();
    auto time_at = [&](float mouse_y) {
        const float y = std::clamp(mouse_y - origin.y, 0.f, lane_h);
        return std::clamp(position + (1.0 - static_cast<double>(y / lane_h)) * kFallLook, 0.0, length);
    };
    if (hovered && ImGui::IsMouseClicked(ImGuiMouseButton_Left)) {
        app.wave_drag = kFallDrag;
        app.loop_drag = -1;
        app.midi_drag_y = ImGui::GetIO().MousePos.y;
    }
    if (app.wave_drag == kFallDrag) {
        if (!ImGui::IsMouseDown(ImGuiMouseButton_Left)) {
            app.wave_drag = -1;
        } else {
            // Phone-style: content follows the finger (drag down → seek forward).
            const float mouse_y = ImGui::GetIO().MousePos.y;
            const double delta =
                static_cast<double>((mouse_y - app.midi_drag_y) / lane_h) * kFallLook;
            app.midi_drag_y = mouse_y;
            if (delta != 0.0) {
                app.player->seek(std::clamp(position + delta, 0.0, length));
            }
        }
    }
    if (hovered && ImGui::IsMouseClicked(ImGuiMouseButton_Right)) {
        if (edit_loop_begin(
                app, transport, time_at(ImGui::GetIO().MousePos.y), length, ImGui::GetIO().MousePos.y, y_of(std::max(0.0, transport.loop_a)),
                y_of(std::max(0.0, transport.loop_b)), 36.f * scale)) {
            app.loop_drag = kFallDrag;
            app.wave_drag = -1;
        }
    }
    if (app.loop_drag == kFallDrag) {
        if (!ImGui::IsMouseDown(ImGuiMouseButton_Right)) {
            app.loop_drag = -1;
        } else {
            edit_loop_drag(app, time_at(ImGui::GetIO().MousePos.y), length);
        }
    }
}

double roll_song_span(double length) {
    return length > 0.25 ? length : 60.0;
}

void clamp_roll(App& app, double length) {
    app.roll_semitones = std::clamp(app.roll_semitones, 12, 84);
    app.roll_bottom = std::clamp(app.roll_bottom, 0, 128 - app.roll_semitones);
    const double song = roll_song_span(length);
    app.roll_seconds = std::clamp(app.roll_seconds, 0.25, song);
    app.roll_time = std::clamp(app.roll_time, 0.0, song - app.roll_seconds);
}

void fit_roll(App& app, int stem, double length) {
    const auto& notes = app.notes[static_cast<std::size_t>(stem)];
    int low = 48;
    int high = 71;
    if (!notes.empty()) {
        low = notes.front().pitch;
        high = notes.front().pitch;
        for (const MidiNote& note : notes) {
            low = std::min(low, note.pitch);
            high = std::max(high, note.pitch);
        }
        low = std::max(0, low - 2);
        high = std::min(127, high + 2);
    }
    int span = std::max(24, high - low + 1);
    span = std::clamp((span + 11) / 12 * 12, 12, 84);
    const int extra = std::max(0, span - (high - low + 1));
    app.roll_semitones = span;
    app.roll_bottom = low - extra / 2;
    app.roll_time = 0;
    app.roll_seconds = roll_song_span(length);
    clamp_roll(app, length);
}

int roll_note_at(
    const std::vector<MidiNote>& notes, int bottom, int semis, double t0, double span, ImVec2 grid_min, ImVec2 grid_max, float row_h, float scale,
    ImVec2 mouse) {
    const float width = std::max(1.f, grid_max.x - grid_min.x);
    for (int index = static_cast<int>(notes.size()) - 1; index >= 0; --index) {
        const MidiNote& note = notes[static_cast<std::size_t>(index)];
        if (note.pitch < bottom || note.pitch >= bottom + semis || note.end < t0 || note.start > t0 + span) {
            continue;
        }
        const float y = grid_min.y + static_cast<float>((bottom + semis - 1) - note.pitch) * row_h;
        float x1 = grid_min.x + static_cast<float>((note.start - t0) / span) * width;
        float x2 = grid_min.x + static_cast<float>((std::max(note.end, note.start) - t0) / span) * width;
        if (x2 < grid_min.x || x1 > grid_max.x || y + row_h < grid_min.y || y > grid_max.y) {
            continue;
        }
        x1 = std::max(x1, grid_min.x);
        x2 = std::min(std::max(x2, x1 + 2.f * scale), grid_max.x);
        if (mouse.x >= x1 && mouse.x <= x2 && mouse.y >= y && mouse.y < y + row_h) {
            return index;
        }
    }
    return -1;
}

double snap_roll_time(const App& app, double time, bool snap) {
    return snap_grid_time(app, time, snap);
}

void draw_roll_grid_line(ImDrawList* draw, float x, float y0, float y1, GridMark mark, float scale) {
    ImU32 color = IM_COL32(255, 255, 255, 14);
    float thick = 1.f;
    if (mark == GridMark::Bar) {
        color = IM_COL32(255, 255, 255, 56);
        thick = std::max(1.f, scale);
    } else if (mark == GridMark::Beat) {
        color = IM_COL32(255, 255, 255, 24);
    }
    draw->AddLine(ImVec2(x, y0), ImVec2(x, y1), color, thick);
}

int roll_note_edge(const MidiNote& note, double t0, double span, ImVec2 grid_min, float grid_w, float scale, ImVec2 mouse) {
    const float x1 = grid_min.x + static_cast<float>((note.start - t0) / span) * grid_w;
    const float x2 = grid_min.x + static_cast<float>((std::max(note.end, note.start) - t0) / span) * grid_w;
    const float handle = std::max(6.f * scale, 5.f);
    if (x2 - x1 < handle * 3.f) {
        return kEditMove;
    }
    if (mouse.x <= x1 + handle) {
        return kEditStart;
    }
    if (mouse.x >= x2 - handle) {
        return kEditEnd;
    }
    return kEditMove;
}

void draw_piano_roll(
    App& app, ImDrawList* draw, ImVec2 origin, ImVec2 size, int stem, const bt::Player::Snapshot& transport, double position, double length,
    bool ready) {
    const float scale = ui_scale();
    const float key_w = std::min(56.f * scale, std::max(36.f * scale, size.x * 0.14f));
    const float ruler_h = std::min(22.f * scale, std::max(16.f * scale, size.y * 0.08f));
    const float overview_band = std::min(52.f * scale, std::max(32.f * scale, size.y * 0.14f));
    const float overview_gap = 6.f * scale;
    const ImVec2 grid_min(origin.x + key_w, origin.y + ruler_h);
    const float grid_bottom = origin.y + size.y - overview_band - overview_gap;
    const ImVec2 grid_max(origin.x + size.x, std::max(grid_min.y + 8.f, grid_bottom));
    const float grid_w = std::max(1.f, grid_max.x - grid_min.x);
    const float grid_h = std::max(1.f, grid_max.y - grid_min.y);

    if (app.roll_song != app.note_song || app.roll_stem != stem) {
        if (app.note_gesture && app.roll_song == app.note_song) {
            finish_note_gesture(app, app.note_gesture_stem);
        } else {
            app.note_gesture = false;
            app.note_gesture_before.clear();
        }
        if (app.wave_drag == kRollNoteDrag || app.wave_drag == kRollOverviewDrag || app.wave_drag == kRollPanDrag ||
            app.wave_drag == kRollSeekDrag || app.wave_drag == kRollBoxDrag || app.roll_erase) {
            app.wave_drag = -1;
            app.roll_note = -1;
            app.roll_erase = false;
            app.roll_drag_group.clear();
        }
        app.roll_selected.clear();
        app.roll_paste = false;
    }
    const bool has_notes = !app.notes[static_cast<std::size_t>(stem)].empty();
    const bool song_changed = app.roll_song != app.note_song || app.roll_stem != stem;
    const bool notes_arrived = has_notes && !app.roll_seen_notes;
    const bool length_arrived = length > 0.25 && app.roll_fit_length <= 0.25;
    if (song_changed || notes_arrived || length_arrived) {
        fit_roll(app, stem, length);
        app.roll_song = app.note_song;
        app.roll_stem = stem;
        app.roll_seen_notes = has_notes;
        app.roll_fit_length = length;
    }
    clamp_roll(app, length);

    const int bottom = app.roll_bottom;
    const int semis = std::max(1, app.roll_semitones);
    const double t0 = app.roll_time;
    const double span = std::max(0.25, app.roll_seconds);
    const float row_h = grid_h / static_cast<float>(semis);
    auto x_of = [&](double time) {
        return grid_min.x + static_cast<float>((time - t0) / span) * grid_w;
    };
    auto y_of = [&](int pitch) {
        const int from_top = (bottom + semis - 1) - pitch;
        return grid_min.y + static_cast<float>(from_top) * row_h;
    };

    draw->AddRectFilled(origin, ImVec2(grid_max.x, origin.y + ruler_h), IM_COL32(28, 28, 28, 255));
    draw->AddRectFilled(ImVec2(origin.x, grid_min.y), ImVec2(grid_min.x, grid_max.y), IM_COL32(36, 36, 36, 255));
    for (int pitch = bottom; pitch < bottom + semis; ++pitch) {
        const float y = y_of(pitch);
        const bool black = black_key(pitch);
        draw->AddRectFilled(ImVec2(grid_min.x, y), ImVec2(grid_max.x, y + row_h), black ? IM_COL32(22, 22, 22, 255) : IM_COL32(30, 30, 30, 255));
        if (pitch % 12 == 0) {
            draw->AddLine(ImVec2(grid_min.x, y), ImVec2(grid_max.x, y), IM_COL32(78, 78, 78, 255), std::max(1.f, scale));
        }
        const float key_width = black ? key_w * 0.62f : key_w - scale;
        const ImU32 key_ink = black ? IM_COL32(18, 18, 18, 255) : IM_COL32(226, 226, 226, 255);
        draw->AddRectFilled(ImVec2(origin.x, y), ImVec2(origin.x + key_width, y + std::max(1.f, row_h - scale)), key_ink);
        if (!black && row_h >= 12.f * scale && pitch % 12 == 0) {
            char name[8];
            midi_note_name(pitch, name, sizeof name);
            const float font = std::min(13.f * scale, row_h * 0.72f);
            const ImVec2 text = text_size(font, name);
            text_at(draw, ImVec2(origin.x + 4.f * scale, y + (row_h - text.y) * 0.5f), font, IM_COL32(28, 28, 28, 255), name);
        }
    }

    const float pixels_per_second = grid_w / static_cast<float>(span);
    const double tick_steps[] = {0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300};
    double tick = tick_steps[std::size(tick_steps) - 1];
    for (double candidate : tick_steps) {
        if (static_cast<float>(candidate) * pixels_per_second >= 72.f * scale) {
            tick = candidate;
            break;
        }
    }
    const double first_tick = std::ceil((t0 - 1e-6) / tick) * tick;
    for (double mark = first_tick; mark < t0 + span; mark += tick) {
        if (mark < -1e-4) {
            continue;
        }
        const float x = x_of(mark);
        char label[16];
        if (tick < 1.0) {
            std::snprintf(label, sizeof label, "%.1f", mark);
        } else {
            const int total = static_cast<int>(std::floor(mark + 1e-3));
            std::snprintf(label, sizeof label, "%d:%02d", total / 60, total % 60);
        }
        text_at(draw, ImVec2(x + 3.f * scale, origin.y + (ruler_h - 12.f * scale) * 0.5f), 12.f * scale, IM_COL32(176, 176, 176, 255), label);
    }
    const bool have_beats = app.beat_times != nullptr && !app.beat_times->empty();
    const int parts = division_parts(app.grid_division);
    bool show_sub = false;
    if (have_beats) {
        const double gap = minimum_gap(*app.beat_times, t0, t0 + span);
        show_sub = parts > 1 && gap > 1e-6 && static_cast<float>(gap / static_cast<double>(parts)) * pixels_per_second >= 8.f * scale;
    } else if (parts > 1) {
        const double beat = app.bpm > 1.f ? 60.0 / static_cast<double>(app.bpm) : 0.5;
        show_sub = static_cast<float>(beat / static_cast<double>(parts)) * pixels_per_second >= 8.f * scale;
    }
    auto paint_tick = [&](double mark, GridMark kind) {
        if (kind == GridMark::Sub && !show_sub) {
            return;
        }
        draw_roll_grid_line(draw, x_of(mark), grid_min.y, grid_max.y, kind, scale);
    };
    if (app.grid) {
        if (have_beats) {
            each_grid_tick(*app.beat_times, app.grid_division, t0, t0 + span, paint_tick);
        } else {
            each_bpm_tick(app.bpm, app.beat_offset, app.grid_division, t0, t0 + span, paint_tick);
        }
    }

    draw->PushClipRect(grid_min, grid_max, true);
    if (app.roll_selected.size() != app.notes[static_cast<std::size_t>(stem)].size()) {
        app.roll_selected.assign(app.notes[static_cast<std::size_t>(stem)].size(), 0);
    }
    for (std::size_t note_i = 0; note_i < app.notes[static_cast<std::size_t>(stem)].size(); ++note_i) {
        const MidiNote& note = app.notes[static_cast<std::size_t>(stem)][note_i];
        if (note.pitch < bottom || note.pitch >= bottom + semis || note.end < t0 || note.start > t0 + span) {
            continue;
        }
        const float x1 = std::max(grid_min.x, x_of(note.start));
        const float x2 = std::min(grid_max.x, x_of(std::max(note.end, note.start)));
        const float y = y_of(note.pitch);
        const float pad = std::min(2.f * scale, row_h * 0.16f);
        const bool sounding = note.start <= position && position < note.end;
        const float velocity = std::clamp(static_cast<float>(note.velocity) / 127.f, 0.f, 1.f);
        const float alpha = sounding ? 1.f : 0.55f + 0.4f * velocity;
        const ImVec2 note_min(x1, y + pad);
        const ImVec2 note_max(std::max(x1 + 2.f * scale, x2), y + row_h - pad);
        const float radius = std::min(3.f * scale, row_h * 0.2f);
        draw->AddRectFilled(note_min, note_max, with_alpha(kPitchColor[note.pitch % 12], alpha), radius);
        if (note_i < app.roll_selected.size() && app.roll_selected[note_i]) {
            draw->AddRect(note_min, note_max, IM_COL32(242, 242, 242, 230), radius, 0, std::max(1.5f, scale));
        }
    }
    if (position >= t0 && position <= t0 + span) {
        const float x = x_of(position);
        draw->AddLine(ImVec2(x, grid_min.y), ImVec2(x, grid_max.y), IM_COL32(242, 242, 242, 255), std::max(1.5f, scale));
    }
    if (ready && length > 0) {
        draw_ab_loop(
            draw, scale, grid_min, grid_max, transport.loop_a >= 0, x_of(std::max(0.0, transport.loop_a)), transport.loop_b >= 0,
            x_of(std::max(0.0, transport.loop_b)), false, IM_COL32(242, 242, 242, 36), IM_COL32(242, 242, 242, 255),
            IM_COL32(242, 242, 242, 255), IM_COL32(26, 26, 26, 255), IM_COL32(26, 26, 26, 180));
    }
    draw->PopClipRect();
    draw->AddLine(ImVec2(grid_min.x, origin.y), ImVec2(grid_min.x, grid_max.y), IM_COL32(12, 12, 12, 255), std::max(1.f, scale));

    const double song = roll_song_span(length);
    const ImVec2 overview_min(grid_min.x, std::min(grid_max.y + overview_gap, origin.y + size.y - 8.f));
    const ImVec2 overview_max(grid_max.x, std::max(overview_min.y + 8.f, origin.y + size.y));
    const float overview_w = std::max(1.f, overview_max.x - overview_min.x);
    const float overview_h = std::max(1.f, overview_max.y - overview_min.y);
    auto overview_x = [&](double time) {
        return overview_min.x + static_cast<float>(time / song) * overview_w;
    };
    auto overview_y = [&](double pitch) {
        return overview_min.y + static_cast<float>((127.5 - pitch) / 128.0) * overview_h;
    };
    float view_x1 = overview_x(t0);
    float view_x2 = overview_x(t0 + span);
    float view_y1 = overview_y(static_cast<double>(bottom + semis - 1) + 0.5);
    float view_y2 = overview_y(static_cast<double>(bottom) - 0.5);
    const float min_grab = 6.f * scale;
    if (view_x2 - view_x1 < min_grab) {
        const float mid = (view_x1 + view_x2) * 0.5f;
        view_x1 = mid - min_grab * 0.5f;
        view_x2 = mid + min_grab * 0.5f;
    }
    if (view_y2 - view_y1 < min_grab) {
        const float mid = (view_y1 + view_y2) * 0.5f;
        view_y1 = mid - min_grab * 0.5f;
        view_y2 = mid + min_grab * 0.5f;
    }
    view_x1 = std::clamp(view_x1, overview_min.x, overview_max.x);
    view_x2 = std::clamp(view_x2, overview_min.x, overview_max.x);
    view_y1 = std::clamp(view_y1, overview_min.y, overview_max.y);
    view_y2 = std::clamp(view_y2, overview_min.y, overview_max.y);
    draw->AddRectFilled(overview_min, overview_max, IM_COL32(16, 16, 16, 255));
    draw->PushClipRect(overview_min, overview_max, true);
    const float dot = std::max(1.25f, 1.6f * scale);
    for (const MidiNote& note : app.notes[static_cast<std::size_t>(stem)]) {
        const float cx = overview_x((note.start + note.end) * 0.5);
        const float cy = overview_y(note.pitch);
        draw->AddRectFilled(
            ImVec2(cx - dot, cy - dot), ImVec2(cx + dot, cy + dot), with_alpha(kPitchColor[note.pitch % 12], 0.85f));
    }
    draw->PopClipRect();
    draw->AddRectFilled(ImVec2(view_x1, view_y1), ImVec2(view_x2, view_y2), IM_COL32(242, 242, 242, 36));
    draw->AddRect(ImVec2(view_x1, view_y1), ImVec2(view_x2, view_y2), IM_COL32(242, 242, 242, 210), 0.f, 0, std::max(1.f, scale));

    const bool hovered = ImGui::IsItemHovered();
    ImGuiIO& io = ImGui::GetIO();
    const ImVec2 mouse = io.MousePos;
    const bool on_keys = mouse.x >= origin.x && mouse.x < grid_min.x && mouse.y >= grid_min.y && mouse.y < grid_max.y;
    const bool on_ruler = mouse.x >= grid_min.x && mouse.x < grid_max.x && mouse.y >= origin.y && mouse.y < grid_min.y;
    const bool on_grid = mouse.x >= grid_min.x && mouse.x < grid_max.x && mouse.y >= grid_min.y && mouse.y < grid_max.y;
    const bool on_overview =
        mouse.x >= overview_min.x && mouse.x < overview_max.x && mouse.y >= overview_min.y && mouse.y < overview_max.y;
    const bool on_viewport = on_overview && mouse.x >= view_x1 && mouse.x <= view_x2 && mouse.y >= view_y1 && mouse.y <= view_y2;
    auto& stem_notes = app.notes[static_cast<std::size_t>(stem)];
    const bool use_snap = !io.KeyCtrl;
    auto time_at = [&](float x) {
        return std::max(0.0, t0 + static_cast<double>((x - grid_min.x) / grid_w) * span);
    };
    auto pitch_at = [&](float y) {
        const float row = std::clamp((y - grid_min.y) / std::max(row_h, 1.f), 0.f, static_cast<float>(semis) - 0.001f);
        return std::clamp(bottom + semis - 1 - static_cast<int>(std::floor(row)), 0, 127);
    };
    auto min_length = [&](double start) {
        return use_snap ? grid_cell(app, start) : 0.05;
    };
    auto note_selected = [&](int index) {
        return index >= 0 && index < static_cast<int>(app.roll_selected.size()) && app.roll_selected[static_cast<std::size_t>(index)];
    };
    auto clear_selection = [&] {
        app.roll_selected.assign(stem_notes.size(), 0);
    };
    auto select_only = [&](int index) {
        clear_selection();
        if (index >= 0 && index < static_cast<int>(app.roll_selected.size())) {
            app.roll_selected[static_cast<std::size_t>(index)] = 1;
        }
    };
    auto capture_drag_group = [&](int primary) {
        app.roll_drag_group.clear();
        if (note_selected(primary)) {
            for (int index = 0; index < static_cast<int>(stem_notes.size()); ++index) {
                if (!note_selected(index)) {
                    continue;
                }
                const MidiNote& note = stem_notes[static_cast<std::size_t>(index)];
                app.roll_drag_group.push_back({index, note.pitch, note.start, note.end});
            }
        }
        if (app.roll_drag_group.empty() && primary >= 0 && primary < static_cast<int>(stem_notes.size())) {
            const MidiNote& note = stem_notes[static_cast<std::size_t>(primary)];
            app.roll_drag_group.push_back({primary, note.pitch, note.start, note.end});
        }
    };
    auto erase_under_cursor = [&] {
        for (;;) {
            const int hit = roll_note_at(stem_notes, bottom, semis, t0, span, grid_min, grid_max, row_h, scale, mouse);
            if (hit < 0) {
                break;
            }
            stem_notes.erase(stem_notes.begin() + hit);
        }
        app.roll_selected.assign(stem_notes.size(), 0);
    };
    auto clipboard_origin_pitch = [&]() {
        int pitch = 60;
        double start = 0;
        bool any = false;
        for (const MidiNote& note : app.roll_clipboard) {
            if (!any || note.start < start) {
                start = note.start;
                pitch = note.pitch;
                any = true;
            }
        }
        return pitch;
    };
    auto build_paste_notes = [&](double anchor_time, int anchor_pitch, std::vector<MidiNote>& out) {
        out.clear();
        if (app.roll_clipboard.empty()) {
            return;
        }
        const int origin_pitch = clipboard_origin_pitch();
        out.reserve(app.roll_clipboard.size());
        for (const MidiNote& clip : app.roll_clipboard) {
            MidiNote note = clip;
            note.start = anchor_time + clip.start;
            note.end = anchor_time + clip.end;
            note.pitch = std::clamp(anchor_pitch + (clip.pitch - origin_pitch), 0, 127);
            if (length > 0) {
                note.start = std::clamp(note.start, 0.0, length);
                note.end = std::clamp(note.end, 0.0, length);
            } else {
                note.start = std::max(0.0, note.start);
                note.end = std::max(note.start + 0.01, note.end);
            }
            if (note.end > note.start) {
                out.push_back(note);
            }
        }
    };
    auto paste_anchor = [&](double& anchor_time, int& anchor_pitch) {
        const float x = std::clamp(mouse.x, grid_min.x, grid_max.x);
        const float y = std::clamp(mouse.y, grid_min.y, grid_max.y - 0.001f);
        anchor_time = snap_roll_time(app, time_at(x), use_snap);
        anchor_pitch = pitch_at(y);
    };
    auto commit_paste = [&] {
        if (app.roll_clipboard.empty()) {
            app.roll_paste = false;
            return;
        }
        double anchor_time = 0;
        int anchor_pitch = 60;
        paste_anchor(anchor_time, anchor_pitch);
        std::vector<MidiNote> placed;
        build_paste_notes(anchor_time, anchor_pitch, placed);
        if (placed.empty()) {
            app.roll_paste = false;
            return;
        }
        begin_note_gesture(app, stem);
        const int first = static_cast<int>(stem_notes.size());
        stem_notes.insert(stem_notes.end(), placed.begin(), placed.end());
        clear_selection();
        app.roll_selected.assign(stem_notes.size(), 0);
        for (int index = first; index < static_cast<int>(stem_notes.size()); ++index) {
            app.roll_selected[static_cast<std::size_t>(index)] = 1;
        }
        finish_note_gesture(app, stem);
        app.roll_paste = false;
    };

    if (app.wave_drag == kRollNoteDrag) {
        if (app.roll_note >= 0 && app.roll_note < static_cast<int>(stem_notes.size())) {
            MidiNote& note = stem_notes[static_cast<std::size_t>(app.roll_note)];
            const bool nudged = std::fabs(mouse.x - app.roll_grab_x) > 2.f || std::fabs(mouse.y - app.roll_grab_y) > 2.f;
            if (app.roll_edit == kEditPlace) {
                note.pitch = pitch_at(mouse.y);
                note.end = std::max(note.start + min_length(note.start), snap_roll_time(app, time_at(mouse.x), use_snap));
            } else if (!nudged) {
                note.pitch = app.roll_note_pitch;
                note.start = app.roll_note_start;
                note.end = app.roll_note_end;
            } else if (app.roll_edit == kEditEnd) {
                note.pitch = app.roll_note_pitch;
                note.start = app.roll_note_start;
                note.end = std::max(note.start + min_length(note.start), snap_roll_time(app, time_at(mouse.x), use_snap));
            } else if (app.roll_edit == kEditStart) {
                const double limit = app.roll_note_end - min_length(app.roll_note_start);
                note.pitch = app.roll_note_pitch;
                note.end = app.roll_note_end;
                note.start = std::clamp(snap_roll_time(app, time_at(mouse.x), use_snap), 0.0, std::max(0.0, limit));
            } else if (app.roll_drag_group.size() > 1) {
                const double delta = time_at(mouse.x) - time_at(app.roll_grab_x);
                const int pitch_delta = pitch_at(mouse.y) - app.roll_note_pitch;
                const double primary_start =
                    std::max(0.0, snap_roll_time(app, app.roll_note_start + delta, use_snap));
                const double time_delta = primary_start - app.roll_note_start;
                for (const App::RollDragNote& grab : app.roll_drag_group) {
                    if (grab.index < 0 || grab.index >= static_cast<int>(stem_notes.size())) {
                        continue;
                    }
                    MidiNote& moved = stem_notes[static_cast<std::size_t>(grab.index)];
                    moved.pitch = std::clamp(grab.pitch + pitch_delta, 0, 127);
                    const double duration = std::max(0.01, grab.end - grab.start);
                    moved.start = std::max(0.0, grab.start + time_delta);
                    moved.end = moved.start + duration;
                }
            } else {
                const double delta = time_at(mouse.x) - time_at(app.roll_grab_x);
                const double duration = std::max(0.01, app.roll_note_end - app.roll_note_start);
                note.pitch = pitch_at(mouse.y);
                note.start = std::max(0.0, snap_roll_time(app, app.roll_note_start + delta, use_snap));
                note.end = note.start + duration;
            }
        }
        if (!ImGui::IsMouseDown(ImGuiMouseButton_Left)) {
            finish_note_gesture(app, stem);
            app.wave_drag = -1;
            app.roll_note = -1;
            app.roll_drag_group.clear();
        }
    } else if (app.wave_drag == kRollBoxDrag) {
        if (!ImGui::IsMouseDown(ImGuiMouseButton_Left)) {
            const float x0 = std::min(app.roll_grab_x, mouse.x);
            const float x1 = std::max(app.roll_grab_x, mouse.x);
            const float y0 = std::min(app.roll_grab_y, mouse.y);
            const float y1 = std::max(app.roll_grab_y, mouse.y);
            const bool nudged = (x1 - x0) > 2.f || (y1 - y0) > 2.f;
            clear_selection();
            if (nudged) {
                const double t_lo = time_at(x0);
                const double t_hi = time_at(x1);
                const int p_hi = pitch_at(y0);
                const int p_lo = pitch_at(y1);
                for (int index = 0; index < static_cast<int>(stem_notes.size()); ++index) {
                    const MidiNote& note = stem_notes[static_cast<std::size_t>(index)];
                    if (note.end < t_lo || note.start > t_hi || note.pitch < p_lo || note.pitch > p_hi) {
                        continue;
                    }
                    app.roll_selected[static_cast<std::size_t>(index)] = 1;
                }
            } else {
                const int hit = roll_note_at(stem_notes, bottom, semis, t0, span, grid_min, grid_max, row_h, scale, mouse);
                if (hit >= 0) {
                    app.roll_selected[static_cast<std::size_t>(hit)] = 1;
                }
            }
            app.wave_drag = -1;
        }
    } else if (app.wave_drag == kRollOverviewDrag) {
        const float dx = mouse.x - app.roll_grab_x;
        const float dy = mouse.y - app.roll_grab_y;
        app.roll_time = app.roll_grab_time + static_cast<double>(dx) / static_cast<double>(overview_w) * song;
        app.roll_bottom =
            app.roll_grab_bottom - static_cast<int>(std::lround(static_cast<double>(dy) / static_cast<double>(overview_h) * 128.0));
        clamp_roll(app, length);
        if (!ImGui::IsMouseDown(ImGuiMouseButton_Left)) {
            app.wave_drag = -1;
        }
    }

    if (app.wave_drag == kRollPanDrag) {
        if (!ImGui::IsMouseDown(ImGuiMouseButton_Middle)) {
            app.wave_drag = -1;
        } else {
            // Phone-style: content follows the pointer (drag right → earlier time, drag down → higher pitch).
            const float dx = mouse.x - app.roll_grab_x;
            const float dy = mouse.y - app.roll_grab_y;
            app.roll_time = app.roll_grab_time - static_cast<double>(dx / grid_w) * span;
            app.roll_bottom = app.roll_grab_bottom + static_cast<int>(std::lround(static_cast<double>(dy / row_h)));
            clamp_roll(app, length);
        }
    } else if (app.wave_drag == kRollSeekDrag) {
        if (!ImGui::IsMouseDown(ImGuiMouseButton_Left)) {
            app.wave_drag = -1;
        } else if (ready && length > 0) {
            app.player->seek(std::clamp(time_at(mouse.x), 0.0, length));
        }
    }
    if (hovered && app.wave_drag < 0 && !app.roll_erase && ImGui::IsMouseClicked(ImGuiMouseButton_Middle)) {
        app.wave_drag = kRollPanDrag;
        app.roll_grab_x = mouse.x;
        app.roll_grab_y = mouse.y;
        app.roll_grab_time = app.roll_time;
        app.roll_grab_bottom = app.roll_bottom;
    }
    if (!app.roll_paste && hovered && app.wave_drag < 0 && ready && length > 0 && on_ruler &&
        ImGui::IsMouseClicked(ImGuiMouseButton_Left)) {
        app.wave_drag = kRollSeekDrag;
        app.player->seek(std::clamp(time_at(mouse.x), 0.0, length));
    }

    if (app.roll_paste) {
        if (ImGui::IsKeyPressed(ImGuiKey_Escape, false) ||
            (hovered && ImGui::IsMouseClicked(ImGuiMouseButton_Right))) {
            app.roll_paste = false;
        } else if ((hovered && on_grid && ImGui::IsMouseClicked(ImGuiMouseButton_Left)) ||
                   ImGui::IsKeyPressed(ImGuiKey_Enter, false)) {
            commit_paste();
        }
    } else if (hovered && app.wave_drag < 0 && !app.roll_erase && ImGui::IsMouseClicked(ImGuiMouseButton_Left)) {
        if (on_grid && io.KeyShift) {
            app.wave_drag = kRollBoxDrag;
            app.roll_grab_x = mouse.x;
            app.roll_grab_y = mouse.y;
        } else if (on_viewport) {
            app.wave_drag = kRollOverviewDrag;
            app.roll_grab_x = mouse.x;
            app.roll_grab_y = mouse.y;
            app.roll_grab_time = app.roll_time;
            app.roll_grab_bottom = app.roll_bottom;
        } else if (on_grid) {
            const int hit = roll_note_at(stem_notes, bottom, semis, t0, span, grid_min, grid_max, row_h, scale, mouse);
            if (hit >= 0) {
                if (!note_selected(hit)) {
                    select_only(hit);
                }
                begin_note_gesture(app, stem);
                app.wave_drag = kRollNoteDrag;
                app.roll_note = hit;
                app.roll_note_pitch = stem_notes[static_cast<std::size_t>(hit)].pitch;
                app.roll_note_start = stem_notes[static_cast<std::size_t>(hit)].start;
                app.roll_note_end = stem_notes[static_cast<std::size_t>(hit)].end;
                app.roll_edit = roll_note_edge(stem_notes[static_cast<std::size_t>(hit)], t0, span, grid_min, grid_w, scale, mouse);
                app.roll_grab_x = mouse.x;
                app.roll_grab_y = mouse.y;
                if (app.roll_edit == kEditMove) {
                    capture_drag_group(hit);
                } else {
                    app.roll_drag_group.clear();
                }
            } else {
                clear_selection();
                begin_note_gesture(app, stem);
                const double start = snap_roll_time(app, time_at(mouse.x), use_snap);
                MidiNote created;
                created.pitch = pitch_at(mouse.y);
                created.start = start;
                created.end = start + min_length(start);
                created.velocity = 100;
                stem_notes.push_back(created);
                app.roll_selected.assign(stem_notes.size(), 0);
                app.roll_selected.back() = 1;
                app.wave_drag = kRollNoteDrag;
                app.roll_note = static_cast<int>(stem_notes.size()) - 1;
                app.roll_edit = kEditPlace;
                app.roll_note_pitch = created.pitch;
                app.roll_note_start = created.start;
                app.roll_note_end = created.end;
                app.roll_grab_x = mouse.x;
                app.roll_grab_y = mouse.y;
                app.roll_drag_group.clear();
            }
        }
    }
    if (!app.roll_paste && app.roll_erase) {
        if (ImGui::IsMouseDown(ImGuiMouseButton_Right)) {
            if (on_grid) {
                erase_under_cursor();
            }
        } else {
            finish_note_gesture(app, stem);
            app.roll_erase = false;
        }
    } else if (!app.roll_paste && hovered && app.wave_drag < 0 && on_grid && ImGui::IsMouseClicked(ImGuiMouseButton_Right)) {
        begin_note_gesture(app, stem);
        app.roll_erase = true;
        erase_under_cursor();
    }
    if (hovered && app.wave_drag < 0 && !app.roll_erase && (io.MouseWheel != 0.f || io.MouseWheelH != 0.f)) {
        bool consumed = false;
        const bool over_roll = on_keys || on_ruler || on_grid || on_overview;
        if (io.KeyCtrl && io.KeyShift && !io.KeyAlt && io.MouseWheel != 0.f && over_roll) {
            // Ctrl+Shift+wheel zooms the pitch axis toward the cursor.
            const int steps = std::max(1, static_cast<int>(std::lround(std::fabs(io.MouseWheel))));
            const int dir = io.MouseWheel > 0.f ? -12 : 12;
            int next = semis;
            for (int step = 0; step < steps; ++step) {
                next = std::clamp(next + dir, 12, 84);
            }
            const float frac = std::clamp((mouse.y - grid_min.y) / grid_h, 0.f, 1.f);
            const float pitch = static_cast<float>(bottom + semis) - frac * static_cast<float>(semis);
            app.roll_semitones = next;
            app.roll_bottom = static_cast<int>(std::lround(pitch - (1.f - frac) * static_cast<float>(next)));
            consumed = true;
        } else if (io.KeyCtrl && !io.KeyShift && !io.KeyAlt && io.MouseWheel != 0.f && over_roll) {
            // Ctrl+wheel zooms time toward the cursor (works on the grid, not only the ruler).
            const float frac = std::clamp((mouse.x - grid_min.x) / grid_w, 0.f, 1.f);
            const double anchor = t0 + static_cast<double>(frac) * span;
            const double next = std::clamp(span * std::pow(0.8, static_cast<double>(io.MouseWheel)), 0.25, song);
            app.roll_seconds = next;
            app.roll_time = anchor - static_cast<double>(frac) * next;
            consumed = true;
        } else if (on_keys && io.MouseWheel != 0.f) {
            const int steps = std::max(1, static_cast<int>(std::lround(std::fabs(io.MouseWheel))));
            const int dir = io.MouseWheel > 0.f ? -12 : 12;
            int next = semis;
            for (int step = 0; step < steps; ++step) {
                next = std::clamp(next + dir, 12, 84);
            }
            const float frac = std::clamp((mouse.y - grid_min.y) / grid_h, 0.f, 1.f);
            const float pitch = static_cast<float>(bottom + semis) - frac * static_cast<float>(semis);
            app.roll_semitones = next;
            app.roll_bottom = static_cast<int>(std::lround(pitch - (1.f - frac) * static_cast<float>(next)));
            consumed = true;
        } else if (on_ruler && io.MouseWheel != 0.f) {
            const float frac = std::clamp((mouse.x - grid_min.x) / grid_w, 0.f, 1.f);
            const double anchor = t0 + static_cast<double>(frac) * span;
            const double next = std::clamp(span * std::pow(0.8, static_cast<double>(io.MouseWheel)), 0.25, song);
            app.roll_seconds = next;
            app.roll_time = anchor - static_cast<double>(frac) * next;
            consumed = true;
        } else if (on_grid) {
            if (io.KeyShift || io.MouseWheelH != 0.f) {
                const float notches = io.MouseWheelH != 0.f && !io.KeyShift ? io.MouseWheelH : io.MouseWheel;
                app.roll_time -= static_cast<double>(notches) * span * 0.1;
            } else if (io.MouseWheel != 0.f) {
                app.roll_bottom += io.MouseWheel > 0.f ? 2 : -2;
            }
            consumed = true;
        }
        if (consumed || on_keys || on_ruler || on_grid || on_overview) {
            if (consumed) {
                clamp_roll(app, length);
            }
            io.MouseWheel = 0.f;
            io.MouseWheelH = 0.f;
        }
    }

    if (app.roll_paste && !app.roll_clipboard.empty()) {
        double anchor_time = 0;
        int anchor_pitch = 60;
        paste_anchor(anchor_time, anchor_pitch);
        std::vector<MidiNote> preview;
        build_paste_notes(anchor_time, anchor_pitch, preview);
        draw->PushClipRect(grid_min, grid_max, true);
        for (const MidiNote& note : preview) {
            if (note.pitch < bottom || note.pitch >= bottom + semis || note.end < t0 || note.start > t0 + span) {
                continue;
            }
            const float x1 = std::max(grid_min.x, x_of(note.start));
            const float x2 = std::min(grid_max.x, x_of(std::max(note.end, note.start)));
            const float y = y_of(note.pitch);
            const float pad = std::min(2.f * scale, row_h * 0.16f);
            const ImVec2 note_min(x1, y + pad);
            const ImVec2 note_max(std::max(x1 + 2.f * scale, x2), y + row_h - pad);
            const float radius = std::min(3.f * scale, row_h * 0.2f);
            draw->AddRectFilled(note_min, note_max, with_alpha(kPitchColor[note.pitch % 12], 0.4f), radius);
            draw->AddRect(note_min, note_max, IM_COL32(242, 242, 242, 200), radius, 0, std::max(1.5f, scale));
        }
        draw->PopClipRect();
    }
    if (app.wave_drag == kRollBoxDrag) {
        const ImVec2 box_min(std::min(app.roll_grab_x, mouse.x), std::min(app.roll_grab_y, mouse.y));
        const ImVec2 box_max(std::max(app.roll_grab_x, mouse.x), std::max(app.roll_grab_y, mouse.y));
        draw->PushClipRect(grid_min, grid_max, true);
        draw->AddRectFilled(box_min, box_max, IM_COL32(242, 242, 242, 36));
        draw->AddRect(box_min, box_max, IM_COL32(242, 242, 242, 210), 0.f, 0, std::max(1.f, scale));
        draw->PopClipRect();
    }
}

}  // namespace

void draw_ab_marker(
    ImDrawList* draw, float scale, ImVec2 area_min, ImVec2 area_max, float pos, float other, bool start, bool time_down, const char* label,
    ImU32 line, ImU32 tag, ImU32 ink, ImU32 outline) {
    const bool shown = time_down ? pos >= area_min.y - 1.f && pos <= area_max.y + 1.f : pos >= area_min.x - 1.f && pos <= area_max.x + 1.f;
    if (!shown) {
        return;
    }
    const ImVec2 text = ImGui::CalcTextSize(label);
    const float pad_x = 5.f * scale;
    const float pad_y = 2.f * scale;
    const float tag_w = text.x + pad_x * 2.f;
    const float tag_h = text.y + pad_y * 2.f;
    const float gap = 3.f * scale;
    const float radius = 4.f * scale;
    const float thick = std::max(2.f, scale);
    float x = 0.f;
    float y = 0.f;
    if (!time_down) {
        draw->AddRectFilled(ImVec2(pos - thick * 0.5f, area_min.y), ImVec2(pos + thick * 0.5f, area_max.y), line);
        x = start ? pos + gap : pos - gap - tag_w;
        y = area_min.y + gap;
        const bool crowded = other >= area_min.x - 1.f && std::fabs(other - pos) < tag_w + gap * 3.f;
        if (start && crowded) {
            x = pos - gap - tag_w;
        } else if (!start && crowded) {
            x = pos + gap;
        }
        x = std::clamp(x, area_min.x + 1.f, std::max(area_min.x + 1.f, area_max.x - tag_w - 1.f));
    } else {
        draw->AddRectFilled(ImVec2(area_min.x, pos - thick * 0.5f), ImVec2(area_max.x, pos + thick * 0.5f), line);
        x = area_min.x + gap;
        y = start ? pos - gap - tag_h : pos + gap;
        const bool crowded = other >= area_min.y - 1.f && std::fabs(other - pos) < tag_h + gap * 3.f;
        if (start && crowded) {
            y = pos + gap;
        } else if (!start && crowded) {
            y = pos - gap - tag_h;
        }
        y = std::clamp(y, area_min.y + 1.f, std::max(area_min.y + 1.f, area_max.y - tag_h - 1.f));
    }
    const ImVec2 tag_min(x, y);
    const ImVec2 tag_max(x + tag_w, y + tag_h);
    draw->AddRectFilled(tag_min, tag_max, tag, radius);
    draw->AddRect(tag_min, tag_max, outline, radius, 0, std::max(1.f, scale * 0.75f));
    draw->AddText(ImVec2(x + pad_x, y + pad_y), ink, label);
}

void draw_ab_loop(
    ImDrawList* draw, float scale, ImVec2 area_min, ImVec2 area_max, bool has_a, float a_pos, bool has_b, float b_pos, bool time_down, ImU32 wash,
    ImU32 line, ImU32 tag, ImU32 ink, ImU32 outline) {
    if (has_a && has_b) {
        const float lo = std::min(a_pos, b_pos);
        const float hi = std::max(a_pos, b_pos);
        ImVec2 wash_min = area_min;
        ImVec2 wash_max = area_max;
        if (!time_down) {
            wash_min.x = std::max(area_min.x, lo);
            wash_max.x = std::min(area_max.x, hi);
        } else {
            wash_min.y = std::max(area_min.y, lo);
            wash_max.y = std::min(area_max.y, hi);
        }
        if (wash_max.x > wash_min.x && wash_max.y > wash_min.y) {
            draw->AddRectFilled(wash_min, wash_max, wash);
        }
    }
    draw->PushClipRect(area_min, area_max, true);
    if (has_a) {
        draw_ab_marker(draw, scale, area_min, area_max, a_pos, has_b ? b_pos : -1.0e9f, true, time_down, "A", line, tag, ink, outline);
    }
    if (has_b) {
        draw_ab_marker(draw, scale, area_min, area_max, b_pos, has_a ? a_pos : -1.0e9f, false, time_down, "B", line, tag, ink, outline);
    }
    draw->PopClipRect();
}

const char* kPitchNames[12] = {"C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"};

void instrument_pitch_bounds(bool bass, int& low, int& high) {
    const int* opens = bass ? kBassOpen : kGuitarOpen;
    const int strings = bass ? 4 : 6;
    int bottom = opens[0];
    int top = opens[0];
    for (int index = 1; index < strings; ++index) {
        bottom = std::min(bottom, opens[index]);
        top = std::max(top, opens[index]);
    }
    low = bottom;
    high = top + kFretCount;
}

void viewport_pitch_range(const App& app, int& lo, int& hi) {
    if (app.midi_view == MidiView::Guitar || app.midi_view == MidiView::Bass) {
        const bool bass = app.midi_view == MidiView::Bass;
        lo = bass ? app.bass_pitch_min : app.guitar_pitch_min;
        hi = bass ? app.bass_pitch_max : app.guitar_pitch_max;
    } else {
        lo = std::min(app.key_min, app.key_max);
        hi = std::max(app.key_min, app.key_max);
    }
    if (lo > hi) {
        std::swap(lo, hi);
    }
}

void midi_note_name(int pitch, char* text, std::size_t size) {
    const int wrapped = pitch % 12;
    const int degree = wrapped < 0 ? wrapped + 12 : wrapped;
    const int octave = (pitch - degree) / 12 - 1;
    std::snprintf(text, size, "%s%d", kPitchNames[degree], octave);
}

void draw_midi_canvas(App& app, const bt::Player::Snapshot& transport, double length, bool ready, ImVec2 size) {
    if (!ImGui::IsMouseDown(ImGuiMouseButton_Left) && app.wave_drag == kFallDrag) {
        app.wave_drag = -1;
    }
    if (app.note_stage != NoteStage::Midi &&
        (app.wave_drag == kRollNoteDrag || app.wave_drag == kRollOverviewDrag || app.wave_drag == kRollPanDrag ||
         app.wave_drag == kRollSeekDrag || app.wave_drag == kRollBoxDrag || app.roll_erase || app.roll_paste)) {
        if (app.note_gesture) {
            finish_note_gesture(app, app.note_gesture_stem);
        }
        app.wave_drag = -1;
        app.roll_note = -1;
        app.roll_erase = false;
        app.roll_drag_group.clear();
        app.roll_selected.clear();
        app.roll_paste = false;
    }
    if (!ImGui::IsMouseDown(ImGuiMouseButton_Right) && app.loop_drag == kFallDrag) {
        app.loop_drag = -1;
    }
    const ImVec2 origin = ImGui::GetCursorScreenPos();
    size.x = std::max(1.f, size.x);
    size.y = std::max(1.f, size.y);
    ImGui::InvisibleButton("midi-view", size);
    const bool hovered = ImGui::IsItemHovered();
    if (hovered && app.note_stage != NoteStage::Midi && app.midi_view == MidiView::Fall) {
        ImGui::SetMouseCursor(ImGuiMouseCursor_ResizeNS);
    }
    ImDrawList* draw = ImGui::GetWindowDrawList();
    const int canvas_begin = draw->VtxBuffer.Size;
    const ImVec2 corner(origin.x + size.x, origin.y + size.y);
    draw->PushClipRect(origin, corner, true);
    draw->AddRectFilled(origin, corner, IM_COL32(20, 20, 20, 255));
    const int stem = std::clamp(app.selected, 0, bt::kStemCount - 1);
    if (app.note_stage == NoteStage::Midi) {
        draw_piano_roll(app, draw, origin, size, stem, transport, transport.position, length, ready);
    } else if (app.midi_view == MidiView::Guitar || app.midi_view == MidiView::Bass) {
        draw_fretboard(app, draw, origin, size, stem, transport.position);
    } else {
        draw_fall(app, draw, origin, size, stem, transport.position, length, ready);
    }
    draw_beat_frame(app, draw, origin, size);
    draw->PopClipRect();
    if (!ready) {
        const float alpha = ImGui::GetStyle().DisabledAlpha;
        for (int index = canvas_begin; index < draw->VtxBuffer.Size; ++index) {
            ImVec4 color = ImGui::ColorConvertU32ToFloat4(draw->VtxBuffer[index].col);
            color.w *= alpha;
            draw->VtxBuffer[index].col = ImGui::ColorConvertFloat4ToU32(color);
        }
    }
}

void midi_copy_selection(App& app) {
    if (app.note_stage != NoteStage::Midi) {
        return;
    }
    const int stem = std::clamp(app.selected, 0, bt::kStemCount - 1);
    auto& stem_notes = app.notes[static_cast<std::size_t>(stem)];
    if (app.roll_selected.size() != stem_notes.size()) {
        app.roll_selected.assign(stem_notes.size(), 0);
    }
    app.roll_clipboard.clear();
    app.roll_paste = false;
    double origin = 0;
    bool any = false;
    for (int index = 0; index < static_cast<int>(stem_notes.size()); ++index) {
        if (index >= static_cast<int>(app.roll_selected.size()) || !app.roll_selected[static_cast<std::size_t>(index)]) {
            continue;
        }
        const MidiNote& note = stem_notes[static_cast<std::size_t>(index)];
        if (!any || note.start < origin) {
            origin = note.start;
            any = true;
        }
    }
    if (!any) {
        return;
    }
    for (int index = 0; index < static_cast<int>(stem_notes.size()); ++index) {
        if (index >= static_cast<int>(app.roll_selected.size()) || !app.roll_selected[static_cast<std::size_t>(index)]) {
            continue;
        }
        MidiNote copy = stem_notes[static_cast<std::size_t>(index)];
        copy.start -= origin;
        copy.end -= origin;
        if (copy.end > copy.start) {
            app.roll_clipboard.push_back(copy);
        }
    }
}

void midi_begin_paste_preview(App& app) {
    if (app.note_stage != NoteStage::Midi || app.roll_clipboard.empty()) {
        return;
    }
    app.wave_drag = -1;
    app.roll_note = -1;
    app.roll_erase = false;
    app.roll_drag_group.clear();
    app.roll_paste = true;
}

void midi_duplicate_selection(App& app) {
    midi_copy_selection(app);
    midi_begin_paste_preview(app);
}

void midi_delete_selection(App& app) {
    if (app.note_stage != NoteStage::Midi || app.roll_paste || app.roll_erase) {
        return;
    }
    const int stem = std::clamp(app.selected, 0, bt::kStemCount - 1);
    auto& stem_notes = app.notes[static_cast<std::size_t>(stem)];
    if (app.roll_selected.size() != stem_notes.size()) {
        app.roll_selected.assign(stem_notes.size(), 0);
    }
    bool any = false;
    for (char selected : app.roll_selected) {
        if (selected) {
            any = true;
            break;
        }
    }
    if (!any) {
        return;
    }
    begin_note_gesture(app, stem);
    std::vector<MidiNote> kept;
    kept.reserve(stem_notes.size());
    for (int index = 0; index < static_cast<int>(stem_notes.size()); ++index) {
        if (index >= static_cast<int>(app.roll_selected.size()) || !app.roll_selected[static_cast<std::size_t>(index)]) {
            kept.push_back(stem_notes[static_cast<std::size_t>(index)]);
        }
    }
    stem_notes = std::move(kept);
    app.roll_selected.assign(stem_notes.size(), 0);
    finish_note_gesture(app, stem);
}

}  // namespace btapp
