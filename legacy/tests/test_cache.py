"""Cache fingerprinting + disk-cache round-trip.

Fingerprint determinism/order-independence is what makes the light/heavy compile skip correct;
the disk round-trip must survive both float audio and the structured MIDI dtype, and treat a
corrupt entry as a miss (not a crash).
"""

import numpy as np
import pytest

from core import disk_cache
from core.base_node import BaseNode, NOTE_DTYPE


# ----- fingerprint -----

class _FakeNode:
    """Minimal stand-in exposing compute_fingerprint with a controllable param signature."""
    __class__ = BaseNode  # not needed; compute_fingerprint uses type(self).__name__

    def __init__(self, sig):
        self._sig = sig

    def get_param_signature(self):
        return self._sig

    compute_fingerprint = BaseNode.compute_fingerprint


def test_fingerprint_is_deterministic():
    n = _FakeNode("gain=1.5")
    assert n.compute_fingerprint(["a", "b"]) == n.compute_fingerprint(["a", "b"])


def test_fingerprint_order_independent_inputs():
    # inputs are sorted before hashing, so input order must not matter.
    n = _FakeNode("x")
    assert n.compute_fingerprint(["a", "b"]) == n.compute_fingerprint(["b", "a"])


def test_fingerprint_changes_with_params_and_inputs():
    base = _FakeNode("gain=1.0").compute_fingerprint(["src"])
    assert _FakeNode("gain=2.0").compute_fingerprint(["src"]) != base   # param change
    assert _FakeNode("gain=1.0").compute_fingerprint(["other"]) != base  # input change


# ----- disk cache round-trip -----

@pytest.fixture
def tmp_cache(tmp_path, monkeypatch):
    # Redirect the cache root to a temp dir so we never touch the real .cache.
    monkeypatch.setattr(disk_cache, "CACHE_ROOT", str(tmp_path / "cache"))
    yield


def test_float_audio_roundtrip(tmp_cache):
    arr = np.random.default_rng(0).standard_normal((2, 1000)).astype(np.float32)
    assert disk_cache.save("MyNode", "fp1", {"out_audio": arr}) is True
    loaded = disk_cache.load("MyNode", "fp1")
    assert loaded is not None
    assert np.allclose(loaded["out_audio"], arr)


def test_structured_midi_dtype_roundtrip(tmp_cache):
    notes = np.zeros(3, dtype=NOTE_DTYPE)
    notes["pitch"] = [60, 64, 67]
    notes["start"] = [0.0, 0.5, 1.0]
    notes["end"] = [0.4, 0.9, 1.4]
    notes["velocity"] = [100, 90, 80]
    assert disk_cache.save("BasicPitch", "fpM", {"out_midi": notes}) is True
    loaded = disk_cache.load("BasicPitch", "fpM")
    assert loaded is not None
    # structured dtype must survive losslessly (not coerced to float32)
    assert loaded["out_midi"].dtype == NOTE_DTYPE
    assert list(loaded["out_midi"]["pitch"]) == [60, 64, 67]


def test_miss_returns_none(tmp_cache):
    assert disk_cache.load("MyNode", "does-not-exist") is None
    assert disk_cache.load("MyNode", "") is None      # empty fingerprint -> miss


def test_corrupt_entry_is_a_miss_not_a_crash(tmp_cache):
    import os
    disk_cache.save("MyNode", "fpC", {"out": np.zeros((2, 10), np.float32)})
    path = disk_cache._entry_path("MyNode", "fpC")
    with open(path, "wb") as f:                        # clobber with garbage
        f.write(b"not a valid npz file")
    assert disk_cache.load("MyNode", "fpC") is None    # treated as a miss, no exception


def test_save_skips_nonarray_values(tmp_cache):
    # A stub/passthrough node with a non-array value shouldn't write a junk entry.
    assert disk_cache.save("MyNode", "fpX", {"out": None}) is False
    assert disk_cache.load("MyNode", "fpX") is None
