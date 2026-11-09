#include "bt/session.hpp"

#include "bt/project.hpp"

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <bcrypt.h>

#include <algorithm>
#include <atomic>
#include <cmath>
#include <condition_variable>
#include <deque>
#include <exception>
#include <fstream>
#include <mutex>
#include <thread>
#include <unordered_map>
#include <utility>

namespace bt {
namespace {

std::string utf8_of(const std::filesystem::path& path) {
    try {
        const std::u8string utf8 = path.u8string();
        return std::string(reinterpret_cast<const char*>(utf8.data()), utf8.size());
    } catch (const std::exception&) {
        return "path";
    }
}

std::string hex_encode(const unsigned char* data, std::size_t size) {
    static constexpr char kDigits[] = "0123456789abcdef";
    std::string text(size * 2, '0');
    for (std::size_t i = 0; i < size; ++i) {
        text[i * 2] = kDigits[data[i] >> 4];
        text[i * 2 + 1] = kDigits[data[i] & 0x0f];
    }
    return text;
}

std::expected<std::string, std::string> sha256_file(const std::filesystem::path& path) {
    BCRYPT_ALG_HANDLE algorithm = nullptr;
    if (BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) != 0) {
        return std::unexpected("sha256 is unavailable");
    }
    const struct CloseAlgorithm {
        BCRYPT_ALG_HANDLE handle;
        ~CloseAlgorithm() {
            if (handle != nullptr) {
                BCryptCloseAlgorithmProvider(handle, 0);
            }
        }
    } close_algorithm{algorithm};

    DWORD object_size = 0;
    DWORD hash_size = 0;
    DWORD ignored = 0;
    if (BCryptGetProperty(
            algorithm, BCRYPT_OBJECT_LENGTH, reinterpret_cast<PUCHAR>(&object_size), sizeof(object_size),
            &ignored, 0) != 0 ||
        BCryptGetProperty(
            algorithm, BCRYPT_HASH_LENGTH, reinterpret_cast<PUCHAR>(&hash_size), sizeof(hash_size), &ignored,
            0) != 0) {
        return std::unexpected("sha256 is unavailable");
    }

    std::vector<UCHAR> object(object_size);
    std::vector<UCHAR> digest(hash_size);
    BCRYPT_HASH_HANDLE hash = nullptr;
    if (BCryptCreateHash(algorithm, &hash, object.data(), object_size, nullptr, 0, 0) != 0) {
        return std::unexpected("sha256 is unavailable");
    }
    const struct CloseHash {
        BCRYPT_HASH_HANDLE handle;
        ~CloseHash() {
            if (handle != nullptr) {
                BCryptDestroyHash(handle);
            }
        }
    } close_hash{hash};

    std::ifstream input(path, std::ios::binary);
    if (!input) {
        return std::unexpected("cannot open " + utf8_of(path));
    }
    std::vector<char> buffer(1 << 20);
    while (input) {
        input.read(buffer.data(), static_cast<std::streamsize>(buffer.size()));
        const auto count = input.gcount();
        if (count > 0 &&
            BCryptHashData(hash, reinterpret_cast<PUCHAR>(buffer.data()), static_cast<ULONG>(count), 0) != 0) {
            return std::unexpected("sha256 failed");
        }
    }

    if (BCryptFinishHash(hash, digest.data(), static_cast<ULONG>(digest.size()), 0) != 0) {
        return std::unexpected("sha256 failed");
    }
    return hex_encode(digest.data(), digest.size());
}

bool valid_model_key(const std::string& key) {
    return !key.empty() && key != "." && key != ".." && key.find_first_of("\\/:") == std::string::npos;
}

std::expected<std::shared_ptr<const Stems>, std::string> read_cached_stems(const std::filesystem::path& directory) {
    auto stems = std::make_shared<Stems>();
    for (int stem = 0; stem < kStemCount; ++stem) {
        const auto name = std::string(kStemNames[static_cast<std::size_t>(stem)]);
        auto audio = read_float_wav(directory / (name + ".wav"));
        if (!audio) {
            return std::unexpected(audio.error());
        }
        if (audio->sample_rate != kSampleRate || audio->channels != 2 || audio->frames <= 0) {
            return std::unexpected("cached " + name + " is not 44100 Hz stereo");
        }
        if (stem == 0) {
            stems->frames = audio->frames;
        } else if (audio->frames != stems->frames) {
            return std::unexpected("cached " + name + " length does not match drums");
        }
        stems->audio[static_cast<std::size_t>(stem)] = std::move(audio->samples);
    }
    return stems;
}

}  // namespace

