"""Headless Compressor processor contracts."""

from dataclasses import FrozenInstanceError

import numpy as np
import pytest


def _parameters(**overrides):
    from nodes.compressor_node.processor import CompressorParameters

    values = dict(
        threshold_db=-18.0, ratio=4.0, makeup_db=0.0,
        attack_ms=0.1, release_ms=100.0)
    values.update(overrides)
    return CompressorParameters(**values)


def test_compressor_processor_attenuates_and_returns_immutable_telemetry():
    from nodes.compressor_node.processor import CompressorProcessor

    result = CompressorProcessor().process_block(
        np.ones((4096, 2), dtype=np.float32),
        parameters=_parameters(),
        sample_rate=44100,
    )

    assert float(np.abs(result.output[-256:]).max()) < 1.0
    assert result.telemetry.gain_reduction_db > 0.0
    assert result.telemetry.input_level_db > -1.0
    with pytest.raises(FrozenInstanceError):
        result.telemetry.gain_reduction_db = 0.0


def test_compressor_transfer_curve_is_pure():
    from nodes.compressor_node.processor import CompressorProcessor

    inputs, outputs = CompressorProcessor.transfer_curve(_parameters())
    below = inputs < -18.0

    assert np.allclose(outputs[below], inputs[below])
    assert outputs[-1] == pytest.approx(-13.5)