"""Core data types that travel between nodes.

Extracted from ``core.base_node`` so the pin/packet vocabulary lives on its own, independent of the
``BaseNode`` class. ``base_node`` re-exports these (``from core.datatypes import *``) so existing
``from core.base_node import *`` / ``from core import *`` call sites keep working unchanged.
"""

import numpy as np
from enum import Enum, auto


class DataType(Enum):
    AUDIO = auto()
    TEXT = auto()
    MIDI = auto()
    # Scalar families. Each carries a single value in a canonical, human-readable unit so a node
    # is free to display it however it likes (BPM as beats, Hz as a note name, ...). They are
    # DISTINCT enums even when two happen to share a unit range, so the ::TYPE link guard rejects
    # musically nonsensical wiring (e.g. a PITCH feeding a TEMPO input). Values travel as numpy
    # 0-d / shape-(1,) arrays so they reuse the existing mem/disk cache + fingerprint machinery.
    TEMPO = auto()   # value = BPM  (beats per minute)
    TIME = auto()    # value = seconds
    PITCH = auto()   # value = Hz
    VOL = auto()     # value = dB


# Payload format carried by MIDI pins. A note is (pitch, start, end, velocity):
#   pitch    -> MIDI note number 0..127
#   start/end-> note on/off time in seconds
#   velocity -> MIDI velocity 0..127
# Storing notes as a numpy structured array (not a PrettyMIDI object) means the mem/disk
# caches and fingerprint propagation reuse the existing ndarray machinery for free.
NOTE_DTYPE = np.dtype([
    ("pitch", np.int16),
    ("start", np.float32),
    ("end", np.float32),
    ("velocity", np.uint8),
])


class DataPacket:
    def __init__(self, data: np.ndarray, data_type: DataType, fingerprint: str = ""):
        self.data = data
        self.data_type = data_type
        self.fingerprint = fingerprint  # cheap content identity; propagated by the pipeline


def slice_stereo_block(m, start: int, frames: int):
    """Slice a (channels, samples) array into a (n, 2) float32 playback block.

    Returns None past the end. Pure and allocation-light; safe to call on the engine
    producer thread. Mono is duplicated to stereo; >2 channels are truncated to the first 2.
    """
    if m is None:
        return None
    total = m.shape[-1]
    if start >= total:
        return None
    end = min(start + frames, total)
    out = m[:, start:end].T
    ch = out.shape[1]
    if ch == 1:
        out = np.repeat(out, 2, axis=1)
    elif ch > 2:
        out = out[:, :2]
    return np.ascontiguousarray(out, dtype=np.float32)
