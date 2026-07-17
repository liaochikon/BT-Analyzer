"""Headless per-channel runner for whole-track audio effects."""

from dataclasses import dataclass
from enum import Enum, auto

import numpy as np


class AudioEffectStatus(Enum):
    NO_INPUT = auto()
    DEPENDENCY_UNAVAILABLE = auto()
    EFFECT_FAILED = auto()
    PROCESSED = auto()


@dataclass(frozen=True, slots=True)
class AudioEffectResult:
    status: AudioEffectStatus
    audio: np.ndarray | None = None
    input_samples: int = 0
    error: str = ""


def process_per_channel(audio, *, transform, is_noop: bool,
                        progress=None) -> AudioEffectResult:
    if audio is None:
        return AudioEffectResult(AudioEffectStatus.NO_INPUT)
    normalized = np.ascontiguousarray(audio, dtype=np.float32)
    if normalized.ndim == 1:
        normalized = normalized[np.newaxis, :]
    input_samples = normalized.shape[1]
    if is_noop:
        if progress is not None:
            progress(1.0)
        return AudioEffectResult(
            AudioEffectStatus.PROCESSED, normalized, input_samples)

    if progress is not None:
        progress(0.1)
    try:
        channels = []
        channel_count = normalized.shape[0]
        for channel in range(channel_count):
            channels.append(transform(normalized[channel]))
            if progress is not None:
                progress(0.1 + 0.85 * (channel + 1) / channel_count)
        output = np.ascontiguousarray(np.stack(channels), dtype=np.float32)
    except Exception as exc:
        return AudioEffectResult(
            AudioEffectStatus.EFFECT_FAILED,
            input_samples=input_samples,
            error=str(exc),
        )
    return AudioEffectResult(
        AudioEffectStatus.PROCESSED, output, input_samples)