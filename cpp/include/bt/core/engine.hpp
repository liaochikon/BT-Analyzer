#pragma once

#include "bt/basic_pitch.hpp"
#include "bt/core/tab.hpp"
#include "bt/session.hpp"

#include <array>
#include <chrono>
#include <cstdint>
#include <expected>
#include <filesystem>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace bt::core {

// Headless orchestration for agents / CLI / tests. No UI, no audio device required.
struct EngineOptions {
    SessionOptions session;
    // Optional Basic Pitch ONNX. Empty → transcribe_stem returns an error until set.
    std::filesystem::path pitch_model;
};

class Engine {
public:
    static std::expected<std::unique_ptr<Engine>, std::string> create(EngineOptions options);

    Engine(const Engine&) = delete;
    Engine& operator=(const Engine&) = delete;
    Engine(Engine&&) = delete;
    Engine& operator=(Engine&&) = delete;
    ~Engine();

    Session& session();
    const Session& session() const;

    // Same job id semantics as Session::open_song / open_cache.
    std::uint64_t import_audio(std::filesystem::path source, bool refresh = false);
    std::uint64_t open_cache(std::filesystem::path directory);
    void close();
    void cancel();

    std::shared_ptr<const SongSnapshot> snapshot() const;
    std::shared_ptr<const SongSnapshot> wait(std::uint64_t job) const;
    std::shared_ptr<const SongSnapshot> wait_for(std::uint64_t job, std::chrono::milliseconds timeout) const;

    std::uint64_t set_gain(int stem, float gain);
    std::uint64_t set_pan(int stem, float pan);
    std::uint64_t set_mute(int stem, bool mute);
    std::uint64_t set_solo(int stem, bool solo);

    // Requires SongPhase::Ready and a loaded pitch model. Stem 0 (drums) returns {}.
    // Nullopt parameters → pitch_parameters_for_stem(stem).
    std::expected<std::vector<Note>, std::string> transcribe_stem(
        int stem, std::optional<PitchParameters> parameters = std::nullopt);

    // Writes notes next to the open project cache when possible; otherwise to `path`.
    std::expected<void, std::string> save_notes(const std::filesystem::path& path, const std::vector<Note>& notes);

    // Notes written by transcribe_stem, plus TAB score / beat / fingering state for agents.
    // When share_notes() is set, accessors use that array (GUI App.notes) instead of the owned buffer.
    void share_notes(std::array<std::vector<Note>, bt::kStemCount>* external);
    const std::array<std::vector<Note>, bt::kStemCount>& notes() const;
    std::array<std::vector<Note>, bt::kStemCount>& notes();
    const std::vector<Note>& notes(int stem) const;
    std::vector<Note>& notes(int stem);
    const std::array<TabScore, bt::kStemCount>& scores() const;
    std::array<TabScore, bt::kStemCount>& scores();
    const TabScore& score(int stem) const;
    TabScore& score(int stem);
    const bt::BeatGrid& beats() const;
    bt::BeatGrid& beats();
    const FingeringParams& fingering() const;
    FingeringParams& fingering();
    BeatDivision grid_division() const;
    BeatDivision& grid_division();

    // Track the mix. On failure, install a 120 BPM lattice over the song length when audio exists.
    bool track_beats();
    // Empty `selected` quantizes every note of the stem.
    bool quantize(int stem, const std::vector<char>& selected = {});
    bool resolve_tab(int stem);
    bool build_tab(int stem, bool on_tab_only = false);
    bool convert_to_tab(int stem);
    std::expected<void, std::string> export_tab(int stem, const std::filesystem::path& path);

private:
    explicit Engine(std::unique_ptr<Session> session, std::filesystem::path pitch_model);
    TabWorkspace workspace();

    std::unique_ptr<Session> session_;
    std::filesystem::path pitch_model_;
    mutable std::unique_ptr<BasicPitchModel> pitch_;
    std::array<std::vector<Note>, bt::kStemCount> notes_owned_{};
    std::array<std::vector<Note>, bt::kStemCount>* notes_ = &notes_owned_;
    std::array<TabScore, bt::kStemCount> scores_{};
    bt::BeatGrid beats_{};
    BeatDivision grid_division_ = BeatDivision::Beat;
    FingeringParams fingering_{};
};

}  // namespace bt::core
