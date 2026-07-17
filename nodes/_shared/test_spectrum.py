"""Headless rolling spectrum snapshot contracts."""

from dataclasses import FrozenInstanceError

import numpy as np
import pytest

from nodes._shared.spectrum import RollingSpectrum


def test_spectrum_snapshot_is_detached_and_immutable():
    block = np.ones((32, 2), dtype=np.float32)
    spectrum = RollingSpectrum(sample_rate=8000, fft_size=32, db_floor=-60.0)
    spectrum.push(block)
    snapshot = spectrum.snapshot(n_points=8, f_min=20.0, f_max=3000.0)
    block[:] = 0.0

    assert snapshot is not None
    assert len(snapshot.frequencies) == 8
    assert len(snapshot.levels_db) == 8
    assert all(-60.0 <= level <= 0.0 for level in snapshot.levels_db)
    with pytest.raises(FrozenInstanceError):
        snapshot.levels_db = ()


def test_spectrum_snapshot_is_none_before_audio():
    spectrum = RollingSpectrum(sample_rate=8000, fft_size=32)

    assert spectrum.snapshot() is None