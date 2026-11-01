#include "beats.hpp"

#include "monitor.hpp"
#include "ui.hpp"

#include "bt/contract.hpp"

#include <algorithm>
#include <condition_variable>
#include <cmath>
#include <cstdio>
#include <mutex>
#include <thread>
#include <utility>

namespace btapp {
namespace {

double rms_of(const std::vector<float>& mono) {
    if (mono.empty()) {
        return 0;
    }
    double sum = 0;
    for (float sample : mono) {
        sum += static_cast<double>(sample) * static_cast<double>(sample);
    }
    return std::sqrt(sum / static_cast<double>(mono.size()));
}

double song_seconds(const std::shared_ptr<const bt::SongSnapshot>& song) {
    if (song == nullptr) {
        return 0;
    }
    if (song->stems != nullptr && song->stems->frames > 0) {
        return static_cast<double>(song->stems->frames) / static_cast<double>(bt::kSampleRate);
    }
    if (song->mix != nullptr && song->mix->frames > 0) {
        return static_cast<double>(song->mix->frames) / static_cast<double>(bt::kSampleRate);
    }
    return 0;
}

std::vector<float> mono_from(const float* channel_major, std::int64_t frames) {
    std::vector<float> mono(static_cast<std::size_t>(frames));
    const float* right = channel_major + frames;
    for (std::int64_t i = 0; i < frames; ++i) {
        mono[static_cast<std::size_t>(i)] = 0.5f * (channel_major[i] + right[i]);
    }
    return mono;
}

}  // namespace

struct BeatService::State {
    std::mutex mutex;
    std::condition_variable cv;
    bool stop = false;
    bool has_request = false;
    Request request;
    std::uint64_t ticket = 0;
    std::vector<Result> results;
    std::thread worker;
};

BeatService::BeatService() : state_(std::make_unique<State>()) {
    state_->worker = std::thread([this] { thread_main(); });
}

BeatService::~BeatService() {
    {
        std::lock_guard lock(state_->mutex);
        state_->stop = true;
    }
    state_->cv.notify_all();
    if (state_->worker.joinable()) {
        state_->worker.join();
    }
}

std::uint64_t BeatService::request(
    std::uint64_t job, std::filesystem::path cache_file, std::shared_ptr<const bt::SongSnapshot> song, bool refresh) {
    std::uint64_t ticket = 0;
    {
        std::lock_guard lock(state_->mutex);
        ticket = ++state_->ticket;
        state_->request = Request{job, ticket, std::move(cache_file), std::move(song), refresh};
        state_->has_request = true;
    }
    state_->cv.notify_all();
    return ticket;
}

std::vector<BeatService::Result> BeatService::poll() {
    std::lock_guard lock(state_->mutex);
    std::vector<Result> results;
    results.swap(state_->results);
    return results;
}

std::size_t BeatService::parked_results() {
    std::lock_guard lock(state_->mutex);
    return state_->results.size();
}

void BeatService::thread_main() {
    for (;;) {
        Request request;
        {
            std::unique_lock lock(state_->mutex);
            state_->cv.wait(lock, [&] { return state_->stop || state_->has_request; });
            if (state_->stop && !state_->has_request) {
                return;
            }
            request = std::move(state_->request);
            state_->has_request = false;
        }
        const auto ticket = request.ticket;
        auto cancelled = [&] {
            std::lock_guard lock(state_->mutex);
            return state_->stop || state_->ticket != ticket;
        };
        auto finish = [&](Result result) {
            std::lock_guard lock(state_->mutex);
            if (state_->ticket != ticket) {
                result.cancelled = true;
                result.error.clear();
            }
            state_->results.push_back(std::move(result));
        };
        if (cancelled()) {
            finish(Result{request.job, ticket, true, {}, {}});
            continue;
        }
        if (!request.refresh && std::filesystem::exists(request.cache_file)) {
            if (auto loaded = bt::load_beats(request.cache_file)) {
                bt::extend_beat_edges(*loaded, song_seconds(request.song));
                finish(Result{request.job, ticket, false, {}, *loaded});
                continue;
            }
        }
        const auto& song = request.song;
        if (song == nullptr || song->stems == nullptr || song->stems->frames <= 0) {
            finish(Result{request.job, ticket, false, "stem audio is incomplete", {}});
            continue;
        }
        const auto& stems = *song->stems;
        if (stems.audio[0].size() < static_cast<std::size_t>(stems.frames) * 2) {
            finish(Result{request.job, ticket, false, "stem audio is incomplete", {}});
            continue;
        }
        auto drums = mono_from(stems.audio[0].data(), stems.frames);
        std::vector<float> chosen = std::move(drums);
        if (song->mix != nullptr && song->mix->frames == stems.frames && song->mix->channels >= 1) {
            auto mix = song->mix->channels == 1
                           ? std::vector<float>(song->mix->samples.begin(), song->mix->samples.begin() + song->mix->frames)
                           : mono_from(song->mix->samples.data(), song->mix->frames);
            const double drum_rms = rms_of(chosen);
            const double mix_rms = rms_of(mix);
            if (drum_rms < 1e-4 || (mix_rms > 0 && drum_rms < 0.02 * mix_rms)) {
                chosen = std::move(mix);
            }
        }
        if (cancelled()) {
            finish(Result{request.job, ticket, true, {}, {}});
            continue;
        }
        auto grid = bt::track_beats(chosen.data(), static_cast<std::int64_t>(chosen.size()), bt::kSampleRate, [&] { return cancelled(); });
        if (!grid) {
            const bool was_cancelled = grid.error() == "cancelled" || cancelled();
            finish(Result{request.job, ticket, was_cancelled, was_cancelled ? std::string{} : grid.error(), {}});
            continue;
        }
        if (cancelled()) {
            finish(Result{request.job, ticket, true, {}, {}});
            continue;
        }
        if (auto stored = bt::save_beats(request.cache_file, *grid); !stored) {
            finish(Result{request.job, ticket, false, stored.error(), {}});
            continue;
        }
        bt::extend_beat_edges(*grid, song_seconds(request.song));
        finish(Result{request.job, ticket, false, {}, *grid});
    }
}

void sync_beats(App& app, const std::shared_ptr<const bt::SongSnapshot>& song) {
    if (song == nullptr) {
        return;
    }
    if (song->job != app.beat_song) {
        app.beat_song = song->job;
        app.beat_ticket = 0;
        app.beat_offset = 0;
        app.bpm = 120.f;
        app.beat_ready = false;
        app.beat_pending = false;
        app.beat_refresh = false;
        app.beat_times.reset();
        app.beat_detail.clear();
    }
    if (app.beat_service != nullptr) {
        for (auto& result : app.beat_service->poll()) {
            if (result.job != app.beat_song || result.ticket != app.beat_ticket) {
                continue;
            }
            app.beat_pending = false;
            if (result.cancelled) {
                app.beat_ready = false;
                log_phrase(LogLevel::Warn, app.language, "拍點已取消", "拍点已取消", "Beat tracking cancelled");
                continue;
            }
            app.beat_ready = true;
            if (!result.error.empty() || !(result.grid.bpm > 0.f)) {
                app.beat_detail = result.error.empty() ? "no beats" : result.error;
                log_phrase(LogLevel::Error, app.language, "拍點失敗", "拍点失败", "Beat tracking failed", " · " + app.beat_detail);
                continue;
            }
            app.beat_detail.clear();
            char summary[96];
            std::snprintf(
                summary, sizeof summary, " · %.1f BPM  +%.2fs · %zu", static_cast<double>(result.grid.bpm), result.grid.offset,
                result.grid.times.size());
            log_phrase(LogLevel::Info, app.language, "拍點完成", "拍点完成", "Beats ready", summary);
            if (!result.grid.times.empty()) {
                app.beat_times = std::make_shared<const std::vector<double>>(result.grid.times);
            } else {
                app.beat_times.reset();
            }
            app.bpm = std::clamp(result.grid.bpm, 40.f, 240.f);
            app.beat_offset = std::max(0.0, result.grid.offset);
        }
    }
    if (app.beat_service == nullptr || song->phase != bt::SongPhase::Ready || song->cache_directory.empty()) {
        return;
    }
    if (app.beat_ready && !app.beat_refresh) {
        return;
    }
    const bool refresh = app.beat_refresh;
    app.beat_refresh = false;
    app.beat_pending = true;
    app.beat_ticket = app.beat_service->request(song->job, song->cache_directory / "beats.txt", song, refresh);
    app.beat_ready = true;
    log_phrase(LogLevel::Info, app.language, "拍點偵測開始", "拍点检测开始", "Beat tracking started");
}

void rerun_beats(App& app) {
    app.beat_ready = false;
    app.beat_refresh = true;
    app.beat_pending = false;
}

}  // namespace btapp
