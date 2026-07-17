"""Headless stateful limiter processor."""

from dataclasses import dataclass

import numpy as np

from core.dsp import block_peak_level, envelope_coeff, peak_envelope


@dataclass(frozen=True, slots=True)
class LimiterParameters:
    pre_gain_db: float
    gain_db: float
    ceiling_db: float
    attack_ms: float
    release_ms: float


@dataclass(frozen=True, slots=True)
class LimiterBlock:
    output: np.ndarray
    driven_input: np.ndarray


class LimiterProcessor:
    """Apply envelope limiting while preserving follower state across blocks."""

    def __init__(self):
        self._envelope = 0.0

    def process_block(
            self, block, *, parameters: LimiterParameters,
            sample_rate: int) -> LimiterBlock:
        driven = block
        if parameters.pre_gain_db != 0.0:
            driven = (
                block * np.float32(10.0 ** (parameters.pre_gain_db / 20.0))
            ).astype(np.float32)
        level = block_peak_level(driven)

        attack = envelope_coeff(parameters.attack_ms, float(sample_rate))
        release = envelope_coeff(parameters.release_ms, float(sample_rate))
        smoothed, envelope = peak_envelope(
            level,
            np.float32(self._envelope),
            np.float32(attack),
            np.float32(release),
        )
        self._envelope = float(envelope)

        ceiling = float(10.0 ** (parameters.ceiling_db / 20.0))
        gain = np.minimum(1.0, ceiling / (smoothed + np.float32(1e-9))).astype(
            np.float32)
        limited = (driven * gain[:, None]).astype(np.float32)
        np.clip(limited, -ceiling, ceiling, out=limited)

        if parameters.gain_db != 0.0:
            output = (
                limited * np.float32(10.0 ** (parameters.gain_db / 20.0))
            ).astype(np.float32)
        else:
            output = limited
        return LimiterBlock(output=output, driven_input=driven)