"""Headless BS-RoFormer processor contracts."""

import os

import numpy as np


class _Separator:
    def __init__(self):
        self.model_name = None
        self.input_path = None

    def load_model(self, *, model_filename):
        self.model_name = model_filename

    def separate(self, input_path, *, custom_output_names):
        self.input_path = input_path
        return [
            custom_output_names["Vocals"] + ".wav",
            custom_output_names["Instrumental"] + ".wav",
        ]


def test_bs_roformer_processor_restores_cuda_cleans_temp_and_maps_stems(tmp_path):
    from nodes.bs_roformer_node.processor import BSRoformerProcessor

    separator = _Separator()
    environment = {"CUDA_VISIBLE_DEVICES": "7", "PATH": "tools"}
    removed = []
    progress = []
    written = []
    temporary_dir = str(tmp_path)

    def write(path, data, sample_rate):
        written.append((path, data.copy(), sample_rate))

    def read(path, **_kwargs):
        return np.ones((8, 2), dtype=np.float32), 44100

    result = BSRoformerProcessor().separate(
        np.ones(8, dtype=np.float32),
        model_name="model.ckpt",
        requested_device="cpu",
        sample_rate=44100,
        ffmpeg_cache_dir="ffmpeg-cache",
        progress=progress.append,
        separator_factory=lambda **_kwargs: separator,
        ensure_ffmpeg=lambda *_args: None,
        write=write,
        read=read,
        exists=lambda _path: True,
        make_temp_dir=lambda **_kwargs: temporary_dir,
        remove_tree=lambda path, **_kwargs: removed.append(path),
        environ=environment,
    )

    assert result.ok
    assert result.stems["vocals"].shape == (2, 8)
    assert result.stems["instrumental"].shape == (2, 8)
    assert separator.model_name == "model.ckpt"
    assert separator.input_path == os.path.join(temporary_dir, "input.wav")
    assert written[0][1].shape == (8, 2)
    assert environment["CUDA_VISIBLE_DEVICES"] == "7"
    assert removed == [temporary_dir]
    assert progress == [0.05, 0.15, 0.9]


def test_bs_roformer_processor_classifies_failure_and_always_cleans(tmp_path):
    from core.inference_contracts import InferenceErrorCode
    from nodes.bs_roformer_node.processor import BSRoformerProcessor

    class BrokenSeparator(_Separator):
        def load_model(self, **_kwargs):
            raise RuntimeError("download failed")

    environment = {"PATH": "tools"}
    removed = []
    result = BSRoformerProcessor().separate(
        np.ones((2, 8), dtype=np.float32),
        model_name="model.ckpt",
        requested_device="cpu",
        sample_rate=44100,
        ffmpeg_cache_dir="ffmpeg-cache",
        separator_factory=lambda **_kwargs: BrokenSeparator(),
        ensure_ffmpeg=lambda *_args: None,
        write=lambda *_args: None,
        make_temp_dir=lambda **_kwargs: str(tmp_path),
        remove_tree=lambda path, **_kwargs: removed.append(path),
        environ=environment,
    )

    assert result.error.code is InferenceErrorCode.MODEL_LOAD_FAILED
    assert result.error.detail == "download failed"
    assert "CUDA_VISIBLE_DEVICES" not in environment
    assert removed == [str(tmp_path)]


def test_bs_roformer_processor_distinguishes_execution_and_output_failures(tmp_path):
    from core.inference_contracts import InferenceErrorCode
    from nodes.bs_roformer_node.processor import BSRoformerProcessor

    class ExecutionFailure(_Separator):
        def separate(self, *_args, **_kwargs):
            raise RuntimeError("inference crashed")

    common = {
        "mix": np.ones((2, 8), dtype=np.float32),
        "model_name": "model.ckpt",
        "requested_device": "auto",
        "sample_rate": 44100,
        "ffmpeg_cache_dir": "ffmpeg-cache",
        "ensure_ffmpeg": lambda *_args: None,
        "write": lambda *_args: None,
        "make_temp_dir": lambda **_kwargs: str(tmp_path),
        "remove_tree": lambda *_args, **_kwargs: None,
        "environ": {"PATH": "tools"},
    }
    execution_failed = BSRoformerProcessor().separate(
        **common,
        separator_factory=lambda **_kwargs: ExecutionFailure(),
    )
    output_failed = BSRoformerProcessor().separate(
        **common,
        separator_factory=lambda **_kwargs: _Separator(),
        exists=lambda _path: False,
    )

    assert execution_failed.error.code is InferenceErrorCode.EXECUTION_FAILED
    assert execution_failed.error.detail == "inference crashed"
    assert output_failed.error.code is InferenceErrorCode.OUTPUT_INVALID
    assert "missing 'vocals' output" in output_failed.error.detail