#include "bt/project.hpp"

#include <algorithm>
#include <charconv>
#include <cmath>
#include <exception>
#include <fstream>
#include <sstream>
#include <system_error>

namespace bt {
namespace {

constexpr std::string_view kHeader = "bt-song 1";

std::filesystem::path path_from_utf8(std::string_view text) {
    std::u8string utf8(reinterpret_cast<const char8_t*>(text.data()), text.size());
    return std::filesystem::path(utf8);
}

std::string path_to_utf8(const std::filesystem::path& path) {
    const std::u8string utf8 = path.u8string();
    return std::string(reinterpret_cast<const char*>(utf8.data()), utf8.size());
}

std::string strip_cr(std::string text) {
    if (!text.empty() && text.back() == '\r') {
        text.pop_back();
    }
    return text;
}

bool is_hex_hash(std::string_view text) {
    if (text.size() != 64) {
        return false;
    }
    for (unsigned char character : text) {
        const bool digit = character >= '0' && character <= '9';
        const bool lower = character >= 'a' && character <= 'f';
        if (!digit && !lower) {
            return false;
        }
    }
    return true;
}

std::string hash_of_folder(std::string_view filename) {
    if (is_hex_hash(filename)) {
        return std::string(filename);
    }
    if (filename.size() > 65 && filename[filename.size() - 65] == '_') {
        const auto tail = filename.substr(filename.size() - 64);
        if (is_hex_hash(tail)) {
            return std::string(tail);
        }
    }
    return {};
}

std::filesystem::path project_root(const std::filesystem::path& cache_root, std::string_view model_key) {
    if (model_key.empty()) {
        return cache_root;
    }
    return cache_root / path_from_utf8(model_key);
}

std::size_t utf8_width(unsigned char lead) {
    if ((lead & 0x80u) == 0) {
        return 1;
    }
    if ((lead & 0xe0u) == 0xc0u) {
        return 2;
    }
    if ((lead & 0xf0u) == 0xe0u) {
        return 3;
    }
    if ((lead & 0xf8u) == 0xf0u) {
        return 4;
    }
    return 1;
}

bool forbidden_path_byte(unsigned char character) {
    if (character < 0x20u || character == 0x7fu) {
        return true;
    }
    switch (character) {
    case '<':
    case '>':
    case ':':
    case '"':
    case '/':
    case '\\':
    case '|':
    case '?':
    case '*':
        return true;
    default:
        return false;
    }
}

std::string sanitize_prefix(std::string_view name) {
    std::string out;
    for (std::size_t index = 0; index < name.size();) {
        const auto lead = static_cast<unsigned char>(name[index]);
        const std::size_t width = std::min(utf8_width(lead), name.size() - index);
        if (out.size() + width > 80) {
            break;
        }
        if (width == 1 && forbidden_path_byte(lead)) {
            out.push_back('_');
        } else {
            out.append(name.substr(index, width));
        }
        index += width;
    }
    while (!out.empty() && (out.front() == ' ' || out.front() == '.')) {
        out.erase(out.begin());
    }
    while (!out.empty() && (out.back() == ' ' || out.back() == '.')) {
        out.pop_back();
    }
    return out;
}

bool filename_survives_code_page(const std::filesystem::path& directory) {
    try {
        (void)directory.filename().string();
        return true;
    } catch (const std::exception&) {
        return false;
    }
}

std::filesystem::path directory_for_new(
    const std::filesystem::path& root, std::string_view name, std::string_view hash) {
    const std::string prefix = sanitize_prefix(name);
    if (!prefix.empty()) {
        const auto named = root / path_from_utf8(prefix + "_" + std::string(hash));
        if (filename_survives_code_page(named)) {
            return named;
        }
    }
    return root / path_from_utf8(std::string(hash));
}

std::string one_line(std::string_view text) {
    std::string out;
    out.reserve(text.size());
    for (char character : text) {
        if (character != '\r' && character != '\n') {
            out.push_back(character);
        }
    }
    while (!out.empty() && out.front() == ' ') {
        out.erase(out.begin());
    }
    while (!out.empty() && out.back() == ' ') {
        out.pop_back();
    }
    return out;
}

struct SongFile {
    std::string name;
    double seconds = 0;
    std::filesystem::path source;
    bool present = false;
};

SongFile read_song_file(const std::filesystem::path& path) {
    std::ifstream input(path, std::ios::binary);
    SongFile file;
    if (!input) {
        return file;
    }
    std::string header;
    if (!std::getline(input, header) || strip_cr(header) != kHeader) {
        return file;
    }
    std::string name;
    std::string seconds;
    std::string source;
    std::getline(input, name);
    std::getline(input, seconds);
    std::getline(input, source);
    file.present = true;
    file.name = one_line(strip_cr(name));
    file.source = path_from_utf8(strip_cr(source));
    const std::string seconds_text = strip_cr(seconds);
    double value = 0;
    const auto parsed = std::from_chars(seconds_text.data(), seconds_text.data() + seconds_text.size(), value);
    if (parsed.ec == std::errc{} && std::isfinite(value) && value > 0) {
        file.seconds = value;
    }
    return file;
}

std::expected<void, std::string> write_song_file(const std::filesystem::path& directory, const ProjectEntry& entry) {
    std::error_code error;
    std::filesystem::create_directories(directory, error);
    if (error) {
        return std::unexpected("cannot create " + path_to_utf8(directory));
    }
    std::ofstream output(directory / "song.txt", std::ios::binary | std::ios::trunc);
    if (!output) {
        return std::unexpected("cannot write song.txt");
    }
    output << kHeader << '\n' << one_line(entry.name) << '\n';
    std::ostringstream seconds;
    seconds.setf(std::ios::fmtflags(0), std::ios::floatfield);
    seconds.precision(12);
    seconds << entry.seconds;
    output << seconds.str() << '\n' << path_to_utf8(entry.source) << '\n';
    if (!output) {
        return std::unexpected("cannot write song.txt");
    }
    return {};
}

ProjectEntry entry_from_directory(const std::filesystem::path& directory) {
    const std::string filename = path_to_utf8(directory.filename());
    ProjectEntry entry;
    entry.hash = hash_of_folder(filename);
    entry.directory = directory;
    const SongFile file = read_song_file(directory / "song.txt");
    entry.seconds = file.seconds;
    entry.source = file.source;
    if (!file.name.empty()) {
        entry.name = file.name;
    } else if (filename.size() > entry.hash.size()) {
        entry.name = filename.substr(0, filename.size() - entry.hash.size() - 1);
    } else {
        entry.name = entry.hash.substr(0, 8);
    }
    return entry;
}

bool is_bare_hash_folder(const std::filesystem::path& directory) {
    try {
        return is_hex_hash(path_to_utf8(directory.filename()));
    } catch (const std::exception&) {
        return false;
    }
}

}  // namespace

std::string display_name_from_path(const std::filesystem::path& path) {
    std::string name = path_to_utf8(path.filename());
    const auto dot = name.rfind('.');
    if (dot != std::string::npos && dot > 0) {
        name.resize(dot);
    }
    name = one_line(name);
    if (name.empty()) {
        return "song";
    }
    return name;
}

std::vector<ProjectEntry> list_projects(const std::filesystem::path& cache_root, std::string_view model_key) {
    const auto root = project_root(cache_root, model_key);
    std::error_code error;
    if (!std::filesystem::is_directory(root, error)) {
        return {};
    }
    struct Ranked {
        std::filesystem::file_time_type when;
        ProjectEntry entry;
    };
    std::vector<Ranked> ranked;
    for (std::filesystem::directory_iterator cursor(root, error), end; !error && cursor != end; cursor.increment(error)) {
        std::filesystem::path child_path;
        std::filesystem::file_time_type when{};
        try {
            if (!cursor->is_directory()) {
                continue;
            }
            child_path = cursor->path();
            std::error_code time_error;
            when = cursor->last_write_time(time_error);
        } catch (const std::exception&) {
            continue;
        }
        std::string filename;
        try {
            filename = path_to_utf8(child_path.filename());
        } catch (const std::exception&) {
            continue;
        }
        if (filename.size() >= 4 && filename.ends_with(".tmp")) {
            continue;
        }
        if (hash_of_folder(filename).empty()) {
            continue;
        }
        Ranked row;
        try {
            row.entry = entry_from_directory(child_path);
        } catch (const std::exception&) {
            continue;
        }
        row.when = when;
        ranked.push_back(std::move(row));
    }
    std::sort(ranked.begin(), ranked.end(), [](const Ranked& left, const Ranked& right) { return left.when > right.when; });
    std::vector<ProjectEntry> projects;
    projects.reserve(ranked.size());
    for (Ranked& row : ranked) {
        if (is_bare_hash_folder(row.entry.directory)) {
            bool named = false;
            for (const Ranked& other : ranked) {
                if (other.entry.hash == row.entry.hash && !is_bare_hash_folder(other.entry.directory)) {
                    named = true;
                    break;
                }
            }
            if (named) {
                continue;
            }
        }
        projects.push_back(std::move(row.entry));
    }
    return projects;
}

std::expected<ProjectEntry, std::string> locate_project(
    const std::filesystem::path& cache_root, std::string_view model_key, std::string_view hash,
    std::string_view suggested_name, const std::filesystem::path& source) {
    if (!is_hex_hash(hash)) {
        return std::unexpected("song hash is invalid");
    }
    const auto root = project_root(cache_root, model_key);
    ProjectEntry found;
    bool have = false;
    std::error_code error;
    if (std::filesystem::is_directory(root, error)) {
        for (std::filesystem::directory_iterator cursor(root, error), end; !error && cursor != end; cursor.increment(error)) {
            std::filesystem::path child_path;
            try {
                if (!cursor->is_directory()) {
                    continue;
                }
                child_path = cursor->path();
            } catch (const std::exception&) {
                continue;
            }
            std::string filename;
            try {
                filename = path_to_utf8(child_path.filename());
            } catch (const std::exception&) {
                continue;
            }
            if (filename.size() >= 4 && filename.ends_with(".tmp")) {
                continue;
            }
            if (hash_of_folder(filename) != hash) {
                continue;
            }
            ProjectEntry candidate;
            try {
                candidate = entry_from_directory(child_path);
            } catch (const std::exception&) {
                continue;
            }
            const bool bare = is_bare_hash_folder(child_path);
            const bool found_bare = have && is_bare_hash_folder(found.directory);
            if (!have || (!bare && found_bare)) {
                found = std::move(candidate);
                have = true;
            }
        }
    }
    if (have) {
        const SongFile file = read_song_file(found.directory / "song.txt");
        if (!file.present || file.name.empty()) {
            const std::string suggested = one_line(suggested_name);
            if (!suggested.empty()) {
                found.name = suggested;
            }
        }
        if (found.source.empty()) {
            found.source = source;
        }
        return found;
    }
    const std::string name = one_line(suggested_name).empty() ? std::string("song") : one_line(suggested_name);
    ProjectEntry created;
    created.name = name;
    created.hash = std::string(hash);
    created.directory = directory_for_new(root, name, hash);
    created.source = source;
    return created;
}

void migrate_project_folders(const std::filesystem::path& from_directory, const std::filesystem::path& saves_directory) {
    std::error_code error;
    std::filesystem::create_directories(saves_directory, error);
    if (!std::filesystem::is_directory(from_directory, error)) {
        return;
    }
    std::vector<std::filesystem::path> children;
    for (std::filesystem::directory_iterator cursor(from_directory, error), end; !error && cursor != end; cursor.increment(error)) {
        try {
            if (cursor->is_directory()) {
                children.push_back(cursor->path());
            }
        } catch (const std::exception&) {
        }
    }
    for (const std::filesystem::path& child : children) {
        std::string filename;
        try {
            filename = path_to_utf8(child.filename());
        } catch (const std::exception&) {
            continue;
        }
        if (filename.size() >= 4 && filename.ends_with(".tmp")) {
            continue;
        }
        if (hash_of_folder(filename).empty()) {
            continue;
        }
        const auto destination = saves_directory / child.filename();
        std::error_code move_error;
        if (std::filesystem::exists(destination, move_error)) {
            continue;
        }
        std::filesystem::rename(child, destination, move_error);
    }
}

std::expected<ProjectEntry, std::string> commit_project(ProjectEntry entry) {
    if (!is_hex_hash(entry.hash)) {
        return std::unexpected("song hash is invalid");
    }
    std::error_code error;
    if (!std::filesystem::is_directory(entry.directory, error)) {
        return std::unexpected("song folder is missing");
    }
    const SongFile existing = read_song_file(entry.directory / "song.txt");
    if (!existing.name.empty()) {
        entry.name = existing.name;
    }
    if (existing.seconds > 0) {
        entry.seconds = existing.seconds;
    }
    if (!existing.source.empty()) {
        entry.source = existing.source;
    }
    if (entry.name.empty()) {
        entry.name = "song";
    }
    if (auto written = write_song_file(entry.directory, entry); !written) {
        return std::unexpected(written.error());
    }
    return entry;
}

std::expected<ProjectEntry, std::string> rename_project(const std::filesystem::path& directory, std::string_view name) {
    std::error_code error;
    if (!std::filesystem::is_directory(directory, error)) {
        return std::unexpected("song folder is missing");
    }
    ProjectEntry entry = entry_from_directory(directory);
    if (!is_hex_hash(entry.hash)) {
        return std::unexpected("song folder is not a project");
    }
    const std::string next = one_line(name);
    if (next.empty()) {
        return std::unexpected("song name is empty");
    }
    entry.name = next;
    if (auto written = write_song_file(entry.directory, entry); !written) {
        return std::unexpected(written.error());
    }
    return entry;
}

std::expected<void, std::string> write_project_seconds(const std::filesystem::path& directory, double seconds) {
    if (!(seconds > 0) || !std::isfinite(seconds)) {
        return std::unexpected("duration is invalid");
    }
    std::error_code error;
    if (!std::filesystem::is_directory(directory, error)) {
        return std::unexpected("song folder is missing");
    }
    ProjectEntry entry = entry_from_directory(directory);
    if (!is_hex_hash(entry.hash)) {
        return std::unexpected("song folder is not a project");
    }
    entry.seconds = seconds;
    return write_song_file(directory, entry);
}

std::expected<void, std::string> delete_project(const std::filesystem::path& directory) {
    std::error_code error;
    std::filesystem::remove_all(directory, error);
    if (error) {
        return std::unexpected("cannot delete song folder");
    }
    return {};
}

}  // namespace bt
