"""Headless audio-to-note processor backed by the Basic Pitch library."""

from dataclasses import dataclass
import os
import tempfile

import numpy as np
import soundfile as sf

from core.datatypes import NOTE_DTYPE


def _load_basic_pitch_api():
    try:
        from basic_pitch import ICASSP_2022_MODEL_PATH
        from basic_pitch.inference import Model, predict
    except ImportError as exc:
        raise RuntimeError(
            "Basic Pitch is unavailable. Install requirements-ai-cpu.txt "
            "or requirements-ai-gpu.txt."
        ) from exc
    return Model, predict, ICASSP_2022_MODEL_PATH


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

    _model = None

    @classmethod
    def get_model(cls):
        if cls._model is None:
            model_type, _, model_path = _load_basic_pitch_api()
            cls._model = model_type(model_path)
        return cls._model

    def transcribe(self, audio, *, model, parameters: BasicPitchParameters,
                   sample_rate: int, progress=None):
        audio = np.asarray(audio, dtype=np.float32)
        if audio.ndim == 1:
            file_audio = audio
        elif audio.ndim == 2:
            file_audio = audio.T
        else:
            raise ValueError(f"Expected mono or channel-first audio, got {audio.shape}")

        _, predict, _ = _load_basic_pitch_api()
        with tempfile.TemporaryDirectory(prefix="bt-basic-pitch-") as temp_dir:
            audio_path = os.path.join(temp_dir, "input.wav")
            sf.write(audio_path, file_audio, sample_rate, subtype="FLOAT")
            _, _, note_events = predict(
                audio_path,
                model,
                onset_threshold=parameters.onset_threshold,
                frame_threshold=parameters.frame_threshold,
                minimum_note_length=parameters.minimum_note_length,
                minimum_frequency=parameters.minimum_frequency,
                maximum_frequency=parameters.maximum_frequency,
                multiple_pitch_bends=False,
                melodia_trick=parameters.melodia_trick,
            )

        if progress is not None:
            progress(0.9)

        notes = np.empty(len(note_events), dtype=NOTE_DTYPE)
        for index, event in enumerate(note_events):
            start_s, end_s, pitch, amplitude = event[:4]
            velocity = int(np.clip(round(float(amplitude) * 127), 1, 127))
            notes[index] = (int(pitch), float(start_s), float(end_s), velocity)
        return notes[np.argsort(notes["start"], kind="stable")]
