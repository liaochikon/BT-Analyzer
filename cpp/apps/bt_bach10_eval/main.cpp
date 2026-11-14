#include "bt/audio.hpp"
#include "bt/basic_pitch.hpp"
#include "bt/note_score.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

namespace fs = std::filesystem;

namespace {

constexpr double kOnsetTolSec = 0.050;
constexpr double kPitchTol = 0.5;

struct GtNote {
    double start = 0.0;
    double end = 0.0;
    int pitch = 0;
    int voice = 0;
};

struct FMeasure {
    int hits = 0;
    int est = 0;
    int ref = 0;
    float precision = 0.f;
    float recall = 0.f;
    float f1 = 0.f;
};

struct VariantAgg {
    std::string name;
    double f_sum = 0.0;
    double p_sum = 0.0;
    double r_sum = 0.0;
    double n_sum = 0.0;
    int count = 0;
};

void print_usage() {
    std::cerr
        << "bt-bach10-eval --dataset <Bach10-dir> [--model <nmp.onnx>] [--limit N] [--sweep]\n"
        << "  Default: BP vs energy-mask(current).\n"
        << "  --sweep: ablate mask presets (BP once per file, then each mask).\n";
}

std::vector<GtNote> load_gt_txt(const fs::path& path) {
    std::ifstream in(path);
    std::vector<GtNote> notes;
    if (!in) {
        return notes;
    }
    std::string line;
    while (std::getline(in, line)) {
        if (line.empty() || line[0] == '#') {
            continue;
        }
        std::istringstream ss(line);
        double onset_ms = 0.0;
        double offset_ms = 0.0;
        int pitch = 0;
        int voice = 0;
        if (!(ss >> onset_ms >> offset_ms >> pitch >> voice)) {
            continue;
        }
        if (offset_ms <= onset_ms) {
            continue;
        }
        notes.push_back(GtNote{onset_ms * 0.001, offset_ms * 0.001, pitch, voice});
    }
    return notes;
}

FMeasure note_f_measure(const std::vector<bt::Note>& est, const std::vector<GtNote>& ref) {
    FMeasure out;
    out.est = static_cast<int>(est.size());
    out.ref = static_cast<int>(ref.size());
    if (out.est == 0 || out.ref == 0) {
        return out;
    }
    std::vector<char> used(est.size(), 0);
    int hits = 0;
    for (const GtNote& gt : ref) {
        int best = -1;
        double best_onset = 1e9;
        for (std::size_t i = 0; i < est.size(); ++i) {
            if (used[i]) {
                continue;
            }
            const bt::Note& n = est[i];
            if (std::fabs(static_cast<double>(n.pitch) - gt.pitch) > kPitchTol) {
                continue;
            }
            const double d_onset = std::fabs(n.start - gt.start);
            if (d_onset > kOnsetTolSec) {
                continue;
            }
            if (d_onset < best_onset) {
                best_onset = d_onset;
                best = static_cast<int>(i);
            }
        }
        if (best >= 0) {
            used[static_cast<std::size_t>(best)] = 1;
            ++hits;
        }
    }
    out.hits = hits;
    out.precision = static_cast<float>(hits) / static_cast<float>(out.est);
    out.recall = static_cast<float>(hits) / static_cast<float>(out.ref);
    if (out.precision + out.recall > 1e-8f) {
        out.f1 = 2.f * out.precision * out.recall / (out.precision + out.recall);
    }
    return out;
}

std::vector<float> to_stereo_channel_major(const bt::Audio& audio) {
    std::vector<float> stereo(static_cast<std::size_t>(audio.frames * 2), 0.f);
    if (audio.channels <= 0 || audio.frames <= 0 || audio.samples.empty()) {
        return stereo;
    }
    if (audio.channels == 1) {
        for (std::int64_t i = 0; i < audio.frames; ++i) {
            const float s = audio.samples[static_cast<std::size_t>(i)];
            stereo[static_cast<std::size_t>(i)] = s;
            stereo[static_cast<std::size_t>(audio.frames + i)] = s;
        }
        return stereo;
    }
    if (static_cast<std::int64_t>(audio.samples.size()) >= audio.frames * 2) {
        std::copy_n(audio.samples.data(), static_cast<std::size_t>(audio.frames * 2), stereo.begin());
    }
    return stereo;
}

std::vector<float> to_mono_44100(const bt::Audio& audio) {
    std::vector<float> mono(static_cast<std::size_t>(audio.frames), 0.f);
    if (audio.channels == 1) {
        std::copy_n(audio.samples.data(), static_cast<std::size_t>(audio.frames), mono.begin());
        return mono;
    }
    const float* left = audio.samples.data();
    const float* right = audio.samples.data() + audio.frames;
    for (std::int64_t i = 0; i < audio.frames; ++i) {
        mono[static_cast<std::size_t>(i)] = 0.5f * (left[i] + right[i]);
    }
    return mono;
}

struct PieceFiles {
    std::string id;
    fs::path mix_wav;
    fs::path gt_txt;
    std::vector<std::pair<std::string, fs::path>> instruments;
};

std::vector<PieceFiles> discover_pieces(const fs::path& root) {
    static const char* kParts[] = {"violin", "clarinet", "saxphone", "bassoon"};
    std::vector<PieceFiles> pieces;
    if (!fs::exists(root)) {
        return pieces;
    }
    for (const auto& entry : fs::directory_iterator(root)) {
        if (!entry.is_directory()) {
            continue;
        }
        const fs::path dir = entry.path();
        const std::string name = dir.filename().string();
        if (name == "Code" || name.starts_with(".")) {
            continue;
        }
        PieceFiles piece;
        piece.id = name;
        piece.mix_wav = dir / (name + ".wav");
        piece.gt_txt = dir / (name + ".txt");
        if (!fs::exists(piece.mix_wav) || !fs::exists(piece.gt_txt)) {
            continue;
        }
        for (const char* part : kParts) {
            const fs::path wav = dir / (name + "-" + part + ".wav");
            if (fs::exists(wav)) {
                piece.instruments.emplace_back(part, wav);
            }
        }
        pieces.push_back(std::move(piece));
    }
    std::sort(pieces.begin(), pieces.end(), [](const PieceFiles& a, const PieceFiles& b) {
        return a.id < b.id;
    });
    return pieces;
}

int voice_for_instrument(const std::string& name) {
    if (name == "violin") {
        return 1;
    }
    if (name == "clarinet") {
        return 2;
    }
    if (name == "saxphone") {
        return 3;
    }
    if (name == "bassoon") {
        return 4;
    }
    return 0;
}

std::vector<GtNote> filter_voice(const std::vector<GtNote>& all, int voice) {
    std::vector<GtNote> out;
    for (const GtNote& n : all) {
        if (n.voice == voice) {
            out.push_back(n);
        }
    }
    return out;
}

bool load_bp_notes(
    const bt::BasicPitchModel& model, const fs::path& wav, std::vector<bt::Note>& def_notes,
    std::vector<float>& mono, std::int64_t& frames) {
    auto audio = bt::load_audio(wav);
    if (!audio || audio->sample_rate != bt::kSampleRate) {
        std::cerr << "  load failed: " << wav.string() << "\n";
        return false;
    }
    const auto stereo = to_stereo_channel_major(*audio);
    auto bp_audio = bt::stem_to_basic_pitch_audio(stereo, audio->frames);
    if (!bp_audio) {
        std::cerr << "  resample failed: " << bp_audio.error() << "\n";
        return false;
    }
    auto activations = model.infer_activations(bp_audio->data(), static_cast<std::int64_t>(bp_audio->size()));
    if (!activations) {
        std::cerr << "  infer failed: " << activations.error() << "\n";
        return false;
    }
    bt::PitchParameters base;
    def_notes = bt::notes_from_activations(activations->view(), base);
    mono = to_mono_44100(*audio);
    frames = audio->frames;
    return true;
}

void add_agg(VariantAgg& agg, const FMeasure& m) {
    agg.f_sum += m.f1;
    agg.p_sum += m.precision;
    agg.r_sum += m.recall;
    agg.n_sum += m.est;
    ++agg.count;
}

}  // namespace

