"""Headless MIDI export processor contracts."""

import numpy as np


class _Note:
    def __init__(self, **values):
        self.values = values


class _Instrument:
    def __init__(self, program):
        self.program = program
        self.notes = []


class _MidiFile:
    def __init__(self):
        self.instruments = []
        self.written_path = None

    def write(self, path):
        self.written_path = path


class _MidiModule:
    Note = _Note
    Instrument = _Instrument

    def __init__(self, midi_file=None):
        self.midi_file = midi_file or _MidiFile()

    def PrettyMIDI(self):
        return self.midi_file


def test_midi_export_processor_maps_note_rows_and_writes_file():
    from core.datatypes import NOTE_DTYPE
    from nodes.midi_export_node.processor import MidiExportProcessor, MidiExportStatus

    notes = np.array([
        (60, 0.25, 0.75, 100),
        (64, 1.0, 1.5, 80),
    ], dtype=NOTE_DTYPE)
    midi_module = _MidiModule()

    result = MidiExportProcessor().export(
        notes, "notes.mid", midi_module=midi_module)

    assert result.status is MidiExportStatus.EXPORTED
    assert result.notes == 2
    assert midi_module.midi_file.written_path == "notes.mid"
    instrument = midi_module.midi_file.instruments[0]
    assert instrument.program == 0
    assert instrument.notes[0].values == {
        "velocity": 100,
        "pitch": 60,
        "start": 0.25,
        "end": 0.75,
    }


def test_midi_export_processor_reports_no_input_and_write_failure():
    from core.datatypes import NOTE_DTYPE
    from nodes.midi_export_node.processor import MidiExportProcessor, MidiExportStatus

    processor = MidiExportProcessor()
    no_input = processor.export(
        np.zeros(0, dtype=NOTE_DTYPE), "notes.mid", midi_module=_MidiModule())
    failing_file = _MidiFile()
    failing_file.write = lambda _path: (_ for _ in ()).throw(OSError("disk full"))
    failed = processor.export(
        np.array([(60, 0.0, 1.0, 100)], dtype=NOTE_DTYPE),
        "notes.mid",
        midi_module=_MidiModule(failing_file),
    )

    assert no_input.status is MidiExportStatus.NO_INPUT
    assert failed.status is MidiExportStatus.WRITE_FAILED
    assert failed.error == "disk full"