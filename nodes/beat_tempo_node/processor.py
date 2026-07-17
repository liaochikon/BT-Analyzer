"""Headless beat and tempo analysis processor."""

from dataclasses import dataclass

import numpy as np

from core.datatypes import NOTE_DTYPE


@dataclass(frozen=True, slots=True)
class BeatTempoResult:
    tempo: float
    beat_offset: float
    notes: np.ndarray


class BeatTempoProcessor:
    CLICK_PITCH = 42
    CLICK_VELOCITY = 100
    CLICK_DURATION = 0.05

    def process(self, audio, *, sample_rate: int, tracker=None,
                progress=None) -> BeatTempoResult:
        normalized = np.ascontiguousarray(audio, dtype=np.float32)
        mono = normalized.mean(axis=0) if normalized.ndim == 2 else normalized

        if tracker is None:
            import librosa
            tracker = librosa.beat.beat_track

        if progress is not None:
            progress(0.1)
        tempo, beat_times = tracker(y=mono, sr=sample_rate, units="time")
        result = self.build_click_track(tempo, beat_times)
        if progress is not None:
            progress(0.85)
        return result

    @classmethod
    def build_click_track(cls, tempo, beat_times) -> BeatTempoResult:
        tempo_value = float(np.atleast_1d(tempo)[0])
        times = np.atleast_1d(np.asarray(beat_times, dtype=np.float32))
        beat_offset = float(times[0]) if len(times) else 0.0

        notes = np.zeros(len(times), dtype=NOTE_DTYPE)
        notes["pitch"] = cls.CLICK_PITCH
        notes["start"] = times
        notes["end"] = times + np.float32(cls.CLICK_DURATION)
        notes["velocity"] = cls.CLICK_VELOCITY
        return BeatTempoResult(tempo_value, beat_offset, notes)