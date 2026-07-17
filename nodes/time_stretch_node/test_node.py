"""Time Stretch node: unity rate short-circuits to a passthrough (no librosa call); bake guard."""

import numpy as np
import pytest

from core.base_node import DataType, DataPacket
from nodes._shared.librosa_effect import AudioEffectStatus
from nodes.time_stretch_node.processor import TimeStretchProcessor


class FakeLibrosa:
    class effects:
        @staticmethod
        def time_stretch(*, y, rate):
            return y[::2] if rate == 2.0 else y


def test_time_stretch_noop_passthrough(make_node):
    ts = make_node("Time Stretch")
    ts.rate = 1.0
    audio = (np.random.default_rng(2).standard_normal((2, 2000)) * 0.5).astype(np.float32)
    ts.input_values[ts.in_audio] = DataPacket(audio, DataType.AUDIO)
    assert ts.bake() is True
    assert np.allclose(ts.cached_outputs[ts.out_audio], audio, atol=1e-6)


def test_baked_node_publish_guard(make_node):
    # A heavy node that hasn't been baked must publish nothing and report failure, not crash.
    ts = make_node("Time Stretch")
    assert ts.publish_outputs() is False


def test_time_stretch_processor_runs_per_channel_headlessly():
    audio = np.arange(16, dtype=np.float32).reshape(2, 8)
    result = TimeStretchProcessor().process(
        audio, rate=2.0, librosa_module=FakeLibrosa)

    assert result.status is AudioEffectStatus.PROCESSED
    assert result.audio.shape == (2, 4)
    assert result.audio[0] == pytest.approx([0.0, 2.0, 4.0, 6.0])
