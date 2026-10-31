"""Portable audio decoding and resampling without librosa."""

import numpy as np


def resample_audio(audio, source_rate: int, target_rate: int, *, resample=None):
    """Resample mono or channel-first audio and preserve float32/channel orientation."""
    normalized = np.ascontiguousarray(audio, dtype=np.float32)
    if source_rate == target_rate:
        return normalized
    if normalized.ndim not in (1, 2):
        raise ValueError(f"expected mono or channel-first audio, got shape {normalized.shape}")
    if resample is None:
        from soxr import resample
    frames_first = normalized if normalized.ndim == 1 else normalized.T
    converted = resample(frames_first, source_rate, target_rate, quality="HQ")
    if normalized.ndim == 2:
        converted = converted.T
    return np.ascontiguousarray(converted, dtype=np.float32)


def decode_audio(path: str, target_rate: int, *, read=None, resample=None):
    """Decode a file to a channel-first float32 array at ``target_rate``."""
    if read is None:
        import soundfile
        read = soundfile.read
    frames, source_rate = read(path, dtype="float32", always_2d=True)
    channel_first = np.ascontiguousarray(frames.T, dtype=np.float32)
    return resample_audio(
        channel_first, int(source_rate), int(target_rate), resample=resample)