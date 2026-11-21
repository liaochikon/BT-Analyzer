#pragma once

#include <cstdint>
#include <expected>
#include <filesystem>
#include <functional>
#include <string>
#include <vector>

namespace bt {

// One constant-tempo piece of the song. Beats inside it are offset + n * (60 / bpm).
struct BeatSpan {
    double start = 0;
    double end = 0;
    float bpm = 0.f;
    double offset = 0;
};

// track_beats follows librosa 0.11: mel spectral flux, autocorrelation tempo, Ellis DP.
// times from that pass still wobble. regularize_beat_grid replaces them with a lattice.
struct BeatGrid {
    float bpm = 0.f;
    double offset = 0;
    std::vector<double> times;
    std::vector<BeatSpan> spans;
};

std::expected<BeatGrid, std::string> track_beats(
    const float* mono, std::int64_t frames, int sample_rate, const std::function<bool()>& cancelled = {});

// After detection, fill the silence before the first beat and after the last.
// Each side starts at the nearest detected beat and steps at the region's average tempo.
void extend_beat_edges(BeatGrid& grid, double duration);

// Lock detected beats to a constant lattice. One BPM covers the song when the pulse
// stays in one family; a lasting tempo change becomes its own span.
void regularize_beat_grid(BeatGrid& grid, double duration);

std::expected<BeatGrid, std::string> load_beats(const std::filesystem::path& path);
std::expected<void, std::string> save_beats(const std::filesystem::path& path, const BeatGrid& grid);

}  // namespace bt
