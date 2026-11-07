"""Separate a mix with a fixed-segment htdemucs_6s ONNX graph.

The graph itself only accepts ``(1, 2, SEGMENT_SAMPLES)``. This module is the
host half: stereo normalize, triangular overlap-add, and stem naming. Chunk
padding follows Demucs ``TensorChunk.padded`` plus ``center_trim`` so a short
tail uses the same context the PyTorch node uses.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np

from htdemucs6s_onnx.contract import (
    DEFAULT_OVERLAP,
    INPUT_NAME,
    OUTPUT_NAME,
    SAMPLE_RATE,
    SEGMENT_SAMPLES,
    STEMS,
)

SegmentRunner = Callable[[np.ndarray], np.ndarray]


def prepare_mix(mix: np.ndarray) -> np.ndarray:
    """Return contiguous ``(2, samples)`` float32. Mono is duplicated."""
    audio = np.ascontiguousarray(mix, dtype=np.float32)
    if audio.ndim == 1:
        audio = audio[np.newaxis, :]
    if audio.ndim != 2:
        raise ValueError(f"mix must be (samples,) or (channels, samples), got {audio.shape}")
    if audio.shape[1] == 0:
        raise ValueError("mix has no samples")
    if audio.shape[0] == 1:
        audio = np.repeat(audio, 2, axis=0)
    elif audio.shape[0] > 2:
        audio = audio[:2]
    return np.ascontiguousarray(audio, dtype=np.float32)


def file_normalization(mix: np.ndarray) -> tuple[np.ndarray, float, float]:
    """Match ``DemucsProcessor``: unbiased std of the channel-mean waveform."""
    reference = mix.mean(axis=0)
    mean = float(reference.mean())
    deviation = float(reference.std(ddof=1 if reference.shape[0] > 1 else 0)) + 1e-8
    normalized = (mix - mean) / deviation
    return np.ascontiguousarray(normalized, dtype=np.float32), mean, deviation


def triangular_weight(segment_samples: int, transition_power: float = 1.0) -> np.ndarray:
    if segment_samples < 2:
        raise ValueError("segment_samples must be at least 2")
    if transition_power < 1:
        raise ValueError("transition_power must be >= 1")
    half = segment_samples // 2
    weight = np.concatenate([
        np.arange(1, half + 1, dtype=np.float32),
        np.arange(segment_samples - half, 0, -1, dtype=np.float32),
    ])
    if weight.shape[0] != segment_samples:
        raise ValueError("triangular window length does not match the segment")
    weight /= weight.max()
    if transition_power != 1:
        weight = weight ** transition_power
    return weight


def pad_segment(mix: np.ndarray, offset: int, segment_samples: int) -> tuple[np.ndarray, int]:
    """Center-pad one chunk to the static ONNX length. Returns the unpadded length."""
    if offset < 0 or offset >= mix.shape[1]:
        raise ValueError(f"offset {offset} is outside mix length {mix.shape[1]}")
    chunk_length = min(segment_samples, mix.shape[1] - offset)
    delta = segment_samples - chunk_length
    start = offset - delta // 2
    end = start + segment_samples
    src_start = max(0, start)
    src_end = min(mix.shape[1], end)
    dst_start = src_start - start
    padded = np.zeros((mix.shape[0], segment_samples), dtype=np.float32)
    padded[:, dst_start:dst_start + (src_end - src_start)] = mix[:, src_start:src_end]
    return padded, chunk_length


def center_trim(audio: np.ndarray, length: int) -> np.ndarray:
    delta = audio.shape[-1] - length
    if delta < 0:
        raise ValueError(f"cannot trim {audio.shape[-1]} samples down to {length}")
    if delta == 0:
        return audio
    start = delta // 2
    end = audio.shape[-1] - (delta - start)
    return audio[..., start:end]


def separate(
    mix: np.ndarray,
    run_segment: SegmentRunner,
    *,
    overlap: float = DEFAULT_OVERLAP,
    segment_samples: int = SEGMENT_SAMPLES,
    progress: Callable[[float], None] | None = None,
) -> dict[str, np.ndarray]:
    """Run a segment model over a whole mix and return stem name to ``(2, samples)``."""
    if not 0 <= overlap < 1:
        raise ValueError("overlap must be in [0, 1)")
    audio = prepare_mix(mix)
    normalized, mean, deviation = file_normalization(audio)
    stems = _overlap_add(
        normalized,
        run_segment,
        overlap=overlap,
        segment_samples=segment_samples,
        progress=progress,
    )
    restored = stems * deviation + mean
    return {
        name: np.ascontiguousarray(restored[index], dtype=np.float32)
        for index, name in enumerate(STEMS)
    }


def _overlap_add(
    mix: np.ndarray,
    run_segment: SegmentRunner,
    *,
    overlap: float,
    segment_samples: int,
    progress: Callable[[float], None] | None,
) -> np.ndarray:
    length = mix.shape[1]
    stride = max(1, int((1 - overlap) * segment_samples))
    weight = triangular_weight(segment_samples)
    offsets = range(0, length, stride)
    total = len(offsets)
    accum = np.zeros((len(STEMS), mix.shape[0], length), dtype=np.float32)
    sum_weight = np.zeros(length, dtype=np.float32)

    for index, offset in enumerate(offsets):
        padded, chunk_length = pad_segment(mix, offset, segment_samples)
        raw = run_segment(padded[np.newaxis, :, :])
        chunk = _as_stem_chunk(raw, segment_samples)
        trimmed = center_trim(chunk, chunk_length)
        window = weight[:chunk_length]
        accum[:, :, offset:offset + chunk_length] += trimmed * window
        sum_weight[offset:offset + chunk_length] += window
        if progress is not None:
            progress((index + 1) / total)

    if np.any(sum_weight <= 0):
        raise RuntimeError("overlap-add left a sample with zero weight")
    return accum / sum_weight


def _as_stem_chunk(raw: np.ndarray, segment_samples: int) -> np.ndarray:
    chunk = np.asarray(raw, dtype=np.float32)
    expected = (1, len(STEMS), 2, segment_samples)
    if chunk.shape != expected:
        raise ValueError(f"segment model returned {chunk.shape}, expected {expected}")
    return chunk[0]


class OnnxSegmentModel:
    """ONNX Runtime wrapper for one static htdemucs_6s segment."""

    def __init__(self, model_path: Path | str, providers: list[str] | None = None):
        import onnxruntime as ort

        self.model_path = Path(model_path)
        self.session = ort.InferenceSession(
            str(self.model_path),
            providers=providers or ["CPUExecutionProvider"],
        )
        model_input = self.session.get_inputs()[0]
        model_output = self.session.get_outputs()[0]
        if model_input.name != INPUT_NAME or model_output.name != OUTPUT_NAME:
            raise ValueError(
                f"{self.model_path} uses '{model_input.name}' -> '{model_output.name}', "
                f"expected '{INPUT_NAME}' -> '{OUTPUT_NAME}'"
            )
        self._check_static_shape(model_input.shape, [1, 2, SEGMENT_SAMPLES], "input")
        self._check_static_shape(
            model_output.shape, [1, len(STEMS), 2, SEGMENT_SAMPLES], "output")

    def __call__(self, mix: np.ndarray) -> np.ndarray:
        return self.session.run(
            [OUTPUT_NAME],
            {INPUT_NAME: np.ascontiguousarray(mix, dtype=np.float32)},
        )[0]

    @staticmethod
    def _check_static_shape(actual, expected, label: str) -> None:
        if len(actual) != len(expected):
            raise ValueError(f"ONNX {label} rank {actual} does not match {expected}")
        for got, want in zip(actual, expected):
            if isinstance(got, int) and got != want:
                raise ValueError(f"ONNX {label} shape {actual} does not match {expected}")


def resample_audio(audio: np.ndarray, sample_rate: int, target_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Resample ``(channels, samples)`` audio. Returns the input when rates match."""
    if sample_rate == target_rate:
        return np.ascontiguousarray(audio, dtype=np.float32)
    if sample_rate <= 0 or target_rate <= 0:
        raise ValueError(f"invalid sample rate {sample_rate} -> {target_rate}")
    import soxr

    converted = soxr.resample(
        np.ascontiguousarray(audio.T, dtype=np.float32),
        sample_rate,
        target_rate,
        quality="HQ",
    )
    if converted.ndim == 1:
        converted = converted[:, np.newaxis]
    return np.ascontiguousarray(converted.T, dtype=np.float32)


def load_wav(path: Path | str) -> np.ndarray:
    """Read an audio file and return ``(channels, samples)`` float32 at 44.1 kHz."""
    import soundfile as sf

    audio, sample_rate = sf.read(path, always_2d=True, dtype="float32")
    return resample_audio(np.ascontiguousarray(audio.T, dtype=np.float32), sample_rate)


def write_stems(stems: dict[str, np.ndarray], output_dir: Path | str) -> list[Path]:
    import soundfile as sf

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    written = []
    for name in STEMS:
        path = destination / f"{name}.wav"
        sf.write(path, stems[name].T, SAMPLE_RATE, subtype="FLOAT")
        written.append(path)
    return written
