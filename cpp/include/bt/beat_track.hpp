#pragma once

#include <cstdint>
#include <expected>
#include <filesystem>
#include <functional>
#include <string>
#include <vector>

namespace bt {

// Traditional grid is offset + n * (60 / bpm). Detected beats keep every time from the Ellis DP.
// This follows librosa 0.11 beat_track: mel spectral flux, autocorrelation tempo, Ellis DP.
struct BeatGrid {
    float bpm = 0.f;
    double offset = 0;
    std::vector<double> times;
};

std::expected<BeatGrid, std::string> track_beats(
    const float* mono, std::int64_t frames, int sample_rate, const std::function<bool()>& cancelled = {});

// After detection, fill the silence before the first beat and after the last.
// Each side starts at the nearest detected beat and steps at the region's average tempo.
void extend_beat_edges(BeatGrid& grid, double duration);

std::expected<BeatGrid, std::string> load_beats(const std::filesystem::path& path);
std::expected<void, std::string> save_beats(const std::filesystem::path& path, const BeatGrid& grid);

}  // namespace bt
