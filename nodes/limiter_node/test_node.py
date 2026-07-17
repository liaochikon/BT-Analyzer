"""Limiter node: brick-wall ceiling on a sustained loud block."""

import numpy as np


def test_limiter_clamps_to_ceiling(make_node):
    lim = make_node("Limiter")
    lim.ceiling_db = -6.0                # ceiling ~= 0.501 linear
    lim.attack_ms = 0.1                  # react fast so a sustained tone is caught within the block
    ceiling_lin = 10.0 ** (-6.0 / 20.0)
    loud = np.ones((4096, 2), dtype=np.float32)   # well above the ceiling
    out = lim._process_block(loud)
    # The tail (after the envelope has engaged) must not exceed the ceiling by more than a hair.
    assert float(np.abs(out[-256:]).max()) <= ceiling_lin + 0.05


def test_limiter_declarative_params_preserve_serialization_and_signature(make_node):
    limiter = make_node("Limiter")
    limiter.apply_params({
        "pre_gain_db": 3.0,
        "gain_db": -2.0,
        "ceiling_db": -1.5,
        "attack_ms": 0.0,
        "release_ms": 0.0,
    })

    assert limiter.serialize_params() == {
        "pre_gain_db": 3.0,
        "gain_db": -2.0,
        "ceiling_db": -1.5,
        "attack_ms": 0.1,
        "release_ms": 1.0,
    }
    assert limiter.get_param_signature() == (
        "pre=3.00,gain=-2.00,ceiling=-1.50,atk=0.10,rel=1.00")
