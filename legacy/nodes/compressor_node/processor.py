"""Headless stateful compressor processor."""

from dataclasses import dataclass

import numpy as np

from core.dsp import block_peak_level, envelope_coeff, peak_envelope


@dataclass(frozen=True, slots=True)
class CompressorParameters:
    threshold_db: float
    ratio: float
    makeup_db: float
    attack_ms: float
    release_ms: float


@dataclass(frozen=True, slots=True)
class CompressorTelemetry:
    gain_reduction_db: float = 0.0
    input_level_db: float = -90.0


@dataclass(frozen=True, slots=True)
class CompressorBlock:
    output: np.ndarray
    telemetry: CompressorTelemetry


class CompressorProcessor:
    """Apply compression while preserving envelope state across blocks."""

    def __init__(self):
        self._envelope = 0.0

    def process_block(
            self, block, *, parameters: CompressorParameters,
            sample_rate: int) -> CompressorBlock:
        level = block_peak_level(block)
        attack = envelope_coeff(parameters.attack_ms, float(sample_rate))
        release = envelope_coeff(parameters.release_ms, float(sample_rate))
        smoothed, envelope = peak_envelope(
            level,
            np.float32(self._envelope),
            np.float32(attack),
            np.float32(release),
        )
        self._envelope = float(envelope)

        level_db = 20.0 * np.log10(smoothed + np.float32(1e-9))
        over = level_db - parameters.threshold_db
        reduction_db = np.where(
            over > 0.0,
            over * (1.0 / parameters.ratio - 1.0),
            0.0,
        )
        gain = np.power(
            10.0, (reduction_db + parameters.makeup_db) / 20.0
        ).astype(np.float32)
        output = (block * gain[:, None]).astype(np.float32)
        telemetry = CompressorTelemetry(
            gain_reduction_db=(
                float(-reduction_db.min()) if reduction_db.size else 0.0),
            input_level_db=(float(level_db.max()) if level_db.size else -90.0),
        )
        return CompressorBlock(output=output, telemetry=telemetry)

    @staticmethod
    def transfer_curve(parameters: CompressorParameters):
        inputs = np.linspace(-60.0, 0.0, 121)
        over = inputs - parameters.threshold_db
        reduction = np.where(
            over > 0.0,
            over * (1.0 / parameters.ratio - 1.0),
            0.0,
        )
        return inputs, inputs + reduction + parameters.makeup_db