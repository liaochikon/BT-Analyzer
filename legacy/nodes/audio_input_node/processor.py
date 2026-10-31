"""Headless audio file decoding processor."""

import numpy as np

from core.audio_io import decode_audio


class AudioInputProcessor:
    """Decode an audio file into a channel-first array at the target sample rate."""

    def decode(self, path: str, *, sample_rate: int, read=None, resample=None,
               progress=None):
        data = decode_audio(
            path, sample_rate, read=read, resample=resample)
        if progress is not None:
            progress(0.2)
        if progress is not None:
            progress(0.4)
        return data