#include "bt/contract.hpp"
#include "bt/core/engine.hpp"
#include "bt/log.hpp"
#include "bt/roformer.hpp"
#include "bt/separator.hpp"

#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <expected>
#include <filesystem>
#include <functional>
#include <iostream>
#include <memory>
#include <string>
#include <string_view>
#include <vector>

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <psapi.h>

namespace {

using Clock = std::chrono::steady_clock;

void usage() {
    std::cerr
        << "bt-agent — headless BT-Analyzer pipeline for agents\n"
        << "\n"
        << "Usage:\n"
        << "  bt-agent --cache <dir> --model <bs_roformer.onnx> [--pitch <nmp.onnx>]\n"
        << "           [--stem <0-5>] [--out <dir>] [--refresh] <audio>\n"
        << "  bt-agent --cache <dir> --model <bs_roformer.onnx> --open-cache <project>\n"
        << "           [--pitch <nmp.onnx>] [--stem <0-5>] [--out <dir>]\n"
        << "\n"
        << "Steps: import|open-cache → Ready → residency probe → beats → (optional) transcribe →\n"
        << "        quantize → TAB → save .notes → export ASCII tab\n"
        << "Default stem is 4 (guitar). Pass --pitch to enable transcription.\n";
}

struct Args {
    std::filesystem::path audio;
    std::filesystem::path cache;
    std::filesystem::path open_cache;
    std::filesystem::path separate_model;
    std::filesystem::path pitch_model;
    std::filesystem::path out;
    int stem = bt::core::kGuitarStem;
    bool refresh = false;
};

std::expected<Args, std::string> parse(int argc, char** argv) {
    Args args;
    std::vector<std::string> positionals;
    for (int i = 1; i < argc; ++i) {
        const std::string token = argv[i];
        auto need = [&](const char* name) -> std::expected<std::filesystem::path, std::string> {
            if (i + 1 >= argc) {
                return std::unexpected(std::string("missing value for ") + name);
            }
            return std::filesystem::path(argv[++i]);
        };
        if (token == "--help" || token == "-h") {
            usage();
            std::exit(0);
        } else if (token == "--cache") {
            auto value = need("--cache");
            if (!value) {
                return std::unexpected(value.error());
            }
            args.cache = *value;
        } else if (token == "--model") {
            auto value = need("--model");
            if (!value) {
                return std::unexpected(value.error());
            }
            args.separate_model = *value;
        } else if (token == "--pitch") {
            auto value = need("--pitch");
            if (!value) {
                return std::unexpected(value.error());
            }
            args.pitch_model = *value;
        } else if (token == "--out") {
            auto value = need("--out");
            if (!value) {
                return std::unexpected(value.error());
            }
            args.out = *value;
        } else if (token == "--stem") {
            if (i + 1 >= argc) {
                return std::unexpected("missing value for --stem");
            }
            args.stem = std::atoi(argv[++i]);
        } else if (token == "--refresh") {
            args.refresh = true;
        } else if (token == "--open-cache") {
            auto value = need("--open-cache");
            if (!value) {
                return std::unexpected(value.error());
            }
            args.open_cache = *value;
        } else if (!token.empty() && token[0] == '-') {
            return std::unexpected("unknown flag: " + token);
        } else {
            positionals.push_back(token);
        }
    }
    if (args.open_cache.empty()) {
        if (positionals.size() != 1) {
            return std::unexpected("expected exactly one audio path (or pass --open-cache)");
        }
        args.audio = positionals[0];
    } else if (!positionals.empty()) {
        return std::unexpected("audio path is not used with --open-cache");
    }
    if (args.cache.empty()) {
        return std::unexpected("--cache is required");
    }
    if (args.separate_model.empty()) {
        return std::unexpected("--model is required");
    }
    if (args.out.empty()) {
        args.out = args.cache / "agent-out";
    }
    if (args.stem < 0 || args.stem >= bt::kStemCount) {
        return std::unexpected("--stem out of range");
    }
    return args;
}

double ms_since(Clock::time_point start) {
    return std::chrono::duration<double, std::milli>(Clock::now() - start).count();
}

std::uint64_t working_set_mb() {
    PROCESS_MEMORY_COUNTERS_EX counters{};
    counters.cb = sizeof counters;
    if (!GetProcessMemoryInfo(GetCurrentProcess(), reinterpret_cast<PROCESS_MEMORY_COUNTERS*>(&counters),
                              sizeof counters)) {
        return 0;
    }
    return static_cast<std::uint64_t>(counters.WorkingSetSize / (1024ull * 1024ull));
}

std::uint64_t peak_working_set_mb() {
    PROCESS_MEMORY_COUNTERS_EX counters{};
    counters.cb = sizeof counters;
    if (!GetProcessMemoryInfo(GetCurrentProcess(), reinterpret_cast<PROCESS_MEMORY_COUNTERS*>(&counters),
                              sizeof counters)) {
        return 0;
    }
    return static_cast<std::uint64_t>(counters.PeakWorkingSetSize / (1024ull * 1024ull));
}

std::size_t stem_bytes(const bt::Stems& stems, int stem) {
    if (stem < 0 || stem >= bt::kStemCount) {
        return 0;
    }
    return stems.audio[static_cast<std::size_t>(stem)].size() * sizeof(float);
}

std::size_t stems_resident_bytes(const bt::Stems& stems) {
    std::size_t total = 0;
    int loaded = 0;
    for (int stem = 0; stem < bt::kStemCount; ++stem) {
        const auto bytes = stem_bytes(stems, stem);
        if (bytes >= static_cast<std::size_t>(stems.frames) * 2 * sizeof(float) && stems.frames > 0) {
            ++loaded;
            total += bytes;
        }
    }
    (void)loaded;
    return total;
}

void report_stems(const char* label, const bt::Stems* stems) {
    if (stems == nullptr) {
        std::cout << "metric stems " << label << " none\n";
        return;
    }
    std::size_t total = 0;
    int loaded = 0;
    std::cout << "metric stems " << label;
    for (int stem = 0; stem < bt::kStemCount; ++stem) {
        const auto bytes = stem_bytes(*stems, stem);
        const bool ok = stems->frames > 0 &&
                        bytes >= static_cast<std::size_t>(stems->frames) * 2 * sizeof(float);
        if (ok) {
            ++loaded;
            total += bytes;
        }
        std::cout << " " << bt::kStemNames[static_cast<std::size_t>(stem)] << "="
                  << (ok ? static_cast<int>(bytes / (1024 * 1024)) : 0) << "MB";
    }
    std::cout << " loaded=" << loaded << "/" << bt::kStemCount
              << " audio=" << (total / (1024 * 1024)) << "MB"
              << " ws=" << working_set_mb() << "MB\n";
}

void step(const char* name, Clock::time_point& mark) {
    const double dt = ms_since(mark);
    std::cout << "metric step " << name << " " << static_cast<int>(dt) << "ms"
              << " ws=" << working_set_mb() << "MB"
              << " peak=" << peak_working_set_mb() << "MB\n";
    mark = Clock::now();
}

}  // namespace

