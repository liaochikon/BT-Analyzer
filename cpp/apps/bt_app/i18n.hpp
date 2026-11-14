#pragma once

#include "bt/contract.hpp"

#include <filesystem>
#include <string>
#include <string_view>

namespace btapp {

// UTF-8 key=value locale files under locales/: zh-Hant.txt, zh-Hans.txt, en.txt.
// language: 0=zh-Hant, 1=zh-Hans, 2=en.
bool i18n_load(const std::filesystem::path& locales_directory);
void i18n_set_language(int language);
int i18n_language();
const char* t(std::string_view key);
const char* t(std::string_view key, int language);
// UTF-16 for Win32 dialogs / MessageBox. Empty if the key is missing.
std::wstring tw(std::string_view key);
std::wstring tw(std::string_view key, int language);
// Resolve stored notes that may be i18n keys (error.*) or raw text.
const char* stored(std::string_view text);
const char* stem_label(int stem);
const char* stem_label(int stem, int language);

}  // namespace btapp