struct Session::Command {
    enum class Kind { Open, OpenCache, Close, Relocate, Cancel, Gain, Pan, Mute, Solo, Shutdown };

    Kind kind = Kind::Shutdown;
    std::uint64_t job = 0;
    std::uint64_t revision = 0;
    std::filesystem::path path;
    std::string label;
    int stem = 0;
    float gain = 1.f;
    bool flag = false;
};

struct Session::State {
    std::mutex queue_mu;
    std::condition_variable queue_cv;
    std::deque<Command> queue;
    mutable std::mutex publish_mu;
    mutable std::condition_variable publish_cv;
    std::shared_ptr<const SongSnapshot> snapshot;
    std::unordered_map<std::uint64_t, std::shared_ptr<const SongSnapshot>> finished;
    std::atomic<std::uint64_t> live_job{0};
    std::atomic<std::uint64_t> next_job{0};
    std::atomic<std::uint64_t> next_revision{0};
    std::array<float, kStemCount> gain{};
    std::array<float, kStemCount> pan{};
    std::array<bool, kStemCount> mute{};
    std::array<bool, kStemCount> solo{};
    std::uint64_t revision = 0;
    std::uint64_t job = 0;
    std::filesystem::path source;
    std::filesystem::path cache_directory;
    std::string project_hash;
    std::string project_name;
    std::shared_ptr<const Audio> mix;
    std::shared_ptr<const Stems> stems;
    std::thread worker;
};

Session::Session(SessionOptions options) : options_(std::move(options)), state_(std::make_unique<State>()) {
    state_->gain.fill(1.f);
    auto initial = std::make_shared<SongSnapshot>();
    initial->gain = state_->gain;
    initial->pan = state_->pan;
    state_->snapshot = std::move(initial);
}

void Session::start() {
    state_->worker = std::thread([this] { thread_main(); });
}

Session::~Session() {
    if (state_ == nullptr) {
        return;
    }
    state_->live_job.store(0);
    if (state_->worker.joinable()) {
        enqueue(Command{.kind = Command::Kind::Shutdown});
        state_->worker.join();
    }
}

std::expected<std::unique_ptr<Session>, std::string> Session::create(SessionOptions options) {
    if (options.cache_directory.empty()) {
        return std::unexpected("cache directory is empty");
    }
    if (!options.model_key.empty() && !valid_model_key(options.model_key)) {
        return std::unexpected("model key must be a single directory name");
    }
    if (!options.run_segment && !options.separate_mix) {
        return std::unexpected("segment runner is empty");
    }
    std::error_code error;
    std::filesystem::create_directories(options.cache_directory, error);
    if (error) {
        return std::unexpected("cannot create " + utf8_of(options.cache_directory));
    }

    auto session = std::unique_ptr<Session>(new Session(std::move(options)));
    session->start();
    return session;
}

void Session::enqueue(Command command) {
    {
        std::lock_guard lock(state_->queue_mu);
        state_->queue.push_back(std::move(command));
    }
    state_->queue_cv.notify_one();
}

std::uint64_t Session::issue_revision() {
    return state_->next_revision.fetch_add(1) + 1;
}

std::uint64_t Session::open_song(std::filesystem::path source) {
    const auto job = state_->next_job.fetch_add(1) + 1;
    state_->live_job.store(job);
    enqueue(Command{.kind = Command::Kind::Open, .job = job, .path = std::move(source)});
    return job;
}

