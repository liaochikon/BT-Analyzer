#include "bt/roformer.hpp"
#include "bt/session.hpp"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <expected>
#include <filesystem>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#define NOMINMAX
#include <windows.h>

namespace {

void print_usage() {
    std::cerr << "bt-separate --model <bs_roformer_sw.onnx> <audio> <output-dir> [--reference <stem-dir>]\n"
              << "bt-separate --model <bs_roformer_sw.onnx> --cache <dir> <audio> [--reference <stem-dir>]\n";
}

struct Args {
    std::filesystem::path model;
    std::filesystem::path audio;
    std::filesystem::path output;
    std::filesystem::path reference;
    std::filesystem::path cache;
};

std::atomic<bt::Session*> g_session{nullptr};

BOOL WINAPI on_console_ctrl(DWORD kind) {
    if (kind != CTRL_C_EVENT && kind != CTRL_BREAK_EVENT) {
        return FALSE;
    }
    static std::atomic<int> hits{0};
    if (hits.fetch_add(1) > 0) {
        return FALSE;
    }
    if (bt::Session* session = g_session.load()) {
        session->cancel();
    }
    return TRUE;
}

std::expected<Args, std::string> parse_args(int argc, char** argv) {
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
        if (token == "--model") {
            auto value = need("--model");
            if (!value) return std::unexpected(value.error());
            args.model = *value;
        } else if (token == "--reference") {
            auto value = need("--reference");
            if (!value) return std::unexpected(value.error());
            args.reference = *value;
        } else if (token == "--cache") {
            auto value = need("--cache");
            if (!value) return std::unexpected(value.error());
            args.cache = *value;
        } else if (token == "--help" || token == "-h") {
            print_usage();
            std::exit(0);
        } else {
            positionals.emplace_back(token);
        }
    }
    if (args.model.empty()) {
        print_usage();
        return std::unexpected("expected --model");
    }
    if (!args.cache.empty()) {
        if (positionals.size() != 1) {
            print_usage();
            return std::unexpected("expected --model, --cache, and an input file");
        }
        args.audio = positionals[0];
        return args;
    }
    if (positionals.size() != 2) {
        print_usage();
        return std::unexpected("expected --model, an input file, and an output directory");
    }
    args.audio = positionals[0];
    args.output = positionals[1];
    return args;
}

float max_abs_diff(const bt::Audio& actual, const float* expected, std::int64_t frames);

int compare_reference(const bt::Stems& stems, const std::filesystem::path& reference) {
    float worst = 0.f;
    for (int stem = 0; stem < bt::kStemCount; ++stem) {
        const auto name = std::string(bt::kStemNames[static_cast<std::size_t>(stem)]);
        auto expected = bt::read_float_wav(reference / (name + ".wav"));
        if (!expected) {
            std::cerr << expected.error() << '\n';
            return 1;
        }
        if (expected->frames != stems.frames || expected->channels != 2) {
            std::cerr << name << " length " << expected->frames << " != " << stems.frames << '\n';
            return 2;
        }
        const float diff = max_abs_diff(
            *expected, stems.audio[static_cast<std::size_t>(stem)].data(), stems.frames);
        worst = std::max(worst, diff);
        std::cout << name << " max_abs " << diff << '\n';
    }
    std::cout << "worst " << worst << '\n';
    if (worst > 1e-3f) {
        std::cerr << "stem difference exceeded 1e-3\n";
        return 2;
    }
    return 0;
}

