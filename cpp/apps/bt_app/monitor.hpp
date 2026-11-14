#pragma once

#include <filesystem>
#include <string>
#include <string_view>

namespace btapp {

struct App;

enum class LogLevel { Info, Warn, Error };

void monitor_start(const std::filesystem::path& cache_directory);
void log_message(LogLevel level, std::string text);
void log_phrase(LogLevel level, std::string_view key, const std::string& tail = {});
void log_stem(LogLevel level, int stem, std::string_view key, const std::string& tail = {});
void log_startup(const App& app);
void draw_monitor(App& app);
void draw_mono_progress(const char* id, float fraction, float width, float height, const char* overlay);

}  // namespace btapp
