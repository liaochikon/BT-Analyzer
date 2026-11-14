#include "bt/basic_pitch.hpp"

#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_floating_point.hpp>

#include <filesystem>
#include <vector>

namespace {

void fill_bin(std::vector<float>& matrix, int time, int bin, float value) {
    matrix[static_cast<std::size_t>(time * 88 + bin)] = value;
}

}  // namespace

TEST_CASE("an onset grows into one note") {
    constexpr int kFrames = 40;
    constexpr int kBin = 40;
    std::vector<float> note(static_cast<std::size_t>(kFrames * 88), 0.f);
    std::vector<float> onset = note;
    fill_bin(onset, 8, kBin, 0.9f);
    for (int time = 8; time < 36; ++time) {
        fill_bin(note, time, kBin, 0.8f);
    }
    const auto notes = bt::notes_from_activations(bt::PitchActivations{kFrames, note.data(), onset.data()}, {});
    REQUIRE(notes.size() == 1);
    REQUIRE(notes[0].pitch == kBin + 21);
    REQUIRE(notes[0].end > notes[0].start);
    REQUIRE(notes[0].velocity > 1);
}

TEST_CASE("a short onset is dropped") {
    constexpr int kFrames = 40;
    std::vector<float> note(static_cast<std::size_t>(kFrames * 88), 0.f);
    std::vector<float> onset = note;
    fill_bin(onset, 8, 10, 0.9f);
    for (int time = 8; time < 12; ++time) {
        fill_bin(note, time, 10, 0.8f);
    }
    const auto notes = bt::notes_from_activations(bt::PitchActivations{kFrames, note.data(), onset.data()}, {});
    REQUIRE(notes.empty());
}

TEST_CASE("a frequency window removes notes outside it") {
    constexpr int kFrames = 40;
    std::vector<float> note(static_cast<std::size_t>(kFrames * 88), 0.f);
    std::vector<float> onset = note;
    fill_bin(onset, 8, 40, 0.9f);
    for (int time = 8; time < 36; ++time) {
        fill_bin(note, time, 40, 0.8f);
    }
    bt::PitchParameters parameters;
    parameters.minimum_frequency = 2000.f;
    const auto notes = bt::notes_from_activations(bt::PitchActivations{kFrames, note.data(), onset.data()}, parameters);
    REQUIRE(notes.empty());
}

TEST_CASE("melodia keeps a sustained tone that has no onset") {
    constexpr int kFrames = 48;
    constexpr int kBin = 20;
    std::vector<float> note(static_cast<std::size_t>(kFrames * 88), 0.f);
    std::vector<float> onset(note.size(), 0.f);
    for (int time = 8; time < 40; ++time) {
        fill_bin(note, time, kBin, 0.85f);
    }
    bt::PitchParameters parameters;
    parameters.melodia = true;
    const auto notes = bt::notes_from_activations(bt::PitchActivations{kFrames, note.data(), onset.data()}, parameters);
    REQUIRE(notes.size() == 1);
    REQUIRE(notes[0].pitch == kBin + 21);
}

TEST_CASE("stem parameters give bass a lower range and vocals the melodia step") {
    const auto bass = bt::pitch_parameters_for_stem(1);
    const auto other = bt::pitch_parameters_for_stem(2);
    const auto guitar = bt::pitch_parameters_for_stem(4);
    const auto piano = bt::pitch_parameters_for_stem(5);
    const auto vocals = bt::pitch_parameters_for_stem(3);
    REQUIRE(bass.maximum_frequency == 400.f);
    REQUIRE(guitar.maximum_frequency == 0.f);
    REQUIRE(bass.minimum_frequency == 30.f);
    REQUIRE_FALSE(guitar.melodia);
    REQUIRE_FALSE(piano.melodia);
    REQUIRE_FALSE(other.melodia);
    REQUIRE(other.maximum_frequency == 0.f);
    REQUIRE_THAT(other.onset_threshold, Catch::Matchers::WithinAbs(0.20, 1e-6));
    REQUIRE_THAT(other.frame_threshold, Catch::Matchers::WithinAbs(0.20, 1e-6));
    REQUIRE(vocals.melodia);
    REQUIRE(bt::stem_skips_pitch(0));
    REQUIRE_FALSE(bt::stem_skips_pitch(2));
    REQUIRE_THAT(guitar.onset_threshold, Catch::Matchers::WithinAbs(0.30, 1e-6));
    REQUIRE_THAT(guitar.frame_threshold, Catch::Matchers::WithinAbs(0.25, 1e-6));
    REQUIRE_THAT(piano.onset_threshold, Catch::Matchers::WithinAbs(0.35, 1e-6));
    REQUIRE_THAT(piano.frame_threshold, Catch::Matchers::WithinAbs(0.30, 1e-6));
    REQUIRE_THAT(piano.minimum_note_ms, Catch::Matchers::WithinAbs(180.0, 1e-6));
}

TEST_CASE("the bundled model accepts one silent window") {
    const std::filesystem::path candidates[] = {
        "models/nmp.onnx",
        "../models/nmp.onnx",
        "../../models/nmp.onnx",
        "../../../models/nmp.onnx",
    };
    std::filesystem::path model_path;
    for (const auto& candidate : candidates) {
        if (std::filesystem::exists(candidate)) {
            model_path = candidate;
            break;
        }
    }
    if (model_path.empty()) {
        SKIP("models/nmp.onnx is not present");
    }
    auto model = bt::BasicPitchModel::open(model_path);
    REQUIRE(model);
    std::vector<float> silence(22050, 0.f);
    auto notes = model->transcribe(silence.data(), static_cast<std::int64_t>(silence.size()), {});
    REQUIRE(notes);
    REQUIRE(notes->empty());
}

TEST_CASE("notes round-trip through the cache file") {
    const auto path = std::filesystem::temp_directory_path() / "bt-notes-roundtrip.notes";
    std::filesystem::remove(path);
    const std::vector<bt::Note> notes{{60, 0.25, 0.75, 96}, {64, 1.0, 1.5, 80}};
    REQUIRE(bt::save_notes(path, notes));
    const auto loaded = bt::load_notes(path);
    REQUIRE(loaded);
    REQUIRE(loaded->size() == 2);
    REQUIRE((*loaded)[0].pitch == 60);
    REQUIRE_THAT((*loaded)[0].start, Catch::Matchers::WithinAbs(0.25, 1e-6));
    REQUIRE((*loaded)[1].velocity == 80);
    const std::vector<bt::Note> replaced{{62, 0.5, 1.25, 70}};
    REQUIRE(bt::save_notes(path, replaced));
    const auto overwritten = bt::load_notes(path);
    REQUIRE(overwritten);
    REQUIRE(overwritten->size() == 1);
    REQUIRE((*overwritten)[0].pitch == 62);
    std::filesystem::remove(path);
}
