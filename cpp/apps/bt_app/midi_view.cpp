#include "midi_view.hpp"

#include <algorithm>
#include <cmath>
#include <cstdio>

namespace btapp {
namespace {

constexpr int kFallDrag = 8;
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
    const double age = ImGui::GetTime() - app.beat_flash_at;
    const double duration = app.beat_flash_down ? 0.17 : 0.12;
    if (!app.metro || age < 0 || age >= duration) {
        return;
    }
    const float fade = 1.f - static_cast<float>(age / duration);
    const int alpha = static_cast<int>(fade * fade * (app.beat_flash_down ? 242.f : 158.f));
    const float thick = (app.beat_flash_down ? 4.f : 3.f) * ui_scale();
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
    const int num_frets = std::clamp(app.fret_count, 12, 24);
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
    instrument_pitch_bounds(bass, num_frets, span_low, span_high);
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
        const float norm = std::clamp(static_cast<float>(velocity) / static_cast<float>(std::max(1, app.fret_bright)), 0.f, 1.f);
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
    if (app.metro) {
        each_metro_beat(app, position, position + kFallLook, [&](double time, bool downbeat) {
            draw->AddRectFilled(
                ImVec2(origin.x, y_of(time)), ImVec2(origin.x + size.x, y_of(time) + (downbeat ? std::max(1.f, scale) : 1.f)),
                downbeat ? IM_COL32(255, 255, 255, 71) : IM_COL32(255, 255, 255, 26));
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
    for (int pitch = lo; pitch <= hi; ++pitch) {
        if (black_key(pitch)) {
            continue;
        }
        const float x = x_of_pitch(pitch);
        const ImU32 color = (pitch >= 0 && pitch < 128 && live[pitch]) ? kPitchColor[pitch % 12] : IM_COL32(228, 228, 228, 255);
        draw->AddRectFilled(ImVec2(x + scale, origin.y + lane_h), ImVec2(x + std::max(scale + 1.f, slot), origin.y + size.y), color);
    }
    for (int pitch = lo; pitch <= hi; ++pitch) {
        if (!black_key(pitch)) {
            continue;
        }
        const float x = x_of_pitch(pitch);
        const ImU32 color = (pitch >= 0 && pitch < 128 && live[pitch]) ? kPitchColor[pitch % 12] : IM_COL32(22, 22, 22, 255);
        draw->AddRectFilled(ImVec2(x + slot * 0.2f, origin.y + lane_h), ImVec2(x + slot * 0.8f, origin.y + lane_h + keyboard_h * 0.62f), color);
    }
    // Dim keys outside the pitch window, same idea as the fretboard wash.
    for (int pitch = lo; pitch <= hi; ++pitch) {
        if (pitch >= range_lo && pitch <= range_hi) {
            continue;
        }
        const float x = x_of_pitch(pitch);
        if (black_key(pitch)) {
            draw->AddRectFilled(
                ImVec2(x + slot * 0.2f, origin.y + lane_h), ImVec2(x + slot * 0.8f, origin.y + lane_h + keyboard_h * 0.62f),
                IM_COL32(0, 0, 0, 145));
        } else {
            draw->AddRectFilled(
                ImVec2(x + scale, origin.y + lane_h), ImVec2(x + std::max(scale + 1.f, slot), origin.y + size.y),
                IM_COL32(0, 0, 0, 145));
        }
    }
    const float key_font = std::min(12.f * scale, std::max(8.f * scale, slot * 0.38f));
    for (int pitch = lo; pitch <= hi; ++pitch) {
        char name[8];
        midi_note_name(pitch, name, sizeof name);
        const float x = x_of_pitch(pitch);
        const bool black = black_key(pitch);
        const float key_w = black ? slot * 0.6f : std::max(1.f, slot - scale);
        float font = key_font;
        ImVec2 ts = text_size(font, name);
        const float limit = key_w - 2.f * scale;
        if (ts.x > limit) {
            if (pitch % 12 != 0) {
                continue;
            }
            font = std::max(7.f * scale, std::min(key_font, limit / 2.4f));
            ts = text_size(font, name);
            if (ts.x > limit || ts.y > keyboard_h * 0.9f) {
                continue;
            }
        }
        const bool in_range = pitch >= range_lo && pitch <= range_hi;
        const bool sounding = in_range && pitch >= 0 && pitch < 128 && live[pitch];
        const ImU32 ink = !in_range ? IM_COL32(120, 120, 120, 180)
                                    : (black || sounding ? IM_COL32(245, 245, 245, 230) : IM_COL32(28, 28, 28, 230));
        const float text_x = black ? x + slot * 0.5f - ts.x * 0.5f : x + (slot + scale) * 0.5f - ts.x * 0.5f;
        const float text_y = black ? origin.y + lane_h + keyboard_h * 0.62f - ts.y - 2.f * scale : origin.y + size.y - ts.y - 3.f * scale;
        text_at(draw, ImVec2(text_x, text_y), font, ink, name);
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

void instrument_pitch_bounds(bool bass, int fret_count, int& low, int& high) {
    const int* opens = bass ? kBassOpen : kGuitarOpen;
    const int strings = bass ? 4 : 6;
    int bottom = opens[0];
    int top = opens[0];
    for (int index = 1; index < strings; ++index) {
        bottom = std::min(bottom, opens[index]);
        top = std::max(top, opens[index]);
    }
    low = bottom;
    high = top + std::clamp(fret_count, 12, 24);
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
    if (!ImGui::IsMouseDown(ImGuiMouseButton_Right) && app.loop_drag == kFallDrag) {
        app.loop_drag = -1;
    }
    const ImVec2 origin = ImGui::GetCursorScreenPos();
    ImGui::InvisibleButton("midi-view", size);
    const bool hovered = ImGui::IsItemHovered();
    if (hovered && app.midi_view == MidiView::Fall) {
        ImGui::SetMouseCursor(ImGuiMouseCursor_ResizeNS);
    }
    ImDrawList* draw = ImGui::GetWindowDrawList();
    const int canvas_begin = draw->VtxBuffer.Size;
    const ImVec2 corner(origin.x + size.x, origin.y + size.y);
    draw->PushClipRect(origin, corner, true);
    draw->AddRectFilled(origin, corner, IM_COL32(20, 20, 20, 255));
    const int stem = std::clamp(app.selected, 0, bt::kStemCount - 1);
    if (app.midi_view == MidiView::Guitar || app.midi_view == MidiView::Bass) {
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

}  // namespace btapp
