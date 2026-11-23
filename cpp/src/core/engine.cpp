#include "bt/core/engine.hpp"

#include "bt/beat_track.hpp"
#include "bt/contract.hpp"
#include "bt/log.hpp"

#include <algorithm>
#include <stdexcept>
#include <string>
#include <utility>

namespace bt::core {
namespace {

std::expected<void, std::string> ensure_ready(const SongSnapshot& song) {
    if (song.phase != SongPhase::Ready || song.stems == nullptr) {
        return std::unexpected("song is not ready");
    }
    return {};
}

}  // namespace

std::expected<std::unique_ptr<Engine>, std::string> Engine::create(EngineOptions options) {
    auto session = Session::create(std::move(options.session));
    if (!session) {
        return std::unexpected(session.error());
    }
    return std::unique_ptr<Engine>(new Engine(std::move(*session), std::move(options.pitch_model)));
}

Engine::Engine(std::unique_ptr<Session> session, std::filesystem::path pitch_model)
    : session_(std::move(session)), pitch_model_(std::move(pitch_model)) {}

Engine::~Engine() = default;

Session& Engine::session() {
    return *session_;
}

const Session& Engine::session() const {
    return *session_;
}

std::uint64_t Engine::import_audio(std::filesystem::path source, bool refresh) {
    return session_->open_song(std::move(source), refresh);
}

std::uint64_t Engine::open_cache(std::filesystem::path directory) {
    return session_->open_cache(std::move(directory));
}

void Engine::close() {
    session_->close_song();
}

void Engine::cancel() {
    session_->cancel();
}

std::shared_ptr<const SongSnapshot> Engine::snapshot() const {
    return session_->snapshot();
}

std::shared_ptr<const SongSnapshot> Engine::wait(std::uint64_t job) const {
    return session_->wait(job);
}

std::shared_ptr<const SongSnapshot> Engine::wait_for(std::uint64_t job, std::chrono::milliseconds timeout) const {
    return session_->wait_for(job, timeout);
}

std::uint64_t Engine::set_gain(int stem, float gain) {
    return session_->set_gain(stem, gain);
}

std::uint64_t Engine::set_pan(int stem, float pan) {
    return session_->set_pan(stem, pan);
}

std::uint64_t Engine::set_mute(int stem, bool mute) {
    return session_->set_mute(stem, mute);
}

std::uint64_t Engine::set_solo(int stem, bool solo) {
    return session_->set_solo(stem, solo);
}

std::expected<std::vector<Note>, std::string> Engine::transcribe_stem(
    int stem, std::optional<PitchParameters> parameters) {
    if (stem < 0 || stem >= kStemCount) {
        return std::unexpected("stem out of range");
    }
    if (stem_skips_pitch(stem)) {
        return std::vector<Note>{};
    }
    const auto song = snapshot();
    if (song == nullptr) {
        return std::unexpected("no song");
    }
    if (auto ready = ensure_ready(*song); !ready) {
        return std::unexpected(ready.error());
    }
    const PitchParameters params = parameters.has_value() ? *parameters : pitch_parameters_for_stem(stem);
    if (pitch_ == nullptr) {
        if (pitch_model_.empty()) {
            return std::unexpected("pitch model path is empty");
        }
        auto opened = BasicPitchModel::open(pitch_model_);
        if (!opened) {
            return std::unexpected(opened.error());
        }
        pitch_ = std::make_unique<BasicPitchModel>(std::move(*opened));
    }
    const Stems& stems = *song->stems;
    const auto& channel_major = stems.audio[static_cast<std::size_t>(stem)];
    auto mono = stem_to_basic_pitch_audio(channel_major, stems.frames);
    if (!mono) {
        return std::unexpected(mono.error());
    }
    bt::log(bt::LogLevel::Info, std::string("transcribe · ") + std::string(kStemNames[static_cast<std::size_t>(stem)]));
    auto notes = pitch_->transcribe(mono->data(), static_cast<std::int64_t>(mono->size()), params);
    if (!notes) {
        bt::log(bt::LogLevel::Error, std::string("transcribe failed · ") + notes.error());
        return notes;
    }
    (*notes_)[static_cast<std::size_t>(stem)] = *notes;
    bt::log(
        bt::LogLevel::Info,
        std::string("transcribed · ") + std::string(kStemNames[static_cast<std::size_t>(stem)]) + " · " +
            std::to_string(notes->size()));
    return notes;
}

std::expected<void, std::string> Engine::save_notes(const std::filesystem::path& path, const std::vector<Note>& notes) {
    auto stored = bt::save_notes(path, notes);
    if (!stored) {
        bt::log(bt::LogLevel::Error, std::string("notes save failed · ") + stored.error());
    } else {
        bt::log(bt::LogLevel::Info, std::string("notes saved · ") + path.filename().string());
    }
    return stored;
}

void Engine::share_notes(std::array<std::vector<Note>, bt::kStemCount>* external) {
    notes_ = external != nullptr ? external : &notes_owned_;
}

const std::array<std::vector<Note>, bt::kStemCount>& Engine::notes() const {
    return *notes_;
}

std::array<std::vector<Note>, bt::kStemCount>& Engine::notes() {
    return *notes_;
}

const std::vector<Note>& Engine::notes(int stem) const {
    if (stem < 0 || stem >= bt::kStemCount) {
        throw std::out_of_range("stem out of range");
    }
    return (*notes_)[static_cast<std::size_t>(stem)];
}

std::vector<Note>& Engine::notes(int stem) {
    if (stem < 0 || stem >= bt::kStemCount) {
        throw std::out_of_range("stem out of range");
    }
    return (*notes_)[static_cast<std::size_t>(stem)];
}

const std::array<TabScore, bt::kStemCount>& Engine::scores() const {
    return scores_;
}

std::array<TabScore, bt::kStemCount>& Engine::scores() {
    return scores_;
}

const TabScore& Engine::score(int stem) const {
    if (stem < 0 || stem >= bt::kStemCount) {
        throw std::out_of_range("stem out of range");
    }
    return scores_[static_cast<std::size_t>(stem)];
}

TabScore& Engine::score(int stem) {
    if (stem < 0 || stem >= bt::kStemCount) {
        throw std::out_of_range("stem out of range");
    }
    return scores_[static_cast<std::size_t>(stem)];
}

const bt::BeatGrid& Engine::beats() const {
    return beats_;
}

bt::BeatGrid& Engine::beats() {
    return beats_;
}

const FingeringParams& Engine::fingering() const {
    return fingering_;
}

FingeringParams& Engine::fingering() {
    return fingering_;
}

BeatDivision Engine::grid_division() const {
    return grid_division_;
}

BeatDivision& Engine::grid_division() {
    return grid_division_;
}

TabWorkspace Engine::workspace() {
    TabWorkspace workspace;
    workspace.notes = notes_;
    workspace.scores = &scores_;
    workspace.beats = beats_;
    workspace.grid_division = grid_division_;
    workspace.fingering = fingering_;
    const auto song = snapshot();
    if (song != nullptr && song->mix != nullptr && song->mix->sample_rate > 0) {
        workspace.length = static_cast<double>(song->mix->frames) / static_cast<double>(song->mix->sample_rate);
    }
    return workspace;
}

bool Engine::track_beats() {
    beats_ = {};
    const auto song = snapshot();
    if (song == nullptr || song->mix == nullptr || song->mix->frames <= 0 || song->mix->samples.empty()) {
        return false;
    }
    const bt::Audio& mix = *song->mix;
    const int rate = mix.sample_rate > 0 ? mix.sample_rate : bt::kSampleRate;
    const double length = static_cast<double>(mix.frames) / static_cast<double>(rate);
    std::vector<float> mono(static_cast<std::size_t>(mix.frames), 0.f);
    const auto frames = static_cast<std::size_t>(mix.frames);
    if (mix.channels <= 1) {
        const auto count = std::min(frames, mix.samples.size());
        std::copy_n(mix.samples.begin(), count, mono.begin());
    } else {
        const bool have_right = mix.samples.size() >= frames * 2;
        const float* left = mix.samples.data();
        const float* right = have_right ? left + mix.frames : left;
        for (std::int64_t i = 0; i < mix.frames; ++i) {
            mono[static_cast<std::size_t>(i)] = 0.5f * (left[i] + right[i]);
        }
    }
    auto grid = bt::track_beats(mono.data(), mix.frames, rate);
    if (grid) {
        bt::regularize_beat_grid(*grid, length);
        beats_ = std::move(*grid);
        if (!beats_.times.empty()) {
            return true;
        }
    }
    beats_ = {};
    beats_.bpm = 120.f;
    beats_.offset = 0;
    constexpr double step = 60.0 / 120.0;
    for (double time = 0; time < length - 1e-9 && beats_.times.size() < 100000; time += step) {
        beats_.times.push_back(time);
    }
    if (beats_.times.empty()) {
        beats_.times.push_back(0.0);
    }
    return !beats_.times.empty();
}

bool Engine::quantize(int stem, const std::vector<char>& selected) {
    if (stem < 0 || stem >= bt::kStemCount) {
        return false;
    }
    auto workspace = this->workspace();
    const bool ok = quantize_notes(workspace, stem, selected);
    fingering_ = workspace.fingering;
    if (ok) {
        bt::log(bt::LogLevel::Info, std::string("quantize · ") + std::string(kStemNames[static_cast<std::size_t>(stem)]));
    }
    return ok;
}

bool Engine::resolve_tab(int stem) {
    if (stem < 0 || stem >= bt::kStemCount) {
        return false;
    }
    auto workspace = this->workspace();
    const bool ok = resolve_fingerings(workspace, stem);
    fingering_ = workspace.fingering;
    if (ok) {
        bt::log(bt::LogLevel::Info, std::string("tab resolve · ") + std::string(kStemNames[static_cast<std::size_t>(stem)]));
    }
    return ok;
}

bool Engine::build_tab(int stem, bool on_tab_only) {
    if (stem < 0 || stem >= bt::kStemCount) {
        return false;
    }
    auto workspace = this->workspace();
    const bool ok = build_tab_score(workspace, stem, on_tab_only);
    fingering_ = workspace.fingering;
    return ok;
}

bool Engine::convert_to_tab(int stem) {
    if (stem < 0 || stem >= bt::kStemCount) {
        return false;
    }
    auto workspace = this->workspace();
    const bool ok = convert_notes_to_tab(workspace, stem, {});
    fingering_ = workspace.fingering;
    if (ok) {
        bt::log(bt::LogLevel::Info, std::string("tab convert · ") + std::string(kStemNames[static_cast<std::size_t>(stem)]));
    }
    return ok;
}

std::expected<void, std::string> Engine::export_tab(int stem, const std::filesystem::path& path) {
    if (stem < 0 || stem >= bt::kStemCount) {
        return std::unexpected("Invalid stem");
    }
    const auto workspace = this->workspace();
    const TabExportResult result = export_tab_text(workspace, stem, path);
    if (!result.ok) {
        const std::string error =
            result.error.empty() ? std::string("Could not write TAB file") : result.error;
        bt::log(bt::LogLevel::Error, std::string("tab export failed · ") + error);
        return std::unexpected(error);
    }
    bt::log(bt::LogLevel::Info, std::string("tab exported · ") + path.filename().string());
    return {};
}

}  // namespace bt::core
