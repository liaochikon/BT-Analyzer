#include "notes.hpp"

#include "monitor.hpp"
#include "ui.hpp"

#include "bt/contract.hpp"

#include <algorithm>
#include <condition_variable>
#include <deque>
#include <mutex>
#include <stdexcept>
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
    if (stem < 0 || stem >= bt::kStemCount || song.stems == nullptr || song.cache_directory.empty() ||
        app.note_service == nullptr) {
        return;
    }
    const auto index = static_cast<std::size_t>(stem);
    app.note_status[index] = NoteStatus::Running;
    app.note_detail[index].clear();
    const auto cache_file = song.cache_directory / (std::string(bt::kStemNames[index]) + ".notes");
    app.note_ticket[index] = app.note_service->request(
        song.job, stem, cache_file, song.stems, parameters_from_draft(app.pitch_applied[index]), refresh);
    const char* tail = refresh ? (app.language == 2 ? " · rerun" : " · 重跑") : "";
    log_stem(LogLevel::Info, app.language, stem, "轉譜開始", "转谱开始", "Transcribe", tail);
}

int clamp_workers(int workers) {
    return std::clamp(workers, 1, 8);
}

}  // namespace

struct NoteService::State {
    std::mutex mutex;
    std::condition_variable cv;
    bool stop = false;
    std::deque<Request> queue;
    std::uint64_t ticket = 0;
    std::vector<Result> results;
    std::vector<std::unique_ptr<bt::BasicPitchModel>> models;
    std::vector<std::thread> workers;
    std::unique_ptr<std::atomic<float>[]> slot_progress;
    int slot_count = 0;
    std::atomic<int> running{0};
};

void NoteService::launch_workers(int workers) {
    state_->stop = false;
    state_->workers.clear();
    state_->workers.reserve(static_cast<std::size_t>(workers));
    for (int index = 0; index < workers; ++index) {
        state_->workers.emplace_back([this, index] { worker_main(index); });
    }
}

bool NoteService::rebuild_locked(int workers) {
    workers = clamp_workers(workers);
    std::vector<std::unique_ptr<bt::BasicPitchModel>> models;
    if (!model_path_.empty()) {
        models.reserve(static_cast<std::size_t>(workers));
        for (int index = 0; index < workers; ++index) {
            auto opened = bt::BasicPitchModel::open(model_path_);
            if (!opened) {
                return false;
            }
            models.push_back(std::make_unique<bt::BasicPitchModel>(std::move(*opened)));
        }
    }
    state_->stop = true;
    state_->queue.clear();
    state_->models = std::move(models);
    state_->slot_count = workers;
    state_->slot_progress = std::make_unique<std::atomic<float>[]>(static_cast<std::size_t>(workers));
    for (int index = 0; index < workers; ++index) {
        state_->slot_progress[index].store(0.f);
    }
    return true;
}

NoteService::NoteService(const std::filesystem::path& model_path, int workers)
    : model_path_(model_path), state_(std::make_unique<State>()) {
    workers = clamp_workers(workers);
    if (!rebuild_locked(workers)) {
        throw std::runtime_error("cannot open pitch model pool");
    }
    launch_workers(workers);
}

NoteService::NoteService(Transcriber transcriber)
    : transcribe_(std::move(transcriber)), state_(std::make_unique<State>()) {
    state_->slot_count = 1;
    state_->slot_progress = std::make_unique<std::atomic<float>[]>(1);
    state_->slot_progress[0].store(0.f);
    launch_workers(1);
}

NoteService::~NoteService() {
    {
        std::lock_guard lock(state_->mutex);
        state_->stop = true;
        state_->queue.clear();
    }
    state_->cv.notify_all();
    for (auto& worker : state_->workers) {
        if (worker.joinable()) {
            worker.join();
        }
    }
}

int NoteService::workers() const {
    std::lock_guard lock(state_->mutex);
    return std::max(1, static_cast<int>(state_->workers.size()));
}

bool NoteService::set_workers(int workers) {
    workers = clamp_workers(workers);
    std::vector<std::thread> old;
    {
        std::lock_guard lock(state_->mutex);
        if (state_->running.load() > 0 || !state_->queue.empty()) {
            return false;
        }
        if (static_cast<int>(state_->workers.size()) == workers) {
            return true;
        }
        if (!rebuild_locked(workers)) {
            return false;
        }
        old.swap(state_->workers);
    }
    state_->cv.notify_all();
    for (auto& worker : old) {
        if (worker.joinable()) {
            worker.join();
        }
    }
    {
        std::lock_guard lock(state_->mutex);
        launch_workers(workers);
    }
    return true;
}

std::uint64_t NoteService::request(
    std::uint64_t job, int stem, std::filesystem::path cache_file, std::shared_ptr<const bt::Stems> stems,
    bt::PitchParameters parameters, bool refresh) {
    std::uint64_t ticket = 0;
    {
        std::lock_guard lock(state_->mutex);
        ticket = ++state_->ticket;
        state_->queue.push_back(
            Request{job, ticket, stem, std::move(cache_file), std::move(stems), parameters, refresh});
    }
    state_->cv.notify_one();
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
    float sum = 0.f;
    int count = 0;
    const int slots = state_->slot_count;
    for (int index = 0; index < slots; ++index) {
        const float fraction = state_->slot_progress[index].load();
        if (fraction > 0.f || state_->running.load() > 0) {
            sum += fraction;
            ++count;
        }
    }
    if (count <= 0) {
        return 0.f;
    }
    return sum / static_cast<float>(count);
}

