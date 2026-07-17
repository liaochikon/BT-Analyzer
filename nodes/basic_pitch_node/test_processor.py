"""Headless Basic Pitch processor contracts."""

import numpy as np


def test_basic_pitch_processor_materializes_sorted_notes(monkeypatch):
    from core.datatypes import NOTE_DTYPE
    from nodes.basic_pitch_node.processor import BasicPitchParameters, BasicPitchProcessor

    observed = {}
    model_output = {
        "note": np.zeros((2, 88), dtype=np.float32),
        "onset": np.zeros((2, 88), dtype=np.float32),
        "contour": np.zeros((2, 264), dtype=np.float32),
    }

    def fake_inference(audio, *, sample_rate, session):
        observed.update(audio=audio, sample_rate=sample_rate, session=session)
        return model_output

    def fake_decode(received, **parameters):
        observed.update(output=received, parameters=parameters)
        return [
            (1.0, 1.5, 64, 0.5),
            (0.2, 0.7, 60, 1.0),
        ]

    monkeypatch.setattr("nodes.basic_pitch_node.processor.run_inference", fake_inference)
    monkeypatch.setattr("nodes.basic_pitch_node.processor.decode_notes", fake_decode)

    progress = []
    parameters = BasicPitchParameters(
        melodia_trick=True,
        onset_threshold=0.5,
        frame_threshold=0.3,
        minimum_note_length=100.0,
        minimum_frequency=None,
        maximum_frequency=4200.0,
    )
    notes = BasicPitchProcessor().transcribe(
        np.zeros((1, 256), dtype=np.float32),
        model="fake-model",
        parameters=parameters,
        sample_rate=44100,
        progress=progress.append,
    )

    assert notes.dtype == NOTE_DTYPE
    assert notes["pitch"].tolist() == [60, 64]
    assert notes["velocity"].tolist() == [127, 64]
    assert observed["session"] == "fake-model"
    assert observed["sample_rate"] == 44100
    assert observed["output"] is model_output
    assert observed["parameters"]["maximum_frequency"] == 4200.0
    assert progress == [0.9]