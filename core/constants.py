"""Global audio constants shared across the whole application.

These live at the base layer so any module (engine, transport, nodes) can import
them without depending on the UI or config layers.
"""

GLOBAL_SAMPLE_RATE = 44100  # internal working sample rate; all audio is aligned to this

# Selectable internal sample rates offered in Settings. All audio (decode, resample, bake,
# playback) is aligned to the active rate, so changing it only takes effect on the next launch
# (the value is copied by other modules at import time and the audio stream / transport
# singletons are built from it).
SUPPORTED_SAMPLE_RATES = (44100, 48000, 88200, 96000)

# Inference device for ML nodes (e.g. Demucs). "auto" picks CUDA when available and falls
# back to CPU; set to "cuda" or "cpu" to force a specific device.
INFERENCE_DEVICE = "auto"


def format_mmss(seconds: float, pad_minutes: bool = True) -> str:
    """Format a duration in seconds as ``M:SS`` (mm:ss). The transport readout and the audio-output
    node zero-pad minutes (``05:07``); the chord-chart scrolling timeline label intentionally does
    not (``5:07``), so callers that want the un-padded form pass ``pad_minutes=False``.

    Negative inputs clamp to 0 (a duration is never negative; a transient sub-zero position from a
    pre-roll/seek would otherwise render as ``-1:57``)."""
    m, s = divmod(max(0, int(seconds)), 60)
    return f"{m:02d}:{s:02d}" if pad_minutes else f"{m}:{s:02d}"
