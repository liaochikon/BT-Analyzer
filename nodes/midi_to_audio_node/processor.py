"""Headless MIDI-to-audio synthesis processor."""

from dataclasses import dataclass
from enum import Enum, auto

import numpy as np


class MidiToAudioStatus(Enum):
    NO_INPUT = auto()
    SYNTHESIS_FAILED = auto()
    SYNTHESIZED = auto()


@dataclass(frozen=True, slots=True)
class MidiToAudioResult:
    status: MidiToAudioStatus
    audio: np.ndarray | None = None
    notes: int = 0
    error: str = ""


class MidiToAudioProcessor:
    def synthesize(self, notes, *, sample_rate: int, midi_module=None,
                   progress=None) -> MidiToAudioResult:
        if notes is None:
            return MidiToAudioResult(MidiToAudioStatus.NO_INPUT)

        if progress is not None:
            progress(0.1)
        try:
            if midi_module is None:
                import pretty_midi as midi_module
            midi_file = midi_module.PrettyMIDI()
            instrument = midi_module.Instrument(program=0)
            for note in notes:
                instrument.notes.append(midi_module.Note(
                    velocity=int(note["velocity"]),
                    pitch=int(note["pitch"]),
                    start=float(note["start"]),
                    end=float(note["end"]),
                ))
            midi_file.instruments.append(instrument)
            if progress is not None:
                progress(0.4)
            audio = midi_file.synthesize(fs=sample_rate)
        except Exception as exc:
            return MidiToAudioResult(
                MidiToAudioStatus.SYNTHESIS_FAILED, error=str(exc))

        if progress is not None:
            progress(0.8)
        normalized = np.asarray(audio, dtype=np.float32)
        if normalized.size == 0:
            normalized = np.zeros(1, dtype=np.float32)
        peak = float(np.max(np.abs(normalized)))
        if peak > 1.0:
            normalized = normalized / peak * 0.95
        mono = np.ascontiguousarray(normalized[np.newaxis, :], dtype=np.float32)
        return MidiToAudioResult(
            MidiToAudioStatus.SYNTHESIZED, audio=mono, notes=len(notes))