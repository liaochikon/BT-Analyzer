#include "bt/audio.hpp"
#include "bt/basic_pitch.hpp"
#include "bt/harmonic_deghost.hpp"
#include "bt/note_score.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
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

struct StemJob {
    std::string track;
    std::string stem_id;
    std::string inst;  // Guitar / Piano
    fs::path wav;
    fs::path mid;
};

void print_usage() {
    std::cerr
        << "bt-babyslakh-eval --dataset <BabySlakh-dir> [--model nmp.onnx]\n"
        << "  [--limit-tracks N] [--limit-stems N] [--max-sec S] [--bp-sweep]\n"
        << "  Default: energy + deghost ablation.\n"
        << "  --bp-sweep: grid onset/frame/min_ms (activations inferred once per stem).\n";
}

struct NamedParams {
    std::string name;
    bt::PitchParameters parameters;
};

std::vector<NamedParams> bp_param_grid() {
    std::vector<NamedParams> out;
    const float onsets[] = {0.20f, 0.25f, 0.30f, 0.35f};
    const float frames[] = {0.20f, 0.25f, 0.30f, 0.35f};
    const float mins[] = {127.7f, 150.f, 180.f};
    for (float onset : onsets) {
        for (float frame : frames) {
            for (float min_ms : mins) {
                bt::PitchParameters p;
                p.onset_threshold = onset;
                p.frame_threshold = frame;
                p.minimum_note_ms = min_ms;
                char buf[64];
                std::snprintf(
                    buf, sizeof(buf), "o%.2f_f%.2f_m%.0f", static_cast<double>(onset),
                    static_cast<double>(frame), static_cast<double>(min_ms));
                out.push_back(NamedParams{buf, p});
            }
        }
    }
    return out;
}

std::uint32_t read_be32(const unsigned char* p) {
    return (static_cast<std::uint32_t>(p[0]) << 24) | (static_cast<std::uint32_t>(p[1]) << 16) |
           (static_cast<std::uint32_t>(p[2]) << 8) | static_cast<std::uint32_t>(p[3]);
}

std::uint16_t read_be16(const unsigned char* p) {
    return static_cast<std::uint16_t>((static_cast<unsigned>(p[0]) << 8) | p[1]);
}

std::uint32_t read_vlq(const std::vector<unsigned char>& data, std::size_t& i) {
    std::uint32_t value = 0;
    for (int guard = 0; guard < 4; ++guard) {
        if (i >= data.size()) {
            return value;
        }
        const unsigned char b = data[i++];
        value = (value << 7) | static_cast<std::uint32_t>(b & 0x7f);
        if ((b & 0x80) == 0) {
            break;
        }
    }
    return value;
}

