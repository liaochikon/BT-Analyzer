"""compute_waveform_envelope: whole-track min/max downsampling for the scrubber overview.

Bin-edge / carry accounting is exactly the kind of code that breaks silently. Uses a fake
``render`` callable (no engine, lock=None) so it's pure.
"""

import numpy as np
import pytest

from core.playback_engine import compute_waveform_envelope

SR = 44100
CH = 2


def _ramp_render(length):
    """render(start, frames) -> a rising-amplitude sine as (n, CH); None past the end."""
    def render(start, frames):
        n = min(frames, length - start)
        if n <= 0:
            return None
        t = np.arange(start, start + n) / SR
        amp = (t / (length / SR)).astype(np.float32)          # 0..1 amplitude ramp
        x = (np.sin(2 * np.pi * 440 * t) * amp).astype(np.float32)
        return np.repeat(x[:, None], CH, axis=1)
    return render


def test_returns_none_for_empty():
    assert compute_waveform_envelope(None, 0) is None
    assert compute_waveform_envelope(_ramp_render(1000), 0) is None


def test_bin_count_and_symmetry():
    mn, mx = compute_waveform_envelope(_ramp_render(SR * 2), SR * 2, bins=100)
    assert len(mn) == 100 and len(mx) == 100
    assert np.allclose(mn, -mx)                # symmetric (scans |amplitude|)
    assert np.isfinite(mn).all() and np.isfinite(mx).all()


def test_bins_clamped_to_length():
    # bins can't exceed sample count.
    mn, mx = compute_waveform_envelope(_ramp_render(50), 50, bins=1000)
    assert len(mx) <= 50


def test_amplitude_ramp_captured():
    _, mx = compute_waveform_envelope(_ramp_render(SR * 2), SR * 2, bins=100)
    assert mx[-1] > mx[0]                       # last bin louder than first (ramp)
    assert mx[0] < 0.2 and mx[-1] > 0.8         # spans ~0..1
    assert (mx[10:90] > 0).all()                # no empty middle bins


def test_constant_signal_flat_envelope():
    def render(start, frames):
        n = min(frames, SR - start)
        if n <= 0:
            return None
        return np.full((n, CH), 0.5, dtype=np.float32)
    mn, mx = compute_waveform_envelope(render, SR, bins=50)
    assert np.allclose(mx, 0.5, atol=1e-3)
    assert np.allclose(mn, -0.5, atol=1e-3)
