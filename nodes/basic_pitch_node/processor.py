"""Headless Basic Pitch ONNX audio-to-note processor."""

from dataclasses import dataclass
import os

import numpy as np

from core.datatypes import NOTE_DTYPE
from core.onnx_runtime import OnnxSessionProvider
from nodes.basic_pitch_node.onnx_inference import decode_notes, run_inference


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODEL_PATH = os.path.join(_PROJECT_ROOT, "models", "basic_pitch", "nmp.onnx")


@dataclass(frozen=True, slots=True)
class BasicPitchParameters:
    melodia_trick: bool
    onset_threshold: float
    frame_threshold: float
    minimum_note_length: float
    minimum_frequency: float | None
    maximum_frequency: float | None


class BasicPitchProcessor:
    """Materialize sorted NOTE_DTYPE events from an audio array."""

    _sessions = OnnxSessionProvider()

    @classmethod
    def get_model(cls, requested_device="auto"):
        return cls._sessions.get(MODEL_PATH, requested_device)

    def transcribe(self, audio, *, model, parameters: BasicPitchParameters,
                   sample_rate: int, progress=None):
        model_output = run_inference(audio, sample_rate=sample_rate, session=model)
        note_events = decode_notes(
            model_output,
            onset_threshold=parameters.onset_threshold,
            frame_threshold=parameters.frame_threshold,
            minimum_note_length_ms=parameters.minimum_note_length,
            minimum_frequency=parameters.minimum_frequency,
            maximum_frequency=parameters.maximum_frequency,
            melodia_trick=parameters.melodia_trick,
        )

        if progress is not None:
            progress(0.9)

        notes = np.empty(len(note_events), dtype=NOTE_DTYPE)
        for index, (start_s, end_s, pitch, amplitude) in enumerate(note_events):
            velocity = int(np.clip(round(float(amplitude) * 127), 1, 127))
            notes[index] = (int(pitch), float(start_s), float(end_s), velocity)
        return notes[np.argsort(notes["start"], kind="stable")]
