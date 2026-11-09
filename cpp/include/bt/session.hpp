#pragma once

#include "bt/separator.hpp"

#include <array>
#include <chrono>
#include <cstdint>
#include <expected>
#include <filesystem>
#include <memory>
#include <string>

namespace bt {

enum class SongPhase { Empty, Decoding, Separating, Ready, Cancelled, Failed };

inline bool song_settled(SongPhase phase) {
    return phase == SongPhase::Ready || phase == SongPhase::Cancelled || phase == SongPhase::Failed;
}

// Immutable view of the open song. Copies share the mix and stem buffers.
struct SongSnapshot {
    std::uint64_t job = 0;
    std::uint64_t revision = 0;
    SongPhase phase = SongPhase::Empty;
    float progress = 0.f;
    bool from_cache = false;
    std::string detail;
    std::filesystem::path source;
    std::filesystem::path cache_directory;
    std::string project_hash;
    std::string project_name;
    std::shared_ptr<const Audio> mix;
    std::shared_ptr<const Stems> stems;
    std::array<float, kStemCount> gain{};
    std::array<float, kStemCount> pan{};
    std::array<bool, kStemCount> mute{};
    std::array<bool, kStemCount> solo{};
};

struct SessionOptions {
    std::filesystem::path cache_directory;
    std::string model_key = std::string(kModelKey);
    SegmentRunner run_segment;
    SeparateConfig separate;
};

// Owns the song. Callers post commands; the session thread is the only writer of song state.
// Cancel takes effect before the next segment. A finished cache entry is
// cache_directory / model_key / {name}_{sha256} / song.txt + wavs.
// An empty model key stores projects directly in cache_directory.
// The folder name is the sha256 when the display name cannot be encoded.
class Session {
public:
    static std::expected<std::unique_ptr<Session>, std::string> create(SessionOptions options);

    Session(const Session&) = delete;
    Session& operator=(const Session&) = delete;
    Session(Session&&) = delete;
    Session& operator=(Session&&) = delete;
    ~Session();

    std::uint64_t open_song(std::filesystem::path source);
    std::uint64_t open_cache(std::filesystem::path directory);
    void close_song();
    void relocate_cache(std::filesystem::path directory, std::string name);
    void cancel();
    std::uint64_t set_gain(int stem, float gain);
    std::uint64_t set_pan(int stem, float pan);
    std::uint64_t set_mute(int stem, bool mute);
    std::uint64_t set_solo(int stem, bool solo);

    std::shared_ptr<const SongSnapshot> snapshot() const;
    std::shared_ptr<const SongSnapshot> wait(std::uint64_t job) const;
    std::shared_ptr<const SongSnapshot> wait_for(std::uint64_t job, std::chrono::milliseconds timeout) const;
    bool wait_for_revision(std::uint64_t revision, std::chrono::milliseconds timeout) const;

private:
    struct Command;

    explicit Session(SessionOptions options);
    void start();
    void enqueue(Command command);
    void thread_main();
    void run_open(const Command& command);
    void run_open_cache(const Command& command);
    void run_close();
    void run_relocate(const Command& command);
    void apply_mixer(const Command& command);
    void drain_mixer();
    void note_cancel(const Command& command);
    std::uint64_t issue_revision();
    std::shared_ptr<SongSnapshot> blank(SongPhase phase, float progress, bool from_cache, std::string detail) const;
    void publish(std::shared_ptr<SongSnapshot> next);

    struct CacheHit {
        std::shared_ptr<const Stems> stems;
    };
    std::expected<std::shared_ptr<CacheHit>, std::string> load_cache(const std::filesystem::path& directory) const;
    std::expected<void, std::string> store_cache(const std::filesystem::path& directory, const Stems& stems) const;

    SessionOptions options_;
    struct State;
    std::unique_ptr<State> state_;
};

}  // namespace bt
