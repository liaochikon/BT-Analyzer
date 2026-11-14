#pragma once

#include "bt/basic_pitch.hpp"

#include <atomic>
#include <cstdint>
#include <expected>
#include <filesystem>
#include <functional>
#include <memory>
#include <string>
#include <vector>

namespace bt {
struct SongSnapshot;
}

namespace btapp {

struct App;

using Transcriber = std::function<std::expected<std::vector<bt::Note>, std::string>(
    const float* samples, std::int64_t frames, const bt::PitchParameters& parameters,
    const std::function<void(float)>& progress, const std::function<bool()>& cancelled)>;

class NoteService {
public:
    // Opens one Basic Pitch session per worker. workers is clamped to [1, 8].
    explicit NoteService(const std::filesystem::path& model_path, int workers);
    // Test helper: one worker around a callable transcriber.
    explicit NoteService(Transcriber transcriber);
    ~NoteService();

    NoteService(const NoteService&) = delete;
    NoteService& operator=(const NoteService&) = delete;

    int workers() const;
    // Rebuilds the pool when idle. Returns false if a job is still running.
    bool set_workers(int workers);

    std::uint64_t request(
        std::uint64_t job, int stem, std::filesystem::path cache_file, std::shared_ptr<const bt::Stems> stems,
        bt::PitchParameters parameters, bool refresh);

    struct Result {
        std::uint64_t job = 0;
        std::uint64_t ticket = 0;
        int stem = 0;
        bool cancelled = false;
        std::string error;
        std::vector<bt::Note> notes;
        bt::PitchParameters parameters;
    };

    std::vector<Result> poll();
    std::size_t parked_results();
    // Per-stem job fraction in [0, 1]. Idle / unknown stems report 0.
    float stem_progress(int stem) const;
    void clear_stem_progress();
    int running_count() const;

private:
    struct Request {
        std::uint64_t job = 0;
        std::uint64_t ticket = 0;
        int stem = 0;
        std::filesystem::path cache_file;
        std::shared_ptr<const bt::Stems> stems;
        bt::PitchParameters parameters;
        bool refresh = false;
    };

    void worker_main(int slot);
    void launch_workers(int workers);
    bool rebuild_locked(int workers);

    std::filesystem::path model_path_;
    Transcriber transcribe_;
    struct State;
    std::unique_ptr<State> state_;
};

void sync_notes(App& app, const bt::SongSnapshot& song);
void rerun_notes(App& app, int stem);

}  // namespace btapp