int main(int argc, char** argv) {
    auto parsed = parse(argc, argv);
    if (!parsed) {
        std::cerr << parsed.error() << "\n\n";
        usage();
        return 2;
    }
    const Args& args = *parsed;

    bt::set_log_sink([](bt::LogLevel level, std::string_view text) {
        if (level == bt::LogLevel::Error) {
            std::cerr << "log " << text << "\n";
        } else {
            std::cout << "log " << text << "\n";
        }
    });

    const auto wall0 = Clock::now();
    auto mark = wall0;
    std::cout << "metric start ws=" << working_set_mb() << "MB\n";

    auto model_holder = std::make_shared<std::unique_ptr<bt::BsRoformerModel>>();
    bt::core::EngineOptions options;
    options.session.cache_directory = args.cache;
    options.pitch_model = args.pitch_model;
    options.session.separate_mix =
        [path = args.separate_model, model_holder](
            const bt::Audio& audio, const std::function<void(float)>& progress,
            const std::function<bool()>& cancelled) -> std::expected<bt::Stems, std::string> {
            if (*model_holder == nullptr) {
                auto opened = bt::BsRoformerModel::open(path, 0, true);
                if (!opened) {
                    opened = bt::BsRoformerModel::open(path);
                }
                if (!opened) {
                    return std::unexpected(opened.error());
                }
                *model_holder = std::make_unique<bt::BsRoformerModel>(std::move(*opened));
            }
            return (*model_holder)->separate(audio, progress, cancelled);
        };

    auto engine = bt::core::Engine::create(std::move(options));
    if (!engine) {
        std::cerr << "engine: " << engine.error() << "\n";
        return 1;
    }
    bt::core::Engine& eng = **engine;
    step("engine_create", mark);

    std::uint64_t job = 0;
    if (!args.open_cache.empty()) {
        std::cout << "open-cache " << args.open_cache.string() << "\n";
        job = eng.open_cache(args.open_cache);
    } else {
        std::cout << "import " << args.audio.string() << (args.refresh ? " (refresh)" : "") << "\n";
        job = eng.import_audio(args.audio, args.refresh);
    }
    auto song = eng.wait_for(job, std::chrono::minutes(60));
    if (song == nullptr || song->phase != bt::SongPhase::Ready) {
        std::cerr << "import failed";
        if (song != nullptr) {
            std::cerr << ": " << song->detail;
        }
        std::cerr << "\n";
        return 1;
    }
    step(args.open_cache.empty() ? (song->from_cache ? "import_cache" : "import_separate") : "open_cache", mark);
    std::cout << "ready frames=" << song->stems->frames
              << " sec=" << (static_cast<double>(song->stems->frames) / static_cast<double>(bt::kSampleRate))
              << (song->from_cache ? " (cache)" : " (separated)") << "\n";
    report_stems("after_ready", song->stems.get());

    // Probe #4: unload everything except the target stem, then reload it if needed.
    {
        std::array<bool, bt::kStemCount> need{};
        need[static_cast<std::size_t>(args.stem)] = true;
        const auto rev = eng.session().sync_stem_residency(need);
        eng.session().wait_for_revision(rev, std::chrono::seconds(60));
        song = eng.snapshot();
        step("residency_thin", mark);
        report_stems("after_thin", song != nullptr ? song->stems.get() : nullptr);
    }

    if (!eng.track_beats()) {
        std::cout << "warn: beat tracking used fallback lattice\n";
    } else {
        std::cout << "beats bpm=" << eng.beats().bpm << " count=" << eng.beats().times.size() << "\n";
    }
    step("beats", mark);

    std::error_code ec;
    std::filesystem::create_directories(args.out, ec);

    if (!args.pitch_model.empty()) {
        // Ensure stem audio is resident for transcription (may no-op if already kept).
        std::array<bool, bt::kStemCount> need{};
        need[static_cast<std::size_t>(args.stem)] = true;
        const auto rev = eng.session().sync_stem_residency(need);
        eng.session().wait_for_revision(rev, std::chrono::seconds(60));
        song = eng.snapshot();
        report_stems("before_transcribe", song != nullptr ? song->stems.get() : nullptr);

        std::cout << "transcribe stem " << args.stem << " (" << bt::kStemNames[static_cast<std::size_t>(args.stem)]
                  << ")\n";
        auto notes = eng.transcribe_stem(args.stem);
        if (!notes) {
            std::cerr << "transcribe: " << notes.error() << "\n";
            return 1;
        }
        std::cout << "notes=" << notes->size() << "\n";
        step("transcribe", mark);
    } else {
        std::cout << "skip transcribe (pass --pitch to enable)\n";
        step("transcribe_skipped", mark);
    }

    if (!eng.notes(args.stem).empty()) {
        eng.quantize(args.stem);
        step("quantize", mark);
        eng.convert_to_tab(args.stem);
        eng.resolve_tab(args.stem);
        eng.build_tab(args.stem);
        step("tab", mark);

        const auto notes_path =
            args.out / (std::string(bt::kStemNames[static_cast<std::size_t>(args.stem)]) + ".notes");
        if (auto saved = eng.save_notes(notes_path, eng.notes(args.stem)); !saved) {
            std::cerr << "save notes: " << saved.error() << "\n";
            return 1;
        }
        std::cout << "wrote " << notes_path.string() << "\n";

        const auto tab_path =
            args.out / (std::string(bt::kStemNames[static_cast<std::size_t>(args.stem)]) + ".tab.txt");
        if (auto exported = eng.export_tab(args.stem, tab_path); !exported) {
            std::cerr << "export tab: " << exported.error() << "\n";
            return 1;
        }
        std::cout << "wrote " << tab_path.string() << "\n";
        step("export", mark);
    }

    std::cout << "metric total " << static_cast<int>(ms_since(wall0)) << "ms"
              << " ws=" << working_set_mb() << "MB"
              << " peak=" << peak_working_set_mb() << "MB\n";
    std::cout << "ok\n";
    return 0;
}
