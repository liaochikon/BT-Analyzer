"""Headless Demucs processor contracts."""

import numpy as np


class _Cuda:
    def __init__(self, available):
        self._available = available

    def is_available(self):
        return self._available


class _Torch:
    def __init__(self, cuda_available=False):
        self.cuda = _Cuda(cuda_available)


class _Model:
    sources = ("drums", "vocals")

    def __init__(self):
        self.evaluated = False

    def eval(self):
        self.evaluated = True


def _parameters():
    from nodes.demucs_node.processor import DemucsParameters

    return DemucsParameters(shifts=1, split=True, overlap=0.25, segment=None)


def test_demucs_processor_normalizes_mix_maps_stems_and_falls_back_to_cpu():
    from nodes.demucs_node.processor import DemucsProcessor

    observed = {}
    progress = []
    model = _Model()

    def run_model(received_model, mix, device, parameters, *_args):
        observed.update(model=received_model, mix=mix, device=device, parameters=parameters)
        return np.stack((np.ones_like(mix), np.full_like(mix, 2.0)))

    result = DemucsProcessor().separate(
        np.ones(8, dtype=np.float32),
        model_name="test-model",
        parameters=_parameters(),
        requested_device="cuda",
        progress=progress.append,
        torch_module=_Torch(cuda_available=False),
        apply_model=object(),
        model_loader=lambda _name: model,
        run_model=run_model,
    )

    assert result.ok
    assert result.device == "cpu"
    assert result.warnings == ("CUDA requested but unavailable; falling back to CPU.",)
    assert model.evaluated
    assert observed["mix"].shape == (2, 8)
    assert result.stems["drums"].shape == (2, 8)
    assert result.stems["vocals"][0, 0] == 2.0
    assert progress == [0.05, 0.15, 0.9]


def test_demucs_processor_returns_model_and_execution_errors():
    from core.inference_contracts import InferenceErrorCode
    from nodes.demucs_node.processor import DemucsProcessor

    load_failed = DemucsProcessor().separate(
        np.ones((2, 8), dtype=np.float32),
        model_name="missing",
        parameters=_parameters(),
        requested_device="cpu",
        torch_module=_Torch(),
        apply_model=object(),
        model_loader=lambda _name: (_ for _ in ()).throw(RuntimeError("download failed")),
    )
    execution_failed = DemucsProcessor().separate(
        np.ones((2, 8), dtype=np.float32),
        model_name="test-model",
        parameters=_parameters(),
        requested_device="cpu",
        torch_module=_Torch(),
        apply_model=object(),
        model_loader=lambda _name: _Model(),
        run_model=lambda *_args: (_ for _ in ()).throw(RuntimeError("inference crashed")),
    )

    assert load_failed.error.code is InferenceErrorCode.MODEL_LOAD_FAILED
    assert load_failed.error.detail == "download failed"
    assert execution_failed.error.code is InferenceErrorCode.EXECUTION_FAILED
    assert execution_failed.error.detail == "inference crashed"


def test_demucs_processor_rejects_mismatched_model_output():
    from core.inference_contracts import InferenceErrorCode
    from nodes.demucs_node.processor import DemucsProcessor

    result = DemucsProcessor().separate(
        np.ones((2, 8), dtype=np.float32),
        model_name="test-model",
        parameters=_parameters(),
        requested_device="cpu",
        torch_module=_Torch(),
        apply_model=object(),
        model_loader=lambda _name: _Model(),
        run_model=lambda *_args: np.ones((1, 2, 8), dtype=np.float32),
    )

    assert result.error.code is InferenceErrorCode.OUTPUT_INVALID
    assert "1 stems for 2 names" in result.error.detail