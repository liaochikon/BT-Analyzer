#include "notes.hpp"

#include "monitor.hpp"
#include "ui.hpp"

#include "bt/contract.hpp"

#include <condition_variable>
#include <mutex>
#include <thread>
#include <utility>

namespace btapp {
namespace {

bt::PitchParameters parameters_from_draft(const PitchDraft& draft) {
    bt::PitchParameters parameters;
    parameters.melodia = draft.melodia;
    parameters.onset_threshold = draft.onset;
    parameters.frame_threshold = draft.frame;
    parameters.minimum_note_ms = static_cast<float>(draft.min_ms);
    parameters.minimum_frequency = draft.min_hz > 0 ? static_cast<float>(draft.min_hz) : 0.f;
    parameters.maximum_frequency = draft.max_hz > 0 ? static_cast<float>(draft.max_hz) : 0.f;
    return parameters;
}

void queue_notes(App& app, const bt::SongSnapshot& song, int stem, bool refresh) {
    if (stem < 0 || stem >= bt::kStemCount || song.stems == nullptr || song.cache_directory.empty() || app.note_service == nullptr) {
        return;
    }
    const auto index = static_cast<std::size_t>(stem);
    app.note_status[index] = NoteStatus::Running;
    app.note_progress = 0.f;
    app.note_detail[index].clear();
    const auto cache_file = song.cache_directory / (std::string(bt::kStemNames[index]) + ".notes");
    app.note_ticket[index] = app.note_service->request(
        song.job, stem, cache_file, song.stems, parameters_from_draft(app.pitch_applied[index]), refresh);
    const char* tail = refresh ? (app.language == 2 ? " · rerun" : " · 重跑") : "";
    log_stem(LogLevel::Info, app.language, stem, "轉譜開始", "转谱开始", "Transcribe", tail);
}

}  // namespace

struct NoteService::State {
    std::mutex mutex;
    std::condition_variable cv;
    bool stop = false;
    bool has_request = false;
    Request request;
    std::uint64_t ticket = 0;
    std::vector<Result> results;
    std::atomic<float> progress{0.f};
    std::thread worker;
};

NoteService::NoteService(std::unique_ptr<bt::BasicPitchModel> model)
    : model_(std::move(model)), state_(std::make_unique<State>()) {
    transcribe_ = [this](
                      const float* samples, std::int64_t frames, const bt::PitchParameters& parameters,
                      const std::function<void(float)>& progress, const std::function<bool()>& cancelled) {
        return model_->transcribe(samples, frames, parameters, progress, cancelled);
    };
    state_->worker = std::thread([this] { thread_main(); });
}

NoteService::NoteService(Transcriber transcriber)
    : transcribe_(std::move(transcriber)), state_(std::make_unique<State>()) {
    state_->worker = std::thread([this] { thread_main(); });
}

NoteService::~NoteService() {
    {
        std::lock_guard lock(state_->mutex);
        state_->stop = true;
    }
    state_->cv.notify_all();
    if (state_->worker.joinable()) {
        state_->worker.join();
    }
}

std::uint64_t NoteService::request(
    std::uint64_t job, int stem, std::filesystem::path cache_file, std::shared_ptr<const bt::Stems> stems,
    bt::PitchParameters parameters, bool refresh) {
    std::uint64_t ticket = 0;
    {
        std::lock_guard lock(state_->mutex);
        ticket = ++state_->ticket;
        state_->request = Request{job, ticket, stem, std::move(cache_file), std::move(stems), parameters, refresh};
        state_->has_request = true;
        state_->progress.store(0.f);
    }
    state_->cv.notify_all();
    return ticket;
}

std::vector<NoteService::Result> NoteService::poll() {
    std::lock_guard lock(state_->mutex);
    std::vector<Result> results;
    results.swap(state_->results);
    return results;
}

std::size_t NoteService::parked_results() {
    std::lock_guard lock(state_->mutex);
    return state_->results.size();
}

float NoteService::progress() const {
    return state_->progress.load();
}

void NoteService::thread_main() {
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
                result.notes.clear();
            }
            state_->results.push_back(std::move(result));
        };
        if (cancelled()) {
            finish(Result{request.job, ticket, request.stem, true, {}, {}});
            continue;
        }
        if (!request.refresh && std::filesystem::exists(request.cache_file)) {
            auto loaded = bt::load_notes(request.cache_file);
            if (loaded) {
                finish(Result{request.job, ticket, request.stem, false, {}, std::move(*loaded)});
                continue;
            }
        }
        if (request.stems == nullptr || request.stem < 0 || request.stem >= bt::kStemCount) {
            finish(Result{request.job, ticket, request.stem, false, "stem audio is incomplete", {}});
            continue;
        }
        auto audio = bt::stem_to_basic_pitch_audio(
            request.stems->audio[static_cast<std::size_t>(request.stem)], request.stems->frames);
        if (!audio) {
            finish(Result{request.job, ticket, request.stem, false, audio.error(), {}});
            continue;
        }
        if (cancelled()) {
            finish(Result{request.job, ticket, request.stem, true, {}, {}});
            continue;
        }
        auto notes = transcribe_(
            audio->data(), static_cast<std::int64_t>(audio->size()), request.parameters,
            [&](float fraction) { state_->progress.store(fraction); },
            [&] { return cancelled(); });
        if (!notes) {
            const bool was_cancelled = notes.error() == "cancelled" || cancelled();
            finish(Result{request.job, ticket, request.stem, was_cancelled, was_cancelled ? std::string{} : notes.error(), {}});
            continue;
        }
        if (cancelled()) {
            finish(Result{request.job, ticket, request.stem, true, {}, {}});
            continue;
        }
        if (auto stored = bt::save_notes(request.cache_file, *notes); !stored) {
            finish(Result{request.job, ticket, request.stem, false, stored.error(), {}});
            continue;
        }
        state_->progress.store(1.f);
        finish(Result{request.job, ticket, request.stem, false, {}, std::move(*notes)});
    }
}

