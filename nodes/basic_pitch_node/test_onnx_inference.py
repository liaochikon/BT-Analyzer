import numpy as np
import pytest


class _FakeSession:
    def __init__(self):
        self.inputs = []

    def run(self, output_names, inputs):
        self.inputs.append((output_names, inputs))
        return [
            np.zeros((1, 172, 88), dtype=np.float32),
            np.zeros((1, 172, 88), dtype=np.float32),
            np.zeros((1, 172, 264), dtype=np.float32),
        ]


def test_run_inference_resamples_windows_and_unwraps_outputs():
    from nodes.basic_pitch_node.onnx_inference import INPUT_NAME, run_inference

    session = _FakeSession()
    observed = {}

    def resample(audio, source_rate, target_rate, *, quality):
        observed.update(
            shape=audio.shape,
            source_rate=source_rate,
            target_rate=target_rate,
            quality=quality,
        )
        return audio[::2]

    output = run_inference(
        np.zeros((2, 44100), dtype=np.float32),
        sample_rate=44100,
        session=session,
        resample=resample,
    )

    assert observed == {
        "shape": (44100,),
        "source_rate": 44100,
        "target_rate": 22050,
        "quality": "HQ",
    }
    assert session.inputs[0][1][INPUT_NAME].shape == (1, 43844, 1)
    assert output["note"].shape == (86, 88)
    assert output["onset"].shape == (86, 88)
    assert output["contour"].shape == (86, 264)


def test_decode_notes_materializes_polyphonic_event():
    from nodes.basic_pitch_node.onnx_inference import decode_notes

    frames = np.zeros((60, 88), dtype=np.float32)
    onsets = np.zeros_like(frames)
    frames[2:30, 39] = 0.8
    onsets[2, 39] = 0.9

    notes = decode_notes(
        {
            "note": frames,
            "onset": onsets,
            "contour": np.zeros((60, 264), dtype=np.float32),
        },
        onset_threshold=0.5,
        frame_threshold=0.3,
        minimum_note_length_ms=50.0,
        minimum_frequency=None,
        maximum_frequency=None,
        melodia_trick=False,
    )

    assert len(notes) == 1
    assert notes[0][2] == 60
    assert notes[0][3] == pytest.approx(0.8)