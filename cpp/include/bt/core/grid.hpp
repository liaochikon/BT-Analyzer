#pragma once

#include "bt/core/types.hpp"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <vector>

namespace bt::core {

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

}  // namespace bt::core
