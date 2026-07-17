"""Regression tests for core.pipeline heavy-bake input materialization.

`_materialize_streamed_inputs()` renders a streamable AUDIO input (Gain/EQ, whose execute() leaves
data=None) into a concrete array before a heavy bake. It uses numpy, which core.pipeline must import
explicitly (a wildcard import used to be relied on and silently stopped exporting np, turning this
data path into a runtime NameError that no preset/validator test covers).
"""

import types

import numpy as np

import core.pipeline as PIPE
from core.datatypes import DataPacket, DataType


def test_materialize_streamed_input_builds_channels_samples_packet(monkeypatch):
    pin = "node_Gain_1_out_audio::AUDIO"
    # A streamable node's output packet: right type, but data not yet materialized.
    node = types.SimpleNamespace(input_values={pin: DataPacket(None, DataType.AUDIO, "fp123")})

    def fake_source(input_pin):
        assert input_pin == pin
        # One full (frames, channels) block, then end-of-stream.
        def render(start, length):
            return np.ones((100, 2), dtype=np.float32) if start == 0 else None
        return render, 100

    monkeypatch.setattr(PIPE, "build_stream_source", fake_source)

    PIPE._materialize_streamed_inputs(node)   # must not raise NameError

    out = node.input_values[pin]
    assert out.data is not None
    assert out.data.dtype == np.float32
    assert out.data.shape == (2, 100)             # transposed to (channels, samples)
    assert out.data.flags["C_CONTIGUOUS"]
    assert out.fingerprint == "fp123"             # fingerprint preserved


def test_materialize_skips_when_source_empty(monkeypatch):
    pin = "node_Gain_1_out_audio::AUDIO"
    original = DataPacket(None, DataType.AUDIO, "fp")
    node = types.SimpleNamespace(input_values={pin: original})
    # A source that renders nothing must leave the packet untouched (no crash, no empty array).
    monkeypatch.setattr(PIPE, "build_stream_source", lambda p: (lambda s, l: None, 0))
    PIPE._materialize_streamed_inputs(node)
    assert node.input_values[pin] is original
