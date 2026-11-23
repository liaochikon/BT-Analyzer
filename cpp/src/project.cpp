#include "bt/project.hpp"

#include "bt/contract.hpp"

#include <algorithm>
#include <charconv>
#include <cmath>
#include <exception>
#include <fstream>
#include <sstream>
#include <system_error>
#include <type_traits>

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
    std::string model;
    std::string device;
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
    std::string extra;
    while (std::getline(input, extra)) {
        extra = strip_cr(extra);
        if (extra.rfind("model=", 0) == 0) {
            file.model = one_line(extra.substr(6));
        } else if (extra.rfind("device=", 0) == 0) {
            file.device = one_line(extra.substr(7));
        }
    }
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
    if (!entry.model_key.empty() || !entry.device.empty()) {
        output << "model=" << one_line(entry.model_key) << '\n';
        output << "device=" << one_line(entry.device) << '\n';
    }
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
    entry.model_key = file.model;
    entry.device = file.device;
    if (entry.model_key.empty()) {
        try {
            const std::string parent = path_to_utf8(directory.parent_path().filename());
            if (is_separation_key(parent)) {
                entry.model_key = parent;
            }
        } catch (const std::exception&) {
        }
    }
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

constexpr std::string_view kPlaybackHeader = "bt-playback 1";
constexpr const char* kPlaybackName = "playback.txt";

std::string number_text(double value, int precision) {
    std::ostringstream text;
    text.setf(std::ios::fmtflags(0), std::ios::floatfield);
    text.precision(precision);
    text << value;
    return text.str();
}

bool parse_number(std::string_view token, float& value) {
    float parsed = 0.f;
    const auto result = std::from_chars(token.data(), token.data() + token.size(), parsed);
    if (result.ec != std::errc{} || result.ptr != token.data() + token.size() || !std::isfinite(parsed)) {
        return false;
    }
    value = parsed;
    return true;
}

bool parse_number(std::string_view token, double& value) {
    double parsed = 0;
    const auto result = std::from_chars(token.data(), token.data() + token.size(), parsed);
    if (result.ec != std::errc{} || result.ptr != token.data() + token.size() || !std::isfinite(parsed)) {
        return false;
    }
    value = parsed;
    return true;
}

bool parse_float_list(std::string_view text, float* out, int count) {
    for (int index = 0; index < count; ++index) {
        const auto comma = text.find(',');
        const auto token = comma == std::string_view::npos ? text : text.substr(0, comma);
        if (!parse_number(token, out[index])) {
            return false;
        }
        if (comma == std::string_view::npos) {
            return index == count - 1;
        }
        text.remove_prefix(comma + 1);
    }
    return text.empty();
}

bool parse_flag_list(std::string_view text, bool* out, int count) {
    for (int index = 0; index < count; ++index) {
        const auto comma = text.find(',');
        const auto token = comma == std::string_view::npos ? text : text.substr(0, comma);
        if (token == "1") {
            out[index] = true;
        } else if (token == "0") {
            out[index] = false;
        } else {
            return false;
        }
        if (comma == std::string_view::npos) {
            return index == count - 1;
        }
        text.remove_prefix(comma + 1);
    }
    return text.empty();
}

bool known_division(std::string_view token) {
    return token == "bar" || token == "beat" || token == "half" || token == "triplet" || token == "quarter";
}

