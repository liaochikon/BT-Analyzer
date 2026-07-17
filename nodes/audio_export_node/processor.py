"""Headless audio export processor."""

from dataclasses import dataclass
from enum import Enum, auto

import numpy as np
import soundfile as sf


class AudioExportStatus(Enum):
    NO_INPUT = auto()
    EMPTY = auto()
    WRITE_FAILED = auto()
    EXPORTED = auto()


@dataclass(frozen=True, slots=True)
class AudioExportResult:
    status: AudioExportStatus
    frames: int = 0
    error: str = ""


class AudioExportProcessor:
    """Render a frozen audio source in blocks and write a WAV file."""

    def export(self, render, length: int, path: str, *, render_lock,
               sample_rate: int, block_size: int, progress=None,
               write=sf.write) -> AudioExportResult:
        if render is None or length <= 0:
            return AudioExportResult(AudioExportStatus.NO_INPUT)

        chunks = []
        start = 0
        if progress is not None:
            progress(0.0)
        while start < length:
            with render_lock:
                block = render(start, block_size)
            if block is None or len(block) == 0:
                break
            chunks.append(block)
            start += len(block)
            if progress is not None:
                progress(min(start / length, 1.0))

        if not chunks:
            return AudioExportResult(AudioExportStatus.EMPTY)

        data = np.concatenate(chunks, axis=0)
        try:
            write(path, data, sample_rate, subtype="PCM_16")
        except Exception as exc:
            return AudioExportResult(AudioExportStatus.WRITE_FAILED, error=str(exc))

        if progress is not None:
            progress(1.0)
        return AudioExportResult(AudioExportStatus.EXPORTED, frames=data.shape[0])