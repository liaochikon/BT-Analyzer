#pragma once

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

}  // namespace bt