void apply_playback_line(ProjectPlayback& playback, std::string_view key, std::string_view raw) {
    float numbers[kStemCount];
    bool flags[kStemCount];
    if (key == "gain" && parse_float_list(raw, numbers, kStemCount)) {
        for (int index = 0; index < kStemCount; ++index) {
            playback.gain[static_cast<std::size_t>(index)] = std::clamp(numbers[index], 0.f, 4.f);
        }
    } else if (key == "pan" && parse_float_list(raw, numbers, kStemCount)) {
        for (int index = 0; index < kStemCount; ++index) {
            playback.pan[static_cast<std::size_t>(index)] = std::clamp(numbers[index], -1.f, 1.f);
        }
    } else if (key == "mute" && parse_flag_list(raw, flags, kStemCount)) {
        for (int index = 0; index < kStemCount; ++index) {
            playback.mute[static_cast<std::size_t>(index)] = flags[index];
        }
    } else if (key == "solo" && parse_flag_list(raw, flags, kStemCount)) {
        for (int index = 0; index < kStemCount; ++index) {
            playback.solo[static_cast<std::size_t>(index)] = flags[index];
        }
    } else if (key == "speed") {
        float value = 0.f;
        if (parse_number(raw, value)) {
            playback.speed = std::clamp(value, 0.1f, 2.f);
        }
    } else if (key == "master_db" || key == "click_db") {
        float value = 0.f;
        if (parse_number(raw, value)) {
            value = std::clamp(value, -60.f, 6.f);
            if (key == "master_db") {
                playback.master_db = value;
            } else {
                playback.click_db = value;
            }
        }
    } else if (key == "loop" || key == "loop_snap" || key == "count_in" || key == "metro" || key == "grid") {
        if (raw == "0" || raw == "1") {
            const bool enabled = raw == "1";
            if (key == "loop") {
                playback.loop = enabled;
            } else if (key == "loop_snap") {
                playback.loop_snap = enabled;
            } else if (key == "count_in") {
                playback.count_in = enabled;
            } else if (key == "grid") {
                playback.grid = enabled;
            } else {
                playback.metro = enabled;
            }
        }
    } else if (key == "loop_a" || key == "loop_b") {
        double value = 0;
        if (parse_number(raw, value)) {
            if (!(value >= 0.0)) {
                value = -1;
            }
            if (key == "loop_a") {
                playback.loop_a = value;
            } else {
                playback.loop_b = value;
            }
        }
    } else if (key == "beat_division" && known_division(raw)) {
        playback.beat_division = std::string(raw);
    } else if (key == "grid_division" && known_division(raw)) {
        playback.grid_division = std::string(raw);
    } else if (key == "rhythm_layout" && (raw == "grid" || raw == "score")) {
        playback.rhythm_layout = std::string(raw);
    } else if (key == "guitar_pitch_min" || key == "guitar_pitch_max" || key == "bass_pitch_min" ||
               key == "bass_pitch_max" || key == "key_min" || key == "key_max" || key == "fret_prefer" ||
               key == "tab_hand_span" || key == "tab_onset_ms") {
        float value = 0.f;
        if (parse_number(raw, value)) {
            int rounded = static_cast<int>(std::lround(value));
            int limit = 127;
            if (key == "fret_prefer") {
                limit = 24;
            } else if (key == "tab_hand_span") {
                limit = 12;
            } else if (key == "tab_onset_ms") {
                limit = 200;
            }
            rounded = std::clamp(rounded, 0, limit);
            if (key == "guitar_pitch_min") {
                playback.guitar_pitch_min = rounded;
            } else if (key == "guitar_pitch_max") {
                playback.guitar_pitch_max = rounded;
            } else if (key == "bass_pitch_min") {
                playback.bass_pitch_min = rounded;
            } else if (key == "bass_pitch_max") {
                playback.bass_pitch_max = rounded;
            } else if (key == "key_min") {
                playback.key_min = rounded;
            } else if (key == "key_max") {
                playback.key_max = rounded;
            } else if (key == "tab_hand_span") {
                playback.tab_hand_span = std::max(1, rounded);
            } else if (key == "tab_onset_ms") {
                playback.tab_onset_ms = rounded;
            } else {
                playback.fret_prefer = rounded;
            }
        }
    } else if (key == "fret_suggest" || key == "fret_allow_open" || key == "fret_show_alt" || key == "fret_harmonic") {
        if (raw == "0" || raw == "1") {
            const bool enabled = raw == "1";
            if (key == "fret_suggest") {
                playback.fret_suggest = enabled;
            } else if (key == "fret_allow_open") {
                playback.fret_allow_open = enabled;
            } else if (key == "fret_show_alt") {
                playback.fret_show_alt = enabled;
            } else {
                playback.fret_harmonic = enabled;
            }
        }
    } else if (key.starts_with("verify_") && key.size() > 7) {
        const bool midi = key.find("_midi_") != std::string_view::npos;
        const auto field = key.substr(key.rfind('_') + 1);
        if (field == "db" && parse_float_list(raw, numbers, kStemCount)) {
            for (int index = 0; index < kStemCount; ++index) {
                auto& lane = midi ? playback.verify[static_cast<std::size_t>(index)].midi
                                  : playback.verify[static_cast<std::size_t>(index)].original;
                lane.db = std::clamp(numbers[index], -60.f, 6.f);
            }
        } else if (field == "pan" && parse_float_list(raw, numbers, kStemCount)) {
            for (int index = 0; index < kStemCount; ++index) {
                auto& lane = midi ? playback.verify[static_cast<std::size_t>(index)].midi
                                  : playback.verify[static_cast<std::size_t>(index)].original;
                lane.pan = std::clamp(numbers[index], -1.f, 1.f);
            }
        } else if ((field == "mute" || field == "solo") && parse_flag_list(raw, flags, kStemCount)) {
            for (int index = 0; index < kStemCount; ++index) {
                auto& lane = midi ? playback.verify[static_cast<std::size_t>(index)].midi
                                  : playback.verify[static_cast<std::size_t>(index)].original;
                if (field == "mute") {
                    lane.mute = flags[index];
                } else {
                    lane.solo = flags[index];
                }
            }
        }
    }
}

}  // namespace

