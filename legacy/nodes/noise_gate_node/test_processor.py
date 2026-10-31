"""Headless Noise Gate processor contracts."""

import numpy as np


def _parameters(**overrides):
    from nodes.noise_gate_node.processor import NoiseGateParameters

    values = {
        "threshold_db": -40.0,
        "attack_ms": 0.1,
        "hold_ms": 0.0,
        "release_ms": 5.0,
        "floor_db": -80.0,
    }
    values.update(overrides)
    return NoiseGateParameters(**values)


def test_noise_gate_processor_closes_on_sustained_silence():
    from nodes.noise_gate_node.processor import NoiseGateProcessor

    processor = NoiseGateProcessor()
    output = processor.process_block(
        np.zeros((8000, 2), dtype=np.float32),
        parameters=_parameters(),
        sample_rate=44100,
    )

    assert float(np.abs(output).max()) < 1e-3
    assert processor._gain < 0.01


def test_noise_gate_processor_preserves_hold_across_blocks():
    from nodes.noise_gate_node.processor import NoiseGateProcessor

    processor = NoiseGateProcessor()
    parameters = _parameters(hold_ms=100.0)
    processor.process_block(
        np.ones((16, 2), dtype=np.float32),
        parameters=parameters,
        sample_rate=1000,
    )
    held = processor._hold
    processor.process_block(
        np.zeros((16, 2), dtype=np.float32),
        parameters=parameters,
        sample_rate=1000,
    )

    assert held == 99
    assert processor._hold == 83