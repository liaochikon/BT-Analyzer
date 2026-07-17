from pathlib import Path


class _FakeOrt:
    def __init__(self, providers):
        self.providers = providers
        self.created = []

    def get_available_providers(self):
        return self.providers

    def InferenceSession(self, path, providers):
        session = object()
        self.created.append((path, providers, session))
        return session


def test_onnx_session_provider_selects_cuda_and_caches(tmp_path: Path):
    from core.onnx_runtime import OnnxSessionProvider

    model_path = tmp_path / "model.onnx"
    model_path.touch()
    ort = _FakeOrt(["CUDAExecutionProvider", "CPUExecutionProvider"])
    provider = OnnxSessionProvider()

    first = provider.get(str(model_path), "auto", ort_module=ort)
    second = provider.get(str(model_path), "auto", ort_module=ort)

    assert first.device == "cuda"
    assert first.session is second.session
    assert ort.created[0][1] == ["CUDAExecutionProvider", "CPUExecutionProvider"]
    assert len(ort.created) == 1


def test_onnx_session_provider_reports_cuda_fallback(tmp_path: Path):
    from core.onnx_runtime import OnnxSessionProvider

    model_path = tmp_path / "model.onnx"
    model_path.touch()
    ort = _FakeOrt(["CPUExecutionProvider"])

    result = OnnxSessionProvider().get(str(model_path), "cuda", ort_module=ort)

    assert result.device == "cpu"
    assert result.warnings == ("CUDA requested but unavailable; falling back to CPU.",)
    assert ort.created[0][1] == ["CPUExecutionProvider"]