"""Headless EQ processor contracts."""

import numpy as np


def _parameters(**overrides):
    from nodes.eq_node.processor import EqParameters

    values = dict(
        low_gain=0.0, low_freq=100.0,
        mid_gain=0.0, mid_freq=1000.0, mid_q=1.0,
        high_gain=0.0, high_freq=8000.0)
    values.update(overrides)
    return EqParameters(**values)


def test_eq_processor_unity_is_passthrough_and_preserves_filter_state():
    from nodes.eq_node.processor import EqProcessor

    processor = EqProcessor()
    first = np.ones((64, 2), dtype=np.float32)
    second = np.zeros((64, 2), dtype=np.float32)

    output = processor.process_block(
        first, parameters=_parameters(), sample_rate=44100)
    processor.process_block(
        second, parameters=_parameters(low_gain=6.0), sample_rate=44100)

    assert np.allclose(output, first, atol=1e-6)
    assert set(processor._states) == {"low", "mid", "high"}
    assert all(state.shape == (2, 2) for state in processor._states.values())

    mono = processor.process_block(
        np.ones(32, dtype=np.float32),
        parameters=_parameters(),
        sample_rate=44100,
    )
    assert mono.shape == (32, 1)
    assert all(state.shape == (2, 1) for state in processor._states.values())


def test_eq_processor_response_is_flat_at_unity_and_reflects_mid_boost():
    from nodes.eq_node.processor import EqProcessor

    processor = EqProcessor()
    frequencies, unity = processor.response(
        parameters=_parameters(), sample_rate=44100)
    _, boosted = processor.response(
        parameters=_parameters(mid_gain=6.0), sample_rate=44100)
    middle = int(np.argmin(np.abs(frequencies - 1000.0)))

    assert np.allclose(unity, 0.0, atol=1e-6)
    assert boosted[middle] > 5.5


def test_eq_processor_matches_single_pass_across_block_boundaries():
    from nodes.eq_node.processor import EqProcessor

    audio = np.random.default_rng(42).normal(0, 0.1, (257, 2)).astype(np.float32)
    parameters = _parameters(
        low_gain=4.0,
        mid_gain=-5.0,
        mid_freq=1800.0,
        mid_q=2.5,
        high_gain=3.0,
    )

    continuous = EqProcessor().process_block(
        audio, parameters=parameters, sample_rate=44100)
    chunked_processor = EqProcessor()
    chunked = np.concatenate([
        chunked_processor.process_block(
            audio[:73], parameters=parameters, sample_rate=44100),
        chunked_processor.process_block(
            audio[73:191], parameters=parameters, sample_rate=44100),
        chunked_processor.process_block(
            audio[191:], parameters=parameters, sample_rate=44100),
    ])

    np.testing.assert_allclose(chunked, continuous, rtol=1e-6, atol=1e-7)