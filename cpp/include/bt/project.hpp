#pragma once

#include "bt/contract.hpp"

#include <array>
#include <expected>
#include <filesystem>
#include <string>
#include <string_view>
#include <vector>

namespace bt {

// One song folder. The display name lives in song.txt.
// A new folder is `{display name}_{sha256}` when that name survives the system code page.
// When it does not, the folder name is the sha256 alone.
struct ProjectEntry {
    std::string name;
    std::string hash;
    double seconds = 0;
    std::filesystem::path directory;
    std::filesystem::path source;
    // model_key is the saves subdirectory that produced the stems.
    // device is CPU or CUDA when the file recorded it. Empty on older songs.
    std::string model_key;
    std::string device;
};

std::string display_name_from_path(const std::filesystem::path& path);

// Newest folder first. An empty model key lists cache_root itself.
// Folders without a sha256 suffix are skipped. A sha256 folder is hidden when a named folder for the same hash exists.
std::vector<ProjectEntry> list_projects(const std::filesystem::path& cache_root, std::string_view model_key);

// Moves project folders into saves_directory. A name that already exists there is left in place.
void migrate_project_folders(const std::filesystem::path& from_directory, const std::filesystem::path& saves_directory);

// Reads one project folder. The model is the parent directory when song.txt omits it.
std::expected<ProjectEntry, std::string> read_project(const std::filesystem::path& directory);

// Picks the existing folder for this hash, or the path a new folder will use.
// Does not create the directory. An existing song.txt name is kept.
std::expected<ProjectEntry, std::string> locate_project(
    const std::filesystem::path& cache_root, std::string_view model_key, std::string_view hash,
    std::string_view suggested_name, const std::filesystem::path& source);

// Writes song.txt. The folder path stays the one locate_project chose.
std::expected<ProjectEntry, std::string> commit_project(ProjectEntry entry);

// Changes the display name in song.txt. The folder path stays the same.
std::expected<ProjectEntry, std::string> rename_project(const std::filesystem::path& directory, std::string_view name);

std::expected<void, std::string> write_project_seconds(const std::filesystem::path& directory, double seconds);

std::expected<void, std::string> delete_project(const std::filesystem::path& directory);

// One lane of a stem's verify submix: the dry original, or the MIDI square.
struct ProjectVerifyLane {
    float db = 0.f;
    float pan = 0.f;
    bool mute = false;
    bool solo = false;
    bool operator==(const ProjectVerifyLane&) const = default;
};

// Original stays open. MIDI starts muted, matching a fresh stem strip.
struct ProjectVerifyStem {
    ProjectVerifyLane original{};
    ProjectVerifyLane midi{.mute = true};
    bool operator==(const ProjectVerifyStem&) const = default;
};

// How this song is heard. One file in the song folder, so two projects cannot share it.
struct ProjectPlayback {
    ProjectPlayback() { gain.fill(1.f); }

    std::array<float, kStemCount> gain{};
    std::array<float, kStemCount> pan{};
    std::array<bool, kStemCount> mute{};
    std::array<bool, kStemCount> solo{};
    std::array<ProjectVerifyStem, kStemCount> verify{};
    float speed = 1.f;
    float master_db = 0.f;
    bool loop = false;
    double loop_a = -1;
    double loop_b = -1;
    bool loop_snap = false;
    bool count_in = false;
    bool metro = true;
    float click_db = 0.f;
    // Metronome click division: bar, beat, half, triplet, or quarter.
    std::string beat_division = "beat";
    // Grid lines and note snapping. Own switch and own division.
    bool grid = true;
    std::string grid_division = "beat";
    // Fretboard and piano range for this song. Fret count itself is fixed.
    int guitar_pitch_min = 40;
    int guitar_pitch_max = 86;
    int bass_pitch_min = 28;
    int bass_pitch_max = 65;
    int key_min = 21;
    int key_max = 108;
    int fret_prefer = 0;
    bool fret_suggest = false;
    bool fret_allow_open = false;
    bool fret_show_alt = true;
    bool fret_harmonic = false;
    int tab_hand_span = 4;
    int tab_onset_ms = 2;
    // "grid" (continuous time) or "score" (rhythm tree columns). Applies to MIDI + TAB together.
    std::string rhythm_layout = "grid";
};

inline ProjectPlayback default_project_playback() { return {}; }

// False when playback.txt is missing or is not bt-playback 1. `playback` stays unchanged.
bool read_project_playback(const std::filesystem::path& directory, ProjectPlayback& playback);

// Writes playback.txt. Does not create the folder.
std::expected<void, std::string> write_project_playback(
    const std::filesystem::path& directory, const ProjectPlayback& playback);

// Copies playback.txt when the source has one. A missing file is left missing.
void copy_project_playback(
    const std::filesystem::path& from_directory, const std::filesystem::path& to_directory);

}  // namespace bt
