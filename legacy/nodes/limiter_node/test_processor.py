"""Headless Limiter processor contracts."""

import numpy as np


def test_limiter_processor_applies_drive_ceiling_and_output_gain():
    from nodes.limiter_node.processor import LimiterParameters, LimiterProcessor

    parameters = LimiterParameters(
        pre_gain_db=6.0,
        gain_db=-3.0,
        ceiling_db=-6.0,
        attack_ms=0.1,
        release_ms=50.0,
    )
    block = np.ones((4096, 2), dtype=np.float32)
    result = LimiterProcessor().process_block(
        block, parameters=parameters, sample_rate=44100)

    assert np.allclose(result.driven_input, 10.0 ** (6.0 / 20.0), atol=1e-3)
    expected_peak = 10.0 ** (-9.0 / 20.0)
    assert float(np.abs(result.output).max()) <= expected_peak + 1e-5
    assert result.output.dtype == np.float32


def test_limiter_processor_preserves_envelope_across_blocks():
    from nodes.limiter_node.processor import LimiterParameters, LimiterProcessor

    processor = LimiterProcessor()
    parameters = LimiterParameters(0.0, 0.0, -6.0, 10.0, 100.0)
    loud = np.ones((256, 2), dtype=np.float32)

    processor.process_block(loud, parameters=parameters, sample_rate=44100)
    envelope = processor._envelope
    processor.process_block(loud, parameters=parameters, sample_rate=44100)

    assert envelope > 0.0
    assert processor._envelope >= envelope