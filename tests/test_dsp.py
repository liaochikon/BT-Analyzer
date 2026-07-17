"""DSP kernels (core.dsp): envelope followers + helpers.

These are the numba-jitted per-sample loops the dynamics nodes rely on. Tested against the plain
reference recursion so the behaviour is locked whether or not numba is active.
"""

import numpy as np
import pytest

from core.dsp import peak_envelope, gate_envelope, block_peak_level, envelope_coeff


def _ref_peak(level, env, atk, rel):
    e = env
    out = np.empty_like(level)
    for i in range(len(level)):
        x = float(level[i])
        c = atk if x > e else rel
        e = c * e + (1.0 - c) * x
        out[i] = e
    return out, e


def test_peak_envelope_matches_reference():
    level = np.random.default_rng(0).random(512).astype(np.float32)
    atk, rel = envelope_coeff(10.0, 44100), envelope_coeff(100.0, 44100)
    out, env = peak_envelope(level, np.float32(0.0), np.float32(atk), np.float32(rel))
    ref_out, ref_env = _ref_peak(level, 0.0, atk, rel)
    assert np.allclose(out, ref_out, atol=1e-5)
    assert env == pytest.approx(ref_env, abs=1e-5)


def test_peak_envelope_attack_vs_release():
    # Rising input uses the (faster) attack coeff, falling uses the (slower) release. The core
    # property to lock: a step UP converges faster than a step DOWN of equal size and duration.
    atk = envelope_coeff(1.0, 44100)    # fast
    rel = envelope_coeff(200.0, 44100)  # slow
    N = 300
    up = np.ones(N, dtype=np.float32)
    out_up, _ = peak_envelope(up, np.float32(0.0), np.float32(atk), np.float32(rel))
    down = np.zeros(N, dtype=np.float32)
    out_down, _ = peak_envelope(down, np.float32(1.0), np.float32(atk), np.float32(rel))
    rose = out_up[-1]                   # distance travelled up from 0
    fell = 1.0 - out_down[-1]           # distance travelled down from 1
    assert rose > fell                  # attack is faster than release
    assert out_up[-1] > 0.9             # fast attack essentially settled by 300 samples
    assert out_down[-1] > 0.4           # slow release still well above the floor


def test_peak_envelope_continuity_across_blocks():
    # Splitting a signal into two blocks (carrying env) equals processing it whole.
    level = np.random.default_rng(1).random(300).astype(np.float32)
    a, r = np.float32(0.99), np.float32(0.999)
    whole, _ = peak_envelope(level, np.float32(0.0), a, r)
    b1, env = peak_envelope(level[:128], np.float32(0.0), a, r)
    b2, _ = peak_envelope(level[128:], np.float32(env), a, r)
    assert np.allclose(whole, np.concatenate([b1, b2]), atol=1e-5)


def test_gate_envelope_opens_holds_then_closes():
    # Signal on for 400 samples (gate opens), then off long enough for the release to fully close.
    level = np.concatenate([np.ones(400, np.float32), np.zeros(6000, np.float32)])
    atk, rel = envelope_coeff(1.0, 44100), envelope_coeff(5.0, 44100)
    g, gain, hold = gate_envelope(
        level, np.float32(0.0), 0,
        np.float32(0.1), np.float32(0.0),   # thr_lin, floor_lin
        np.float32(atk), np.float32(rel), 100)   # hold_samps = 100
    assert g[390] > 0.95                   # fully open while signal is above threshold
    assert g[450] > 0.5                    # still held up during the 100-sample hold after drop
    assert g[-1] < 0.05                    # closed to the floor long after the hold expired


def test_gate_hold_keeps_open_briefly_after_drop():
    # A short blip should keep the gate open through the hold window, not snap shut immediately.
    level = np.concatenate([np.ones(20, np.float32), np.zeros(200, np.float32)])
    atk, rel = envelope_coeff(0.5, 44100), envelope_coeff(20.0, 44100)
    g, _, _ = gate_envelope(level, np.float32(0.0), 0,
                            np.float32(0.1), np.float32(0.0),
                            np.float32(atk), np.float32(rel), 100)   # 100-sample hold
    # sample 100 is within (20 + 100) hold window -> still substantially open
    assert g[100] > 0.5


def test_block_peak_level_stereo_and_mono():
    stereo = np.stack([np.array([0.2, -0.9], np.float32), np.array([-0.5, 0.3], np.float32)], axis=1)
    assert np.allclose(block_peak_level(stereo), [0.5, 0.9])   # max(|L|,|R|)
    mono = np.array([-0.7, 0.4], np.float32)
    assert np.allclose(block_peak_level(mono), [0.7, 0.4])


def test_envelope_coeff_monotonic_and_bounded():
    fast = envelope_coeff(1.0, 44100)
    slow = envelope_coeff(500.0, 44100)
    assert 0.0 < fast < slow < 1.0        # longer time -> coeff closer to 1
    assert envelope_coeff(0.0, 44100) < 1.0   # zero time doesn't blow up