std::vector<GtNote> load_midi_notes(const fs::path& path) {
    std::ifstream in(path, std::ios::binary);
    std::vector<GtNote> notes;
    if (!in) {
        return notes;
    }
    std::vector<unsigned char> data((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    if (data.size() < 14 || data[0] != 'M' || data[1] != 'T' || data[2] != 'h' || data[3] != 'd') {
        return notes;
    }
    const std::uint16_t ntrks = read_be16(data.data() + 10);
    const std::uint16_t division = read_be16(data.data() + 12);
    if ((division & 0x8000) != 0 || division == 0 || ntrks == 0) {
        return notes;  // SMPTE not supported
    }
    const double ticks_per_quarter = static_cast<double>(division);

    struct TempoEvent {
        std::uint32_t tick = 0;
        double us_per_quarter = 500000.0;
    };
    std::vector<TempoEvent> tempos{{0, 500000.0}};

    struct MidiEvent {
        std::uint32_t tick = 0;
        int pitch = 0;
        int vel = 0;
        bool on = false;
    };
    std::vector<MidiEvent> events;

    std::size_t pos = 8 + read_be32(data.data() + 4);
    for (std::uint16_t tr = 0; tr < ntrks; ++tr) {
        if (pos + 8 > data.size() || data[pos] != 'M' || data[pos + 1] != 'T' || data[pos + 2] != 'r' ||
            data[pos + 3] != 'k') {
            break;
        }
        const std::uint32_t track_len = read_be32(data.data() + pos + 4);
        pos += 8;
        const std::size_t track_end = std::min(data.size(), pos + track_len);
        std::uint32_t abs_tick = 0;
        unsigned char running = 0;
        while (pos < track_end) {
            abs_tick += read_vlq(data, pos);
            if (pos >= track_end) {
                break;
            }
            unsigned char status = data[pos];
            if (status < 0x80) {
                status = running;
            } else {
                ++pos;
                running = status;
            }
            if (status == 0xff) {
                if (pos >= track_end) {
                    break;
                }
                const unsigned char type = data[pos++];
                const std::uint32_t len = read_vlq(data, pos);
                if (type == 0x51 && len == 3 && pos + 3 <= track_end) {
                    const double us = static_cast<double>((data[pos] << 16) | (data[pos + 1] << 8) | data[pos + 2]);
                    tempos.push_back(TempoEvent{abs_tick, us});
                }
                if (type == 0x2f) {
                    pos = track_end;
                    break;
                }
                pos += len;
                continue;
            }
            if (status == 0xf0 || status == 0xf7) {
                const std::uint32_t len = read_vlq(data, pos);
                pos += len;
                continue;
            }
            const unsigned char cmd = status & 0xf0;
            auto need = [&](std::size_t n) { return pos + n <= track_end; };
            if (cmd == 0x80 || cmd == 0x90) {
                if (!need(2)) {
                    break;
                }
                const int pitch = data[pos++];
                const int vel = data[pos++];
                const bool on = (cmd == 0x90) && vel > 0;
                events.push_back(MidiEvent{abs_tick, pitch, on ? vel : 0, on});
            } else if (cmd == 0xa0 || cmd == 0xb0 || cmd == 0xe0) {
                if (!need(2)) {
                    break;
                }
                pos += 2;
            } else if (cmd == 0xc0 || cmd == 0xd0) {
                if (!need(1)) {
                    break;
                }
                pos += 1;
            } else {
                break;
            }
        }
        pos = track_end;
    }

    std::sort(tempos.begin(), tempos.end(), [](const TempoEvent& a, const TempoEvent& b) {
        return a.tick < b.tick;
    });
    auto tick_to_sec = [&](std::uint32_t tick) {
        double sec = 0.0;
        std::uint32_t prev = 0;
        double uspq = tempos.front().us_per_quarter;
        for (const TempoEvent& te : tempos) {
            if (te.tick >= tick) {
                break;
            }
            sec += (static_cast<double>(te.tick - prev) * uspq) / (ticks_per_quarter * 1e6);
            prev = te.tick;
            uspq = te.us_per_quarter;
        }
        sec += (static_cast<double>(tick - prev) * uspq) / (ticks_per_quarter * 1e6);
        return sec;
    };

    std::sort(events.begin(), events.end(), [](const MidiEvent& a, const MidiEvent& b) {
        return a.tick < b.tick;
    });
    struct Active {
        std::uint32_t tick = 0;
        int vel = 0;
    };
    Active active[128]{};
    bool on[128]{};
    for (const MidiEvent& ev : events) {
        if (ev.pitch < 0 || ev.pitch > 127) {
            continue;
        }
        if (ev.on) {
            if (on[ev.pitch]) {
                notes.push_back(GtNote{tick_to_sec(active[ev.pitch].tick), tick_to_sec(ev.tick), ev.pitch});
            }
            active[ev.pitch] = Active{ev.tick, ev.vel};
            on[ev.pitch] = true;
        } else if (on[ev.pitch]) {
            notes.push_back(GtNote{tick_to_sec(active[ev.pitch].tick), tick_to_sec(ev.tick), ev.pitch});
            on[ev.pitch] = false;
        }
    }
    notes.erase(
        std::remove_if(
            notes.begin(), notes.end(),
            [](const GtNote& n) { return !(n.end > n.start) || n.pitch < 21 || n.pitch > 108; }),
        notes.end());
    return notes;
}

std::string parse_inst_class(const fs::path& yaml, const std::string& stem_id) {
    std::ifstream in(yaml);
    if (!in) {
        return {};
    }
    std::string line;
    bool in_stem = false;
    while (std::getline(in, line)) {
        if (line.find(stem_id + ":") != std::string::npos && line.find("inst_class") == std::string::npos) {
            in_stem = true;
            continue;
        }
        if (in_stem) {
            if (!line.empty() && line[0] != ' ' && line[0] != '\t') {
                break;
            }
            const auto pos = line.find("inst_class:");
            if (pos != std::string::npos) {
                std::string v = line.substr(pos + 11);
                while (!v.empty() && (v.front() == ' ' || v.front() == '\t')) {
                    v.erase(v.begin());
                }
                return v;
            }
        }
    }
    return {};
}

std::vector<StemJob> discover_jobs(const fs::path& root) {
    std::vector<StemJob> jobs;
    fs::path base = root;
    if (fs::exists(root / "babyslakh_16k")) {
        base = root / "babyslakh_16k";
    }
    if (!fs::exists(base)) {
        return jobs;
    }
    std::vector<fs::path> tracks;
    for (const auto& entry : fs::directory_iterator(base)) {
        if (entry.is_directory() && entry.path().filename().string().starts_with("Track")) {
            tracks.push_back(entry.path());
        }
    }
    std::sort(tracks.begin(), tracks.end());
    for (const fs::path& track : tracks) {
        const fs::path meta = track / "metadata.yaml";
        const fs::path stems = track / "stems";
        const fs::path midi = track / "MIDI";
        if (!fs::exists(stems) || !fs::exists(midi)) {
            continue;
        }
        for (const auto& wav_entry : fs::directory_iterator(stems)) {
            if (!wav_entry.is_regular_file() || wav_entry.path().extension() != ".wav") {
                continue;
            }
            const std::string stem_id = wav_entry.path().stem().string();
            const fs::path mid = midi / (stem_id + ".mid");
            if (!fs::exists(mid)) {
                continue;
            }
            std::string inst = parse_inst_class(meta, stem_id);
            if (inst != "Guitar" && inst != "Piano") {
                continue;
            }
            StemJob job;
            job.track = track.filename().string();
            job.stem_id = stem_id;
            job.inst = std::move(inst);
            job.wav = wav_entry.path();
            job.mid = mid;
            jobs.push_back(std::move(job));
        }
    }
    return jobs;
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

bool load_bp_activations(
    const bt::BasicPitchModel& model, const fs::path& wav, bt::PitchActivationBuffer& activations,
    std::vector<float>& mono, std::int64_t& frames, double max_sec) {
    auto audio = bt::load_audio(wav);
    if (!audio || audio->sample_rate != bt::kSampleRate) {
        std::cerr << "  load failed: " << wav.string() << "\n";
        return false;
    }
    if (max_sec > 0.0) {
        const std::int64_t keep = std::min(
            audio->frames, static_cast<std::int64_t>(max_sec * static_cast<double>(bt::kSampleRate)));
        if (keep > 0 && keep < audio->frames) {
            if (audio->channels <= 1) {
                audio->samples.resize(static_cast<std::size_t>(keep));
            } else {
                std::vector<float> clipped(static_cast<std::size_t>(keep * audio->channels));
                for (int ch = 0; ch < audio->channels; ++ch) {
                    const float* src = audio->samples.data() + static_cast<std::int64_t>(ch) * audio->frames;
                    float* dst = clipped.data() + static_cast<std::int64_t>(ch) * keep;
                    std::copy_n(src, static_cast<std::size_t>(keep), dst);
                }
                audio->samples.swap(clipped);
            }
            audio->frames = keep;
        }
    }
    const auto stereo = to_stereo_channel_major(*audio);
    auto bp_audio = bt::stem_to_basic_pitch_audio(stereo, audio->frames);
    if (!bp_audio) {
        std::cerr << "  resample failed: " << bp_audio.error() << "\n";
        return false;
    }
    auto inferred = model.infer_activations(bp_audio->data(), static_cast<std::int64_t>(bp_audio->size()));
    if (!inferred) {
        std::cerr << "  infer failed: " << inferred.error() << "\n";
        return false;
    }
    activations = std::move(*inferred);
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

void print_table(const char* title, const std::vector<VariantAgg>& aggs) {
    if (aggs.empty() || aggs[0].count <= 0) {
        return;
    }
    const double inv = 1.0 / static_cast<double>(aggs[0].count);
    const double raw_f = aggs[0].f_sum * inv;
    std::cout << "\n---- " << title << " (n=" << aggs[0].count << ") ----\n";
    std::cout << std::left << std::setw(22) << "variant" << std::right
              << "    F      P      R     nNotes   dF\n";
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
        std::cout << std::left << std::setw(22) << a.name << std::right << std::fixed << std::setprecision(3)
                  << std::setw(7) << f << std::setw(7) << p << std::setw(7) << r << std::setw(9)
                  << std::setprecision(1) << nn << std::setprecision(3) << std::setw(8) << df << mark << "\n";
    }
}

}  // namespace

int main(int argc, char** argv) {
    fs::path dataset;
    fs::path model_path = "models/nmp.onnx";
    int limit_tracks = 0;
    int limit_stems = 0;
    double max_sec = 0.0;
    bool bp_sweep = false;
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
        } else if (token == "--limit-tracks") {
            if (i + 1 >= argc) {
                std::cerr << "missing value for --limit-tracks\n";
                return 2;
            }
            limit_tracks = std::stoi(argv[++i]);
        } else if (token == "--limit-stems") {
            if (i + 1 >= argc) {
                std::cerr << "missing value for --limit-stems\n";
                return 2;
            }
            limit_stems = std::stoi(argv[++i]);
        } else if (token == "--max-sec") {
            if (i + 1 >= argc) {
                std::cerr << "missing value for --max-sec\n";
                return 2;
            }
            max_sec = std::stod(argv[++i]);
        } else if (token == "--bp-sweep") {
            bp_sweep = true;
        } else if (token == "--help" || token == "-h") {
            print_usage();
            return 0;
        } else {
            std::cerr << "unknown arg: " << token << "\n";
            print_usage();
            return 2;
        }
    }
    std::cout << std::unitbuf;
    std::cerr << std::unitbuf;

    if (dataset.empty()) {
        const fs::path candidates[] = {
            fs::path("datasets") / "BabySlakh",
            fs::path("..") / "datasets" / "BabySlakh",
            fs::path("..") / ".." / "datasets" / "BabySlakh",
            fs::path("..") / ".." / ".." / "datasets" / "BabySlakh",
        };
        for (const fs::path& c : candidates) {
            if (fs::exists(c)) {
                dataset = c;
                break;
            }
        }
    }
    if (dataset.empty() || !fs::exists(dataset)) {
        std::cerr << "BabySlakh dataset not found; pass --dataset\n";
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

    auto jobs = discover_jobs(dataset);
    if (jobs.empty()) {
        std::cerr << "no Guitar/Piano stems found under " << dataset.string() << "\n";
        return 1;
    }
    if (limit_tracks > 0) {
        std::vector<StemJob> filtered;
        std::string last;
        int tracks = 0;
        for (const StemJob& j : jobs) {
            if (j.track != last) {
                if (tracks >= limit_tracks) {
                    break;
                }
                last = j.track;
                ++tracks;
            }
            filtered.push_back(j);
        }
        jobs.swap(filtered);
    }
    if (limit_stems > 0 && static_cast<int>(jobs.size()) > limit_stems) {
        jobs.resize(static_cast<std::size_t>(limit_stems));
    }

    auto prepare_gt = [&](std::vector<GtNote> gt) {
        if (max_sec > 0.0) {
            gt.erase(
                std::remove_if(
                    gt.begin(), gt.end(),
                    [&](const GtNote& n) { return n.start >= max_sec; }),
                gt.end());
            for (GtNote& n : gt) {
                n.end = std::min(n.end, max_sec);
            }
            gt.erase(
                std::remove_if(gt.begin(), gt.end(), [](const GtNote& n) { return !(n.end > n.start); }),
                gt.end());
        }
        return gt;
    };

    if (bp_sweep) {
        const auto grid = bp_param_grid();
        std::vector<VariantAgg> all_aggs;
        all_aggs.reserve(grid.size());
        for (const NamedParams& np : grid) {
            all_aggs.push_back(VariantAgg{np.name});
        }
        std::vector<VariantAgg> guitar_aggs = all_aggs;
        std::vector<VariantAgg> piano_aggs = all_aggs;

        std::cout << "BabySlakh BP param sweep  onset<=50ms pitch<=0.5\n"
                  << "dataset=" << fs::weakly_canonical(dataset).string() << "\n"
                  << "model=" << fs::weakly_canonical(model_path).string() << "\n"
                  << "stems=" << jobs.size() << "  grid=" << grid.size();
        if (max_sec > 0.0) {
            std::cout << "  max_sec=" << max_sec;
        }
        std::cout << "\n\n";

        for (const StemJob& job : jobs) {
            auto gt = prepare_gt(load_midi_notes(job.mid));
            if (gt.empty()) {
                continue;
            }
            std::cout << "=== " << job.track << "/" << job.stem_id << " (" << job.inst << ") gt=" << gt.size()
                      << " ===\n";
            bt::PitchActivationBuffer activations;
            std::vector<float> mono;
            std::int64_t frames = 0;
            if (!load_bp_activations(*model, job.wav, activations, mono, frames, max_sec)) {
                continue;
            }
            const auto view = activations.view();
            auto apply = [&](std::vector<VariantAgg>& bucket) {
                for (std::size_t i = 0; i < grid.size(); ++i) {
                    const auto notes = bt::notes_from_activations(view, grid[i].parameters);
                    add_agg(bucket[i], note_f_measure(notes, gt));
                }
            };
            apply(all_aggs);
            if (job.inst == "Guitar") {
                apply(guitar_aggs);
            } else {
                apply(piano_aggs);
            }
            const auto def = bt::notes_from_activations(view, bt::PitchParameters{});
            std::cout << "  default_notes=" << def.size() << "\n";
        }

        auto print_top = [](const char* title, const std::vector<VariantAgg>& aggs, int top_n) {
            if (aggs.empty() || aggs[0].count <= 0) {
                return;
            }
            const double inv = 1.0 / static_cast<double>(aggs[0].count);
            double base_f = 0.0;
            for (const VariantAgg& a : aggs) {
                if (a.name == "o0.20_f0.20_m128") {
                    base_f = a.f_sum * inv;
                    break;
                }
            }
            std::vector<std::pair<double, std::size_t>> order;
            for (std::size_t i = 0; i < aggs.size(); ++i) {
                order.emplace_back(aggs[i].f_sum * inv, i);
            }
            std::sort(order.begin(), order.end(), [](const auto& a, const auto& b) {
                return a.first > b.first;
            });
            std::cout << "\n---- " << title << " top" << top_n << " (n=" << aggs[0].count << ") ----\n";
            std::cout << std::left << std::setw(22) << "variant" << std::right
                      << "    F      P      R     nNotes   dF\n";
            const int show = std::min(top_n, static_cast<int>(order.size()));
            for (int k = 0; k < show; ++k) {
                const std::size_t i = order[static_cast<std::size_t>(k)].second;
                const VariantAgg& a = aggs[i];
                const double f = a.f_sum * inv;
                const double p = a.p_sum * inv;
                const double r = a.r_sum * inv;
                const double nn = a.n_sum * inv;
                const double df = f - base_f;
                const char* mark = (a.name == "o0.20_f0.20_m128") ? " *" : (df > 0.002 ? " +" : "  ");
                std::cout << std::left << std::setw(22) << a.name << std::right << std::fixed
                          << std::setprecision(3) << std::setw(7) << f << std::setw(7) << p << std::setw(7)
                          << r << std::setw(9) << std::setprecision(1) << nn << std::setprecision(3)
                          << std::setw(8) << df << mark << "\n";
            }
        };

        print_top("ALL Guitar+Piano", all_aggs, 12);
        print_top("Guitar only", guitar_aggs, 12);
        print_top("Piano only", piano_aggs, 12);
        std::cout << "\n* = default o0.20/f0.20/m128; dF vs that default\n";
        return 0;
    }

    const auto deghosts = bt::deghost_ablation_presets();
    std::vector<VariantAgg> all_aggs;
    all_aggs.push_back(VariantAgg{"raw_bp"});
    all_aggs.push_back(VariantAgg{"energy"});
    for (const bt::DeghostConfig& d : deghosts) {
        all_aggs.push_back(VariantAgg{std::string("e+") + (d.name ? d.name : "?")});
    }
    std::vector<VariantAgg> guitar_aggs = all_aggs;
    std::vector<VariantAgg> piano_aggs = all_aggs;

    std::cout << "BabySlakh deghost ablation  onset<=50ms pitch<=0.5\n"
              << "dataset=" << fs::weakly_canonical(dataset).string() << "\n"
              << "model=" << fs::weakly_canonical(model_path).string() << "\n"
              << "stems=" << jobs.size() << "  deghost_variants=" << deghosts.size();
    if (max_sec > 0.0) {
        std::cout << "  max_sec=" << max_sec;
    }
    std::cout << "\n\n";

    for (const StemJob& job : jobs) {
        auto gt = prepare_gt(load_midi_notes(job.mid));
        if (gt.empty()) {
            std::cerr << "  empty midi: " << job.mid.string() << "\n";
            continue;
        }
        std::cout << "=== " << job.track << "/" << job.stem_id << " (" << job.inst << ") gt=" << gt.size()
                  << " ===\n";
        bt::PitchActivationBuffer activations;
        std::vector<float> mono;
        std::int64_t frames = 0;
        if (!load_bp_activations(*model, job.wav, activations, mono, frames, max_sec)) {
            continue;
        }
        const auto bp = bt::notes_from_activations(activations.view(), bt::PitchParameters{});
        const auto energy = bt::mask_notes_by_midi_energy(
            bp.data(), bp.size(), mono.data(), frames, bt::kSampleRate, bt::energy_mask_default());

        auto apply = [&](std::vector<VariantAgg>& bucket) {
            add_agg(bucket[0], note_f_measure(bp, gt));
            add_agg(bucket[1], note_f_measure(energy, gt));
            for (std::size_t di = 0; di < deghosts.size(); ++di) {
                const auto out = bt::deghost_harmonic_notes(
                    energy.data(), energy.size(), mono.data(), frames, bt::kSampleRate, deghosts[di]);
                add_agg(bucket[di + 2], note_f_measure(out, gt));
            }
        };
        apply(all_aggs);
        if (job.inst == "Guitar") {
            apply(guitar_aggs);
        } else {
            apply(piano_aggs);
        }
        std::cout << "  bp=" << bp.size() << " energy=" << energy.size() << "\n";
    }

    print_table("ALL Guitar+Piano", all_aggs);
    print_table("Guitar only", guitar_aggs);
    print_table("Piano only", piano_aggs);
    std::cout << "\n* = raw BP baseline; + = clearly above raw; - = clearly below raw\n"
              << "e+X = energy mask then deghost variant X\n";
    return 0;
}
