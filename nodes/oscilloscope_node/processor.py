"""Headless oscilloscope capture processor."""

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class OscilloscopeSnapshot:
    samples: tuple[float, ...]


class OscilloscopeProcessor:
    """Publish immutable mono snapshots from producer-thread audio blocks."""

    def __init__(self):
        self._snapshot: OscilloscopeSnapshot | None = None

    @property
    def snapshot(self) -> OscilloscopeSnapshot | None:
        return self._snapshot

    def capture(self, block) -> None:
        if block is None or len(block) == 0:
            return
        audio = np.asarray(block)
        mono = audio.mean(axis=1) if audio.ndim == 2 else audio
        self._snapshot = OscilloscopeSnapshot(
            tuple(float(sample) for sample in mono))