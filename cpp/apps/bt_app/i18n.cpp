#include "i18n.hpp"

#include <array>
#include <fstream>
#include <mutex>
#include <unordered_map>
#include <utility>
#include <vector>

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

namespace btapp {
namespace {

constexpr int kLangCount = 3;
const char* kFiles[kLangCount] = {"zh-Hant.txt", "zh-Hans.txt", "en.txt"};

std::mutex gate;
std::array<std::unordered_map<std::string, std::string>, kLangCount> tables{};
int language = 0;
std::string missing_scratch;

std::wstring utf16_from_utf8(std::string_view utf8) {
    if (utf8.empty()) {
        return {};
    }
    const int size = MultiByteToWideChar(
        CP_UTF8, 0, utf8.data(), static_cast<int>(utf8.size()), nullptr, 0);
    if (size <= 0) {
        return {};
    }
    std::wstring wide(static_cast<std::size_t>(size), L'\0');
    MultiByteToWideChar(
        CP_UTF8, 0, utf8.data(), static_cast<int>(utf8.size()), wide.data(), size);
    return wide;
}

std::string unescape(std::string value) {
    std::string out;
    out.reserve(value.size());
    for (std::size_t i = 0; i < value.size(); ++i) {
        if (value[i] == '\\' && i + 1 < value.size()) {
            const char next = value[i + 1];
            if (next == 'n') {
                out.push_back('\n');
                ++i;
                continue;
            }
            if (next == '\\') {
                out.push_back('\\');
                ++i;
                continue;
            }
        }
        out.push_back(value[i]);
    }
    return out;
}

bool load_one(const std::filesystem::path& path, std::unordered_map<std::string, std::string>& out) {
    std::ifstream input(path, std::ios::binary);
    if (!input) {
        return false;
    }
    std::string line;
    while (std::getline(input, line)) {
        if (!line.empty() && line.back() == '\r') {
            line.pop_back();
        }
        if (line.empty() || line[0] == '#') {
            continue;
        }
        const auto eq = line.find('=');
        if (eq == std::string::npos || eq == 0) {
            continue;
        }
        out[line.substr(0, eq)] = unescape(line.substr(eq + 1));
    }
    return !out.empty();
}

const char* lookup(std::string_view key, int lang) {
    lang = lang < 0 ? 0 : (lang >= kLangCount ? 0 : lang);
    std::lock_guard lock(gate);
    const auto& table = tables[static_cast<std::size_t>(lang)];
    const auto found = table.find(std::string(key));
    if (found != table.end()) {
        return found->second.c_str();
    }
    if (lang != 2) {
        const auto& english = tables[2];
        const auto en = english.find(std::string(key));
        if (en != english.end()) {
            return en->second.c_str();
        }
    }
    missing_scratch.assign(key);
    return missing_scratch.c_str();
}

}  // namespace

bool i18n_load(const std::filesystem::path& locales_directory) {
    std::array<std::unordered_map<std::string, std::string>, kLangCount> next{};
    bool any = false;
    for (int index = 0; index < kLangCount; ++index) {
        if (load_one(locales_directory / kFiles[index], next[static_cast<std::size_t>(index)])) {
            any = true;
        }
    }
    if (!any) {
        return false;
    }
    std::lock_guard lock(gate);
    tables = std::move(next);
    return true;
}

void i18n_set_language(int value) {
    language = value < 0 ? 0 : (value > 2 ? 2 : value);
}

int i18n_language() {
    return language;
}

const char* t(std::string_view key) {
    return lookup(key, language);
}

const char* t(std::string_view key, int lang) {
    return lookup(key, lang);
}

std::wstring tw(std::string_view key) {
    return utf16_from_utf8(t(key));
}

std::wstring tw(std::string_view key, int lang) {
    return utf16_from_utf8(t(key, lang));
}

const char* stored(std::string_view text) {
    if (text.size() >= 6 && text.substr(0, 6) == "error.") {
        return t(text);
    }
    if (text.size() >= 4 && text.substr(0, 4) == "log.") {
        return t(text);
    }
    missing_scratch.assign(text);
    return missing_scratch.c_str();
}

const char* stem_label(int stem, int lang) {
    static const char* keys[bt::kStemCount] = {
        "stem.drums", "stem.bass", "stem.other", "stem.vocals", "stem.guitar", "stem.piano"};
    if (stem < 0 || stem >= bt::kStemCount) {
        return "";
    }
    return t(keys[stem], lang);
}

const char* stem_label(int stem) {
    return stem_label(stem, language);
}

}  // namespace btapp
