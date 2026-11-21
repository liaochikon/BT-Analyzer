#include "playback.hpp"

#include "bt/project.hpp"

#include <algorithm>
#include <cmath>
#include <system_error>

namespace btapp {
namespace {

void remember_loop(App& app, bool enabled, double a, double b) {
    app.playback_loop_on = enabled;
    app.playback_loop_a = a;
    app.playback_loop_b = b;
    app.playback_loop_pending = enabled || a >= 0.0 || b >= 0.0;
    if (app.player != nullptr) {
        app.player->restore_loop(false, -1.0, -1.0);
    }
}

}  // namespace

bool same_project_playback(const bt::ProjectPlayback& left, const bt::ProjectPlayback& right) {
    return left.gain == right.gain && left.pan == right.pan && left.mute == right.mute && left.solo == right.solo &&
           left.speed == right.speed && left.master_db == right.master_db && left.loop == right.loop &&
           left.loop_a == right.loop_a && left.loop_b == right.loop_b && left.loop_snap == right.loop_snap &&
           left.count_in == right.count_in && left.metro == right.metro && left.click_db == right.click_db &&
           left.beat_division == right.beat_division && left.grid == right.grid &&
           left.grid_division == right.grid_division && left.verify == right.verify &&
           left.guitar_pitch_min == right.guitar_pitch_min && left.guitar_pitch_max == right.guitar_pitch_max &&
           left.bass_pitch_min == right.bass_pitch_min && left.bass_pitch_max == right.bass_pitch_max &&
           left.key_min == right.key_min && left.key_max == right.key_max && left.fret_prefer == right.fret_prefer &&
           left.fret_suggest == right.fret_suggest && left.fret_allow_open == right.fret_allow_open &&
           left.fret_show_alt == right.fret_show_alt && left.fret_harmonic == right.fret_harmonic;
}

bt::ProjectPlayback capture_project_playback(const App& app) {
    bt::ProjectPlayback playback = bt::default_project_playback();
    for (int stem = 0; stem < bt::kStemCount; ++stem) {
        const auto index = static_cast<std::size_t>(stem);
        playback.gain[index] = db_to_gain(app.gain_db[index]);
        playback.pan[index] = app.pan[index];
        playback.mute[index] = app.mute[index];
        playback.solo[index] = app.solo[index];
        playback.verify[index] = app.verify[index];
    }
    playback.speed = app.speed;
    playback.master_db = app.master_db;
    playback.click_db = app.click_db;
    playback.metro = app.metro;
    playback.count_in = app.count_in;
    playback.loop_snap = app.loop_snap;
    playback.beat_division = division_token(app.metro_division);
    playback.grid = app.grid;
    playback.grid_division = division_token(app.grid_division);
    playback.guitar_pitch_min = app.guitar_pitch_min;
    playback.guitar_pitch_max = app.guitar_pitch_max;
    playback.bass_pitch_min = app.bass_pitch_min;
    playback.bass_pitch_max = app.bass_pitch_max;
    playback.key_min = app.key_min;
    playback.key_max = app.key_max;
    playback.fret_prefer = app.fret_prefer;
    playback.fret_suggest = app.fret_suggest;
    playback.fret_allow_open = app.fret_allow_open;
    playback.fret_show_alt = app.fret_show_alt;
    playback.fret_harmonic = app.fret_harmonic;
    if (app.playback_loop_pending) {
        playback.loop = app.playback_loop_on;
        playback.loop_a = app.playback_loop_a;
        playback.loop_b = app.playback_loop_b;
    } else if (app.player != nullptr) {
        const auto transport = app.player->snapshot();
        playback.loop = transport.loop;
        playback.loop_a = transport.loop_a;
        playback.loop_b = transport.loop_b;
    }
    return playback;
}

void apply_project_playback(App& app, const bt::ProjectPlayback& playback) {
    app.metro_division = division_from_token(playback.beat_division);
    app.grid = playback.grid;
    app.grid_division = division_from_token(playback.grid_division);
    app.speed = std::clamp(playback.speed, 0.1f, 2.f);
    app.applied_speed = app.speed;
    app.master_db = std::clamp(playback.master_db, -60.f, 6.f);
    app.click_db = std::clamp(playback.click_db, -60.f, 6.f);
    app.metro = playback.metro;
    app.count_in = playback.count_in;
    app.loop_snap = playback.loop_snap;
    auto pitch = [](int value, int low, int high, int fallback) {
        if (value < low || value > high) {
            return fallback;
        }
        return value;
    };
    app.guitar_pitch_min = pitch(playback.guitar_pitch_min, 0, 127, 40);
    app.guitar_pitch_max = pitch(playback.guitar_pitch_max, 0, 127, 86);
    app.bass_pitch_min = pitch(playback.bass_pitch_min, 0, 127, 28);
    app.bass_pitch_max = pitch(playback.bass_pitch_max, 0, 127, 65);
    app.key_min = pitch(playback.key_min, 0, 127, 21);
    app.key_max = pitch(playback.key_max, 0, 127, 108);
    if (app.guitar_pitch_min > app.guitar_pitch_max) {
        std::swap(app.guitar_pitch_min, app.guitar_pitch_max);
    }
    if (app.bass_pitch_min > app.bass_pitch_max) {
        std::swap(app.bass_pitch_min, app.bass_pitch_max);
    }
    if (app.key_min > app.key_max) {
        std::swap(app.key_min, app.key_max);
    }
    app.fret_prefer = std::clamp(playback.fret_prefer, 0, kFretCount);
    app.fret_suggest = playback.fret_suggest;
    app.fret_allow_open = playback.fret_allow_open;
    app.fret_show_alt = playback.fret_show_alt;
    app.fret_harmonic = playback.fret_harmonic;
    for (int stem = 0; stem < bt::kStemCount; ++stem) {
        const auto index = static_cast<std::size_t>(stem);
        float gain = playback.gain[index];
        if (!std::isfinite(gain) || gain < 0.f) {
            gain = 1.f;
        }
        gain = std::clamp(gain, db_to_gain(-60.f), db_to_gain(6.f));
        app.gain_db[index] = gain_to_db(gain);
        float pan = playback.pan[index];
        if (!std::isfinite(pan)) {
            pan = 0.f;
        }
        app.pan[index] = std::clamp(pan, -1.f, 1.f);
        app.mute[index] = playback.mute[index];
        app.solo[index] = playback.solo[index];
        auto lane = playback.verify[index];
        auto clamp_lane = [](bt::ProjectVerifyLane& value) {
            if (!std::isfinite(value.db)) {
                value.db = 0.f;
            }
            value.db = std::clamp(value.db, -60.f, 6.f);
            if (!std::isfinite(value.pan)) {
                value.pan = 0.f;
            }
            value.pan = std::clamp(value.pan, -1.f, 1.f);
        };
        clamp_lane(lane.original);
        clamp_lane(lane.midi);
        app.verify[index] = lane;
        if (app.session != nullptr) {
            app.session->set_gain(stem, gain);
            app.session->set_pan(stem, app.pan[index]);
            app.session->set_mute(stem, app.mute[index]);
            app.session->set_solo(stem, app.solo[index]);
        }
    }
    if (app.player != nullptr) {
        app.player->set_speed(app.speed);
        app.player->set_master_db(app.master_db);
    }
    push_player_mix(app);
    apply_metronome(app);
    remember_loop(app, playback.loop, playback.loop_a, playback.loop_b);
    if (app.session != nullptr) {
        const auto song = app.session->snapshot();
        if (song != nullptr) {
            app.seen_revision = song->revision;
        }
    }
    app.playback_committed = capture_project_playback(app);
    app.playback_dirty = false;
}

void push_player_mix(App& app) {
    if (app.player == nullptr) {
        return;
    }
    std::array<float, bt::kStemCount> gain{};
    for (int stem = 0; stem < bt::kStemCount; ++stem) {
        gain[static_cast<std::size_t>(stem)] = db_to_gain(app.gain_db[static_cast<std::size_t>(stem)]);
    }
    app.player->set_mix(gain, app.mute, app.solo, app.pan);
}

void touch_project_playback(App& app) {
    app.playback_dirty = true;
}

void flush_project_playback(App& app) {
    if (!app.playback_dirty || !app.playback_bound || app.playback_directory.empty()) {
        return;
    }
    std::error_code error;
    if (!std::filesystem::is_directory(app.playback_directory, error) || error) {
        app.playback_dirty = false;
        return;
    }
    if (bt::write_project_playback(app.playback_directory, capture_project_playback(app))) {
        app.playback_dirty = false;
    }
}

void settle_project_loop(App& app, const bt::SongSnapshot& song) {
    if (!app.playback_loop_pending || app.player == nullptr || song.stems == nullptr || song.stems->frames <= 0) {
        return;
    }
    const double length = static_cast<double>(song.stems->frames) / static_cast<double>(bt::kSampleRate);
    const auto mark = [&](double seconds) {
        if (!(seconds >= 0.0) || !std::isfinite(seconds)) {
            return -1.0;
        }
        return std::min(seconds, length);
    };
    const double a = mark(app.playback_loop_a);
    const double b = mark(app.playback_loop_b);
    app.player->restore_loop(app.playback_loop_on && a >= 0.0 && b > a + 0.01, a, b);
    app.playback_loop_pending = false;
    // The player may clamp the marks to the song length. That is not a user change.
    app.playback_committed = capture_project_playback(app);
}

void sync_project_playback(App& app, const bt::SongSnapshot& song) {
    const bool changed = song.job != app.playback_job || song.cache_directory != app.playback_directory;
    if (changed) {
        if (app.playback_bound) {
            flush_project_playback(app);
        }
        app.playback_bound = false;
        app.playback_job = song.job;
        app.playback_directory = song.cache_directory;
        app.playback_loop_pending = false;
        if (song.job == 0 || song.cache_directory.empty()) {
            apply_project_playback(app, bt::default_project_playback());
            return;
        }
    } else if (app.playback_bound || song.job == 0 || song.cache_directory.empty()) {
        return;
    }
    std::error_code error;
    if (!std::filesystem::is_directory(song.cache_directory, error) || error) {
        if (changed) {
            apply_project_playback(app, bt::default_project_playback());
        }
        return;
    }
    bt::ProjectPlayback playback = bt::default_project_playback();
    bt::read_project_playback(song.cache_directory, playback);
    apply_project_playback(app, playback);
    app.playback_bound = true;
}

}  // namespace btapp
