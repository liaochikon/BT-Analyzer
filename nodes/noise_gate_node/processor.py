"""Headless stateful noise gate processor."""

from dataclasses import dataclass

import numpy as np

from core.dsp import block_peak_level, envelope_coeff, gate_envelope


@dataclass(frozen=True, slots=True)
class NoiseGateParameters:
    threshold_db: float
    attack_ms: float
    hold_ms: float
    release_ms: float
    floor_db: float


class NoiseGateProcessor:
    """Apply a gated envelope while preserving gain and hold across blocks."""

    def __init__(self):
        self._gain = 1.0
        self._hold = 0

    def process_block(
            self, block, *, parameters: NoiseGateParameters,
            sample_rate: int):
        level = block_peak_level(block)
        attack = envelope_coeff(parameters.attack_ms, float(sample_rate))
        release = envelope_coeff(parameters.release_ms, float(sample_rate))
        threshold = float(10.0 ** (parameters.threshold_db / 20.0))
        floor = float(10.0 ** (parameters.floor_db / 20.0))
        hold_samples = int(parameters.hold_ms / 1000.0 * sample_rate)

        gain, gain_state, hold_state = gate_envelope(
            level,
            np.float32(self._gain),
            int(self._hold),
            np.float32(threshold),
            np.float32(floor),
            np.float32(attack),
            np.float32(release),
            hold_samples,
        )
        self._gain = float(gain_state)
        self._hold = int(hold_state)
        return (block * gain[:, None]).astype(np.float32)