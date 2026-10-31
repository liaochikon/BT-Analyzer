"""Headless MIDI export processor."""

from dataclasses import dataclass
from enum import Enum, auto


class MidiExportStatus(Enum):
    NO_INPUT = auto()
    WRITE_FAILED = auto()
    EXPORTED = auto()


@dataclass(frozen=True, slots=True)
class MidiExportResult:
    status: MidiExportStatus
    notes: int = 0
    error: str = ""


class MidiExportProcessor:
    """Convert structured note rows to a standard MIDI file."""

    def export(self, notes, path: str, *, midi_module=None) -> MidiExportResult:
        if notes is None or len(notes) == 0:
            return MidiExportResult(MidiExportStatus.NO_INPUT)

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
            midi_file.write(path)
        except Exception as exc:
            return MidiExportResult(MidiExportStatus.WRITE_FAILED, error=str(exc))

        return MidiExportResult(MidiExportStatus.EXPORTED, notes=len(notes))