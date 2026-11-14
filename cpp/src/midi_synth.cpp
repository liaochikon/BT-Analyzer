#include "bt/midi_synth.hpp"

#include "bt/pitch_mask.hpp"

#include <algorithm>
#include <cmath>
#include <numbers>

namespace bt {
namespace {

float envelope(double time_sec, double start, double end, float fade) {
    if (time_sec < start || time_sec >= end) {
        return 0.f;
    }
    const float attack = static_cast<float>(time_sec - start);
    const float release = static_cast<float>(end - time_sec);
    float gain = 1.f;
    if (attack < fade) {
        gain = std::min(gain, attack / fade);
    }
    if (release < fade) {
        gain = std::min(gain, release / fade);
    }
    return std::clamp(gain, 0.f, 1.f);
}

}  // namespace

void MidiSynth::reset() {
    for (Voice& voice : voices_) {
        voice.live = false;
        voice.phase = 0;
    }
}

void MidiSynth::mix(
    float* interleaved, int frames, int start_sample, int lo_midi, int hi_midi, float gain,
    const MidiSynthNote* notes, std::size_t note_count, int sample_rate, float pan_l, float pan_r) {
    if (!interleaved || frames <= 0 || sample_rate <= 0 || gain <= 0.f || notes == nullptr ||
        note_count == 0) {
        return;
    }
    if (lo_midi > hi_midi) {
        std::swap(lo_midi, hi_midi);
    }
    const double inv_sr = 1.0 / static_cast<double>(sample_rate);
    const double two_pi = 2.0 * std::numbers::pi_v<double>;
    const double pi = std::numbers::pi_v<double>;
    const double block_start = static_cast<double>(start_sample) * inv_sr;
    const double block_end = (static_cast<double>(start_sample) + static_cast<double>(frames)) * inv_sr;
    const float fade = kFadeSeconds;

    struct Active {
        const MidiSynthNote* note = nullptr;
        Voice* voice = nullptr;
        double omega = 0;
    };
    Active active[kMaxVoices];
    int active_count = 0;

    for (std::size_t n = 0; n < note_count && active_count < kMaxVoices; ++n) {
        const MidiSynthNote& note = notes[n];
        if (note.pitch < lo_midi || note.pitch > hi_midi || note.end <= note.start) {
            continue;
        }
        if (note.end <= block_start || note.start >= block_end) {
            continue;
        }
        Voice* voice = nullptr;
        for (Voice& candidate : voices_) {
            if (candidate.live && candidate.pitch == note.pitch && candidate.start == note.start &&
                candidate.end == note.end) {
                voice = &candidate;
                break;
            }
        }
        if (voice == nullptr) {
            for (Voice& candidate : voices_) {
                if (!candidate.live) {
                    voice = &candidate;
                    voice->pitch = note.pitch;
                    voice->start = note.start;
                    voice->end = note.end;
                    voice->velocity = note.velocity;
                    voice->phase = 0;
                    voice->live = true;
                    break;
                }
            }
        }
        if (voice == nullptr) {
            continue;
        }
        active[active_count] = {
            &note, voice, two_pi * static_cast<double>(midi_note_to_hz(static_cast<float>(note.pitch))) * inv_sr};
        ++active_count;
    }

    for (Voice& voice : voices_) {
        if (!voice.live) {
            continue;
        }
        bool keep = false;
        for (int i = 0; i < active_count; ++i) {
            if (active[i].voice == &voice) {
                keep = true;
                break;
            }
        }
        if (!keep && (block_end <= voice.start || block_start >= voice.end)) {
            voice.live = false;
            voice.phase = 0;
        }
    }

    for (int i = 0; i < frames; ++i) {
        const double time_sec = (static_cast<double>(start_sample) + static_cast<double>(i)) * inv_sr;
        float sample = 0.f;
        for (int v = 0; v < active_count; ++v) {
            const MidiSynthNote& note = *active[v].note;
            Voice& voice = *active[v].voice;
            if (time_sec < note.start || time_sec >= note.end) {
                continue;
            }
            const float env = envelope(time_sec, note.start, note.end, fade);
            // Square peak amp (~sine * 0.14 * +6 dB).
            const float amp =
                gain * env * (static_cast<float>(std::clamp(note.velocity, 1, 127)) / 127.f) * 0.28f;
            const float square = voice.phase < pi ? 1.f : -1.f;
            sample += square * amp;
            voice.phase += active[v].omega;
            if (voice.phase > two_pi) {
                voice.phase = std::fmod(voice.phase, two_pi);
            }
        }
        interleaved[i * 2] += sample * pan_l;
        interleaved[i * 2 + 1] += sample * pan_r;
    }
}

}  // namespace bt
