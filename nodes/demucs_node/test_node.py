"""Demucs node adapter contracts."""

import numpy as np


def test_demucs_bake_commits_processor_stems_only_on_success(make_node, monkeypatch):
    from core import DataPacket, DataType
    from core.inference_contracts import InferenceError, InferenceErrorCode
    from nodes.demucs_node import node as node_module
    from nodes.demucs_node.processor import DemucsResult

    demucs = make_node("Demucs v6")
    demucs.input_values[demucs.in_mix] = DataPacket(
        np.ones((2, 8), dtype=np.float32), DataType.AUDIO)
    monkeypatch.setattr(node_module, "append_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(node_module.settings, "get", lambda *_args: "cpu")
    demucs._report_progress = lambda _value: None
    drums = np.ones((2, 8), dtype=np.float32)
    vocals = np.full((2, 8), 2.0, dtype=np.float32)
    demucs._processor.separate = lambda *_args, **_kwargs: DemucsResult(
        stems={"drums": drums, "vocals": vocals})

    assert demucs.bake()
    assert demucs.cached_outputs[demucs.out_drums] is drums
    assert demucs.cached_outputs[demucs.out_vocal] is vocals
    assert demucs.cached_outputs[demucs.out_bass].shape == (2, 8)
    assert demucs.is_baked

    previous = dict(demucs.cached_outputs)
    demucs._processor.separate = lambda *_args, **_kwargs: DemucsResult(
        error=InferenceError(
            InferenceErrorCode.EXECUTION_FAILED,
            "Demucs separation failed",
            "out of memory",
        ))

    assert not demucs.bake()
    assert demucs.cached_outputs == previous
    assert not demucs.is_baked