"""RingBuffer: wrap-around read/write + the flush/discard forward-only invariant.

This underlies all audio; a wrap bug is silent and catastrophic.
"""

import numpy as np
import pytest

from core.buffer import RingBuffer


def _block(vals, ch=2):
    a = np.asarray(vals, dtype=np.float32)[:, None]
    return np.repeat(a, ch, axis=1)


def test_write_then_read_roundtrip():
    rb = RingBuffer(8, channels=2)
    n = rb.write(_block([1, 2, 3, 4]))
    assert n == 4
    out = np.zeros((4, 2), dtype=np.float32)
    got, under = rb.read(out)
    assert got == 4 and under is False
    assert np.array_equal(out[:, 0], [1, 2, 3, 4])


def test_write_capped_at_free_space():
    rb = RingBuffer(4, channels=2)
    assert rb.write(_block([1, 2, 3, 4, 5, 6])) == 4    # only 4 slots
    assert rb.free_frames == 0


def test_underrun_zero_fills_and_flags():
    rb = RingBuffer(8, channels=2)
    rb.write(_block([1, 2]))
    out = np.zeros((5, 2), dtype=np.float32)
    got, under = rb.read(out)
    assert got == 2 and under is True
    assert np.array_equal(out[:, 0], [1, 2, 0, 0, 0])   # tail zero-filled


def test_wrap_around_read_write():
    # capacity 4; advance the heads so a subsequent write/read straddles the wrap boundary.
    rb = RingBuffer(4, channels=1)
    rb.write(np.array([[1], [2], [3]], dtype=np.float32))
    out = np.zeros((3, 1), dtype=np.float32)
    rb.read(out)                                        # read_pos now 3
    # this write wraps: 1 slot to end + rest at start
    assert rb.write(np.array([[4], [5], [6]], dtype=np.float32)) == 3
    out2 = np.zeros((3, 1), dtype=np.float32)
    got, under = rb.read(out2)
    assert got == 3 and under is False
    assert np.array_equal(out2[:, 0], [4, 5, 6])        # values intact across the wrap


def test_request_flush_discards_queued_then_zero_fills():
    rb = RingBuffer(8, channels=1)
    rb.write(np.array([[1], [2], [3], [4]], dtype=np.float32))
    rb.request_flush()                                  # everything queued is now stale
    out = np.zeros((4, 1), dtype=np.float32)
    got, under = rb.read(out)
    assert got == 0 and under is True                   # nothing fresh -> all zero
    assert np.array_equal(out[:, 0], [0, 0, 0, 0])


def test_flush_boundary_is_forward_only():
    # After a flush, freshly written audio must still be readable (boundary doesn't over-discard).
    rb = RingBuffer(8, channels=1)
    rb.write(np.array([[1], [2]], dtype=np.float32))
    rb.request_flush()
    rb.write(np.array([[7], [8]], dtype=np.float32))    # written AFTER the flush -> fresh
    out = np.zeros((2, 1), dtype=np.float32)
    got, under = rb.read(out)
    assert got == 2 and under is False
    assert np.array_equal(out[:, 0], [7, 8])
