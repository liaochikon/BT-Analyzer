"""Standalone ONNX inference and note decoding for the Basic Pitch model."""

import numpy as np

from core.audio_io import resample_audio


AUDIO_SAMPLE_RATE = 22050
FFT_HOP = 256
ANNOTATIONS_FPS = AUDIO_SAMPLE_RATE // FFT_HOP
AUDIO_N_SAMPLES = AUDIO_SAMPLE_RATE * 2 - FFT_HOP
ANNOTATION_FRAMES = ANNOTATIONS_FPS * 2
MIDI_OFFSET = 21
MAX_FREQ_INDEX = 87
OVERLAPPING_FRAMES = 30

INPUT_NAME = "serving_default_input_2:0"
OUTPUT_NAMES = (
    "StatefulPartitionedCall:1",
    "StatefulPartitionedCall:2",
    "StatefulPartitionedCall:0",
)


def run_inference(audio, *, sample_rate: int, session, resample=None):
    mono = _as_mono(audio)
    if sample_rate != AUDIO_SAMPLE_RATE:
        mono = resample_audio(
            mono, sample_rate, AUDIO_SAMPLE_RATE, resample=resample)

    original_length = len(mono)
    overlap_samples = OVERLAPPING_FRAMES * FFT_HOP
    hop_size = AUDIO_N_SAMPLES - overlap_samples
    padded = np.concatenate((np.zeros(overlap_samples // 2, dtype=np.float32), mono))
    outputs = {"note": [], "onset": [], "contour": []}

    for start in range(0, len(padded), hop_size):
        window = padded[start:start + AUDIO_N_SAMPLES]
        if len(window) < AUDIO_N_SAMPLES:
            window = np.pad(window, (0, AUDIO_N_SAMPLES - len(window)))
        batch = np.ascontiguousarray(window[None, :, None], dtype=np.float32)
        values = session.run(list(OUTPUT_NAMES), {INPUT_NAME: batch})
        for name, value in zip(outputs, values):
            outputs[name].append(value)

    return {
        name: _unwrap(np.concatenate(values), original_length)
        for name, values in outputs.items()
    }


def decode_notes(model_output, *, onset_threshold: float, frame_threshold: float,
                 minimum_note_length_ms: float, minimum_frequency: float | None,
                 maximum_frequency: float | None, melodia_trick: bool):
    frames = np.array(model_output["note"], copy=True)
    onsets = np.array(model_output["onset"], copy=True)
    _constrain_frequency(onsets, frames, minimum_frequency, maximum_frequency)
    onsets = _infer_onsets(onsets, frames)
    minimum_frames = int(round(
        minimum_note_length_ms / 1000 * (AUDIO_SAMPLE_RATE / FFT_HOP)))
    frame_events = _decode_polyphonic(
        frames,
        onsets,
        onset_threshold,
        frame_threshold,
        minimum_frames,
        melodia_trick,
    )
    times = _frame_times(model_output["contour"].shape[0])
    return [
        (float(times[start]), float(times[end]), pitch, float(amplitude))
        for start, end, pitch, amplitude in frame_events
    ]


def _as_mono(audio):
    normalized = np.ascontiguousarray(audio, dtype=np.float32)
    if normalized.ndim == 1:
        return normalized
    if normalized.ndim != 2:
        raise ValueError(f"expected mono or channel-first audio, got shape {normalized.shape}")
    return np.ascontiguousarray(normalized.mean(axis=0), dtype=np.float32)


def _unwrap(output, original_length):
    trim = OVERLAPPING_FRAMES // 2
    output = output[:, trim:-trim, :]
    frames = output.reshape(output.shape[0] * output.shape[1], output.shape[2])
    original_frames = int(np.floor(original_length * ANNOTATIONS_FPS / AUDIO_SAMPLE_RATE))
    return frames[:original_frames]


def _midi_frequency_index(frequency):
    midi = 69.0 + 12.0 * np.log2(float(frequency) / 440.0)
    return int(round(midi - MIDI_OFFSET))


def _constrain_frequency(onsets, frames, minimum_frequency, maximum_frequency):
    if maximum_frequency is not None:
        index = int(np.clip(_midi_frequency_index(maximum_frequency), 0, frames.shape[1]))
        onsets[:, index:] = 0
        frames[:, index:] = 0
    if minimum_frequency is not None:
        index = int(np.clip(_midi_frequency_index(minimum_frequency), 0, frames.shape[1]))
        onsets[:, :index] = 0
        frames[:, :index] = 0


def _infer_onsets(onsets, frames, differences=2):
    frame_differences = []
    for offset in range(1, differences + 1):
        prefixed = np.concatenate((np.zeros((offset, frames.shape[1])), frames))
        frame_differences.append(prefixed[offset:] - prefixed[:-offset])
    frame_difference = np.min(frame_differences, axis=0)
    frame_difference[frame_difference < 0] = 0
    frame_difference[:differences] = 0
    maximum = np.max(frame_difference)
    if maximum > 0:
        frame_difference *= np.max(onsets) / maximum
    return np.maximum(onsets, frame_difference)


def _decode_polyphonic(frames, onsets, onset_threshold, frame_threshold,
                       minimum_frames, melodia_trick, energy_tolerance=11):
    frame_count = frames.shape[0]
    peaks = np.zeros_like(onsets)
    local_maxima = (
        (onsets[1:-1] > onsets[:-2])
        & (onsets[1:-1] > onsets[2:])
    )
    peaks[1:-1] = np.where(local_maxima, onsets[1:-1], 0)
    onset_time, onset_pitch = np.where(peaks >= onset_threshold)
    remaining = np.array(frames, copy=True)
    events = []

    for start, pitch_index in zip(onset_time[::-1], onset_pitch[::-1]):
        if start >= frame_count - 1:
            continue
        end = _trace_forward(remaining, start + 1, pitch_index,
                             frame_threshold, energy_tolerance, clear=False)
        if end - start <= minimum_frames:
            continue
        amplitude = float(np.mean(frames[start:end, pitch_index]))
        _clear_pitch_region(remaining, start, end, pitch_index)
        events.append((start, end, pitch_index + MIDI_OFFSET, amplitude))

    if melodia_trick:
        while remaining.size and np.max(remaining) > frame_threshold:
            middle, pitch_index = np.unravel_index(np.argmax(remaining), remaining.shape)
            remaining[middle, pitch_index] = 0
            end = _trace_forward(remaining, middle + 1, pitch_index,
                                 frame_threshold, energy_tolerance, clear=True)
            start = _trace_backward(remaining, middle - 1, pitch_index,
                                    frame_threshold, energy_tolerance)
            if end - start <= minimum_frames:
                continue
            amplitude = float(np.mean(frames[start:end, pitch_index]))
            events.append((start, end, pitch_index + MIDI_OFFSET, amplitude))
    return events


def _trace_forward(energy, index, pitch_index, threshold, tolerance, *, clear):
    below = 0
    while index < energy.shape[0] - 1 and below < tolerance:
        below = below + 1 if energy[index, pitch_index] < threshold else 0
        if clear:
            _clear_pitch_region(energy, index, index + 1, pitch_index)
        index += 1
    return index - below


def _trace_backward(energy, index, pitch_index, threshold, tolerance):
    below = 0
    while index > 0 and below < tolerance:
        below = below + 1 if energy[index, pitch_index] < threshold else 0
        _clear_pitch_region(energy, index, index + 1, pitch_index)
        index -= 1
    return index + 1 + below


def _clear_pitch_region(energy, start, end, pitch_index):
    low = max(0, pitch_index - 1)
    high = min(energy.shape[1], pitch_index + 2)
    energy[start:end, low:high] = 0


def _frame_times(frame_count):
    frame_indices = np.arange(frame_count)
    original = frame_indices * FFT_HOP / AUDIO_SAMPLE_RATE
    window_numbers = np.floor(frame_indices / ANNOTATION_FRAMES)
    window_offset = (FFT_HOP / AUDIO_SAMPLE_RATE) * (
        ANNOTATION_FRAMES - AUDIO_N_SAMPLES / FFT_HOP) + 0.0018
    return original - window_offset * window_numbers