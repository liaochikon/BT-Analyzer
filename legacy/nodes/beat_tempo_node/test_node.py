"""Headless Beat / Tempo processor contracts."""

import numpy as np
import pytest

from core.base_node import DataPacket, DataType
from core.datatypes import NOTE_DTYPE
from nodes.beat_tempo_node.processor import BeatTempoProcessor, BeatTempoResult


def test_build_click_track_one_note_per_beat():
    processor = BeatTempoProcessor()
    result = processor.build_click_track(120.0, [0.5, 1.0, 1.5])
    notes = result.notes
    assert result.tempo == pytest.approx(120.0)
    assert result.beat_offset == pytest.approx(0.5)
    assert len(notes) == 3
    assert list(notes["start"]) == pytest.approx([0.5, 1.0, 1.5])
    assert np.all(notes["pitch"] == processor.CLICK_PITCH)
    assert np.all(notes["velocity"] == processor.CLICK_VELOCITY)
    assert list(notes["end"]) == pytest.approx([0.5 + processor.CLICK_DURATION,
                                                1.0 + processor.CLICK_DURATION,
                                                1.5 + processor.CLICK_DURATION])


def test_build_click_track_tempo_array_normalised():
    result = BeatTempoProcessor().build_click_track(np.array([98.7]), [0.0, 0.6])
    assert isinstance(result.tempo, float)
    assert result.tempo == pytest.approx(98.7)


def test_build_click_track_no_beats():
    result = BeatTempoProcessor().build_click_track(120.0, [])
    assert result.beat_offset == 0.0
    assert len(result.notes) == 0


def test_process_downmixes_audio_and_reports_progress():
    captured = {}
    progress = []

    def tracker(*, y, sr, units):
        captured.update(y=y.copy(), sample_rate=sr, units=units)
        return np.array([120.0]), np.array([0.25, 0.75])

    audio = np.array([[1.0, 0.0], [-1.0, 0.5]], dtype=np.float32)
    result = BeatTempoProcessor().process(
        audio, sample_rate=44100, tracker=tracker, progress=progress.append)

    assert captured["y"] == pytest.approx([0.0, 0.25])
    assert captured["sample_rate"] == 44100
    assert captured["units"] == "time"
    assert result.tempo == pytest.approx(120.0)
    assert progress == [0.1, 0.85]


def test_bake_commits_processor_result(make_node, monkeypatch):
    node = make_node("Beat / Tempo")
    node.input_values[node.in_audio] = DataPacket(
        np.zeros((2, 16), dtype=np.float32), DataType.AUDIO)
    notes = np.array([(42, 0.25, 0.30, 100)], dtype=NOTE_DTYPE)
    monkeypatch.setattr(
        node._processor,
        "process",
        lambda *args, **kwargs: BeatTempoResult(120.0, 0.25, notes),
    )

    assert node.bake() is True
    assert node.is_baked is True
    assert node.cached_outputs[node.out_midi] is notes
    assert float(node.cached_outputs[node.out_tempo]) == pytest.approx(120.0)
    assert float(node.cached_outputs[node.out_offset]) == pytest.approx(0.25)


def test_bake_failure_preserves_cached_outputs(make_node, monkeypatch):
    node = make_node("Beat / Tempo")
    node.input_values[node.in_audio] = DataPacket(
        np.zeros((2, 16), dtype=np.float32), DataType.AUDIO)
    previous = np.array([(42, 0.0, 0.05, 100)], dtype=NOTE_DTYPE)
    node.cached_outputs[node.out_midi] = previous
    monkeypatch.setattr(
        node._processor,
        "process",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("tracking failed")),
    )

    assert node.bake() is False
    assert node.cached_outputs[node.out_midi] is previous
