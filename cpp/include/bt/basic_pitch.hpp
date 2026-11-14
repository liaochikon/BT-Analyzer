#pragma once

#include "bt/separator.hpp"

#include <cstdint>
#include <expected>
#include <filesystem>
#include <functional>
#include <memory>
#include <string>
#include <vector>

namespace bt {

struct Note {
    int pitch = 0;
    double start = 0;
    double end = 0;
    int velocity = 100;
};

struct PitchParameters {
    bool melodia = false;
    float onset_threshold = 0.2f;
    float frame_threshold = 0.2f;
    float minimum_note_ms = 127.7f;
    float minimum_frequency = 0.f;
    float maximum_frequency = 0.f;
};

// Per-stem decode defaults: bass band-limits, vocals melodia,
// guitar/piano use stricter onset/frame from BabySlakh sweep; other stays 0.2/0.2.
// Drums skip pitch transcription entirely (empty MIDI).
PitchParameters pitch_parameters_for_stem(int stem);
inline bool stem_skips_pitch(int stem) {
    return stem == 0;  // drums
}

// Row-major activations, time major then 88 piano bins starting at MIDI 21.
struct PitchActivations {
    int frames = 0;
    const float* note = nullptr;
    const float* onset = nullptr;
};

struct PitchActivationBuffer {
    int frames = 0;
    std::vector<float> note;
    std::vector<float> onset;

    PitchActivations view() const {
        return PitchActivations{frames, note.data(), onset.data()};
    }
};

std::vector<Note> notes_from_activations(const PitchActivations& activations, const PitchParameters& parameters);

std::expected<void, std::string> save_notes(const std::filesystem::path& path, const std::vector<Note>& notes);
std::expected<std::vector<Note>, std::string> load_notes(const std::filesystem::path& path);

class BasicPitchModel {
public:
    static std::expected<BasicPitchModel, std::string> open(const std::filesystem::path& model_path);

    BasicPitchModel(BasicPitchModel&&) noexcept;
    BasicPitchModel& operator=(BasicPitchModel&&) noexcept;
    ~BasicPitchModel();

    BasicPitchModel(const BasicPitchModel&) = delete;
    BasicPitchModel& operator=(const BasicPitchModel&) = delete;

    // Mono float audio at 22050 Hz, the full stem from t = 0. ONNX only; decode with notes_from_activations.
    std::expected<PitchActivationBuffer, std::string> infer_activations(
        const float* samples, std::int64_t frames, const std::function<void(float)>& progress = {},
        const std::function<bool()>& cancelled = {}) const;

    // Mono float audio at 22050 Hz, the full stem from t = 0.
    std::expected<std::vector<Note>, std::string> transcribe(
        const float* samples, std::int64_t frames, const PitchParameters& parameters,
        const std::function<void(float)>& progress = {},
        const std::function<bool()>& cancelled = {}) const;

private:
    struct Impl;
    explicit BasicPitchModel(std::unique_ptr<Impl> impl);
    std::unique_ptr<Impl> impl_;
};

// Average a stereo channel-major stem and resample it to 22050 Hz mono.
std::expected<std::vector<float>, std::string> stem_to_basic_pitch_audio(const std::vector<float>& channel_major, std::int64_t frames);

}  // namespace bt
