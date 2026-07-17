"""BS-RoFormer node adapter contracts."""

import numpy as np


def test_bs_roformer_bake_commits_stems_only_on_success(make_node, monkeypatch):
    from core import DataPacket, DataType
    from core.inference_contracts import InferenceError, InferenceErrorCode
    from nodes.bs_roformer_node import node as node_module
    from nodes.bs_roformer_node.processor import BSRoformerResult

    node = make_node("BS-RoFormer")
    node.input_values[node.in_mix] = DataPacket(
        np.ones((2, 8), dtype=np.float32), DataType.AUDIO)
    monkeypatch.setattr(node_module, "append_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(node_module.settings, "get", lambda *_args: "cpu")
    node._report_progress = lambda _value: None
    vocals = np.ones((2, 8), dtype=np.float32)
    instrumental = np.full((2, 8), 2.0, dtype=np.float32)
    node._processor.separate = lambda *_args, **_kwargs: BSRoformerResult(
        stems={"vocals": vocals, "instrumental": instrumental})

    assert node.bake()
    assert node.cached_outputs[node.out_vocal] is vocals
    assert node.cached_outputs[node.out_inst] is instrumental
    assert node.is_baked

    previous = dict(node.cached_outputs)
    node._processor.separate = lambda *_args, **_kwargs: BSRoformerResult(
        error=InferenceError(
            InferenceErrorCode.EXECUTION_FAILED,
            "BS-RoFormer separation failed",
            "out of memory",
        ))

    assert not node.bake()
    assert node.cached_outputs == previous
    assert not node.is_baked