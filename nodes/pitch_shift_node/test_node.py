"""Pitch Shift node: 0 semitones short-circuits to a passthrough (no librosa call)."""

import numpy as np
import pytest

from core.base_node import DataType, DataPacket
from nodes._shared.librosa_effect import AudioEffectStatus
from nodes.pitch_shift_node.processor import PitchShiftProcessor


def test_pitch_shift_noop_passthrough(make_node):
    ps = make_node("Pitch Shift")
    ps.semitones = 0.0
    audio = (np.random.default_rng(3).standard_normal((2, 2000)) * 0.5).astype(np.float32)
    ps.input_values[ps.in_audio] = DataPacket(audio, DataType.AUDIO)
    assert ps.bake() is True
    assert np.allclose(ps.cached_outputs[ps.out_audio], audio, atol=1e-6)


def test_pitch_shift_processor_passes_parameters_per_channel():
    calls = []

    class FakeLibrosa:
        class effects:
            @staticmethod
            def pitch_shift(*, y, sr, n_steps):
                calls.append((sr, n_steps))
                return y + 1.0

    result = PitchShiftProcessor().process(
        np.zeros((2, 8), dtype=np.float32), semitones=3.5,
        sample_rate=44100, librosa_module=FakeLibrosa)

    assert result.status is AudioEffectStatus.PROCESSED
    assert result.audio == pytest.approx(np.ones((2, 8), dtype=np.float32))
    assert calls == [(44100, 3.5), (44100, 3.5)]
