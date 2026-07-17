"""Compressor node: the static transfer curve and the per-block gain-reduction DSP."""

import numpy as np
import pytest


def test_transfer_curve_below_threshold_is_unity(make_node):
    c = make_node("Compressor")
    c.threshold_db = -18.0
    c.ratio = 4.0
    c.makeup_db = 0.0
    x, y = c._transfer_curve()
    # Below the threshold the curve is 1:1 (input == output).
    below = x < c.threshold_db
    assert np.allclose(y[below], x[below])


def test_transfer_curve_compresses_above_threshold(make_node):
    c = make_node("Compressor")
    c.threshold_db = -18.0
    c.ratio = 4.0
    c.makeup_db = 0.0
    x, y = c._transfer_curve()
    # At 0 dBFS: over = 0 - (-18) = 18; reduction = 18*(1/4 - 1) = -13.5 -> y = 0 - 13.5.
    y0 = y[np.argmin(np.abs(x - 0.0))]
    assert y0 == pytest.approx(-13.5, abs=1e-6)


def test_transfer_curve_makeup_shifts_whole_curve(make_node):
    c = make_node("Compressor")
    c.threshold_db, c.ratio, c.makeup_db = -18.0, 4.0, 0.0
    _x0, y0 = c._transfer_curve()
    c.makeup_db = 6.0
    _x1, y1 = c._transfer_curve()
    assert np.allclose(y1, y0 + 6.0)          # makeup is a flat +6 dB offset everywhere


def test_transfer_curve_ratio_1_is_identity_plus_makeup(make_node):
    c = make_node("Compressor")
    c.threshold_db, c.ratio, c.makeup_db = -18.0, 1.0, 0.0
    x, y = c._transfer_curve()
    assert np.allclose(y, x)                  # ratio 1:1 = no compression


def test_process_block_passes_quiet_audio(make_node):
    c = make_node("Compressor")
    c.threshold_db, c.ratio, c.makeup_db = -18.0, 4.0, 0.0
    quiet = (np.ones((2048, 2), dtype=np.float32) * 0.01)   # ~-40 dBFS, below threshold
    out = c._process_block(quiet)
    assert out.shape == quiet.shape and np.isfinite(out).all()
    assert np.allclose(out, quiet, atol=1e-3)               # essentially untouched


def test_process_block_attenuates_loud_audio(make_node):
    c = make_node("Compressor")
    c.threshold_db, c.ratio, c.makeup_db = -18.0, 4.0, 0.0
    c.attack_ms = 0.1                                        # engage fast within the block
    loud = np.ones((4096, 2), dtype=np.float32)             # 0 dBFS, well above threshold
    out = c._process_block(loud)
    assert float(np.abs(out[-256:]).max()) < 1.0           # tail is pulled down
    assert c._latest_gr_db > 0.0                            # gain reduction was reported


def test_compressor_declarative_params_preserve_serialization_and_signature(make_node):
    compressor = make_node("Compressor")
    compressor.apply_params({
        "threshold_db": -20.0,
        "ratio": 0.0,
        "makeup_db": 3.0,
        "attack_ms": 0.0,
        "release_ms": 0.0,
    })

    assert compressor.serialize_params() == {
        "threshold_db": -20.0,
        "ratio": 1.0,
        "makeup_db": 3.0,
        "attack_ms": 0.1,
        "release_ms": 1.0,
    }
    assert compressor.get_param_signature() == (
        "thr=-20.00,ratio=1.00,makeup=3.00,atk=0.10,rel=1.00")