int run_cached(const Args& args) {
    auto model = std::make_shared<std::unique_ptr<bt::BsRoformerModel>>();
    bt::SessionOptions options;
    options.cache_directory = args.cache;
    options.separate_mix = [path = args.model, model](
                               const bt::Audio& audio, const std::function<void(float)>& progress,
                               const std::function<bool()>& cancelled) -> std::expected<bt::Stems, std::string> {
        if (*model == nullptr) {
            auto opened = bt::BsRoformerModel::open(path, 0, true);
            if (!opened) {
                opened = bt::BsRoformerModel::open(path);
            }
            if (!opened) {
                return std::unexpected(opened.error());
            }
            *model = std::make_unique<bt::BsRoformerModel>(std::move(*opened));
        }
        return (*model)->separate(audio, progress, cancelled);
    };
    auto created = bt::Session::create(std::move(options));
    if (!created) {
        std::cerr << created.error() << '\n';
        return 1;
    }
    auto session = std::move(*created);
    g_session.store(session.get());
    const struct ClearSession {
        ~ClearSession() { g_session.store(nullptr); }
    } clear_session;
    SetConsoleCtrlHandler(on_console_ctrl, TRUE);

    const auto job = session->open_song(args.audio);
    std::shared_ptr<const bt::SongSnapshot> result;
    auto printed = bt::SongPhase::Empty;
    float printed_progress = -1.f;
    for (;;) {
        result = session->snapshot();
        if (result->job == job && bt::song_settled(result->phase)) {
            break;
        }
        if (result->job == job && (result->phase != printed || result->progress != printed_progress)) {
            printed = result->phase;
            printed_progress = result->progress;
            if (result->phase == bt::SongPhase::Decoding) {
                std::cout << "decoding\n";
            } else if (result->phase == bt::SongPhase::Separating) {
                std::cout << '\r' << static_cast<int>(result->progress * 1000.f) / 10.f << '%' << std::flush;
            }
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
    if (printed == bt::SongPhase::Separating) {
        std::cout << '\n';
    }
    if (result->phase == bt::SongPhase::Cancelled) {
        std::cout << "cancelled\n";
        return 3;
    }
    if (result->phase != bt::SongPhase::Ready) {
        std::cerr << result->detail << '\n';
        return 1;
    }
    std::cout << "ready " << result->mix->frames << " frames, "
              << (result->from_cache ? "cache hit" : "cache miss") << '\n';
    if (args.reference.empty()) {
        return 0;
    }
    return compare_reference(*result->stems, args.reference);
}

float max_abs_diff(const bt::Audio& actual, const float* expected, std::int64_t frames) {
    float worst = 0.f;
    const std::int64_t count = std::min(actual.frames, frames) * actual.channels;
    for (std::int64_t i = 0; i < count; ++i) {
        worst = std::max(worst, std::fabs(actual.samples[static_cast<std::size_t>(i)] - expected[i]));
    }
    return worst;
}

}  // namespace

int main(int argc, char** argv) {
    auto args = parse_args(argc, argv);
    if (!args) {
        std::cerr << args.error() << '\n';
        return 1;
    }

    if (!args->cache.empty()) {
        return run_cached(*args);
    }

    auto audio = bt::load_audio(args->audio);
    if (!audio) {
        std::cerr << audio.error() << '\n';
        return 1;
    }
    std::cout << "input " << audio->frames << " frames, " << audio->channels << " ch, "
              << audio->sample_rate << " Hz\n";

    auto model = bt::BsRoformerModel::open(args->model, 0, true);
    if (!model) {
        model = bt::BsRoformerModel::open(args->model);
    }
    if (!model) {
        std::cerr << model.error() << '\n';
        return 1;
    }
    std::cout << "chunk " << model->chunk_samples() << " samples, " << model->device() << '\n';

    auto stems = model->separate(*audio, [](float fraction) {
        std::cout << '\r' << static_cast<int>(fraction * 1000.f) / 10.f << '%' << std::flush;
    });
    std::cout << '\n';
    if (!stems) {
        std::cerr << stems.error() << '\n';
        return 1;
    }
    if (auto written = bt::write_stems(args->output, *stems); !written) {
        std::cerr << written.error() << '\n';
        return 1;
    }
    std::cout << "wrote " << args->output.string() << '\n';

    if (args->reference.empty()) {
        return 0;
    }
    return compare_reference(*stems, args->reference);
}
