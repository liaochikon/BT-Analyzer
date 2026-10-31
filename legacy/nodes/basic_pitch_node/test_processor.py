"""Headless Basic Pitch processor contracts."""

import numpy as np


def test_basic_pitch_processor_uses_library_and_materializes_sorted_notes(monkeypatch):
    from core.datatypes import NOTE_DTYPE
    from nodes.basic_pitch_node import processor as processor_module
    from nodes.basic_pitch_node.processor import BasicPitchParameters, BasicPitchProcessor

    observed = {}

    class FakeModel:
        def __init__(self, path):
            observed["model_path"] = path

    def fake_predict(audio_path, model, **parameters):
        import soundfile as sf

        audio, sample_rate = sf.read(audio_path, always_2d=True)
        observed.update(
            audio=audio,
            sample_rate=sample_rate,
            model=model,
            parameters=parameters,
        )
        return {}, object(), [
            (1.0, 1.5, 64, 0.5, None),
            (0.2, 0.7, 60, 1.0, [1, 2]),
        ]

    monkeypatch.setattr(
        processor_module,
        "_load_basic_pitch_api",
        lambda: (FakeModel, fake_predict, "library-model"),
    )
    monkeypatch.setattr(BasicPitchProcessor, "_model", None)

    progress = []
    parameters = BasicPitchParameters(
        melodia_trick=True,
        onset_threshold=0.5,
        frame_threshold=0.3,
        minimum_note_length=100.0,
        minimum_frequency=None,
        maximum_frequency=4200.0,
    )
    processor = BasicPitchProcessor()
    model = processor.get_model()
    notes = processor.transcribe(
        np.zeros((2, 256), dtype=np.float32),
        model=model,
        parameters=parameters,
        sample_rate=44100,
        progress=progress.append,
    )

    assert notes.dtype == NOTE_DTYPE
    assert notes["pitch"].tolist() == [60, 64]
    assert notes["velocity"].tolist() == [127, 64]
    assert observed["model_path"] == "library-model"
    assert observed["model"] is model
    assert observed["sample_rate"] == 44100
    assert observed["audio"].shape == (256, 2)
    assert observed["parameters"]["maximum_frequency"] == 4200.0
    assert observed["parameters"]["multiple_pitch_bends"] is False
    assert progress == [0.9]