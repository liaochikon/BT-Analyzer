#include "notes.hpp"

#include "monitor.hpp"
#include "i18n.hpp"
#include "app_state.hpp"

#include "bt/basic_pitch.hpp"
#include "bt/contract.hpp"
#include "bt/note_score.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <condition_variable>
#include <cstdio>
#include <deque>
#include <mutex>
#include <stdexcept>
#include <thread>
#include <utility>

namespace btapp {
namespace {

void queue_notes(App& app, const bt::SongSnapshot& song, int stem, bool refresh) {
    if (stem < 0 || stem >= bt::kStemCount || song.stems == nullptr || song.cache_directory.empty() ||
        app.note_service == nullptr) {
        return;
    }
    const auto index = static_cast<std::size_t>(stem);
    app.note_status[index] = NoteStatus::Running;
    app.note_detail[index].clear();
    const auto cache_file = song.cache_directory / (std::string(bt::kStemNames[index]) + ".notes");
    // Per-stem policy: fixed decode defaults (UI no longer exposes BP knobs).
    app.note_ticket[index] = app.note_service->request(
        song.job, stem, cache_file, song.stems, bt::pitch_parameters_for_stem(stem), refresh);
    const char* tail = refresh ? t("log.rerun_tail") : "";
    log_stem(LogLevel::Info, stem, "log.transcribe_start", tail);
}

std::vector<float> stem_mono(const bt::Stems& stems, int stem) {
    std::vector<float> mono;
    if (stem < 0 || stem >= bt::kStemCount || stems.frames <= 0) {
        return mono;
    }
    const auto& audio = stems.audio[static_cast<std::size_t>(stem)];
    if (static_cast<std::int64_t>(audio.size()) < stems.frames * 2) {
        return mono;
    }
    mono.resize(static_cast<std::size_t>(stems.frames));
    const float* left = audio.data();
    const float* right = audio.data() + stems.frames;
    for (std::int64_t frame = 0; frame < stems.frames; ++frame) {
        mono[static_cast<std::size_t>(frame)] = 0.5f * (left[frame] + right[frame]);
    }
    return mono;
}

int clamp_workers(int workers) {
    return std::clamp(workers, 1, 8);
}

float notes_display_progress(const App& app) {
    if (app.note_service == nullptr) {
        return 0.f;
    }
    int running = 0;
    int running_stem = -1;
    for (int stem = 0; stem < bt::kStemCount; ++stem) {
        if (app.note_status[static_cast<std::size_t>(stem)] == NoteStatus::Running) {
            running_stem = stem;
            ++running;
        }
    }
    // One active job → show that stem's fraction (Auto / Rerun reads as 0–100%).
    if (running == 1) {
        return app.note_service->stem_progress(running_stem);
    }
    // Parallel wave → finished stems count as 1 so the bar does not reset per worker.
    float sum = 0.f;
    for (int stem = 0; stem < bt::kStemCount; ++stem) {
        const auto status = app.note_status[static_cast<std::size_t>(stem)];
        if (status == NoteStatus::Ready || status == NoteStatus::Failed) {
            sum += 1.f;
        } else if (status == NoteStatus::Running) {
            sum += app.note_service->stem_progress(stem);
        }
    }
    return sum / static_cast<float>(bt::kStemCount);
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
    std::array<std::atomic<float>, bt::kStemCount> stem_progress{};
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
    for (auto& value : state_->stem_progress) {
        value.store(0.f);
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
        state_->queue.push_back(Request{
            job, ticket, stem, std::move(cache_file), std::move(stems), parameters, refresh});
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

float NoteService::stem_progress(int stem) const {
    if (stem < 0 || stem >= bt::kStemCount) {
        return 0.f;
    }
    return std::clamp(state_->stem_progress[static_cast<std::size_t>(stem)].load(), 0.f, 1.f);
}

void NoteService::clear_stem_progress() {
    for (auto& value : state_->stem_progress) {
        value.store(0.f);
    }
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
        const int stem = request.stem;
        const bool stem_ok = stem >= 0 && stem < bt::kStemCount;
        if (stem_ok) {
            state_->stem_progress[static_cast<std::size_t>(stem)].store(0.f);
        }
        auto stop_requested = [&] {
            std::lock_guard lock(state_->mutex);
            return state_->stop;
        };
        auto set_progress = [&](float fraction) {
            const float value = std::clamp(fraction, 0.f, 1.f);
            if (slot >= 0 && slot < state_->slot_count) {
                state_->slot_progress[slot].store(value);
            }
            if (stem_ok) {
                state_->stem_progress[static_cast<std::size_t>(stem)].store(value);
            }
        };
        auto finish = [&](Result result) {
            if (stem_ok) {
                const float done = (!result.cancelled && result.error.empty()) ? 1.f : 0.f;
                state_->stem_progress[static_cast<std::size_t>(stem)].store(done);
            }
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
            finish(Result{request.job, ticket, request.stem, true, {}, {}, {}});
            continue;
        }
        set_progress(0.f);
        if (request.stems == nullptr || request.stem < 0 || request.stem >= bt::kStemCount) {
            finish(Result{request.job, ticket, request.stem, false, "stem audio is incomplete", {}, {}});
            continue;
        }
        // Drums: never run BP — always empty MIDI (rewrite cache so old drum pitches disappear).
        if (bt::stem_skips_pitch(request.stem)) {
            Result result{request.job, ticket, request.stem, false, {}, {}, request.parameters};
            if (auto stored = bt::save_notes(request.cache_file, result.notes); !stored) {
                finish(Result{
                    request.job, ticket, request.stem, false, stored.error(), {}, {}});
                continue;
            }
            set_progress(1.f);
            finish(std::move(result));
            continue;
        }
        if (!request.refresh && std::filesystem::exists(request.cache_file)) {
            auto loaded = bt::load_notes(request.cache_file);
            if (loaded) {
                set_progress(1.f);
                finish(Result{request.job, ticket, request.stem, false, {}, std::move(*loaded), request.parameters});
                continue;
            }
        }
        auto audio = bt::stem_to_basic_pitch_audio(
            request.stems->audio[static_cast<std::size_t>(request.stem)], request.stems->frames);
        if (!audio) {
            finish(Result{request.job, ticket, request.stem, false, audio.error(), {}, {}});
            continue;
        }
        if (stop_requested()) {
            finish(Result{request.job, ticket, request.stem, true, {}, {}, {}});
            continue;
        }
        auto progress = [&](float fraction) { set_progress(fraction); };

        Result result{request.job, ticket, request.stem, false, {}, {}, request.parameters};
        std::expected<std::vector<bt::Note>, std::string> notes;
        if (transcribe_) {
            notes = transcribe_(
                audio->data(), static_cast<std::int64_t>(audio->size()), request.parameters, progress,
                stop_requested);
        } else {
            bt::BasicPitchModel* model = nullptr;
            {
                std::lock_guard lock(state_->mutex);
                if (slot >= 0 && slot < static_cast<int>(state_->models.size())) {
                    model = state_->models[static_cast<std::size_t>(slot)].get();
                }
            }
            if (model == nullptr) {
                finish(Result{
                    request.job, ticket, request.stem, false, "transcription model missing", {}, {}});
                continue;
            }
            notes = model->transcribe(
                audio->data(), static_cast<std::int64_t>(audio->size()), request.parameters, progress,
                stop_requested);
        }
        if (!notes) {
            const bool was_cancelled = notes.error() == "cancelled" || stop_requested();
            finish(Result{
                request.job, ticket, request.stem, was_cancelled,
                was_cancelled ? std::string{} : notes.error(), {}, {}});
            continue;
        }
        // BP proposes; default energy mask (fft2048) subtracts unsupported notes.
        const auto mono = stem_mono(*request.stems, request.stem);
        result.notes = bt::mask_notes_by_midi_energy(
            notes->data(), notes->size(), mono.data(), static_cast<std::int64_t>(mono.size()),
            bt::kSampleRate, bt::energy_mask_default());
        if (stop_requested()) {
            finish(Result{request.job, ticket, request.stem, true, {}, {}, {}});
            continue;
        }
        if (auto stored = bt::save_notes(request.cache_file, result.notes); !stored) {
            finish(Result{
                request.job, ticket, request.stem, false, stored.error(), {}, {}});
            continue;
        }
        progress(1.f);
        finish(std::move(result));
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
        if (app.note_service != nullptr) {
            app.note_service->clear_stem_progress();
        }
    }
    if (app.note_service != nullptr) {
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
                log_stem(LogLevel::Error, result.stem, "log.transcribe_failed", " · " + result.error);
                continue;
            }
            app.notes[index].clear();
            app.notes[index].reserve(result.notes.size());
            for (const bt::Note& note : result.notes) {
                app.notes[index].push_back(note);
            }
            app.note_status[index] = NoteStatus::Ready;
            app.note_detail[index].clear();
            log_stem(
                LogLevel::Info, result.stem, "log.transcribe_done",
                " · " + std::to_string(result.notes.size()));
        }
        app.note_progress = notes_display_progress(app);
    }
    if (song.phase != bt::SongPhase::Ready || song.stems == nullptr || song.job == app.closed_job) {
        return;
    }
    if (!app.pitch_ready || app.note_service == nullptr) {
        const std::string missing = app.pitch_note.empty() ? "models/nmp.onnx" : stored(app.pitch_note);
        for (int stem = 0; stem < bt::kStemCount; ++stem) {
            const auto index = static_cast<std::size_t>(stem);
            if (app.note_status[index] != NoteStatus::Idle && !app.note_refresh[index]) {
                continue;
            }
            app.note_status[index] = NoteStatus::Failed;
            app.note_detail[index] = missing;
            app.note_refresh[index] = false;
            log_stem(LogLevel::Error, stem, "log.transcribe_failed", " · " + missing);
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
