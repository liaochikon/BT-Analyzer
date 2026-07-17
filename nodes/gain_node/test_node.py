"""Gain node: per-block dB gain. Uses the shared make_node fixture (repo-root conftest)."""

import numpy as np


def test_gain_processor_is_headless():
    from nodes.gain_node.processor import GainProcessor

    processor = GainProcessor()
    block = np.ones((8, 2), dtype=np.float32)
    out = processor.process_block(block, gain_db=6.0, bypass=False)
    assert np.allclose(out, 10.0 ** (6.0 / 20.0), atol=1e-3)
    assert processor.process_block(block, gain_db=6.0, bypass=True) is block


def test_gain_applies_db(make_node):
    g = make_node("Gain")
    g.gain_db = 6.0                      # +6 dB ~= x1.995
    out = g._process_block(np.ones((16, 2), dtype=np.float32))
    assert np.allclose(out, 10.0 ** (6.0 / 20.0), atol=1e-3)


def test_gain_unity_is_passthrough(make_node):
    g = make_node("Gain")
    g.gain_db = 0.0
    block = np.random.default_rng(0).standard_normal((32, 2)).astype(np.float32)
    assert np.allclose(g._process_block(block), block, atol=1e-6)


def test_gain_bypass_and_declared_params(make_node):
    g = make_node("Gain")
    g.gain_db = 9.0
    g.bypass = True
    block = np.random.default_rng(2).standard_normal((32, 2)).astype(np.float32)

    assert g._process_block(block) is block
    assert g.serialize_params() == {"gain_db": 9.0, "bypass": True}
    assert g.get_param_signature() == "gain_db=9.000,bypass=True"

    g.apply_params({"gain_db": -3.5, "bypass": False})
    assert g.gain_db == -3.5
    assert g.bypass is False
