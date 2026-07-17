"""MIDI-to-audio processor and node ownership contracts."""

import numpy as np
import pytest

from core.base_node import DataPacket, DataType
from core.datatypes import NOTE_DTYPE
from nodes.midi_to_audio_node.processor import (
    MidiToAudioProcessor,
    MidiToAudioResult,
    MidiToAudioStatus,
)


class FakeMidiModule:
    class Note:
        def __init__(self, **values):
            self.values = values

    class Instrument:
        def __init__(self, program):
            self.program = program
            self.notes = []

    class PrettyMIDI:
        output = np.array([2.0, -1.0, 0.0], dtype=np.float64)

        def __init__(self):
            self.instruments = []

        def synthesize(self, fs):
            self.sample_rate = fs
            return self.output


def _notes():
    return np.array([(60, 0.0, 0.5, 100)], dtype=NOTE_DTYPE)


def test_processor_maps_notes_and_normalizes_mono_audio():
    progress = []
    result = MidiToAudioProcessor().synthesize(
        _notes(), sample_rate=44100, midi_module=FakeMidiModule,
        progress=progress.append)

    assert result.status is MidiToAudioStatus.SYNTHESIZED
    assert result.notes == 1
    assert result.audio.shape == (1, 3)
    assert float(np.max(np.abs(result.audio))) == pytest.approx(0.95)
    assert progress == [0.1, 0.4, 0.8]


def test_processor_returns_structured_input_and_synthesis_failures():
    processor = MidiToAudioProcessor()
    assert processor.synthesize(
        None, sample_rate=44100).status is MidiToAudioStatus.NO_INPUT

    class FailingMidiModule(FakeMidiModule):
        class PrettyMIDI:
            def __init__(self):
                raise RuntimeError("backend unavailable")

    result = processor.synthesize(
        _notes(), sample_rate=44100, midi_module=FailingMidiModule)
    assert result.status is MidiToAudioStatus.SYNTHESIS_FAILED
    assert result.error == "backend unavailable"


def test_processor_replaces_empty_synthesis_with_one_silent_sample():
    class EmptyMidiModule(FakeMidiModule):
        class PrettyMIDI(FakeMidiModule.PrettyMIDI):
            output = np.array([], dtype=np.float64)

    result = MidiToAudioProcessor().synthesize(
        [], sample_rate=44100, midi_module=EmptyMidiModule)

    assert result.status is MidiToAudioStatus.SYNTHESIZED
    assert result.audio.shape == (1, 1)
    assert result.audio[0, 0] == 0.0


def test_node_commits_only_successful_synthesis(make_node, monkeypatch):
    node = make_node("MIDI to Audio")
    node.input_values[node.in_midi] = DataPacket(_notes(), DataType.MIDI)
    previous = np.ones((1, 2), dtype=np.float32)
    node.cached_outputs[node.out_audio] = previous
    monkeypatch.setattr(
        node._processor,
        "synthesize",
        lambda *args, **kwargs: MidiToAudioResult(
            MidiToAudioStatus.SYNTHESIS_FAILED, error="failed"),
    )

    assert node.bake() is False
    assert node.cached_outputs[node.out_audio] is previous

    synthesized = np.zeros((1, 4), dtype=np.float32)
    monkeypatch.setattr(
        node._processor,
        "synthesize",
        lambda *args, **kwargs: MidiToAudioResult(
            MidiToAudioStatus.SYNTHESIZED, synthesized, notes=1),
    )
    assert node.bake() is True
    assert node.cached_outputs[node.out_audio] is synthesized