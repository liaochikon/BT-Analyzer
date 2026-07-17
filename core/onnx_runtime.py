"""Shared ONNX Runtime session creation for portable inference nodes."""

from dataclasses import dataclass
import os


@dataclass(frozen=True, slots=True)
class OnnxSession:
    session: object
    device: str
    warnings: tuple[str, ...] = ()


class OnnxSessionProvider:
    """Create and cache sessions with deterministic CPU/CUDA provider selection."""

    def __init__(self) -> None:
        self._sessions: dict[tuple[str, tuple[str, ...]], object] = {}

    def get(self, model_path: str, requested_device: str, *, ort_module=None) -> OnnxSession:
        if ort_module is None:
            import onnxruntime as ort_module

        path = os.path.abspath(model_path)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"ONNX model not found: {path}")

        available = set(ort_module.get_available_providers())
        requested = str(requested_device).lower()
        use_cuda = requested in ("auto", "cuda") and "CUDAExecutionProvider" in available
        warnings = ()
        if requested == "cuda" and not use_cuda:
            warnings = ("CUDA requested but unavailable; falling back to CPU.",)

        providers = (
            ("CUDAExecutionProvider", "CPUExecutionProvider")
            if use_cuda else ("CPUExecutionProvider",)
        )
        key = (path, providers)
        session = self._sessions.get(key)
        if session is None:
            session = ort_module.InferenceSession(path, providers=list(providers))
            self._sessions[key] = session
        return OnnxSession(session, "cuda" if use_cuda else "cpu", warnings)