std::uint64_t Session::open_cache(std::filesystem::path directory) {
    const auto job = state_->next_job.fetch_add(1) + 1;
    state_->live_job.store(job);
    enqueue(Command{.kind = Command::Kind::OpenCache, .job = job, .path = std::move(directory)});
    return job;
}

void Session::close_song() {
    state_->live_job.store(0);
    enqueue(Command{.kind = Command::Kind::Close});
}

void Session::relocate_cache(std::filesystem::path directory, std::string name) {
    enqueue(Command{.kind = Command::Kind::Relocate, .path = std::move(directory), .label = std::move(name)});
}

void Session::cancel() {
    const auto job = state_->live_job.exchange(0);
    if (job != 0) {
        enqueue(Command{.kind = Command::Kind::Cancel, .job = job});
    }
}

std::uint64_t Session::set_gain(int stem, float gain) {
    const auto revision = issue_revision();
    enqueue(Command{.kind = Command::Kind::Gain, .revision = revision, .stem = stem, .gain = gain});
    return revision;
}

std::uint64_t Session::set_pan(int stem, float pan) {
    const auto revision = issue_revision();
    enqueue(Command{
        .kind = Command::Kind::Pan,
        .revision = revision,
        .stem = stem,
        .gain = std::isfinite(pan) ? std::clamp(pan, -1.f, 1.f) : 0.f,
    });
    return revision;
}

std::uint64_t Session::set_mute(int stem, bool mute) {
    const auto revision = issue_revision();
    enqueue(Command{.kind = Command::Kind::Mute, .revision = revision, .stem = stem, .flag = mute});
    return revision;
}

std::uint64_t Session::set_solo(int stem, bool solo) {
    const auto revision = issue_revision();
    enqueue(Command{.kind = Command::Kind::Solo, .revision = revision, .stem = stem, .flag = solo});
    return revision;
}

std::shared_ptr<const SongSnapshot> Session::snapshot() const {
    std::lock_guard lock(state_->publish_mu);
    return state_->snapshot;
}

std::shared_ptr<const SongSnapshot> Session::wait(std::uint64_t job) const {
    std::unique_lock lock(state_->publish_mu);
    state_->publish_cv.wait(lock, [&] { return state_->finished.contains(job); });
    return state_->finished.at(job);
}

std::shared_ptr<const SongSnapshot> Session::wait_for(
    std::uint64_t job, std::chrono::milliseconds timeout) const {
    std::unique_lock lock(state_->publish_mu);
    const bool arrived = state_->publish_cv.wait_for(lock, timeout, [&] {
        return state_->finished.contains(job);
    });
    if (!arrived) {
        return nullptr;
    }
    return state_->finished.at(job);
}

bool Session::wait_for_revision(std::uint64_t revision, std::chrono::milliseconds timeout) const {
    std::unique_lock lock(state_->publish_mu);
    return state_->publish_cv.wait_for(lock, timeout, [&] {
        return state_->snapshot != nullptr && state_->snapshot->revision >= revision;
    });
}

std::shared_ptr<SongSnapshot> Session::blank(
    SongPhase phase, float progress, bool from_cache, std::string detail) const {
    auto next = std::make_shared<SongSnapshot>();
    next->job = state_->job;
    next->revision = state_->revision;
    next->phase = phase;
    next->progress = progress;
    next->from_cache = from_cache;
    next->detail = std::move(detail);
    next->source = state_->source;
    next->cache_directory = state_->cache_directory;
    next->project_hash = state_->project_hash;
    next->project_name = state_->project_name;
    next->gain = state_->gain;
    next->pan = state_->pan;
    next->mute = state_->mute;
    next->solo = state_->solo;
    next->mix = state_->mix;
    next->stems = state_->stems;
    return next;
}

