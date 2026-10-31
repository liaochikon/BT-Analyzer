"""Headless Audio Velocity Map processor contracts."""

import numpy as np
import pytest

from core.base_node import DataPacket, DataType
from core.datatypes import NOTE_DTYPE
from nodes.audio_velocity_map_node.processor import (
    AudioVelocityMapParameters,
    AudioVelocityMapProcessor,
    AudioVelocityMapStatus,
)


def _parameters():
    return AudioVelocityMapParameters(1.0, 1.0, 48.0, 0.0)


def test_pitch_to_freq():
    processor = AudioVelocityMapProcessor()
    assert processor.pitch_to_freq(69) == pytest.approx(440.0)
    assert processor.pitch_to_freq(81) == pytest.approx(880.0)
    assert processor.pitch_to_freq(57) == pytest.approx(220.0)
    assert processor.pitch_to_freq(60) == pytest.approx(261.6256, abs=1e-3)


def test_amps_to_velocities_loudest_is_max():
    # The loudest note maps to 127 (it's the 0 dB reference).
    vel = AudioVelocityMapProcessor.amps_to_velocities(
        np.array([1.0, 0.5, 0.25]), dynamic_range_db=48.0, gain_db=0.0)
    assert vel[0] == 127


def test_amps_to_velocities_floor_at_dynamic_range():
    dyn = 40.0
    quiet = 10.0 ** (-dyn / 20.0)          # exactly `dyn` dB below the loudest
    vel = AudioVelocityMapProcessor.amps_to_velocities(
        np.array([1.0, quiet]), dynamic_range_db=dyn, gain_db=0.0)
    assert vel[0] == 127 and vel[1] == 1   # a note at/below the window bottom -> velocity 1


def test_amps_to_velocities_all_silent_is_floor():
    vel = AudioVelocityMapProcessor.amps_to_velocities(
        np.zeros(4), dynamic_range_db=48.0, gain_db=0.0)
    assert list(vel) == [1, 1, 1, 1]       # ref <= 0 -> everything at velocity 1, no divide-by-zero


def test_amps_to_velocities_monotonic_and_in_range():
    amps = np.array([1.0, 0.7, 0.4, 0.1, 0.01])
    vel = AudioVelocityMapProcessor.amps_to_velocities(
        amps, dynamic_range_db=48.0, gain_db=0.0)
    assert np.all(vel >= 1) and np.all(vel <= 127)
    assert np.all(np.diff(vel.astype(int)) <= 0)   # louder -> higher velocity (non-increasing here)


def test_processor_maps_reference_tone_without_mutating_input():
    sample_rate = 44100
    time = np.arange(4096, dtype=np.float32) / sample_rate
    audio = np.sin(2.0 * np.pi * 440.0 * time)[np.newaxis, :]
    notes = np.array([(69, 0.0, 0.08, 20)], dtype=NOTE_DTYPE)
    progress = []

    result = AudioVelocityMapProcessor().map_velocities(
        notes, audio, parameters=_parameters(), sample_rate=sample_rate,
        progress=progress.append)

    assert result.status is AudioVelocityMapStatus.MAPPED
    assert result.notes["velocity"][0] == 127
    assert notes["velocity"][0] == 20
    assert progress == [0.1, 0.5, 0.9]


def test_processor_empty_notes_complete_without_stft():
    result = AudioVelocityMapProcessor().map_velocities(
        np.empty(0, dtype=NOTE_DTYPE),
        np.zeros((1, 16), dtype=np.float32),
        parameters=_parameters(), sample_rate=44100,
    )

    assert result.status is AudioVelocityMapStatus.MAPPED
    assert len(result.notes) == 0


def test_node_mapping_failure_preserves_cached_output(make_node, monkeypatch):
    node = make_node("Audio Velocity Map")
    notes = np.array([(69, 0.0, 0.08, 20)], dtype=NOTE_DTYPE)
    node.input_values[node.in_midi] = DataPacket(notes, DataType.MIDI)
    node.input_values[node.in_audio] = DataPacket(
        np.zeros((1, 4096), dtype=np.float32), DataType.AUDIO)
    previous = notes.copy()
    node.cached_outputs[node.out_midi] = previous
    monkeypatch.setattr(
        node._processor,
        "power_spectrogram",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("STFT failed")),
    )

    assert node.bake() is False
    assert node.cached_outputs[node.out_midi] is previous
