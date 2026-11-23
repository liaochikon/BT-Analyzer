#pragma once

#include "bt/basic_pitch.hpp"
#include "bt/beat_track.hpp"
#include "bt/contract.hpp"

#include <array>
#include <string>
#include <string_view>
#include <vector>

namespace bt::core {

// Discrete notation duration for the TAB rhythm tree (shared metric with the MIDI beat grid).
enum class TabDuration {
    Whole = 0,
    Half,
    Quarter,
    Eighth,
    Sixteenth,
    ThirtySecond,
    SixtyFourth,
};

inline constexpr int kTabDurationCount = 7;

struct TabBeat {
    TabDuration duration = TabDuration::Quarter;
    bool dotted = false;
    bool rest = false;
    std::vector<int> note_indices;
    double time = 0;
};

struct TabMeasure {
    double start = 0;
    std::vector<TabBeat> beats;
};

struct TabScore {
    int beats_per_bar = 4;
    std::vector<TabMeasure> measures;
};

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

inline constexpr int kGuitarStringCount = 6;
inline constexpr int kGuitarStem = 4;
// string 0 = high e … 5 = low E (standard tuning).
inline constexpr int kGuitarOpenPitch[kGuitarStringCount] = {64, 59, 55, 50, 45, 40};
// The fretboard is always 24 frets.
inline constexpr int kFretCount = 24;

struct TabSpot {
    int string = 0;
    int fret = 0;
};

struct TabGate {
    bool ok = false;
    bool quantized = true;
    bool in_range = true;
    bool chords_ok = true;
    int unquantized = 0;
    int out_of_range = 0;
    int chord_clashes = 0;
    std::vector<std::string> reasons;
};

struct TabExportResult {
    bool ok = false;
    std::string error;
};

// One playable assignment for an onset group (parallel to note indices).
struct TabCombo {
    std::vector<TabSpot> spots;
    int score = 0;
};

struct TabOnsetGroup {
    double start = 0;
    std::vector<int> note_indices;
    std::vector<TabCombo> combos;
};

enum class TabGroupConflict {
    Ok = 0,
    Warn = 1,   // solvable, but current fingering is incomplete / clashes / over span
    Error = 2,  // no legal combo under current hand-span rules
};

struct FingeringParams {
    int hand_span = 4;
    int onset_ms = 2;
    int prefer_fret = 0;
    float hand_center = 5.f;
};

// Headless inputs for fingering, quantize, and score. `notes` is required.
// `scores` is required for score writes and optional for fingering-only calls.
struct TabWorkspace {
    std::array<std::vector<bt::Note>, bt::kStemCount>* notes = nullptr;
    std::array<TabScore, bt::kStemCount>* scores = nullptr;
    bt::BeatGrid beats{};
    BeatDivision grid_division = BeatDivision::Beat;
    FingeringParams fingering{};
    double length = 0;
};

}  // namespace bt::core
