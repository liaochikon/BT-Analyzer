"""Headless Oscilloscope processor contracts."""

from dataclasses import FrozenInstanceError

import numpy as np
import pytest


def test_oscilloscope_processor_downmixes_to_detached_immutable_snapshot():
    from nodes.oscilloscope_node.processor import OscilloscopeProcessor

    block = np.array([[1.0, -1.0], [0.5, 0.25]], dtype=np.float32)
    processor = OscilloscopeProcessor()
    processor.capture(block)
    snapshot = processor.snapshot
    block[:] = 99.0

    assert snapshot.samples == pytest.approx((0.0, 0.375))
    with pytest.raises(FrozenInstanceError):
        snapshot.samples = ()


def test_oscilloscope_processor_ignores_empty_capture():
    from nodes.oscilloscope_node.processor import OscilloscopeProcessor

    processor = OscilloscopeProcessor()
    processor.capture(np.array([0.25], dtype=np.float32))
    snapshot = processor.snapshot
    processor.capture(np.array([], dtype=np.float32))

    assert processor.snapshot is snapshot