void Session::publish(std::shared_ptr<SongSnapshot> next) {
    std::lock_guard lock(state_->publish_mu);
    if (song_settled(next->phase) && next->job != 0) {
        if (next->phase != SongPhase::Ready) {
            next->mix.reset();
            next->stems.reset();
        }
        for (auto& item : state_->finished) {
            if (item.first == next->job || item.second == nullptr ||
                (item.second->mix == nullptr && item.second->stems == nullptr)) {
                continue;
            }
            auto light = std::make_shared<SongSnapshot>(*item.second);
            light->mix.reset();
            light->stems.reset();
            item.second = std::move(light);
        }
        state_->finished[next->job] = next;
        while (state_->finished.size() > 8) {
            auto drop = state_->finished.end();
            for (auto it = state_->finished.begin(); it != state_->finished.end(); ++it) {
                if (it->first == next->job) {
                    continue;
                }
                if (drop == state_->finished.end() || it->first < drop->first) {
                    drop = it;
                }
            }
            if (drop == state_->finished.end()) {
                break;
            }
            state_->finished.erase(drop);
        }
    }
    state_->snapshot = std::move(next);
    state_->publish_cv.notify_all();
}

void Session::apply_mixer(const Command& command) {
    state_->revision = command.revision;
    const bool stem_ok = command.stem >= 0 && command.stem < kStemCount;
    if (stem_ok && command.kind == Command::Kind::Gain && std::isfinite(command.gain)) {
        state_->gain[static_cast<std::size_t>(command.stem)] = command.gain;
    } else if (stem_ok && command.kind == Command::Kind::Pan) {
        state_->pan[static_cast<std::size_t>(command.stem)] = command.gain;
    } else if (stem_ok && command.kind == Command::Kind::Mute) {
        state_->mute[static_cast<std::size_t>(command.stem)] = command.flag;
    } else if (stem_ok && command.kind == Command::Kind::Solo) {
        state_->solo[static_cast<std::size_t>(command.stem)] = command.flag;
    }
    auto current = snapshot();
    auto next = std::make_shared<SongSnapshot>(*current);
    next->revision = state_->revision;
    next->gain = state_->gain;
    next->pan = state_->pan;
    next->mute = state_->mute;
    next->solo = state_->solo;
    publish(std::move(next));
}

void Session::drain_mixer() {
    for (;;) {
        Command command;
        {
            std::lock_guard lock(state_->queue_mu);
            if (state_->queue.empty()) {
                return;
            }
            const auto kind = state_->queue.front().kind;
            if (kind != Command::Kind::Gain && kind != Command::Kind::Pan && kind != Command::Kind::Mute &&
                kind != Command::Kind::Solo) {
                return;
            }
            command = std::move(state_->queue.front());
            state_->queue.pop_front();
        }
        apply_mixer(command);
    }
}

void Session::note_cancel(const Command& command) {
    auto current = snapshot();
    if (current->job == command.job && !song_settled(current->phase)) {
        state_->job = command.job;
        state_->mix.reset();
        state_->stems.reset();
        publish(blank(SongPhase::Cancelled, current->progress, false, {}));
    }
}

void Session::thread_main() {
    for (;;) {
        Command command;
        {
            std::unique_lock lock(state_->queue_mu);
            state_->queue_cv.wait(lock, [&] { return !state_->queue.empty(); });
            command = std::move(state_->queue.front());
            state_->queue.pop_front();
        }
        try {
            switch (command.kind) {
            case Command::Kind::Shutdown:
                return;
            case Command::Kind::Cancel:
                note_cancel(command);
                break;
            case Command::Kind::Gain:
            case Command::Kind::Pan:
            case Command::Kind::Mute:
            case Command::Kind::Solo:
                apply_mixer(command);
                break;
            case Command::Kind::Open:
                run_open(command);
                break;
            case Command::Kind::OpenCache:
                run_open_cache(command);
                break;
            case Command::Kind::Close:
                run_close();
                break;
            case Command::Kind::Relocate:
                run_relocate(command);
                break;
            }
        } catch (const std::exception& ex) {
            if (command.kind == Command::Kind::Open || command.kind == Command::Kind::OpenCache) {
                state_->job = command.job;
                state_->stems.reset();
                const auto phase = state_->live_job.load() == command.job ? SongPhase::Failed : SongPhase::Cancelled;
                publish(blank(phase, 0.f, false, phase == SongPhase::Failed ? ex.what() : std::string{}));
            }
        }
    }
}

