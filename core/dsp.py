"""Shared DSP kernels for the streaming effect nodes.

The dynamics nodes (Compressor, Limiter, Noise Gate) each need a per-sample envelope
recursion — one that cannot be vectorized with numpy because every sample's coefficient
depends on the running envelope from the previous sample (attack vs release is a data
dependency). The loops process one producer block at a time and are benchmarked against the
real-time block budget so the portable release does not need a JIT runtime.

All kernels are pure and stateless except for the envelope carried in/out explicitly, so the
node owns its cross-block state and the kernel is safe to call from the producer thread.
"""

import numpy as np


def peak_envelope(level, env, atk, rel):
    """One-pole attack/release peak follower (Compressor / Limiter detector).

    ``level`` is the per-sample rectified peak (already max(|L|,|R|)). ``env`` is the running
    envelope carried across blocks. Returns ``(smoothed, env_out)`` where ``smoothed`` is the
    per-sample envelope and ``env_out`` is the final value to store for the next block.
    """
    n = level.shape[0]
    out = np.empty(n, dtype=np.float32)
    e = env
    for i in range(n):
        x = level[i]
        coeff = atk if x > e else rel
        e = coeff * e + (1.0 - coeff) * x
        out[i] = e
    return out, e


def gate_envelope(level, gain, hold, thr_lin, floor_lin, atk, rel, hold_samps):
    """Gated attack/release gain follower (Noise Gate).

    Opens (target 1.0) while the signal is above ``thr_lin`` or within the hold window,
    otherwise closes toward ``floor_lin``. Carries both the smoothed ``gain`` and the remaining
    ``hold`` sample count across blocks. Returns ``(g, gain_out, hold_out)``.
    """
    n = level.shape[0]
    g = np.empty(n, dtype=np.float32)
    cur = gain
    h = hold
    for i in range(n):
        if level[i] > thr_lin:
            h = hold_samps
        if h > 0:
            target = 1.0
            h -= 1
        else:
            target = floor_lin
        coeff = atk if target > cur else rel
        cur = coeff * cur + (1.0 - coeff) * target
        g[i] = cur
    return g, cur, h


def block_peak_level(block):
    """Per-sample rectified peak across channels: max(|ch0|, |ch1|, ...) as float32.

    Shared input stage for all three dynamics detectors. Accepts (frames, channels) or a 1-D
    mono block; always returns a 1-D float32 array of length ``frames``.
    """
    if block.ndim == 1:
        return np.abs(block).astype(np.float32)
    if block.shape[1] >= 2:
        return np.maximum(np.abs(block[:, 0]), np.abs(block[:, 1])).astype(np.float32)
    return np.abs(block[:, 0]).astype(np.float32)


def envelope_coeff(time_ms, sample_rate):
    """One-pole smoothing coefficient for a given time constant (ms) at ``sample_rate``."""
    return float(np.exp(-1.0 / (sample_rate * (max(1e-4, time_ms) / 1000.0))))
