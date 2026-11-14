#pragma once

#include "bt/contract.hpp"

#include <cstddef>
#include <cstdint>
#include <vector>

namespace bt {

struct MidiSynthNote {
    int pitch = 0;
    double start = 0;
    double end = 0;
    int velocity = 100;
};

// Polyphonic square voices locked to the song timeline (sample indices at kSampleRate).
class MidiSynth {
public:
    static constexpr int kMaxVoices = 32;
    static constexpr float kFadeSeconds = 0.004f;

    void reset();
    // Mix mono square into interleaved stereo. Notes outside [lo_midi, hi_midi] are silent.
    void mix(
        float* interleaved, int frames, int start_sample, int lo_midi, int hi_midi, float gain,
        const MidiSynthNote* notes, std::size_t note_count, int sample_rate = kSampleRate,
        float pan_l = 1.f, float pan_r = 1.f);

private:
    struct Voice {
        int pitch = 0;
        double start = 0;
        double end = 0;
        int velocity = 100;
        double phase = 0;
        bool live = false;
    };

    std::vector<Voice> voices_{static_cast<std::size_t>(kMaxVoices)};
};

}  // namespace bt
