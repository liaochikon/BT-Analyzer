"""Headless beat-synchronous chord and key detection processor."""

from dataclasses import dataclass
from enum import Enum, auto

import numpy as np

from core.datatypes import NOTE_DTYPE
from nodes._shared.midi_draw import PITCH_CLASSES


class ChordDetectionStatus(Enum):
    NO_AUDIO = auto()
    INVALID_BEATS = auto()
    DEPENDENCY_UNAVAILABLE = auto()
    DETECTION_FAILED = auto()
    DETECTED = auto()


@dataclass(frozen=True, slots=True)
class ChordSegment:
    start: float
    end: float
    name: str


@dataclass(frozen=True, slots=True)
class ChordDetectionResult:
    status: ChordDetectionStatus
    notes: np.ndarray | None = None
    key_name: str = "--"
    segments: tuple[ChordSegment, ...] = ()
    empty_reason: str = ""
    error: str = ""


class ChordDetectionProcessor:
    HOP = 2048
    MIN_CHORD_DURATION = 0.10
    MAX_BRIDGE_GAP = 1.0
    CHORD_OCTAVE_BASE = 48
    CHORD_VELOCITY = 90

    KS_MAJOR = np.array(
        [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
    KS_MINOR = np.array(
        [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

    @staticmethod
    def beat_times_from_notes(notes) -> np.ndarray | None:
        if notes is None or len(notes) == 0:
            return None
        beat_times = np.unique(np.asarray(notes["start"], dtype=np.float64))
        return beat_times if beat_times.size >= 2 else None

    @staticmethod
    def grid_from_beats(beat_times, factor: float, tail: float,
                        head: float = 0.0) -> np.ndarray:
        beats = np.asarray(beat_times, dtype=np.float64)
        if factor < 1.0:
            subdivisions = max(1, int(round(1.0 / factor)))
            units = [
                beats[index] + (beats[index + 1] - beats[index]) * part / subdivisions
                for index in range(len(beats) - 1)
                for part in range(subdivisions)
            ]
            grid = np.array(units + [beats[-1]], dtype=np.float64)
        elif factor > 1.0:
            grid = beats[::max(1, int(round(factor)))]
        else:
            grid = beats
        if grid.size == 0 or grid[0] > head:
            grid = np.append(head, grid)
        if grid[-1] < tail:
            grid = np.append(grid, tail)
        return np.unique(grid)

    @staticmethod
    def aggregate_units(chroma, rms, frame_times, bounds, aggregate=np.median):
        unit_count = len(bounds) - 1
        segment_chroma = np.zeros((chroma.shape[0], unit_count), dtype=np.float64)
        segment_rms = np.zeros(unit_count, dtype=np.float64)
        frame_units = np.searchsorted(bounds, frame_times, side="right") - 1
        centers = (bounds[:-1] + bounds[1:]) * 0.5
        nearest = np.clip(
            np.searchsorted(frame_times, centers), 0, chroma.shape[1] - 1)
        for unit in range(unit_count):
            mask = frame_units == unit
            if np.any(mask):
                segment_chroma[:, unit] = aggregate(chroma[:, mask], axis=1)
                segment_rms[unit] = aggregate(rms[mask])
            else:
                segment_chroma[:, unit] = chroma[:, nearest[unit]]
                segment_rms[unit] = rms[nearest[unit]]
        return segment_chroma, segment_rms

    @staticmethod
    def build_templates():
        templates = np.zeros((24, 12), dtype=np.float64)
        for root in range(12):
            for interval in (0, 4, 7):
                templates[root, (root + interval) % 12] = 1.0
            for interval in (0, 3, 7):
                templates[12 + root, (root + interval) % 12] = 1.0
        templates /= np.linalg.norm(templates, axis=1, keepdims=True)
        return templates

    @staticmethod
    def chord_name(index: int) -> str:
        if index < 12:
            return PITCH_CLASSES[index]
        return PITCH_CLASSES[index - 12] + "m"

    @classmethod
    def postprocess_units(cls, labels, starts, ends):
        segments = []
        index = 0
        while index < len(labels):
            end_index = index
            while (end_index + 1 < len(labels)
                   and labels[end_index + 1] == labels[index]):
                end_index += 1
            segments.append([
                float(starts[index]), float(ends[end_index]), int(labels[index])])
            index = end_index + 1

        for index in range(1, len(segments) - 1):
            segment = segments[index]
            if (segment[2] == -1
                    and segment[1] - segment[0] <= cls.MAX_BRIDGE_GAP
                    and segments[index - 1][2] >= 0
                    and segments[index - 1][2] == segments[index + 1][2]):
                segment[2] = segments[index - 1][2]

        merged = []
        for segment in segments:
            if merged and merged[-1][2] == segment[2]:
                merged[-1][1] = segment[1]
            else:
                merged.append(list(segment))
        return merged

    @classmethod
    def detect_key(cls, chroma) -> str:
        average = np.asarray(chroma, dtype=np.float64).mean(axis=1)
        if average.sum() <= 0:
            return "--"
        average = average - average.mean()
        if not np.any(average):
            return "--"

        def correlate(profile):
            centered = profile - profile.mean()
            best_index, best_correlation = 0, -2.0
            for index in range(12):
                rotated = np.roll(centered, index)
                denominator = np.linalg.norm(average) * np.linalg.norm(rotated)
                correlation = (
                    float(average @ rotated / denominator)
                    if denominator > 0 else -2.0
                )
                if correlation > best_correlation:
                    best_index, best_correlation = index, correlation
            return best_index, best_correlation

        major_index, major_correlation = correlate(cls.KS_MAJOR)
        minor_index, minor_correlation = correlate(cls.KS_MINOR)
        if major_correlation >= minor_correlation:
            return f"{PITCH_CLASSES[major_index]} major"
        return f"{PITCH_CLASSES[minor_index]} minor"

    @staticmethod
    def empty_result(reason: str = "") -> ChordDetectionResult:
        return ChordDetectionResult(
            ChordDetectionStatus.DETECTED,
            notes=np.zeros(0, dtype=NOTE_DTYPE),
            empty_reason=reason,
        )

    def process(self, audio, beat_notes, *, beat_factor: float, sample_rate: int,
                librosa_module=None, progress=None) -> ChordDetectionResult:
        if audio is None:
            return ChordDetectionResult(ChordDetectionStatus.NO_AUDIO)
        beat_times = self.beat_times_from_notes(beat_notes)
        if beat_times is None:
            return ChordDetectionResult(ChordDetectionStatus.INVALID_BEATS)
        try:
            if librosa_module is None:
                import librosa as librosa_module
        except ImportError as exc:
            return ChordDetectionResult(
                ChordDetectionStatus.DEPENDENCY_UNAVAILABLE, error=str(exc))

        normalized = np.ascontiguousarray(audio, dtype=np.float32)
        mono = normalized.mean(axis=0) if normalized.ndim == 2 else normalized
        if progress is not None:
            progress(0.1)
        try:
            chroma = librosa_module.feature.chroma_cqt(
                y=mono, sr=sample_rate, hop_length=self.HOP)
            rms = librosa_module.feature.rms(
                y=mono, frame_length=2 * self.HOP, hop_length=self.HOP)[0]
        except Exception as exc:
            return ChordDetectionResult(
                ChordDetectionStatus.DETECTION_FAILED, error=str(exc))
        if progress is not None:
            progress(0.6)

        frame_count = min(chroma.shape[1], rms.shape[0])
        chroma = chroma[:, :frame_count]
        rms = rms[:frame_count]
        if frame_count == 0:
            return self.empty_result("no_frames")

        frame_duration = self.HOP / sample_rate
        frame_times = np.arange(frame_count) * self.HOP / sample_rate
        tail = float(frame_times[-1] + frame_duration)
        key_name = self.detect_key(chroma)
        beat_times = beat_times[beat_times <= tail]
        if beat_times.size < 2:
            return self.empty_result("no_beats_within_audio")

        bounds = self.grid_from_beats(beat_times, beat_factor, tail)
        segment_chroma, segment_rms = self.aggregate_units(
            chroma, rms, frame_times, bounds)
        if progress is not None:
            progress(0.8)
        normalized_chroma = segment_chroma / (
            np.linalg.norm(segment_chroma, axis=0, keepdims=True) + 1e-9)
        labels = (self.build_templates() @ normalized_chroma).argmax(axis=0).astype(np.int32)
        labels[segment_rms < 1e-3] = -1
        raw_segments = self.postprocess_units(labels, bounds[:-1], bounds[1:])

        note_rows = []
        segments = []
        for start, end, chord_index in raw_segments:
            if chord_index < 0 or end - start < self.MIN_CHORD_DURATION:
                continue
            is_minor = chord_index >= 12
            root = chord_index % 12
            intervals = (0, 3, 7) if is_minor else (0, 4, 7)
            name = self.chord_name(chord_index)
            segments.append(ChordSegment(start, end, name))
            base = self.CHORD_OCTAVE_BASE + root
            for interval in intervals:
                note_rows.append(
                    (base + interval, start, end, self.CHORD_VELOCITY))
        notes = np.array(note_rows, dtype=NOTE_DTYPE)
        return ChordDetectionResult(
            ChordDetectionStatus.DETECTED,
            notes=notes,
            key_name=key_name,
            segments=tuple(segments),
        )