std::expected<std::shared_ptr<Session::CacheHit>, std::string> Session::load_cache(
    const std::filesystem::path& directory) const {
    std::error_code error;
    if (!std::filesystem::is_directory(directory, error)) {
        return nullptr;
    }
    auto fail = [&](std::string message) -> std::expected<std::shared_ptr<CacheHit>, std::string> {
        std::error_code remove_error;
        std::filesystem::remove_all(directory, remove_error);
        if (remove_error) {
            return std::unexpected(std::move(message));
        }
        return nullptr;
    };

    auto stems = read_cached_stems(directory);
    if (!stems) {
        return fail(stems.error());
    }
    auto hit = std::make_shared<CacheHit>();
    hit->stems = std::move(*stems);
    return hit;
}

std::expected<void, std::string> Session::store_cache(
    const std::filesystem::path& directory, const Stems& stems) const {
    const auto temporary = directory.parent_path() / (directory.filename().wstring() + L".tmp");
    std::error_code error;
    std::filesystem::remove_all(temporary, error);
    std::filesystem::create_directories(temporary, error);
    if (error) {
        return std::unexpected("cannot create " + utf8_of(temporary));
    }
    try {
        if (auto written = write_stems(temporary, stems); !written) {
            std::filesystem::remove_all(temporary, error);
            return written;
        }
        if (std::filesystem::exists(directory, error)) {
            std::filesystem::remove_all(temporary, error);
            return {};
        }
        std::filesystem::rename(temporary, directory, error);
        if (error) {
            std::filesystem::remove_all(temporary, error);
            return std::unexpected("cannot commit cache " + utf8_of(directory));
        }
    } catch (const std::exception& ex) {
        std::filesystem::remove_all(temporary, error);
        return std::unexpected(ex.what());
    }
    return {};
}

