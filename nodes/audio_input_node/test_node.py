"""Audio Input node: the chosen file path survives serialize_extra / apply_extra (preset + undo)."""

import numpy as np


def test_audio_file_selection_commits_custom_state(make_node):
    audio_input = make_node("Audio Input")
    calls = []
    audio_input._parameter_ui_adapter = type("Adapter", (), {
        "apply_extra": lambda _self, *args: calls.append(args),
    })()

    audio_input._file_selected_callback(
        None, {"file_path_name": "C:/songs/selected.wav"})

    assert calls == [("file_path", "C:/songs/selected.wav")]
    assert audio_input.file_path == ""


def test_audio_input_serializes_file_path(make_node):
    n = make_node("Audio Input")
    n.file_path = "C:/songs/x.mp3"
    assert n.serialize_extra() == {"file_path": "C:/songs/x.mp3"}
    n2 = make_node("Audio Input")
    n2.apply_extra({"file_path": "C:/songs/x.mp3"})
    assert n2.file_path == "C:/songs/x.mp3"


def test_audio_input_bake_commits_processor_output_only_on_success(make_node, monkeypatch):
    from nodes.audio_input_node import node as node_module

    audio_input = make_node("Audio Input")
    audio_input.file_path = "song.wav"
    data = np.ones((2, 8), dtype=np.float32)
    progress = []
    monkeypatch.setattr(node_module.os.path, "exists", lambda _path: True)
    monkeypatch.setattr(node_module, "append_log", lambda *_args, **_kwargs: None)
    audio_input._report_progress = progress.append
    audio_input._processor.decode = lambda *_args, **_kwargs: data

    assert audio_input.bake()
    assert audio_input.cached_outputs[audio_input.out_audio] is data
    assert audio_input.is_baked
    assert progress == [0.8, 1.0]

    audio_input.is_baked = True
    audio_input._processor.decode = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("decode failed"))

    assert not audio_input.bake()
    assert not audio_input.is_baked
