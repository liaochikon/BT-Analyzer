"""WSOLA TimeStretcher: pitch-preserving speed change on the producer thread.

Feeds a frozen ``source(start, frames)`` callable (random access, None past the end) and checks
output length tracks 1/rate, output stays finite/bounded, and reset() clears buffered state.
"""

import numpy as np
import pytest

from core.time_stretcher import TimeStretcher

SR = 44100
CH = 2


def _sine_source(freq=220.0, length=SR * 2):
    """A frozen source callable over a finite sine; None past the end."""
    def src(start, frames):
        if start >= length:
            return None
        n = min(frames, length - start)
        t = np.arange(start, start + n) / SR
        x = (np.sin(2 * np.pi * freq * t) * 0.5).astype(np.float32)
        return np.repeat(x[:, None], CH, axis=1)
    return src, length


def _pull_all(ts, chunk=1024, cap=SR * 10):
    out = []
    total = 0
    while total < cap:
        blk = ts.pull(chunk)
        if blk is None:
            break
        out.append(blk)
        total += len(blk)
    return np.concatenate(out, axis=0) if out else np.zeros((0, CH), np.float32)


@pytest.mark.parametrize("rate,factor", [(1.0, 1.0), (0.5, 2.0), (2.0, 0.5)])
def test_output_length_tracks_inverse_rate(rate, factor):
    src, length = _sine_source()
    ts = TimeStretcher(channels=CH)
    ts.set_source(src)
    ts.set_rate(rate)
    ts.reset(0.0)
    out = _pull_all(ts)
    expected = length * factor
    # WSOLA length is approximate (frame/hop granularity); allow 5%.
    assert out.shape[0] == pytest.approx(expected, rel=0.05)


def test_output_is_finite_and_bounded():
    src, _ = _sine_source()
    ts = TimeStretcher(channels=CH)
    ts.set_source(src); ts.set_rate(0.5); ts.reset(0.0)
    out = _pull_all(ts, cap=SR)
    assert np.isfinite(out).all()
    assert np.abs(out).max() < 2.0          # overlap-add shouldn't explode


def test_reset_clears_buffered_output():
    src, _ = _sine_source()
    ts = TimeStretcher(channels=CH)
    ts.set_source(src); ts.set_rate(0.5); ts.reset(0.0)
    ts.pull(2048)                            # buffer some synthesis
    ts.reset(SR)                             # re-align elsewhere
    assert ts._buffered == 0
    assert ts._chunks == []


def test_pull_returns_none_past_end():
    src, length = _sine_source(length=4096)
    ts = TimeStretcher(channels=CH)
    ts.set_source(src); ts.set_rate(1.0); ts.reset(0.0)
    _pull_all(ts)                            # drain everything
    assert ts.pull(1024) is None             # exhausted -> None
