#pragma once

#include "bt/beat_track.hpp"

#include <cstdint>
#include <filesystem>
#include <memory>
#include <string>
#include <vector>

namespace bt {
struct SongSnapshot;
}

namespace btapp {

struct App;

class BeatService {
public:
    BeatService();
    ~BeatService();

    BeatService(const BeatService&) = delete;
    BeatService& operator=(const BeatService&) = delete;

    std::uint64_t request(
        std::uint64_t job, std::filesystem::path cache_file, std::shared_ptr<const bt::SongSnapshot> song, bool refresh = false);

    struct Result {
        std::uint64_t job = 0;
        std::uint64_t ticket = 0;
        bool cancelled = false;
        std::string error;
        bt::BeatGrid grid;
    };

    std::vector<Result> poll();
    std::size_t parked_results();

private:
    struct Request {
        std::uint64_t job = 0;
        std::uint64_t ticket = 0;
        std::filesystem::path cache_file;
        std::shared_ptr<const bt::SongSnapshot> song;
        bool refresh = false;
    };

    void thread_main();

    struct State;
    std::unique_ptr<State> state_;
};

void sync_beats(App& app, const std::shared_ptr<const bt::SongSnapshot>& song);
void rerun_beats(App& app);

}  // namespace btapp
