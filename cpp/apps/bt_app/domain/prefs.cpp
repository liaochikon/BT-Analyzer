#include "prefs.hpp"

#include "i18n.hpp"

#include <cstdlib>
#include <fstream>
#include <string>
#include <system_error>

namespace btapp {

void set_language(App& app, int language) {
    app.language = language < 0 ? 0 : (language > 2 ? 2 : language);
    i18n_set_language(app.language);
}

void load_language(App& app) {
    if (app.language_loaded) {
        return;
    }
    app.language_loaded = true;
    std::ifstream input(app.cache_directory / "ui-language.txt");
    std::string token;
    input >> token;
    if (token == "zh-Hans") {
        set_language(app, 1);
    } else if (token == "en") {
        set_language(app, 2);
    } else {
        set_language(app, 0);
    }
}

void save_language(const App& app) {
    if (app.cache_directory.empty()) {
        return;
    }
    std::error_code error;
    std::filesystem::create_directories(app.cache_directory, error);
    std::ofstream output(app.cache_directory / "ui-language.txt", std::ios::trunc);
    const char* name = app.language == 1 ? "zh-Hans" : app.language == 2 ? "en" : "zh-Hant";
    output << name << '\n';
}

void save_performance(const App& app) {
    if (app.cache_directory.empty()) {
        return;
    }
    std::error_code error;
    std::filesystem::create_directories(app.cache_directory, error);
    std::ofstream output(app.cache_directory / "performance.txt", std::ios::trunc);
    if (!output) {
        return;
    }
    output << "note_workers=" << app.note_workers << '\n';
    output << "demucs_threads=" << app.demucs_threads << '\n';
    output << "seek_step=" << static_cast<int>(std::lround(app.seek_step)) << '\n';
    output << "shortcut_hints=" << (app.shortcut_hints ? '1' : '0') << '\n';
    const std::string model = app.separation_model.empty() ? std::string(bt::kModelKey) : app.separation_model;
    const char* device = app.separation_preference == 1 ? "cpu" : app.separation_preference == 2 ? "cuda" : "auto";
    output << "separation_model=" << model << '\n';
    output << "separation_device=" << device << '\n';
}

void load_performance(App& app) {
    if (app.cache_directory.empty()) {
        return;
    }
    std::ifstream input(app.cache_directory / "performance.txt");
    std::string line;
    while (std::getline(input, line)) {
        const auto eq = line.find('=');
        if (eq == std::string::npos) {
            continue;
        }
        const std::string key = line.substr(0, eq);
        std::string raw = line.substr(eq + 1);
        if (!raw.empty() && raw.back() == '\r') {
            raw.pop_back();
        }
        const int value = std::atoi(raw.c_str());
        if (key == "note_workers") {
            app.note_workers = std::clamp(value, 1, 8);
        } else if (key == "demucs_threads") {
            app.demucs_threads = std::clamp(value, 1, 16);
        } else if (key == "seek_step") {
            app.seek_step = static_cast<float>(std::clamp(value, 1, 20));
        } else if (key == "shortcut_hints") {
            app.shortcut_hints = raw == "1";
        } else if (key == "separation_model" && bt::is_separation_key(raw)) {
            app.separation_model = raw;
        } else if (key == "separation_device") {
            app.separation_preference = raw == "cpu" ? 1 : raw == "cuda" ? 2 : 0;
        }
    }
}

void save_layout(const App& app) {
    if (app.cache_directory.empty()) {
        return;
    }
    std::error_code error;
    std::filesystem::create_directories(app.cache_directory, error);
    std::ofstream output(app.cache_directory / "ui-layout.txt", std::ios::trunc);
    if (!output) {
        return;
    }
    output << "projects_width=" << app.projects_width << '\n';
    output << "projects_restored=" << app.projects_restored << '\n';
    output << "stage_share=" << app.stage_share << '\n';
    output << "mixer_restored=" << app.mixer_restored << '\n';
}

void load_layout(App& app) {
    if (app.layout_loaded) {
        return;
    }
    app.layout_loaded = true;
    if (app.cache_directory.empty()) {
        return;
    }
    std::ifstream input(app.cache_directory / "ui-layout.txt");
    std::string line;
    while (std::getline(input, line)) {
        const auto eq = line.find('=');
        if (eq == std::string::npos) {
            continue;
        }
        const std::string key = line.substr(0, eq);
        std::string raw = line.substr(eq + 1);
        if (!raw.empty() && raw.back() == '\r') {
            raw.pop_back();
        }
        const float value = std::strtof(raw.c_str(), nullptr);
        if (key == "projects_width") {
            app.projects_width = std::clamp(value, 0.f, 640.f);
        } else if (key == "projects_restored") {
            app.projects_restored = std::clamp(value, 160.f, 640.f);
        } else if (key == "stage_share") {
            if (value >= 0.f && value <= 1.f) {
                app.stage_share = value;
            }
        } else if (key == "mixer_restored") {
            if (value >= 0.f && value <= 1.f) {
                app.mixer_restored = value;
            }
        }
    }
    if (app.projects_width > 0.f && app.projects_width < 160.f) {
        app.projects_width = 160.f;
    }
    if (app.projects_restored < 160.f) {
        app.projects_restored = 232.f;
    }
}

void load_output(App& app) {
    if (app.player == nullptr || app.cache_directory.empty()) {
        return;
    }
    std::ifstream input(app.cache_directory / "output-device.txt");
    std::string token;
    std::getline(input, token);
    while (!token.empty() && (token.back() == '\r' || token.back() == ' ')) {
        token.pop_back();
    }
    if (token.empty() || token == "default") {
        return;
    }
    if (auto switched = app.player->use_device(token); !switched) {
        app.device_note = switched.error();
        return;
    }
    app.output_id = token;
    app.device_note.clear();
}

void save_output(const App& app) {
    if (app.cache_directory.empty()) {
        return;
    }
    std::error_code error;
    std::filesystem::create_directories(app.cache_directory, error);
    std::ofstream output(app.cache_directory / "output-device.txt", std::ios::trunc);
    output << (app.output_id.empty() ? "default" : app.output_id) << '\n';
}

}  // namespace btapp