void sync_notes(App& app, const bt::SongSnapshot& song) {
    if (song.job != app.note_song) {
        app.note_song = song.job;
        for (auto& notes : app.notes) {
            notes.clear();
        }
        app.note_status.fill(NoteStatus::Idle);
        app.note_refresh.fill(false);
        for (auto& detail : app.note_detail) {
            detail.clear();
        }
        app.note_progress = 0.f;
    }
    if (app.note_service != nullptr) {
        app.note_progress = app.note_service->progress();
        for (auto& result : app.note_service->poll()) {
            if (result.job != app.note_song || result.stem < 0 || result.stem >= bt::kStemCount) {
                continue;
            }
            const auto index = static_cast<std::size_t>(result.stem);
            if (result.ticket != app.note_ticket[index]) {
                continue;
            }
            if (result.cancelled) {
                if (app.note_status[index] == NoteStatus::Running) {
                    app.note_status[index] = NoteStatus::Idle;
                    log_stem(LogLevel::Warn, app.language, result.stem, "轉譜已取消", "转谱已取消", "Transcribe cancelled");
                }
                continue;
            }
            if (!result.error.empty()) {
                app.note_status[index] = NoteStatus::Failed;
                app.note_detail[index] = result.error;
                app.note_refresh[index] = false;
                log_stem(LogLevel::Error, app.language, result.stem, "轉譜失敗", "转谱失败", "Transcribe failed", " · " + result.error);
                continue;
            }
            app.notes[index].clear();
            app.notes[index].reserve(result.notes.size());
            for (const bt::Note& note : result.notes) {
                app.notes[index].push_back(MidiNote{note.pitch, note.start, note.end, note.velocity});
            }
            app.note_status[index] = NoteStatus::Ready;
            app.note_detail[index].clear();
            app.note_refresh[index] = false;
            log_stem(LogLevel::Info, app.language, result.stem, "轉譜完成", "转谱完成", "Transcribed", " · " + std::to_string(result.notes.size()));
        }
    }
    if (song.phase != bt::SongPhase::Ready || song.stems == nullptr || song.job == app.closed_job) {
        return;
    }
    if (!app.pitch_ready || app.note_service == nullptr) {
        const std::string missing = app.pitch_note.empty() ? "models/nmp.onnx" : app.pitch_note;
        for (int stem = 0; stem < bt::kStemCount; ++stem) {
            const auto index = static_cast<std::size_t>(stem);
            if (app.note_status[index] != NoteStatus::Idle && !app.note_refresh[index]) {
                continue;
            }
            app.note_status[index] = NoteStatus::Failed;
            app.note_detail[index] = missing;
            app.note_refresh[index] = false;
            log_stem(LogLevel::Error, app.language, stem, "轉譜失敗", "转谱失败", "Transcribe failed", " · " + missing);
        }
        return;
    }
    if (song.cache_directory.empty()) {
        return;
    }
    for (int stem = 0; stem < bt::kStemCount; ++stem) {
        const auto index = static_cast<std::size_t>(stem);
        if (app.note_refresh[index] && app.note_status[index] != NoteStatus::Running) {
            queue_notes(app, song, stem, true);
            return;
        }
    }
    for (NoteStatus status : app.note_status) {
        if (status == NoteStatus::Running) {
            return;
        }
    }
    for (int stem = 0; stem < bt::kStemCount; ++stem) {
        if (app.note_status[static_cast<std::size_t>(stem)] == NoteStatus::Idle) {
            queue_notes(app, song, stem, false);
            return;
        }
    }
}

void rerun_notes(App& app, int stem) {
    if (stem < 0 || stem >= bt::kStemCount) {
        return;
    }
    const auto index = static_cast<std::size_t>(stem);
    app.note_refresh[index] = true;
    app.note_ticket[index] = 0;
    app.note_status[index] = NoteStatus::Idle;
    app.note_detail[index].clear();
}

}  // namespace btapp