std::expected<ProjectEntry, std::string> read_project(const std::filesystem::path& directory) {
    std::error_code error;
    if (!std::filesystem::is_directory(directory, error)) {
        return std::unexpected("song folder is missing");
    }
    try {
        ProjectEntry entry = entry_from_directory(directory);
        if (!is_hex_hash(entry.hash)) {
            return std::unexpected("song folder is missing");
        }
        return entry;
    } catch (const std::exception&) {
        return std::unexpected("song folder is missing");
    }
}

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
    // Entries are moved out below, so read the named hashes before any move.
    std::vector<std::string> named_hashes;
    for (const Ranked& row : ranked) {
        if (!is_bare_hash_folder(row.entry.directory)) {
            named_hashes.push_back(row.entry.hash);
        }
    }
    std::vector<ProjectEntry> projects;
    projects.reserve(ranked.size());
    for (Ranked& row : ranked) {
        if (is_bare_hash_folder(row.entry.directory) &&
            std::find(named_hashes.begin(), named_hashes.end(), row.entry.hash) != named_hashes.end()) {
            continue;
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
    if (entry.model_key.empty()) {
        entry.model_key = existing.model;
    }
    if (entry.device.empty()) {
        entry.device = existing.device;
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

bool read_project_playback(const std::filesystem::path& directory, ProjectPlayback& playback) {
    std::ifstream input(directory / kPlaybackName, std::ios::binary);
    if (!input) {
        return false;
    }
    std::string header;
    if (!std::getline(input, header) || strip_cr(header) != kPlaybackHeader) {
        return false;
    }
    ProjectPlayback next = default_project_playback();
    bool has_grid_division = false;
    std::string line;
    while (std::getline(input, line)) {
        line = strip_cr(line);
        const auto eq = line.find('=');
        if (eq == std::string::npos) {
            continue;
        }
        const std::string_view key = std::string_view(line).substr(0, eq);
        has_grid_division = has_grid_division || key == "grid_division";
        apply_playback_line(next, key, std::string_view(line).substr(eq + 1));
    }
    // Files saved before the grid had its own division follow the metronome division.
    if (!has_grid_division) {
        next.grid_division = next.beat_division;
    }
    playback = std::move(next);
    return true;
}

std::expected<void, std::string> write_project_playback(
    const std::filesystem::path& directory, const ProjectPlayback& playback) {
    std::error_code error;
    if (!std::filesystem::is_directory(directory, error) || error) {
        return std::unexpected("song folder is missing");
    }
    const auto path = directory / kPlaybackName;
    const auto temporary = directory / (path.filename().wstring() + L".tmp");
    {
        std::ofstream output(temporary, std::ios::binary | std::ios::trunc);
        if (!output) {
            return std::unexpected("cannot write playback.txt");
        }
        output << kPlaybackHeader << '\n';
        auto write_floats = [&](const char* key, const std::array<float, kStemCount>& values, int precision) {
            output << key << '=';
            for (int index = 0; index < kStemCount; ++index) {
                if (index > 0) {
                    output << ',';
                }
                output << number_text(values[static_cast<std::size_t>(index)], precision);
            }
            output << '\n';
        };
        auto write_flags = [&](const char* key, const std::array<bool, kStemCount>& values) {
            output << key << '=';
            for (int index = 0; index < kStemCount; ++index) {
                if (index > 0) {
                    output << ',';
                }
                output << (values[static_cast<std::size_t>(index)] ? '1' : '0');
            }
            output << '\n';
        };
        write_floats("gain", playback.gain, 8);
        write_floats("pan", playback.pan, 8);
        write_flags("mute", playback.mute);
        write_flags("solo", playback.solo);
        output << "speed=" << number_text(playback.speed, 8) << '\n';
        output << "master_db=" << number_text(playback.master_db, 8) << '\n';
        output << "loop=" << (playback.loop ? '1' : '0') << '\n';
        output << "loop_a=" << number_text(playback.loop_a, 17) << '\n';
        output << "loop_b=" << number_text(playback.loop_b, 17) << '\n';
        output << "loop_snap=" << (playback.loop_snap ? '1' : '0') << '\n';
        output << "count_in=" << (playback.count_in ? '1' : '0') << '\n';
        output << "metro=" << (playback.metro ? '1' : '0') << '\n';
        output << "click_db=" << number_text(playback.click_db, 8) << '\n';
        output << "beat_division=" << (known_division(playback.beat_division) ? playback.beat_division : "beat") << '\n';
        output << "grid=" << (playback.grid ? '1' : '0') << '\n';
        output << "grid_division=" << (known_division(playback.grid_division) ? playback.grid_division : "beat") << '\n';
        output << "guitar_pitch_min=" << playback.guitar_pitch_min << '\n';
        output << "guitar_pitch_max=" << playback.guitar_pitch_max << '\n';
        output << "bass_pitch_min=" << playback.bass_pitch_min << '\n';
        output << "bass_pitch_max=" << playback.bass_pitch_max << '\n';
        output << "key_min=" << playback.key_min << '\n';
        output << "key_max=" << playback.key_max << '\n';
        output << "fret_prefer=" << playback.fret_prefer << '\n';
        output << "fret_suggest=" << (playback.fret_suggest ? '1' : '0') << '\n';
        output << "fret_allow_open=" << (playback.fret_allow_open ? '1' : '0') << '\n';
        output << "fret_show_alt=" << (playback.fret_show_alt ? '1' : '0') << '\n';
        output << "fret_harmonic=" << (playback.fret_harmonic ? '1' : '0') << '\n';
        output << "tab_hand_span=" << playback.tab_hand_span << '\n';
        output << "tab_onset_ms=" << playback.tab_onset_ms << '\n';
        output << "rhythm_layout=" << (playback.rhythm_layout == "score" ? "score" : "grid") << '\n';
        auto write_verify = [&](const char* key, auto lane_value) {
            output << key << '=';
            for (int index = 0; index < kStemCount; ++index) {
                if (index > 0) {
                    output << ',';
                }
                const auto& lane = lane_value(playback.verify[static_cast<std::size_t>(index)]);
                if constexpr (std::is_same_v<decltype(lane), const float&>) {
                    output << number_text(lane, 8);
                } else {
                    output << (lane ? '1' : '0');
                }
            }
            output << '\n';
        };
        write_verify("verify_original_db", [](const ProjectVerifyStem& stem) -> const float& { return stem.original.db; });
        write_verify("verify_original_pan", [](const ProjectVerifyStem& stem) -> const float& { return stem.original.pan; });
        write_verify("verify_original_mute", [](const ProjectVerifyStem& stem) -> const bool& { return stem.original.mute; });
        write_verify("verify_original_solo", [](const ProjectVerifyStem& stem) -> const bool& { return stem.original.solo; });
        write_verify("verify_midi_db", [](const ProjectVerifyStem& stem) -> const float& { return stem.midi.db; });
        write_verify("verify_midi_pan", [](const ProjectVerifyStem& stem) -> const float& { return stem.midi.pan; });
        write_verify("verify_midi_mute", [](const ProjectVerifyStem& stem) -> const bool& { return stem.midi.mute; });
        write_verify("verify_midi_solo", [](const ProjectVerifyStem& stem) -> const bool& { return stem.midi.solo; });
        if (!output) {
            std::filesystem::remove(temporary, error);
            return std::unexpected("cannot write playback.txt");
        }
    }
    std::filesystem::rename(temporary, path, error);
    if (error) {
        std::filesystem::remove(path, error);
        std::filesystem::rename(temporary, path, error);
    }
    if (error) {
        std::filesystem::remove(temporary, error);
        return std::unexpected("cannot write playback.txt");
    }
    return {};
}

void copy_project_playback(const std::filesystem::path& from_directory, const std::filesystem::path& to_directory) {
    std::error_code error;
    const auto source = from_directory / kPlaybackName;
    if (!std::filesystem::is_regular_file(source, error) || error) {
        return;
    }
    if (!std::filesystem::is_directory(to_directory, error) || error) {
        return;
    }
    std::filesystem::copy_file(
        source, to_directory / kPlaybackName, std::filesystem::copy_options::overwrite_existing, error);
}

}  // namespace bt
