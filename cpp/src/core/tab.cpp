#include "bt/core/tab.hpp"

#include "bt/contract.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <functional>
#include <limits>
#include <string>
#include <utility>

namespace bt::core {
namespace {

constexpr double kQuantizeEps = 0.002;
constexpr int kMaxCombos = 64;

using SpotList = std::vector<TabSpot>;

bool stem_in_range(int stem) {
    return stem >= 0 && stem < bt::kStemCount;
}

const std::vector<bt::Note>* notes_at(const TabWorkspace& workspace, int stem) {
    if (!stem_in_range(stem) || workspace.notes == nullptr) {
        return nullptr;
    }
    return &(*workspace.notes)[static_cast<std::size_t>(stem)];
}

std::vector<bt::Note>* notes_at(TabWorkspace& workspace, int stem) {
    if (!stem_in_range(stem) || workspace.notes == nullptr) {
        return nullptr;
    }
    return &(*workspace.notes)[static_cast<std::size_t>(stem)];
}

TabScore* score_at(TabWorkspace& workspace, int stem) {
    if (!stem_in_range(stem) || workspace.scores == nullptr) {
        return nullptr;
    }
    return &(*workspace.scores)[static_cast<std::size_t>(stem)];
}

const TabScore* score_at(const TabWorkspace& workspace, int stem) {
    if (!stem_in_range(stem) || workspace.scores == nullptr) {
        return nullptr;
    }
    return &(*workspace.scores)[static_cast<std::size_t>(stem)];
}

int hand_span_frets(const FingeringParams& params) {
    return std::clamp(params.hand_span, 1, 12);
}

double onset_window_seconds(const FingeringParams& params) {
    return std::max(0.0, static_cast<double>(std::clamp(params.onset_ms, 0, 200)) * 0.001);
}

int fingering_anchor(const FingeringParams& params) {
    const int hand_span = hand_span_frets(params);
    if (params.prefer_fret > 0) {
        return std::clamp(params.prefer_fret - 1, 0, kFretCount - hand_span);
    }
    return std::clamp(static_cast<int>(std::lround(params.hand_center)), 0, kFretCount - hand_span);
}

bool guitar_on_staff(const TabWorkspace& workspace, int stem, int note_index) {
    if (stem != kGuitarStem || note_index < 0) {
        return false;
    }
    const auto* notes = notes_at(workspace, stem);
    return notes != nullptr && note_index < static_cast<int>(notes->size());
}

bool near_grid(double time, const std::vector<double>& grid) {
    if (grid.empty()) {
        return std::abs(time) <= kQuantizeEps;
    }
    double best = std::abs(time - grid.front());
    for (double mark : grid) {
        best = std::min(best, std::abs(time - mark));
        if (best <= kQuantizeEps) {
            return true;
        }
    }
    return best <= kQuantizeEps;
}

std::vector<double> build_grid(const TabWorkspace& workspace, double cover) {
    std::vector<double> grid;
    auto collect = [&](double tick, GridMark) { grid.push_back(tick); };
    if (!workspace.beats.times.empty()) {
        each_grid_tick(workspace.beats.times, workspace.grid_division, 0.0, cover + 8.0, collect);
    } else {
        each_bpm_tick(workspace.beats.bpm, workspace.beats.offset, workspace.grid_division, 0.0, cover + 8.0, collect);
    }
    if (grid.empty()) {
        grid.push_back(0.0);
    }
    return grid;
}

double cover_notes(const std::vector<bt::Note>& notes, double length) {
    double cover = std::max(0.0, length);
    for (const bt::Note& note : notes) {
        cover = std::max(cover, note.end);
        cover = std::max(cover, note.start);
    }
    return cover;
}

double nearest_time(double time, const std::vector<double>& grid) {
    double best = grid.front();
    double best_distance = std::abs(time - best);
    for (double mark : grid) {
        const double distance = std::abs(time - mark);
        if (distance < best_distance) {
            best = mark;
            best_distance = distance;
        }
    }
    return best;
}

SpotList spots_for(int pitch, bool allow_open) {
    TabSpot buffer[8];
    const int count = collect_guitar_spots(pitch, allow_open, buffer, 8);
    return SpotList(buffer, buffer + count);
}

bool assign_chord(const std::vector<SpotList>& options, std::vector<TabSpot>& chosen) {
    const int n = static_cast<int>(options.size());
    chosen.assign(static_cast<std::size_t>(n), {});
    if (n == 0) {
        return true;
    }
    if (n > kGuitarStringCount) {
        return false;
    }
    std::vector<int> order(static_cast<std::size_t>(n));
    for (int i = 0; i < n; ++i) {
        order[static_cast<std::size_t>(i)] = i;
    }
    std::sort(order.begin(), order.end(), [&](int left, int right) {
        return options[static_cast<std::size_t>(left)].size() < options[static_cast<std::size_t>(right)].size();
    });

    std::array<bool, kGuitarStringCount> used{};
    std::function<bool(int)> dfs = [&](int depth) -> bool {
        if (depth >= n) {
            return true;
        }
        const int note_i = order[static_cast<std::size_t>(depth)];
        const SpotList& spots = options[static_cast<std::size_t>(note_i)];
        for (const TabSpot& spot : spots) {
            if (used[static_cast<std::size_t>(spot.string)]) {
                continue;
            }
            used[static_cast<std::size_t>(spot.string)] = true;
            chosen[static_cast<std::size_t>(note_i)] = spot;
            if (dfs(depth + 1)) {
                return true;
            }
            used[static_cast<std::size_t>(spot.string)] = false;
        }
        return false;
    };
    return dfs(0);
}

int score_spot(TabSpot spot, int anchor, int hand_span) {
    const int span = std::max(1, hand_span);
    const int low = anchor;
    const int high = anchor + span;
    int distance = 0;
    if (spot.fret < low) {
        distance = low - spot.fret;
    } else if (spot.fret > high) {
        distance = spot.fret - high;
    } else {
        distance = std::abs(spot.fret - anchor);
    }
    return distance * 10 + spot.string;
}

int fretted_span(const std::vector<TabSpot>& spots) {
    int low = 99;
    int high = -1;
    for (const TabSpot& spot : spots) {
        if (spot.fret <= 0) {
            continue;
        }
        low = std::min(low, spot.fret);
        high = std::max(high, spot.fret);
    }
    if (high < 0) {
        return 0;
    }
    return high - low;
}

float shape_center(const std::vector<TabSpot>& spots) {
    int sum = 0;
    int count = 0;
    for (const TabSpot& spot : spots) {
        if (spot.fret > 0) {
            sum += spot.fret;
            ++count;
        }
    }
    if (count > 0) {
        return static_cast<float>(sum) / static_cast<float>(count);
    }
    return 0.f;
}

int shape_transition_cost(const std::vector<TabSpot>& left, const std::vector<TabSpot>& right) {
    const float jump = std::fabs(shape_center(left) - shape_center(right));
    int string_delta = 0;
    const std::size_t n = std::min(left.size(), right.size());
    for (std::size_t i = 0; i < n; ++i) {
        string_delta += std::abs(left[i].string - right[i].string);
    }
    return static_cast<int>(std::lround(jump * 20.f)) + string_delta * 3;
}

void collect_all_chords(
    const std::vector<SpotList>& options, std::vector<std::vector<TabSpot>>& out, int anchor, int hand_span, int cap) {
    const int n = static_cast<int>(options.size());
    out.clear();
    if (n == 0 || n > kGuitarStringCount || cap <= 0) {
        return;
    }
    for (const SpotList& spots : options) {
        if (spots.empty()) {
            return;
        }
    }
    std::vector<int> order(static_cast<std::size_t>(n));
    for (int i = 0; i < n; ++i) {
        order[static_cast<std::size_t>(i)] = i;
    }
    std::sort(order.begin(), order.end(), [&](int left, int right) {
        return options[static_cast<std::size_t>(left)].size() < options[static_cast<std::size_t>(right)].size();
    });
    std::vector<TabSpot> chosen(static_cast<std::size_t>(n));
    std::array<bool, kGuitarStringCount> used{};
    std::function<void(int)> dfs = [&](int depth) {
        if (static_cast<int>(out.size()) >= cap) {
            return;
        }
        if (depth >= n) {
            if (fretted_span(chosen) <= hand_span) {
                out.push_back(chosen);
            }
            return;
        }
        const int note_i = order[static_cast<std::size_t>(depth)];
        SpotList spots = options[static_cast<std::size_t>(note_i)];
        std::sort(spots.begin(), spots.end(), [&](const TabSpot& left, const TabSpot& right) {
            return score_spot(left, anchor, hand_span) < score_spot(right, anchor, hand_span);
        });
        for (const TabSpot& spot : spots) {
            if (used[static_cast<std::size_t>(spot.string)]) {
                continue;
            }
            used[static_cast<std::size_t>(spot.string)] = true;
            chosen[static_cast<std::size_t>(note_i)] = spot;
            dfs(depth + 1);
            used[static_cast<std::size_t>(spot.string)] = false;
            if (static_cast<int>(out.size()) >= cap) {
                return;
            }
        }
    };
    dfs(0);
    std::sort(out.begin(), out.end(), [&](const std::vector<TabSpot>& left, const std::vector<TabSpot>& right) {
        int score_l = 0;
        int score_r = 0;
        for (const TabSpot& spot : left) {
            score_l += score_spot(spot, anchor, hand_span);
        }
        for (const TabSpot& spot : right) {
            score_r += score_spot(spot, anchor, hand_span);
        }
        return score_l < score_r;
    });
}

void assign_chord_soft(const std::vector<SpotList>& options, std::vector<TabSpot>& chosen, std::vector<char>& assigned) {
    const int n = static_cast<int>(options.size());
    chosen.assign(static_cast<std::size_t>(n), {});
    assigned.assign(static_cast<std::size_t>(n), 0);
    if (n == 0) {
        return;
    }
    if (assign_chord(options, chosen)) {
        assigned.assign(static_cast<std::size_t>(n), 1);
        return;
    }
    std::vector<int> order(static_cast<std::size_t>(n));
    for (int i = 0; i < n; ++i) {
        order[static_cast<std::size_t>(i)] = i;
    }
    std::sort(order.begin(), order.end(), [&](int left, int right) {
        const std::size_t l = static_cast<std::size_t>(left);
        const std::size_t r = static_cast<std::size_t>(right);
        if (options[l].empty() != options[r].empty()) {
            return options[r].empty();
        }
        return options[l].size() < options[r].size();
    });
    std::array<bool, kGuitarStringCount> used{};
    for (int note_i : order) {
        const SpotList& spots = options[static_cast<std::size_t>(note_i)];
        for (const TabSpot& spot : spots) {
            if (used[static_cast<std::size_t>(spot.string)]) {
                continue;
            }
            used[static_cast<std::size_t>(spot.string)] = true;
            chosen[static_cast<std::size_t>(note_i)] = spot;
            assigned[static_cast<std::size_t>(note_i)] = 1;
            break;
        }
    }
}

double measure_fill_quarters(const TabMeasure& measure) {
    double sum = 0;
    for (const TabBeat& beat : measure.beats) {
        sum += tab_duration_quarters(beat.duration, beat.dotted);
    }
    return sum;
}

bool find_next_note_beat(const TabScore& score, int measure, int beat, int& out_measure, int& out_beat) {
    for (int m = measure; m < static_cast<int>(score.measures.size()); ++m) {
        const TabMeasure& bar = score.measures[static_cast<std::size_t>(m)];
        const int start = (m == measure) ? beat + 1 : 0;
        for (int b = start; b < static_cast<int>(bar.beats.size()); ++b) {
            if (!bar.beats[static_cast<std::size_t>(b)].rest) {
                out_measure = m;
                out_beat = b;
                return true;
            }
        }
    }
    return false;
}

void retime_measure_beats(TabMeasure& measure, double beat_seconds) {
    double time = measure.start;
    for (TabBeat& beat : measure.beats) {
        beat.time = time;
        time += tab_duration_quarters(beat.duration, beat.dotted) * beat_seconds;
    }
}

void append_rest_fill(std::vector<TabBeat>& out, double remain_q, double time, double beat_seconds) {
    while (remain_q > 1e-9) {
        TabBeat rest;
        rest.rest = true;
        rest.dotted = false;
        rest.duration = tab_duration_from_quarters(remain_q);
        double quarters = tab_duration_quarters(rest.duration, false);
        if (quarters > remain_q + 1e-9) {
            static const TabDuration kUnits[] = {
                TabDuration::Whole, TabDuration::Half, TabDuration::Quarter, TabDuration::Eighth,
                TabDuration::Sixteenth, TabDuration::ThirtySecond, TabDuration::SixtyFourth};
            rest.duration = TabDuration::SixtyFourth;
            for (TabDuration unit : kUnits) {
                const double unit_q = tab_duration_quarters(unit, false);
                if (unit_q <= remain_q + 1e-9) {
                    rest.duration = unit;
                    break;
                }
            }
            quarters = tab_duration_quarters(rest.duration, false);
        }
        if (quarters <= 1e-9) {
            break;
        }
        rest.time = time;
        out.push_back(rest);
        time += quarters * beat_seconds;
        remain_q -= quarters;
    }
}

void detach_notes_from_score(TabScore& score, const std::vector<int>& note_indices) {
    for (TabMeasure& measure : score.measures) {
        for (TabBeat& beat : measure.beats) {
            if (beat.note_indices.empty()) {
                continue;
            }
            std::vector<int> kept;
            for (int index : beat.note_indices) {
                if (std::find(note_indices.begin(), note_indices.end(), index) == note_indices.end()) {
                    kept.push_back(index);
                }
            }
            beat.note_indices = std::move(kept);
            beat.rest = beat.note_indices.empty();
        }
    }
}

bool beat_has_all_notes(const TabBeat& beat, const std::vector<int>& note_indices) {
    if (beat.rest || beat.note_indices.empty() || note_indices.empty()) {
        return false;
    }
    if (beat.note_indices.size() < note_indices.size()) {
        return false;
    }
    for (int index : note_indices) {
        if (std::find(beat.note_indices.begin(), beat.note_indices.end(), index) == beat.note_indices.end()) {
            return false;
        }
    }
    return true;
}

bool ensure_score_slot_at(TabScore& score, double time, double beat_seconds, int& out_measure, int& out_beat) {
    if (score.measures.empty() || !(beat_seconds > 1e-9)) {
        return false;
    }
    for (int m = 0; m < static_cast<int>(score.measures.size()); ++m) {
        TabMeasure& bar = score.measures[static_cast<std::size_t>(m)];
        for (int b = 0; b < static_cast<int>(bar.beats.size()); ++b) {
            TabBeat& beat = bar.beats[static_cast<std::size_t>(b)];
            const double dur = tab_duration_quarters(beat.duration, beat.dotted) * beat_seconds;
            if (std::abs(beat.time - time) <= 1e-4) {
                out_measure = m;
                out_beat = b;
                return true;
            }
            if (!beat.rest || time < beat.time - 1e-6 || time >= beat.time + dur - 1e-6) {
                continue;
            }
            const double before_q = (time - beat.time) / beat_seconds;
            const double after_q = (beat.time + dur - time) / beat_seconds;
            std::vector<TabBeat> rebuilt;
            rebuilt.reserve(bar.beats.size() + 2);
            for (int i = 0; i < b; ++i) {
                rebuilt.push_back(bar.beats[static_cast<std::size_t>(i)]);
            }
            if (before_q > 1e-6) {
                append_rest_fill(rebuilt, before_q, beat.time, beat_seconds);
            }
            TabBeat slot;
            slot.rest = true;
            slot.time = time;
            slot.dotted = false;
            if (after_q + 1e-9 >= 1.0) {
                slot.duration = TabDuration::Quarter;
            } else {
                slot.duration = tab_duration_from_quarters(std::max(after_q, 1.0 / 64.0));
            }
            const double slot_q = tab_duration_quarters(slot.duration, false);
            rebuilt.push_back(slot);
            const double tail = after_q - slot_q;
            if (tail > 1e-6) {
                append_rest_fill(rebuilt, tail, time + slot_q * beat_seconds, beat_seconds);
            }
            for (int i = b + 1; i < static_cast<int>(bar.beats.size()); ++i) {
                rebuilt.push_back(bar.beats[static_cast<std::size_t>(i)]);
            }
            bar.beats = std::move(rebuilt);
            retime_measure_beats(bar, beat_seconds);
            for (int i = 0; i < static_cast<int>(bar.beats.size()); ++i) {
                if (std::abs(bar.beats[static_cast<std::size_t>(i)].time - time) <= 1e-4) {
                    out_measure = m;
                    out_beat = i;
                    return true;
                }
            }
            out_measure = m;
            out_beat = std::min(b, static_cast<int>(bar.beats.size()) - 1);
            return true;
        }
    }
    return false;
}

void soft_fill_unfingered_groups(TabWorkspace& workspace, int stem) {
    auto* notes = notes_at(workspace, stem);
    if (notes == nullptr) {
        return;
    }
    const bool allow_open = true;
    const double onset_eps = onset_window_seconds(workspace.fingering);
    struct Group {
        double start = 0;
        std::vector<int> indices;
    };
    std::vector<Group> clusters;
    for (int index = 0; index < static_cast<int>(notes->size()); ++index) {
        const double start = (*notes)[static_cast<std::size_t>(index)].start;
        bool placed = false;
        for (Group& group : clusters) {
            if (std::abs(group.start - start) <= onset_eps) {
                group.indices.push_back(index);
                placed = true;
                break;
            }
        }
        if (!placed) {
            clusters.push_back(Group{start, {index}});
        }
    }
    for (const Group& group : clusters) {
        std::array<bool, kGuitarStringCount> used{};
        std::vector<int> need;
        need.reserve(group.indices.size());
        for (int index : group.indices) {
            bt::Note& note = (*notes)[static_cast<std::size_t>(index)];
            if (note_has_fingering(note) && fingering_matches_pitch(note)) {
                used[static_cast<std::size_t>(std::clamp(note.string, 0, kGuitarStringCount - 1))] = true;
                continue;
            }
            need.push_back(index);
        }
        if (need.empty()) {
            continue;
        }
        std::vector<SpotList> options;
        options.reserve(need.size());
        for (int index : need) {
            options.push_back(spots_for((*notes)[static_cast<std::size_t>(index)].pitch, allow_open));
        }
        std::vector<TabSpot> chosen;
        std::vector<char> assigned;
        assign_chord_soft(options, chosen, assigned);
        for (std::size_t i = 0; i < need.size(); ++i) {
            if (!assigned[i]) {
                continue;
            }
            const int string = chosen[i].string;
            if (string < 0 || string >= kGuitarStringCount || used[static_cast<std::size_t>(string)]) {
                assigned[i] = 0;
                continue;
            }
            used[static_cast<std::size_t>(string)] = true;
            bt::Note& note = (*notes)[static_cast<std::size_t>(need[i])];
            const int pitch = note.pitch;
            apply_fingering(note, chosen[i]);
            note.pitch = pitch;
        }
        for (std::size_t i = 0; i < need.size(); ++i) {
            bt::Note& note = (*notes)[static_cast<std::size_t>(need[i])];
            if (note_has_fingering(note) && fingering_matches_pitch(note)) {
                continue;
            }
            const SpotList& spots = options[i];
            if (spots.empty()) {
                int best_string = 0;
                int best_fret = 0;
                int best_dist = std::numeric_limits<int>::max();
                for (int string = 0; string < kGuitarStringCount; ++string) {
                    const int open = kGuitarOpenPitch[string];
                    const int fret = std::clamp(note.pitch - open, 0, kFretCount);
                    const int dist = std::abs((open + fret) - note.pitch);
                    if (dist < best_dist) {
                        best_dist = dist;
                        best_string = string;
                        best_fret = fret;
                    }
                }
                const int pitch = note.pitch;
                apply_fingering(note, TabSpot{best_string, best_fret});
                note.pitch = pitch;
                continue;
            }
            TabSpot pick = spots.front();
            bool found_free = false;
            for (const TabSpot& spot : spots) {
                if (!used[static_cast<std::size_t>(spot.string)]) {
                    pick = spot;
                    found_free = true;
                    break;
                }
            }
            if (!found_free) {
                pick = spots.front();
            } else {
                used[static_cast<std::size_t>(pick.string)] = true;
            }
            const int pitch = note.pitch;
            apply_fingering(note, pick);
            note.pitch = pitch;
        }
    }
}

}  // namespace

bool note_has_fingering(const bt::Note& note) {
    return note.string >= 0 && note.string < kGuitarStringCount && note.fret >= 0 && note.fret <= kFretCount;
}

bool fingering_matches_pitch(const bt::Note& note) {
    if (!note_has_fingering(note)) {
        return false;
    }
    return kGuitarOpenPitch[note.string] + note.fret == note.pitch;
}

void clear_fingering(bt::Note& note) {
    note.string = -1;
    note.fret = -1;
}

void apply_fingering(bt::Note& note, TabSpot spot) {
    note.string = spot.string;
    note.fret = spot.fret;
    note.pitch = kGuitarOpenPitch[spot.string] + spot.fret;
    note.on_tab = true;
}

void revalidate_stem_fingerings(std::vector<bt::Note>& notes) {
    for (bt::Note& note : notes) {
        if (note_has_fingering(note) && !fingering_matches_pitch(note)) {
            clear_fingering(note);
        }
    }
}

int collect_guitar_spots(int pitch, bool allow_open, TabSpot* out, int cap) {
    if (out == nullptr || cap <= 0) {
        return 0;
    }
    const int minimum = allow_open ? 0 : 1;
    int count = 0;
    for (int string = 0; string < kGuitarStringCount; ++string) {
        const int fret = pitch - kGuitarOpenPitch[string];
        if (fret >= minimum && fret <= kFretCount) {
            out[count++] = TabSpot{string, fret};
            if (count >= cap) {
                break;
            }
        }
    }
    return count;
}

bool cycle_note_fingering(bt::Note& note, bool allow_open) {
    TabSpot spots[8];
    const int count = collect_guitar_spots(note.pitch, allow_open, spots, 8);
    if (count <= 0) {
        return false;
    }
    int current = -1;
    if (note_has_fingering(note)) {
        for (int i = 0; i < count; ++i) {
            if (spots[i].string == note.string && spots[i].fret == note.fret) {
                current = i;
                break;
            }
        }
    }
    const int next = current < 0 ? 0 : (current + 1) % count;
    apply_fingering(note, spots[next]);
    return true;
}

bool set_note_string(bt::Note& note, int string, bool allow_open) {
    if (string < 0 || string >= kGuitarStringCount) {
        return false;
    }
    TabSpot spots[8];
    const int count = collect_guitar_spots(note.pitch, allow_open, spots, 8);
    for (int i = 0; i < count; ++i) {
        if (spots[i].string == string) {
            apply_fingering(note, spots[i]);
            return true;
        }
    }
    return false;
}

double tab_duration_quarters(TabDuration duration, bool dotted) {
    double quarters = 1.0;
    switch (duration) {
    case TabDuration::Whole:
        quarters = 4.0;
        break;
    case TabDuration::Half:
        quarters = 2.0;
        break;
    case TabDuration::Quarter:
        quarters = 1.0;
        break;
    case TabDuration::Eighth:
        quarters = 0.5;
        break;
    case TabDuration::Sixteenth:
        quarters = 0.25;
        break;
    case TabDuration::ThirtySecond:
        quarters = 0.125;
        break;
    case TabDuration::SixtyFourth:
        quarters = 0.0625;
        break;
    }
    return dotted ? quarters * 1.5 : quarters;
}

const char* tab_duration_label_key(TabDuration duration) {
    switch (duration) {
    case TabDuration::Whole:
        return "tab.dur_whole";
    case TabDuration::Half:
        return "tab.dur_half";
    case TabDuration::Quarter:
        return "tab.dur_quarter";
    case TabDuration::Eighth:
        return "tab.dur_eighth";
    case TabDuration::Sixteenth:
        return "tab.dur_16";
    case TabDuration::ThirtySecond:
        return "tab.dur_32";
    case TabDuration::SixtyFourth:
        return "tab.dur_64";
    }
    return "tab.dur_quarter";
}

TabDuration tab_duration_step(TabDuration duration, int delta) {
    int index = static_cast<int>(duration);
    index = std::clamp(index + delta, 0, kTabDurationCount - 1);
    return static_cast<TabDuration>(index);
}

TabDuration tab_duration_from_quarters(double quarters) {
    if (!(quarters > 0.0)) {
        return TabDuration::Quarter;
    }
    static const TabDuration kOrder[] = {
        TabDuration::Whole,     TabDuration::Half,         TabDuration::Quarter, TabDuration::Eighth,
        TabDuration::Sixteenth, TabDuration::ThirtySecond, TabDuration::SixtyFourth};
    TabDuration best = TabDuration::Quarter;
    double best_err = 1e9;
    for (TabDuration candidate : kOrder) {
        for (bool dotted : {false, true}) {
            const double value = tab_duration_quarters(candidate, dotted);
            const double err = std::abs(value - quarters);
            if (err < best_err - 1e-9 || (std::abs(err - best_err) <= 1e-9 && !dotted)) {
                best_err = err;
                best = candidate;
            }
        }
    }
    return best;
}

bool tab_score_empty(const TabScore& score) {
    return score.measures.empty();
}

double tab_display_end(
    const std::vector<bt::Note>& notes, const std::vector<int>& group, int note_index, double onset_eps) {
    if (note_index < 0 || note_index >= static_cast<int>(notes.size())) {
        return 0;
    }
    const bt::Note& note = notes[static_cast<std::size_t>(note_index)];
    double group_start = note.start;
    for (int index : group) {
        if (index >= 0 && index < static_cast<int>(notes.size())) {
            group_start = std::min(group_start, notes[static_cast<std::size_t>(index)].start);
        }
    }
    double cut = std::numeric_limits<double>::infinity();
    for (int index = 0; index < static_cast<int>(notes.size()); ++index) {
        if (std::find(group.begin(), group.end(), index) != group.end()) {
            continue;
        }
        const double start = notes[static_cast<std::size_t>(index)].start;
        if (start > group_start + onset_eps && start < cut) {
            cut = start;
        }
    }
    if (std::isfinite(cut) && note.end > cut) {
        return std::max(note.start + 1e-3, cut);
    }
    return note.end;
}

double tab_pulse_seconds(const TabWorkspace& workspace) {
    if (workspace.beats.times.size() >= 2) {
        double sum = 0;
        int count = 0;
        for (std::size_t i = 0; i + 1 < workspace.beats.times.size() && count < 32; ++i) {
            const double gap = workspace.beats.times[i + 1] - workspace.beats.times[i];
            if (gap > 1e-3) {
                sum += gap;
                ++count;
            }
        }
        if (count > 0) {
            return sum / static_cast<double>(count);
        }
    }
    return workspace.beats.bpm > 1.f ? 60.0 / static_cast<double>(workspace.beats.bpm) : 0.5;
}

bool tab_slot_valid(const TabScore& score, int measure, int beat) {
    if (measure < 0 || measure >= static_cast<int>(score.measures.size())) {
        return false;
    }
    return beat >= 0 && beat < static_cast<int>(score.measures[static_cast<std::size_t>(measure)].beats.size());
}

void pad_measure_rests(TabMeasure& measure, int beats_per_bar, double beat_seconds) {
    const double capacity = static_cast<double>(std::max(1, beats_per_bar));
    while (!measure.beats.empty() && measure.beats.back().rest) {
        measure.beats.pop_back();
    }
    double filled = measure_fill_quarters(measure);
    double time = measure.start;
    if (!measure.beats.empty()) {
        const TabBeat& last = measure.beats.back();
        time = last.time + tab_duration_quarters(last.duration, last.dotted) * beat_seconds;
    }
    while (filled + 1e-9 < capacity) {
        const double remain = capacity - filled;
        TabBeat rest;
        rest.rest = true;
        rest.duration = tab_duration_from_quarters(remain);
        rest.dotted = std::abs(tab_duration_quarters(rest.duration, true) - remain) <
                      std::abs(tab_duration_quarters(rest.duration, false) - remain) - 1e-9;
        if (std::abs(tab_duration_quarters(rest.duration, false) - remain) <= 1e-6) {
            rest.dotted = false;
        } else if (std::abs(tab_duration_quarters(rest.duration, true) - remain) <= 1e-6) {
            rest.dotted = true;
        } else {
            static const TabDuration kUnits[] = {
                TabDuration::Whole, TabDuration::Half, TabDuration::Quarter, TabDuration::Eighth,
                TabDuration::Sixteenth, TabDuration::ThirtySecond, TabDuration::SixtyFourth};
            rest.duration = TabDuration::SixtyFourth;
            rest.dotted = false;
            for (TabDuration unit : kUnits) {
                const double quarters = tab_duration_quarters(unit, false);
                if (quarters <= remain + 1e-9) {
                    rest.duration = unit;
                    break;
                }
            }
        }
        rest.time = time;
        const double quarters = tab_duration_quarters(rest.duration, rest.dotted);
        measure.beats.push_back(rest);
        filled += quarters;
        time += quarters * beat_seconds;
        if (quarters <= 1e-9) {
            break;
        }
    }
}

std::vector<TabCombo> enumerate_fingering_combos(
    const TabWorkspace& workspace, int stem, const std::vector<int>& note_indices) {
    std::vector<TabCombo> combos;
    const auto* notes = notes_at(workspace, stem);
    if (notes == nullptr || note_indices.empty()) {
        return combos;
    }
    const int hand_span = hand_span_frets(workspace.fingering);
    std::vector<SpotList> options;
    options.reserve(note_indices.size());
    for (int index : note_indices) {
        if (index < 0 || index >= static_cast<int>(notes->size())) {
            return {};
        }
        TabSpot buffer[8];
        const int count = collect_guitar_spots((*notes)[static_cast<std::size_t>(index)].pitch, true, buffer, 8);
        options.emplace_back(buffer, buffer + count);
    }
    std::vector<std::vector<TabSpot>> raw;
    const int anchor = fingering_anchor(workspace.fingering);
    collect_all_chords(options, raw, anchor, hand_span, kMaxCombos);
    combos.reserve(raw.size());
    for (auto& spots : raw) {
        TabCombo combo;
        combo.spots = std::move(spots);
        for (const TabSpot& spot : combo.spots) {
            combo.score += score_spot(spot, anchor, hand_span);
        }
        combos.push_back(std::move(combo));
    }
    return combos;
}

TabGroupConflict tab_group_conflict(const TabWorkspace& workspace, int stem, const std::vector<int>& note_indices) {
    const auto* notes = notes_at(workspace, stem);
    if (notes == nullptr || note_indices.empty()) {
        return TabGroupConflict::Ok;
    }
    const auto combos = enumerate_fingering_combos(workspace, stem, note_indices);
    if (combos.empty()) {
        return TabGroupConflict::Error;
    }
    const int hand_span = hand_span_frets(workspace.fingering);
    std::vector<TabSpot> current;
    current.reserve(note_indices.size());
    std::array<bool, kGuitarStringCount> used{};
    for (int index : note_indices) {
        if (index < 0 || index >= static_cast<int>(notes->size())) {
            return TabGroupConflict::Warn;
        }
        const bt::Note& note = (*notes)[static_cast<std::size_t>(index)];
        if (!note_has_fingering(note) || !fingering_matches_pitch(note)) {
            return TabGroupConflict::Warn;
        }
        const int string = std::clamp(note.string, 0, kGuitarStringCount - 1);
        if (used[static_cast<std::size_t>(string)]) {
            return TabGroupConflict::Warn;
        }
        used[static_cast<std::size_t>(string)] = true;
        current.push_back(TabSpot{string, note.fret});
    }
    if (fretted_span(current) > hand_span) {
        return TabGroupConflict::Warn;
    }
    return TabGroupConflict::Ok;
}

std::vector<TabOnsetGroup> build_fingering_groups(
    const TabWorkspace& workspace, int stem, const std::vector<int>& note_indices) {
    std::vector<TabOnsetGroup> groups;
    const auto* notes = notes_at(workspace, stem);
    if (notes == nullptr) {
        return groups;
    }
    const double onset_eps = onset_window_seconds(workspace.fingering);
    for (int index : note_indices) {
        if (index < 0 || index >= static_cast<int>(notes->size())) {
            continue;
        }
        const double start = (*notes)[static_cast<std::size_t>(index)].start;
        bool placed = false;
        for (TabOnsetGroup& group : groups) {
            if (std::abs(group.start - start) <= onset_eps) {
                group.note_indices.push_back(index);
                placed = true;
                break;
            }
        }
        if (!placed) {
            groups.push_back(TabOnsetGroup{start, {index}, {}});
        }
    }
    std::sort(groups.begin(), groups.end(), [](const TabOnsetGroup& left, const TabOnsetGroup& right) {
        return left.start < right.start;
    });
    for (TabOnsetGroup& group : groups) {
        group.combos = enumerate_fingering_combos(workspace, stem, group.note_indices);
    }
    return groups;
}

int matching_fingering_combo(const TabWorkspace& workspace, int stem, const TabOnsetGroup& group) {
    if (group.combos.empty()) {
        return -1;
    }
    const auto* notes = notes_at(workspace, stem);
    if (notes == nullptr) {
        return -1;
    }
    for (int c = 0; c < static_cast<int>(group.combos.size()); ++c) {
        const TabCombo& combo = group.combos[static_cast<std::size_t>(c)];
        if (combo.spots.size() != group.note_indices.size()) {
            continue;
        }
        bool match = true;
        for (std::size_t i = 0; i < group.note_indices.size(); ++i) {
            const int index = group.note_indices[i];
            if (index < 0 || index >= static_cast<int>(notes->size())) {
                match = false;
                break;
            }
            const bt::Note& note = (*notes)[static_cast<std::size_t>(index)];
            if (!note_has_fingering(note) || note.string != combo.spots[i].string || note.fret != combo.spots[i].fret) {
                match = false;
                break;
            }
        }
        if (match) {
            return c;
        }
    }
    return -1;
}

int preferred_fingering_combo(const TabWorkspace& workspace, const TabOnsetGroup& group) {
    if (group.combos.empty()) {
        return -1;
    }
    const int anchor = fingering_anchor(workspace.fingering);
    int best = 0;
    int best_cost = std::numeric_limits<int>::max();
    const std::vector<TabSpot> seed{TabSpot{3, std::max(0, anchor)}};
    for (int c = 0; c < static_cast<int>(group.combos.size()); ++c) {
        const int cost = group.combos[static_cast<std::size_t>(c)].score +
                         shape_transition_cost(seed, group.combos[static_cast<std::size_t>(c)].spots);
        if (cost < best_cost) {
            best_cost = cost;
            best = c;
        }
    }
    return best;
}

bool write_fingering_combo(TabWorkspace& workspace, int stem, const TabOnsetGroup& group, int combo_index) {
    if (group.combos.empty() || combo_index < 0 || combo_index >= static_cast<int>(group.combos.size())) {
        return false;
    }
    const TabCombo& combo = group.combos[static_cast<std::size_t>(combo_index)];
    if (combo.spots.size() != group.note_indices.size()) {
        return false;
    }
    auto* notes = notes_at(workspace, stem);
    if (notes == nullptr) {
        return false;
    }
    for (std::size_t i = 0; i < group.note_indices.size(); ++i) {
        const int index = group.note_indices[i];
        if (index < 0 || index >= static_cast<int>(notes->size())) {
            continue;
        }
        bt::Note& note = (*notes)[static_cast<std::size_t>(index)];
        const int pitch = note.pitch;
        apply_fingering(note, combo.spots[i]);
        note.pitch = pitch;
        if (note.string >= 0 && note.string < kGuitarStringCount &&
            kGuitarOpenPitch[note.string] + note.fret != pitch) {
            note.fret = pitch - kGuitarOpenPitch[note.string];
        }
    }
    return true;
}

bool resolve_fingerings(TabWorkspace& workspace, int stem) {
    auto* notes = notes_at(workspace, stem);
    if (notes == nullptr || notes->empty()) {
        return false;
    }
    std::vector<int> all;
    all.reserve(notes->size());
    for (int index = 0; index < static_cast<int>(notes->size()); ++index) {
        all.push_back(index);
    }
    auto groups = build_fingering_groups(workspace, stem, all);
    if (groups.empty()) {
        return false;
    }

    const int n = static_cast<int>(groups.size());
    const int anchor = fingering_anchor(workspace.fingering);
    std::vector<TabSpot> prev_shape{TabSpot{3, std::max(0, anchor)}};
    std::vector<int> pick(static_cast<std::size_t>(n), -1);

    auto group_shape_from_notes = [&](const TabOnsetGroup& group) -> std::vector<TabSpot> {
        std::vector<TabSpot> spots;
        spots.reserve(group.note_indices.size());
        for (int index : group.note_indices) {
            const bt::Note& note = (*notes)[static_cast<std::size_t>(index)];
            if (note_has_fingering(note) && fingering_matches_pitch(note)) {
                spots.push_back(TabSpot{note.string, note.fret});
            }
        }
        return spots;
    };

    auto next_shape_hint = [&](int from) -> std::vector<TabSpot> {
        for (int g = from + 1; g < n; ++g) {
            const TabOnsetGroup& group = groups[static_cast<std::size_t>(g)];
            if (matching_fingering_combo(workspace, stem, group) >= 0) {
                return group_shape_from_notes(group);
            }
            if (!group.combos.empty()) {
                return group.combos.front().spots;
            }
        }
        return {};
    };

    bool any_change = false;
    for (int g = 0; g < n; ++g) {
        const TabOnsetGroup& group = groups[static_cast<std::size_t>(g)];
        const int matched = matching_fingering_combo(workspace, stem, group);
        if (matched >= 0) {
            pick[static_cast<std::size_t>(g)] = matched;
            prev_shape = group.combos[static_cast<std::size_t>(matched)].spots;
            continue;
        }
        if (group.combos.empty()) {
            continue;
        }
        const std::vector<TabSpot> next = next_shape_hint(g);
        int best = 0;
        int best_cost = std::numeric_limits<int>::max();
        for (int c = 0; c < static_cast<int>(group.combos.size()); ++c) {
            const TabCombo& combo = group.combos[static_cast<std::size_t>(c)];
            int cost = combo.score + shape_transition_cost(prev_shape, combo.spots);
            if (!next.empty()) {
                cost += shape_transition_cost(combo.spots, next);
            }
            if (cost < best_cost) {
                best_cost = cost;
                best = c;
            }
        }
        pick[static_cast<std::size_t>(g)] = best;
        prev_shape = group.combos[static_cast<std::size_t>(best)].spots;
        any_change = true;
    }

    if (!any_change) {
        for (const bt::Note& note : *notes) {
            if (!note_has_fingering(note)) {
                any_change = true;
                break;
            }
        }
    }
    if (!any_change) {
        return false;
    }

    for (int g = 0; g < n; ++g) {
        const int c = pick[static_cast<std::size_t>(g)];
        if (c < 0) {
            continue;
        }
        const TabOnsetGroup& group = groups[static_cast<std::size_t>(g)];
        if (c >= static_cast<int>(group.combos.size())) {
            continue;
        }
        if (matching_fingering_combo(workspace, stem, group) == c) {
            continue;
        }
        const TabCombo& combo = group.combos[static_cast<std::size_t>(c)];
        if (combo.spots.size() != group.note_indices.size()) {
            continue;
        }
        for (std::size_t i = 0; i < group.note_indices.size(); ++i) {
            bt::Note& note = (*notes)[static_cast<std::size_t>(group.note_indices[i])];
            const int pitch = note.pitch;
            apply_fingering(note, combo.spots[i]);
            note.pitch = pitch;
        }
    }
    soft_fill_unfingered_groups(workspace, stem);

    for (int g = n - 1; g >= 0; --g) {
        const int c = pick[static_cast<std::size_t>(g)];
        if (c < 0 || c >= static_cast<int>(groups[static_cast<std::size_t>(g)].combos.size())) {
            continue;
        }
        const float center = shape_center(groups[static_cast<std::size_t>(g)].combos[static_cast<std::size_t>(c)].spots);
        const int hand_span = hand_span_frets(workspace.fingering);
        const int next_anchor =
            std::clamp(static_cast<int>(std::lround(center)) - hand_span / 2, 0, kFretCount - hand_span);
        workspace.fingering.hand_center += 0.35f * (static_cast<float>(next_anchor) - workspace.fingering.hand_center);
        break;
    }
    return true;
}

TabGate evaluate_tab_gate(const TabWorkspace& workspace, int stem) {
    TabGate gate;
    const auto* notes = notes_at(workspace, stem);
    if (notes == nullptr) {
        gate.reasons.emplace_back("Invalid stem");
        return gate;
    }
    if (notes->empty()) {
        gate.reasons.emplace_back("No notes");
        return gate;
    }

    const auto grid = build_grid(workspace, cover_notes(*notes, workspace.length));
    const bool allow_open = true;
    const double onset_eps = onset_window_seconds(workspace.fingering);

    struct Group {
        double start = 0;
        std::vector<int> indices;
    };
    std::vector<Group> groups;
    for (int index = 0; index < static_cast<int>(notes->size()); ++index) {
        const bt::Note& note = (*notes)[static_cast<std::size_t>(index)];
        if (!near_grid(note.start, grid)) {
            ++gate.unquantized;
            gate.quantized = false;
        }
        TabSpot buffer[8];
        const int spots = collect_guitar_spots(note.pitch, allow_open, buffer, 8);
        if (spots <= 0) {
            ++gate.out_of_range;
            gate.in_range = false;
        }
        bool placed = false;
        for (Group& group : groups) {
            if (std::abs(group.start - note.start) <= onset_eps) {
                group.indices.push_back(index);
                placed = true;
                break;
            }
        }
        if (!placed) {
            groups.push_back(Group{note.start, {index}});
        }
    }

    for (const Group& group : groups) {
        std::vector<SpotList> options;
        options.reserve(group.indices.size());
        bool group_ok = true;
        for (int index : group.indices) {
            SpotList spots = spots_for((*notes)[static_cast<std::size_t>(index)].pitch, allow_open);
            if (spots.empty()) {
                group_ok = false;
                break;
            }
            options.push_back(std::move(spots));
        }
        std::vector<TabSpot> chosen;
        if (!group_ok || !assign_chord(options, chosen)) {
            ++gate.chord_clashes;
            gate.chords_ok = false;
        }
    }

    if (!gate.quantized) {
        gate.reasons.push_back(std::to_string(gate.unquantized) + " notes are off the grid");
    }
    if (!gate.in_range) {
        gate.reasons.push_back(std::to_string(gate.out_of_range) + " notes are outside guitar range");
    }
    if (!gate.chords_ok) {
        gate.reasons.push_back(std::to_string(gate.chord_clashes) + " chords cannot be fretted on six strings");
    }
    gate.ok = gate.quantized && gate.in_range && gate.chords_ok;
    return gate;
}

bool convert_notes_to_tab(TabWorkspace& workspace, int stem, const std::vector<char>& selected) {
    auto* notes = notes_at(workspace, stem);
    if (notes == nullptr || notes->empty()) {
        return false;
    }
    const bool filter_selection =
        selected.size() == notes->size() &&
        std::any_of(selected.begin(), selected.end(), [](char bit) { return bit != 0; });
    for (int index = 0; index < static_cast<int>(notes->size()); ++index) {
        if (filter_selection && !selected[static_cast<std::size_t>(index)]) {
            continue;
        }
        clear_fingering((*notes)[static_cast<std::size_t>(index)]);
        (*notes)[static_cast<std::size_t>(index)].on_tab = true;
    }
    resolve_fingerings(workspace, stem);
    for (int index = 0; index < static_cast<int>(notes->size()); ++index) {
        if (filter_selection && !selected[static_cast<std::size_t>(index)]) {
            continue;
        }
        if (note_has_fingering((*notes)[static_cast<std::size_t>(index)])) {
            return true;
        }
    }
    return false;
}

TabExportResult export_tab_text(const TabWorkspace& workspace, int stem, const std::filesystem::path& path) {
    TabExportResult result;
    const auto* notes = notes_at(workspace, stem);
    if (notes == nullptr) {
        result.error = "Invalid stem";
        return result;
    }
    if (notes->empty()) {
        result.error = "No notes";
        return result;
    }
    bool any_fingered = false;
    for (const bt::Note& note : *notes) {
        if (note_has_fingering(note) && fingering_matches_pitch(note)) {
            any_fingered = true;
            break;
        }
    }
    if (!any_fingered) {
        result.error = "Generate TAB fingerings first";
        return result;
    }

    const double cover = cover_notes(*notes, workspace.length);
    double bar = 2.0;
    if (workspace.beats.bpm > 1.0) {
        bar = 240.0 / workspace.beats.bpm;
    }
    if (workspace.beats.times.size() >= 5) {
        bar = workspace.beats.times[4] - workspace.beats.times[0];
        if (!(bar > 1e-3)) {
            bar = 2.0;
        }
    }

    std::error_code error;
    std::filesystem::create_directories(path.parent_path(), error);
    std::ofstream output(path, std::ios::trunc);
    if (!output) {
        result.error = "Could not write TAB file";
        return result;
    }

    output << "BT-Analyzer TAB\n";
    output << "stem=" << bt::kStemNames[static_cast<std::size_t>(stem)] << "\n";
    output << "tuning=EADGBE\n\n";

    const char* string_names[kGuitarStringCount] = {"e", "B", "G", "D", "A", "E"};
    const int bars = std::max(1, static_cast<int>(std::ceil(cover / bar)));
    constexpr int cols = 16;
    for (int measure = 0; measure < bars; ++measure) {
        const double t0 = static_cast<double>(measure) * bar;
        const double t1 = t0 + bar;
        std::array<std::string, kGuitarStringCount> lines;
        for (int string = 0; string < kGuitarStringCount; ++string) {
            lines[static_cast<std::size_t>(string)].assign(static_cast<std::size_t>(cols), '-');
        }
        for (const bt::Note& note : *notes) {
            if (note.start < t0 - 1e-6 || note.start >= t1 - 1e-6) {
                continue;
            }
            if (!note_has_fingering(note) || !fingering_matches_pitch(note)) {
                continue;
            }
            const double frac = std::clamp((note.start - t0) / bar, 0.0, 0.999);
            const int col = std::min(cols - 1, static_cast<int>(frac * cols));
            char digits[8];
            std::snprintf(digits, sizeof digits, "%d", note.fret);
            const int len = static_cast<int>(std::strlen(digits));
            auto& line = lines[static_cast<std::size_t>(note.string)];
            for (int d = 0; d < len && col + d < cols; ++d) {
                line[static_cast<std::size_t>(col + d)] = digits[d];
            }
        }
        output << "Bar " << (measure + 1) << '\n';
        for (int string = 0; string < kGuitarStringCount; ++string) {
            output << string_names[string] << "|" << lines[static_cast<std::size_t>(string)] << "|\n";
        }
        output << '\n';
    }
    if (!output) {
        result.error = "Could not write TAB file";
        return result;
    }
    result.ok = true;
    return result;
}

bool build_tab_score(TabWorkspace& workspace, int stem, bool on_tab_only) {
    auto* score = score_at(workspace, stem);
    const auto* notes = notes_at(workspace, stem);
    if (score == nullptr || notes == nullptr) {
        return false;
    }
    *score = {};
    score->beats_per_bar = 4;

    struct Onset {
        double start = 0;
        std::vector<int> indices;
    };
    std::vector<Onset> onsets;
    const double onset_eps = onset_window_seconds(workspace.fingering);
    double cover = 0;
    for (int index = 0; index < static_cast<int>(notes->size()); ++index) {
        if (on_tab_only && !guitar_on_staff(workspace, stem, index)) {
            continue;
        }
        const bt::Note& note = (*notes)[static_cast<std::size_t>(index)];
        cover = std::max(cover, note.end);
        cover = std::max(cover, note.start);
        bool placed = false;
        for (Onset& group : onsets) {
            if (std::abs(group.start - note.start) <= onset_eps) {
                group.indices.push_back(index);
                placed = true;
                break;
            }
        }
        if (!placed) {
            onsets.push_back(Onset{note.start, {index}});
        }
    }
    std::sort(onsets.begin(), onsets.end(), [](const Onset& left, const Onset& right) { return left.start < right.start; });

    std::vector<double> quarters;
    if (!workspace.beats.times.empty()) {
        quarters = workspace.beats.times;
    } else if (!workspace.beats.spans.empty()) {
        for (const bt::BeatSpan& span : workspace.beats.spans) {
            if (!(span.bpm > 1.f) || !(span.end > span.start + 1e-6)) {
                continue;
            }
            const double step = 60.0 / static_cast<double>(span.bpm);
            if (!(step > 1e-6)) {
                continue;
            }
            double t = span.offset;
            if (t < span.start - 1e-9) {
                const double n = std::ceil((span.start - span.offset) / step - 1e-9);
                t = span.offset + n * step;
            }
            for (int guard = 0; guard < 100000 && t < span.end - 1e-9; ++guard, t += step) {
                if (t >= span.start - 1e-6) {
                    quarters.push_back(t);
                }
            }
        }
    }
    if (quarters.empty()) {
        const double step = tab_pulse_seconds(workspace);
        const double offset = std::max(0.0, workspace.beats.offset);
        const double end = std::max(cover + step * 4.0, offset + step * 8.0);
        for (double t = offset; t < end - 1e-9; t += step) {
            quarters.push_back(t);
        }
    }
    if (quarters.empty()) {
        return false;
    }
    std::sort(quarters.begin(), quarters.end());
    quarters.erase(
        std::unique(quarters.begin(), quarters.end(), [](double a, double b) { return std::abs(a - b) <= 1e-6; }),
        quarters.end());

    if (cover > quarters.back() + 1e-6) {
        double step = tab_pulse_seconds(workspace);
        if (!workspace.beats.spans.empty()) {
            const bt::BeatSpan& last = workspace.beats.spans.back();
            if (last.bpm > 1.f) {
                step = 60.0 / static_cast<double>(last.bpm);
            }
        } else if (quarters.size() >= 2) {
            step = quarters.back() - quarters[quarters.size() - 2];
        }
        step = std::max(1e-3, step);
        double t = quarters.back() + step;
        const double end = cover + step * static_cast<double>(score->beats_per_bar);
        for (int guard = 0; guard < 100000 && t < end - 1e-9; ++guard, t += step) {
            quarters.push_back(t);
        }
    }

    const int per_bar = std::max(1, score->beats_per_bar);
    while (static_cast<int>(quarters.size()) % per_bar != 0) {
        double step = tab_pulse_seconds(workspace);
        if (quarters.size() >= 2) {
            step = std::max(1e-3, quarters.back() - quarters[quarters.size() - 2]);
        }
        quarters.push_back(quarters.back() + step);
    }

    std::vector<std::vector<int>> slot_notes(quarters.size());
    for (const Onset& onset : onsets) {
        int best = 0;
        double best_d = std::abs(quarters[0] - onset.start);
        for (int i = 1; i < static_cast<int>(quarters.size()); ++i) {
            const double d = std::abs(quarters[static_cast<std::size_t>(i)] - onset.start);
            if (d < best_d) {
                best_d = d;
                best = i;
            }
        }
        double half = tab_pulse_seconds(workspace) * 0.5;
        if (best + 1 < static_cast<int>(quarters.size())) {
            half = 0.5 * (quarters[static_cast<std::size_t>(best + 1)] - quarters[static_cast<std::size_t>(best)]);
        } else if (best > 0) {
            half = 0.5 * (quarters[static_cast<std::size_t>(best)] - quarters[static_cast<std::size_t>(best - 1)]);
        }
        if (best_d > std::max(1e-3, half) + 1e-9) {
            continue;
        }
        auto& dest = slot_notes[static_cast<std::size_t>(best)];
        dest.insert(dest.end(), onset.indices.begin(), onset.indices.end());
    }

    for (int i = 0; i < static_cast<int>(quarters.size()); i += per_bar) {
        TabMeasure measure;
        measure.start = quarters[static_cast<std::size_t>(i)];
        for (int b = 0; b < per_bar; ++b) {
            const int index = i + b;
            TabBeat beat;
            beat.duration = TabDuration::Quarter;
            beat.dotted = false;
            beat.time = quarters[static_cast<std::size_t>(index)];
            beat.note_indices = std::move(slot_notes[static_cast<std::size_t>(index)]);
            beat.rest = beat.note_indices.empty();
            measure.beats.push_back(std::move(beat));
        }
        score->measures.push_back(std::move(measure));
    }

    resolve_fingerings(workspace, stem);
    return !score->measures.empty();
}

bool sync_tab_score_from_midi(TabWorkspace& workspace, int stem) {
    auto* score = score_at(workspace, stem);
    const auto* notes = notes_at(workspace, stem);
    if (score == nullptr || notes == nullptr || tab_score_empty(*score)) {
        return false;
    }
    const double beat_sec = tab_pulse_seconds(workspace);
    const double onset_eps = onset_window_seconds(workspace.fingering);

    struct Onset {
        double start = 0;
        std::vector<int> indices;
    };
    std::vector<Onset> onsets;
    for (int index = 0; index < static_cast<int>(notes->size()); ++index) {
        if (!guitar_on_staff(workspace, stem, index)) {
            continue;
        }
        const bt::Note& note = (*notes)[static_cast<std::size_t>(index)];
        bool placed = false;
        for (Onset& group : onsets) {
            if (std::abs(group.start - note.start) <= onset_eps) {
                group.indices.push_back(index);
                placed = true;
                break;
            }
        }
        if (!placed) {
            onsets.push_back(Onset{note.start, {index}});
        }
    }
    std::sort(onsets.begin(), onsets.end(), [](const Onset& a, const Onset& b) { return a.start < b.start; });

    for (TabMeasure& measure : score->measures) {
        for (TabBeat& beat : measure.beats) {
            beat.note_indices.clear();
            beat.rest = true;
        }
    }

    for (Onset& onset : onsets) {
        int best_m = -1;
        int best_b = -1;
        double best_d = 1e9;
        for (int m = 0; m < static_cast<int>(score->measures.size()); ++m) {
            const TabMeasure& measure = score->measures[static_cast<std::size_t>(m)];
            for (int b = 0; b < static_cast<int>(measure.beats.size()); ++b) {
                const double d = std::abs(measure.beats[static_cast<std::size_t>(b)].time - onset.start);
                if (d < best_d) {
                    best_d = d;
                    best_m = m;
                    best_b = b;
                }
            }
        }
        double half = beat_sec * 0.5;
        if (best_m >= 0 && best_b >= 0) {
            const TabMeasure& measure = score->measures[static_cast<std::size_t>(best_m)];
            if (best_b + 1 < static_cast<int>(measure.beats.size())) {
                half = 0.5 * (measure.beats[static_cast<std::size_t>(best_b + 1)].time -
                              measure.beats[static_cast<std::size_t>(best_b)].time);
            }
        }
        if (best_m >= 0 && best_d <= std::max(1e-3, half) + 1e-9) {
            TabBeat& beat = score->measures[static_cast<std::size_t>(best_m)].beats[static_cast<std::size_t>(best_b)];
            beat.note_indices.insert(beat.note_indices.end(), onset.indices.begin(), onset.indices.end());
            beat.rest = false;
            continue;
        }

        int grown_m = -1;
        int grown_b = -1;
        if (ensure_score_slot_at(*score, onset.start, beat_sec, grown_m, grown_b)) {
            TabBeat& beat = score->measures[static_cast<std::size_t>(grown_m)].beats[static_cast<std::size_t>(grown_b)];
            if (std::abs(beat.time - onset.start) <= half + 1e-9 || beat.rest) {
                if (std::abs(beat.time - onset.start) <= std::max(1e-3, half) + 1e-9) {
                    beat.note_indices.insert(beat.note_indices.end(), onset.indices.begin(), onset.indices.end());
                    beat.rest = false;
                }
            }
        }
    }
    return true;
}

bool set_tab_duration(
    TabWorkspace& workspace, int stem, int measure, int beat, TabDuration duration, bool dotted, TabEditCaret* caret) {
    auto* score = score_at(workspace, stem);
    auto* notes = notes_at(workspace, stem);
    if (score == nullptr || notes == nullptr || !tab_slot_valid(*score, measure, beat)) {
        return false;
    }
    TabMeasure& bar = score->measures[static_cast<std::size_t>(measure)];
    TabBeat target = bar.beats[static_cast<std::size_t>(beat)];
    const double beat_sec = tab_pulse_seconds(workspace);
    const double capacity = static_cast<double>(std::max(1, score->beats_per_bar));

    int next_m = -1;
    int next_b = -1;
    const bool has_next = find_next_note_beat(*score, measure, beat, next_m, next_b);

    double span_q = 0;
    int replace_until = static_cast<int>(bar.beats.size());
    if (has_next && next_m == measure) {
        span_q = (bar.beats[static_cast<std::size_t>(next_b)].time - target.time) / beat_sec;
        replace_until = next_b;
    } else {
        double before = 0;
        for (int i = 0; i < beat; ++i) {
            before += tab_duration_quarters(
                bar.beats[static_cast<std::size_t>(i)].duration, bar.beats[static_cast<std::size_t>(i)].dotted);
        }
        span_q = capacity - before;
        replace_until = static_cast<int>(bar.beats.size());
        for (int i = beat + 1; i < static_cast<int>(bar.beats.size()); ++i) {
            if (!bar.beats[static_cast<std::size_t>(i)].rest) {
                replace_until = i;
                span_q = (bar.beats[static_cast<std::size_t>(i)].time - target.time) / beat_sec;
                break;
            }
        }
    }
    span_q = std::max(1.0 / 64.0, span_q);

    double want = tab_duration_quarters(duration, dotted);
    if (want > span_q + 1e-9) {
        duration = tab_duration_from_quarters(span_q);
        dotted = std::abs(tab_duration_quarters(duration, true) - span_q) + 1e-9 <
                 std::abs(tab_duration_quarters(duration, false) - span_q);
        want = tab_duration_quarters(duration, dotted);
    }

    std::vector<TabBeat> rebuilt;
    rebuilt.reserve(bar.beats.size() + 4);
    for (int i = 0; i < beat; ++i) {
        rebuilt.push_back(bar.beats[static_cast<std::size_t>(i)]);
    }
    target.duration = duration;
    target.dotted = dotted;
    rebuilt.push_back(target);
    const double remain = span_q - want;
    if (remain > 1e-6) {
        append_rest_fill(rebuilt, remain, target.time + want * beat_sec, beat_sec);
    }
    for (int i = replace_until; i < static_cast<int>(bar.beats.size()); ++i) {
        rebuilt.push_back(bar.beats[static_cast<std::size_t>(i)]);
    }
    bar.beats = std::move(rebuilt);
    retime_measure_beats(bar, beat_sec);

    if (!target.rest && !target.note_indices.empty()) {
        const double nominal_end = target.time + want * beat_sec;
        for (int index : target.note_indices) {
            if (index < 0 || index >= static_cast<int>(notes->size())) {
                continue;
            }
            bt::Note& note = (*notes)[static_cast<std::size_t>(index)];
            note.start = target.time;
            note.end = std::max(note.start + 1e-3, nominal_end);
        }
    }

    int caret_beat = beat;
    for (int i = 0; i < static_cast<int>(bar.beats.size()); ++i) {
        if (!bar.beats[static_cast<std::size_t>(i)].rest &&
            beat_has_all_notes(bar.beats[static_cast<std::size_t>(i)], target.note_indices)) {
            caret_beat = i;
            break;
        }
        if (std::abs(bar.beats[static_cast<std::size_t>(i)].time - target.time) <= 1e-6) {
            caret_beat = i;
        }
    }
    if (caret != nullptr) {
        caret->measure = measure;
        caret->beat = caret_beat;
        caret->duration = duration;
        caret->dotted = dotted;
    }
    return true;
}

bool snap_tab_group(
    TabWorkspace& workspace, int stem, const std::vector<int>& note_indices, int measure, int beat) {
    auto* score = score_at(workspace, stem);
    auto* notes = notes_at(workspace, stem);
    if (score == nullptr || notes == nullptr || note_indices.empty() || !tab_slot_valid(*score, measure, beat)) {
        return false;
    }
    TabBeat& dest = score->measures[static_cast<std::size_t>(measure)].beats[static_cast<std::size_t>(beat)];
    detach_notes_from_score(*score, note_indices);
    const double dest_time = dest.time;
    for (int index : note_indices) {
        if (index < 0 || index >= static_cast<int>(notes->size())) {
            continue;
        }
        bt::Note& note = (*notes)[static_cast<std::size_t>(index)];
        const double dur = std::max(1e-3, note.end - note.start);
        note.start = dest_time;
        note.end = dest_time + dur;
        note.on_tab = true;
    }
    for (int index : note_indices) {
        if (std::find(dest.note_indices.begin(), dest.note_indices.end(), index) == dest.note_indices.end()) {
            dest.note_indices.push_back(index);
        }
    }
    dest.rest = dest.note_indices.empty();
    return true;
}

bool tab_group_snapped(const TabWorkspace& workspace, int stem, const std::vector<int>& note_indices) {
    const auto* score = score_at(workspace, stem);
    if (score == nullptr || note_indices.empty()) {
        return false;
    }
    for (const TabMeasure& measure : score->measures) {
        for (const TabBeat& beat : measure.beats) {
            if (beat_has_all_notes(beat, note_indices)) {
                return true;
            }
        }
    }
    return false;
}

bool quantize_notes(TabWorkspace& workspace, int stem, const std::vector<char>& selected) {
    auto* notes = notes_at(workspace, stem);
    if (notes == nullptr || notes->empty()) {
        return false;
    }
    const bool all = selected.empty();
    if (!all && selected.size() != notes->size()) {
        return false;
    }
    if (!all) {
        bool any_selected = false;
        for (char bit : selected) {
            if (bit) {
                any_selected = true;
                break;
            }
        }
        if (!any_selected) {
            return false;
        }
    }
    double cover = std::max(0.0, workspace.length);
    for (const bt::Note& note : *notes) {
        cover = std::max(cover, std::max(note.start, note.end));
    }
    const std::vector<double> grid = build_grid(workspace, cover);
    bool touched = false;
    for (int index = 0; index < static_cast<int>(notes->size()); ++index) {
        if (!all && !selected[static_cast<std::size_t>(index)]) {
            continue;
        }
        bt::Note& note = (*notes)[static_cast<std::size_t>(index)];
        note.start = std::max(0.0, nearest_time(note.start, grid));
        note.end = std::max(0.0, nearest_time(note.end, grid));
        if (note.end <= note.start + 1e-4) {
            double next = note.start + 0.01;
            for (double mark : grid) {
                if (mark > note.start + 1e-6) {
                    next = mark;
                    break;
                }
            }
            note.end = std::max(note.start + 0.01, next);
        }
        touched = true;
    }
    if (stem == kGuitarStem && workspace.scores != nullptr) {
        build_tab_score(workspace, stem, false);
        sync_tab_score_from_midi(workspace, stem);
        resolve_fingerings(workspace, stem);
    }
    return touched;
}

}  // namespace bt::core