void Session::run_open(const Command& command) {
    state_->job = command.job;
    state_->source = command.path;
    state_->cache_directory.clear();
    state_->project_hash.clear();
    state_->project_name.clear();
    state_->mix.reset();
    state_->stems.reset();

    auto abandon = [&] {
        state_->stems.reset();
        publish(blank(SongPhase::Cancelled, snapshot()->progress, false, {}));
    };
    if (state_->live_job.load() != command.job) {
        abandon();
        return;
    }
    publish(blank(SongPhase::Decoding, 0.f, false, {}));

    auto digest = sha256_file(command.path);
    if (!digest) {
        publish(blank(SongPhase::Failed, 0.f, false, digest.error()));
        return;
    }
    if (state_->live_job.load() != command.job) {
        abandon();
        return;
    }

    const std::string model_key = options_.backend_key ? options_.backend_key() : options_.model_key;
    const std::string device = options_.backend_device ? options_.backend_device() : std::string{};
    auto located = locate_project(
        options_.cache_directory, model_key, *digest, display_name_from_path(command.path), command.path);
    if (!located) {
        publish(blank(SongPhase::Failed, 0.f, false, located.error()));
        return;
    }
    state_->cache_directory = located->directory;
    state_->project_hash = located->hash;
    state_->project_name = located->name;
    publish(blank(SongPhase::Decoding, 0.f, false, {}));

    auto cached = load_cache(located->directory);
    if (!cached) {
        publish(blank(SongPhase::Failed, 0.f, false, cached.error()));
        return;
    }
    if (*cached) {
        if (state_->live_job.load() != command.job) {
            abandon();
            return;
        }
        if (auto committed = commit_project(*located)) {
            state_->cache_directory = committed->directory;
            state_->project_hash = committed->hash;
            state_->project_name = committed->name;
        }
        state_->mix.reset();
        state_->stems = (*cached)->stems;
        publish(blank(SongPhase::Ready, 1.f, true, {}));
        return;
    }

    auto decoded = load_audio(command.path);
    if (!decoded) {
        publish(blank(SongPhase::Failed, 0.f, false, decoded.error()));
        return;
    }
    if (state_->live_job.load() != command.job) {
        abandon();
        return;
    }

    state_->mix = std::make_shared<const Audio>(std::move(*decoded));
    publish(blank(SongPhase::Separating, 0.f, false, {}));

    auto on_progress = [&](float fraction) {
        drain_mixer();
        if (state_->live_job.load() != command.job) {
            return;
        }
        publish(blank(SongPhase::Separating, fraction, false, {}));
    };
    auto on_cancel = [&] {
        drain_mixer();
        return state_->live_job.load() != command.job;
    };
    auto separated = options_.separate_mix
        ? options_.separate_mix(*state_->mix, on_progress, on_cancel)
        : separate_audio(*state_->mix, options_.run_segment, options_.separate, on_progress, on_cancel);
    if (!separated) {
        state_->stems.reset();
        if (separated.error() == kSeparationCancelled) {
            publish(blank(SongPhase::Cancelled, snapshot()->progress, false, {}));
            return;
        }
        publish(blank(SongPhase::Failed, snapshot()->progress, false, separated.error()));
        return;
    }

    if (auto stored = store_cache(state_->cache_directory, *separated); !stored) {
        publish(blank(SongPhase::Failed, 1.f, false, stored.error()));
        return;
    }
    located->model_key = model_key;
    if (!device.empty()) {
        located->device = device;
    }
    if (auto committed = commit_project(*located)) {
        state_->cache_directory = committed->directory;
        state_->project_hash = committed->hash;
        state_->project_name = committed->name;
    }
    state_->stems = std::make_shared<const Stems>(std::move(*separated));
    state_->mix.reset();
    publish(blank(SongPhase::Ready, 1.f, false, {}));
}

void Session::run_open_cache(const Command& command) {
    state_->job = command.job;
    state_->source.clear();
    state_->cache_directory = command.path;
    state_->project_hash.clear();
    state_->project_name.clear();
    state_->mix.reset();
    state_->stems.reset();
    if (state_->live_job.load() != command.job) {
        publish(blank(SongPhase::Cancelled, 0.f, false, {}));
        return;
    }
    auto entry = read_project(command.path);
    if (!entry) {
        publish(blank(SongPhase::Failed, 0.f, false, entry.error()));
        return;
    }
    state_->cache_directory = entry->directory;
    state_->project_hash = entry->hash;
    state_->project_name = entry->name;
    state_->source = entry->source;
    auto cached = load_cache(entry->directory);
    if (!cached || !*cached) {
        publish(blank(SongPhase::Failed, 0.f, false, cached ? std::string("cached song is incomplete") : cached.error()));
        return;
    }
    if (state_->live_job.load() != command.job) {
        state_->stems.reset();
        publish(blank(SongPhase::Cancelled, 0.f, false, {}));
        return;
    }
    state_->mix.reset();
    state_->stems = (*cached)->stems;
    publish(blank(SongPhase::Ready, 1.f, true, {}));
}

void Session::run_close() {
    state_->job = 0;
    state_->source.clear();
    state_->cache_directory.clear();
    state_->project_hash.clear();
    state_->project_name.clear();
    state_->mix.reset();
    state_->stems.reset();
    publish(blank(SongPhase::Empty, 0.f, false, {}));
}

void Session::run_relocate(const Command& command) {
    auto current = snapshot();
    if (current == nullptr || state_->project_hash.empty() || current->phase == SongPhase::Empty) {
        return;
    }
    state_->cache_directory = command.path;
    state_->project_name = command.label;
    publish(blank(current->phase, current->progress, current->from_cache, current->detail));
}

}  // namespace bt