int NoteService::running_count() const {
    return state_->running.load();
}

void NoteService::worker_main(int slot) {
    for (;;) {
        Request request;
        {
            std::unique_lock lock(state_->mutex);
            state_->cv.wait(lock, [&] { return state_->stop || !state_->queue.empty(); });
            if (state_->stop && state_->queue.empty()) {
                return;
            }
            request = std::move(state_->queue.front());
            state_->queue.pop_front();
            state_->running.fetch_add(1);
        }
        const auto ticket = request.ticket;
        auto stop_requested = [&] {
            std::lock_guard lock(state_->mutex);
            return state_->stop;
        };
        auto finish = [&](Result result) {
            {
                std::lock_guard lock(state_->mutex);
                state_->results.push_back(std::move(result));
            }
            state_->running.fetch_sub(1);
            if (slot >= 0 && slot < state_->slot_count) {
                state_->slot_progress[slot].store(0.f);
            }
        };
        if (stop_requested()) {
            finish(Result{request.job, ticket, request.stem, true, {}, {}});
            continue;
        }
        if (slot >= 0 && slot < state_->slot_count) {
            state_->slot_progress[slot].store(0.f);
        }
        if (!request.refresh && std::filesystem::exists(request.cache_file)) {
            auto loaded = bt::load_notes(request.cache_file);
            if (loaded) {
                if (slot >= 0 && slot < state_->slot_count) {
                    state_->slot_progress[slot].store(1.f);
                }
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
        if (stop_requested()) {
            finish(Result{request.job, ticket, request.stem, true, {}, {}});
            continue;
        }
        auto progress = [&](float fraction) {
            if (slot >= 0 && slot < state_->slot_count) {
                state_->slot_progress[slot].store(fraction);
            }
        };
        std::expected<std::vector<bt::Note>, std::string> notes;
        if (transcribe_) {
            notes = transcribe_(
                audio->data(), static_cast<std::int64_t>(audio->size()), request.parameters, progress, stop_requested);
        } else {
            bt::BasicPitchModel* model = nullptr;
            {
                std::lock_guard lock(state_->mutex);
                if (slot >= 0 && slot < static_cast<int>(state_->models.size())) {
                    model = state_->models[static_cast<std::size_t>(slot)].get();
                }
            }
            if (model == nullptr) {
                finish(Result{request.job, ticket, request.stem, false, "transcription model missing", {}});
                continue;
            }
            notes = model->transcribe(
                audio->data(), static_cast<std::int64_t>(audio->size()), request.parameters, progress, stop_requested);
        }
        if (!notes) {
            const bool was_cancelled = notes.error() == "cancelled" || stop_requested();
            finish(Result{
                request.job, ticket, request.stem, was_cancelled, was_cancelled ? std::string{} : notes.error(), {}});
            continue;
        }
        if (stop_requested()) {
            finish(Result{request.job, ticket, request.stem, true, {}, {}});
            continue;
        }
        if (auto stored = bt::save_notes(request.cache_file, *notes); !stored) {
            finish(Result{request.job, ticket, request.stem, false, stored.error(), {}});
            continue;
        }
        progress(1.f);
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
            if (result.stem < 0 || result.stem >= bt::kStemCount) {
                continue;
            }
            const auto index = static_cast<std::size_t>(result.stem);
            if (result.ticket != app.note_ticket[index]) {
                continue;
            }
            app.note_refresh[index] = false;
            if (result.cancelled) {
                if (app.note_status[index] == NoteStatus::Running) {
                    app.note_status[index] = NoteStatus::Idle;
                }
                continue;
            }
            if (!result.error.empty()) {
                app.note_status[index] = NoteStatus::Failed;
                app.note_detail[index] = result.error;
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
            log_stem(
                LogLevel::Info, app.language, result.stem, "轉譜完成", "转谱完成", "Transcribed",
                " · " + std::to_string(result.notes.size()));
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

    const int capacity = app.note_service->workers();
    int inflight = 0;
    for (NoteStatus status : app.note_status) {
        if (status == NoteStatus::Running) {
            ++inflight;
        }
    }
    for (int stem = 0; stem < bt::kStemCount && inflight < capacity; ++stem) {
        const auto index = static_cast<std::size_t>(stem);
        if (app.note_refresh[index] && app.note_status[index] != NoteStatus::Running) {
            queue_notes(app, song, stem, true);
            ++inflight;
        }
    }
    for (int stem = 0; stem < bt::kStemCount && inflight < capacity; ++stem) {
        if (app.note_status[static_cast<std::size_t>(stem)] == NoteStatus::Idle) {
            queue_notes(app, song, stem, false);
            ++inflight;
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
