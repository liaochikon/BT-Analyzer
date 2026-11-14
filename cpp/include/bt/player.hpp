#pragma once

#include "bt/midi_synth.hpp"
#include "bt/pitch_mask.hpp"
#include "bt/separator.hpp"

#include <array>
#include <atomic>
#include <cstdint>
#include <expected>
#include <memory>
#include <string>
#include <string_view>
#include <vector>

namespace bt {

struct PlaybackDevice {
    std::string id;
    std::string name;
};

// Plays the stem mix. The device callback only reads a ring buffer.
// Seek, speed, and loop positions are on the song timeline (the waveform).
class Player {
public:
    enum class PitchMaskBackend { Dsp, Spectral, Harmonic };

    struct VerifyLane {
        float gain = 1.f;
        float pan = 0.f;
        bool mute = false;
        bool solo = false;
    };

    struct Snapshot {
        bool playing = false;
        bool ready = false;
        double position = 0;
        double length = 0;
        float speed = 1.f;
        float master_db = 0.f;
        bool loop = false;
        double loop_a = -1;
        double loop_b = -1;
        bool counting = false;
    };

    Player();
    ~Player();

    Player(const Player&) = delete;
    Player& operator=(const Player&) = delete;

    void set_song(std::shared_ptr<const Stems> stems);
    void clear_song();
    void set_mix(
        const std::array<float, kStemCount>& gain, const std::array<bool, kStemCount>& mute,
        const std::array<bool, kStemCount>& solo, const std::array<float, kStemCount>& pan = {});
    // Per-stem verify submix: dry original + MIDI square (additive, not a stem replace).
    // Each stem is independent; solo/mute only affect that stem's original vs MIDI lanes.
    void set_verify_mix(
        int stem, const VerifyLane& original, const VerifyLane& midi, int lo_midi, int hi_midi,
        std::shared_ptr<const std::vector<MidiSynthNote>> notes = {},
        std::uint64_t notes_revision = 0);
    // Band-limits or harmonic-masks the selected stem to the MIDI viewport.
    // Spectral/Harmonic rebuild on a background worker (UI only publishes; no hitch).
    void set_pitch_mask(
        bool enabled, int stem, int lo_midi, int hi_midi,
        PitchMaskBackend backend = PitchMaskBackend::Harmonic,
        std::shared_ptr<const std::vector<PitchMaskNote>> notes = {},
        std::uint64_t notes_revision = 0);
    // Blocks until the latest pitch-mask job is applied (for tests / sync render).
    void wait_pitch_mask();
    bool pitch_mask_building() const;
    float pitch_mask_progress() const;

    void play();
    void pause();
    void stop();
    void count_in(int beats);
    void seek(double seconds);
    void set_speed(float speed);
    void set_master_db(float db);
    void set_loop_enabled(bool enabled);
    void set_loop_a(double seconds);
    void set_loop_b(double seconds);
    void clear_loop();
    void set_metronome(
        bool enabled, float bpm, float click_db, double beat_offset, bool follow_beats = false,
        std::shared_ptr<const std::vector<double>> beats = {});

    Snapshot snapshot() const;
    // Synchronous path for tests. Do not call it while the device thread is running.
    int render(float* interleaved, int frames);
    std::vector<PlaybackDevice> playback_devices();
    std::expected<void, std::string> start_device();
    std::expected<void, std::string> use_device(std::string_view id);
    const std::string& device_error() const { return device_error_; }

private:
    bool ensure_context();
    std::expected<void, std::string> open_playback(const void* device_id);
    struct Mix;
    struct PitchMask;
    struct VerifyMix;

    void wake();
    void discard_ring();
    void install_song();
    void apply_transport();
    void reset_pitch_mask_filters();
    void sync_pitch_mask_filters();
    void clear_spectral_mask();
    void request_spectral_mask_rebuild();
    void mask_thread_main();
    bool spectral_cache_matches(const PitchMask& mask, const Stems* stems) const;
    void reset_midi_synth();
    int read_source(int start, int frames, float* interleaved);
    int synthesize(float* interleaved, int frames);
    void arm_click(bool downbeat);
    void accept_count_in();
    int render_count_in(float* interleaved, int frames);
    void render_click(float* interleaved, int frames);
    void mix_metronome(float* interleaved, int frames, double from, double to);
    int ring_space() const;
    void ring_write(const float* interleaved, int frames);
    int ring_read(float* interleaved, int frames);
    void thread_main();

    struct State;
    std::unique_ptr<State> state_;
    std::string device_error_;
};

}  // namespace bt
