"""Basic Pitch node adapter contracts."""

import numpy as np


def test_basic_pitch_bake_passes_library_model_and_commits_notes(make_node, monkeypatch):
    from core import DataPacket, DataType
    from core.datatypes import NOTE_DTYPE
    from nodes.basic_pitch_node import node as node_module

    node = make_node("Basic Pitch")
    audio = np.ones((2, 8), dtype=np.float32)
    node.input_values[node.in_audio] = DataPacket(audio, DataType.AUDIO)
    monkeypatch.setattr(node_module, "append_log", lambda *_args, **_kwargs: None)
    progress = []
    node._report_progress = progress.append
    model = object()
    notes = np.array([(60, 0.0, 0.5, 100)], dtype=NOTE_DTYPE)
    observed = {}
    node._processor.get_model = lambda: model

    def transcribe(received_audio, **kwargs):
        observed.update(audio=received_audio, **kwargs)
        return notes

    node._processor.transcribe = transcribe

    assert node.bake()
    assert observed["audio"] is audio
    assert observed["model"] is model
    assert observed["parameters"].maximum_frequency == 4200.0
    assert node.cached_outputs[node.out_midi] is notes
    assert node.is_baked
    assert progress == [0.05, 0.15, 1.0]


def test_basic_pitch_bake_rejects_missing_input(make_node, monkeypatch):
    from nodes.basic_pitch_node import node as node_module

    node = make_node("Basic Pitch")
    node.is_baked = True
    monkeypatch.setattr(node_module, "append_log", lambda *_args, **_kwargs: None)

    assert not node.bake()
    assert not node.is_baked


def test_basic_pitch_bake_handles_model_and_transcription_failures(make_node, monkeypatch):
    from core import DataPacket, DataType
    from nodes.basic_pitch_node import node as node_module

    node = make_node("Basic Pitch")
    node.input_values[node.in_audio] = DataPacket(
        np.ones((1, 8), dtype=np.float32), DataType.AUDIO)
    monkeypatch.setattr(node_module, "append_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(node_module.logger, "exception", lambda *_args, **_kwargs: None)
    node._report_progress = lambda _value: None
    node.is_baked = True
    node._processor.get_model = lambda: (_ for _ in ()).throw(RuntimeError("load failed"))

    assert not node.bake()
    assert not node.is_baked

    node.is_baked = True
    node._processor.get_model = lambda: object()
    node._processor.transcribe = lambda *_args, **_kwargs: (
        _ for _ in ()).throw(RuntimeError("predict failed"))

    assert not node.bake()
    assert not node.is_baked