int main(int argc, char** argv) {
    fs::path dataset;
    fs::path model_path = "models/nmp.onnx";
    int limit = 0;
    bool sweep = false;
    for (int i = 1; i < argc; ++i) {
        const std::string token = argv[i];
        auto need = [&](const char* name) -> fs::path {
            if (i + 1 >= argc) {
                std::cerr << "missing value for " << name << "\n";
                std::exit(2);
            }
            return fs::path(argv[++i]);
        };
        if (token == "--dataset") {
            dataset = need("--dataset");
        } else if (token == "--model") {
            model_path = need("--model");
        } else if (token == "--limit") {
            if (i + 1 >= argc) {
                std::cerr << "missing value for --limit\n";
                return 2;
            }
            limit = std::stoi(argv[++i]);
        } else if (token == "--sweep") {
            sweep = true;
        } else if (token == "--help" || token == "-h") {
            print_usage();
            return 0;
        } else {
            std::cerr << "unknown arg: " << token << "\n";
            print_usage();
            return 2;
        }
    }
    if (dataset.empty()) {
        const fs::path candidates[] = {
            fs::path("datasets") / "Bach10",
            fs::path("..") / "datasets" / "Bach10",
            fs::path("..") / ".." / "datasets" / "Bach10",
            fs::path("..") / ".." / ".." / "datasets" / "Bach10",
        };
        for (const fs::path& c : candidates) {
            if (fs::exists(c)) {
                dataset = c;
                break;
            }
        }
    }
    if (dataset.empty() || !fs::exists(dataset)) {
        std::cerr << "Bach10 dataset not found; pass --dataset\n";
        return 2;
    }
    if (!fs::exists(model_path)) {
        const fs::path alt[] = {
            fs::path("..") / "models" / "nmp.onnx",
            fs::path("..") / ".." / "models" / "nmp.onnx",
            fs::path("..") / ".." / ".." / "models" / "nmp.onnx",
        };
        for (const fs::path& c : alt) {
            if (fs::exists(c)) {
                model_path = c;
                break;
            }
        }
    }
    auto model = bt::BasicPitchModel::open(model_path);
    if (!model) {
        std::cerr << "open model failed: " << model.error() << "\n";
        return 1;
    }

    auto pieces = discover_pieces(dataset);
    if (pieces.empty()) {
        std::cerr << "no Bach10 pieces\n";
        return 1;
    }
    if (limit > 0 && static_cast<int>(pieces.size()) > limit) {
        pieces.resize(static_cast<std::size_t>(limit));
    }

    std::vector<bt::EnergyMaskConfig> presets;
    if (sweep) {
        presets = bt::energy_mask_ablation_presets();
    } else {
        presets.push_back(bt::energy_mask_default());
    }

    std::vector<VariantAgg> part_aggs;
    part_aggs.reserve(presets.size() + 1);
    part_aggs.push_back(VariantAgg{"raw_bp"});
    for (const bt::EnergyMaskConfig& p : presets) {
        part_aggs.push_back(VariantAgg{p.name ? p.name : "?"});
    }
    std::vector<VariantAgg> mix_aggs = part_aggs;

    std::cout << "Bach10 ablation  onset<=50ms pitch<=0.5\n"
              << "dataset=" << fs::weakly_canonical(dataset).string() << "\n"
              << "model=" << fs::weakly_canonical(model_path).string() << "\n"
              << "pieces=" << pieces.size() << "  variants=" << presets.size() << " (+ raw_bp)\n\n";

    auto process_file = [&](const fs::path& wav, const std::vector<GtNote>& gt, bool is_mix) {
        std::vector<bt::Note> def_notes;
        std::vector<float> mono;
        std::int64_t frames = 0;
        if (!load_bp_notes(*model, wav, def_notes, mono, frames)) {
            return;
        }
        const FMeasure raw = note_f_measure(def_notes, gt);
        add_agg(is_mix ? mix_aggs[0] : part_aggs[0], raw);
        for (std::size_t vi = 0; vi < presets.size(); ++vi) {
            const auto masked = bt::mask_notes_by_midi_energy(
                def_notes.data(), def_notes.size(), mono.data(), frames, bt::kSampleRate, presets[vi]);
            const FMeasure m = note_f_measure(masked, gt);
            add_agg(is_mix ? mix_aggs[vi + 1] : part_aggs[vi + 1], m);
        }
    };

    for (const PieceFiles& piece : pieces) {
        const auto gt_all = load_gt_txt(piece.gt_txt);
        if (gt_all.empty()) {
            continue;
        }
        std::cout << "=== " << piece.id << " ===\n";
        process_file(piece.mix_wav, gt_all, true);
        for (const auto& [part, wav] : piece.instruments) {
            const auto gt = filter_voice(gt_all, voice_for_instrument(part));
            if (!gt.empty()) {
                process_file(wav, gt, false);
            }
        }
    }

    auto print_table = [](const char* title, const std::vector<VariantAgg>& aggs) {
        if (aggs.empty() || aggs[0].count <= 0) {
            return;
        }
        const double inv = 1.0 / static_cast<double>(aggs[0].count);
        const double raw_f = aggs[0].f_sum * inv;
        std::cout << "\n---- " << title << " (n=" << aggs[0].count << ") ----\n";
        std::cout << std::left << std::setw(18) << "variant" << std::right << "    F      P      R     nNotes   dF\n";
        std::vector<std::pair<double, std::size_t>> order;
        for (std::size_t i = 0; i < aggs.size(); ++i) {
            order.emplace_back(aggs[i].f_sum * inv, i);
        }
        std::sort(order.begin(), order.end(), [](const auto& a, const auto& b) { return a.first > b.first; });
        for (const auto& [f, i] : order) {
            const VariantAgg& a = aggs[i];
            const double p = a.p_sum * inv;
            const double r = a.r_sum * inv;
            const double nn = a.n_sum * inv;
            const double df = f - raw_f;
            const char* mark = (i == 0) ? " *" : (df > 0.002 ? " +" : (df < -0.002 ? " -" : "  "));
            std::cout << std::left << std::setw(18) << a.name << std::right << std::fixed << std::setprecision(3)
                      << std::setw(7) << f << std::setw(7) << p << std::setw(7) << r << std::setw(9)
                      << std::setprecision(1) << nn << std::setprecision(3) << std::setw(8) << df << mark
                      << "\n";
        }
    };

    print_table("mix", mix_aggs);
    print_table("part", part_aggs);
    std::cout << "\n* = raw BP baseline; + = clearly above raw; - = clearly below raw\n";
    return 0;
}
