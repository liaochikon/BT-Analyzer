"""Noise Gate node: closes on sustained silence."""

import numpy as np


def test_noise_gate_closes_on_silence(make_node):
    gate = make_node("Noise Gate")
    gate.threshold_db = -40.0
    gate.floor_db = -80.0
    gate.hold_ms = 0.0
    gate.release_ms = 5.0
    # Long silence (below threshold) -> gate should pull the gain down toward the floor.
    quiet = np.zeros((8000, 2), dtype=np.float32)
    out = gate._process_block(quiet)
    assert float(np.abs(out).max()) < 1e-3


def test_noise_gate_declarative_params_preserve_serialization_and_signature(make_node):
    gate = make_node("Noise Gate")
    gate.apply_params({
        "threshold_db": -45.0,
        "attack_ms": 0.0,
        "hold_ms": -1.0,
        "release_ms": 0.0,
        "floor_db": -70.0,
    })

    assert gate.serialize_params() == {
        "threshold_db": -45.0,
        "attack_ms": 0.1,
        "hold_ms": 0.0,
        "release_ms": 1.0,
        "floor_db": -70.0,
    }
    assert gate.get_param_signature() == (
        "thr=-45.00,atk=0.10,hold=0.00,rel=1.00,floor=-70.00")
