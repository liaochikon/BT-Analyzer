"""Headless Demucs source-separation processor."""

from dataclasses import dataclass

import numpy as np

from core.inference_contracts import InferenceError, InferenceErrorCode


@dataclass(frozen=True, slots=True)
class DemucsParameters:
    shifts: int
    split: bool
    overlap: float
    segment: float | None


@dataclass(frozen=True, slots=True)
class DemucsResult:
    stems: dict[str, np.ndarray] | None = None
    error: InferenceError | None = None
    warnings: tuple[str, ...] = ()
    device: str = "cpu"

    def __post_init__(self):
        if (self.stems is None) == (self.error is None):
            raise ValueError("DemucsResult requires exactly one of stems or error")

    @property
    def ok(self) -> bool:
        return self.error is None and self.stems is not None


class DemucsProcessor:
    """Normalize audio, run Demucs, and return channel-first stems by model name."""

    def __init__(self, model_cache=None):
        self._models = model_cache if model_cache is not None else {}

    def separate(self, mix, *, model_name: str, parameters: DemucsParameters,
                 requested_device: str, progress=None, torch_module=None,
                 apply_model=None, model_loader=None, run_model=None) -> DemucsResult:
        try:
            if torch_module is None:
                import torch as torch_module
            if apply_model is None:
                from demucs.apply import apply_model
            if model_loader is None:
                from demucs.pretrained import get_model
                model_loader = get_model
        except ImportError as exc:
            return DemucsResult(error=InferenceError(
                InferenceErrorCode.DEPENDENCY_UNAVAILABLE,
                "Demucs/torch unavailable",
                str(exc),
            ))

        mix = self.normalize_mix(mix)
        device, warnings = self.resolve_device(requested_device, torch_module)
        if progress is not None:
            progress(0.05)

        try:
            model = self._models.get(model_name)
            if model is None:
                model = model_loader(model_name)
                model.eval()
                self._models[model_name] = model
        except Exception as exc:
            return DemucsResult(error=InferenceError(
                InferenceErrorCode.MODEL_LOAD_FAILED,
                f"Failed to load Demucs model '{model_name}'",
                str(exc),
            ), warnings=warnings, device=device)

        if progress is not None:
            progress(0.15)
        try:
            runner = run_model or self._run_model
            sources = runner(
                model, mix, device, parameters, torch_module, apply_model)
        except Exception as exc:
            return DemucsResult(error=InferenceError(
                InferenceErrorCode.EXECUTION_FAILED,
                "Demucs separation failed",
                str(exc),
            ), warnings=warnings, device=device)

        try:
            if not model.sources:
                raise ValueError("model declares no output stems")
            if len(sources) != len(model.sources):
                raise ValueError(
                    f"model returned {len(sources)} stems for {len(model.sources)} names")
            stems = {
                name: np.ascontiguousarray(sources[index], dtype=np.float32)
                for index, name in enumerate(model.sources)
            }
        except Exception as exc:
            return DemucsResult(error=InferenceError(
                InferenceErrorCode.OUTPUT_INVALID,
                "Demucs returned invalid output",
                str(exc),
            ), warnings=warnings, device=device)

        if progress is not None:
            progress(0.9)
        return DemucsResult(stems=stems, warnings=warnings, device=device)

    @staticmethod
    def normalize_mix(mix):
        normalized = np.ascontiguousarray(mix, dtype=np.float32)
        if normalized.ndim == 1:
            normalized = normalized[np.newaxis, :]
        if normalized.shape[0] == 1:
            normalized = np.repeat(normalized, 2, axis=0)
        elif normalized.shape[0] > 2:
            normalized = normalized[:2]
        return normalized

    @staticmethod
    def resolve_device(requested_device: str, torch_module):
        requested = str(requested_device).lower()
        if requested in ("auto", "cuda") and torch_module.cuda.is_available():
            return "cuda", ()
        if requested == "cuda":
            return "cpu", ("CUDA requested but unavailable; falling back to CPU.",)
        return "cpu", ()

    @staticmethod
    def _run_model(model, mix, device, parameters, torch_module, apply_model):
        wav = torch_module.from_numpy(mix)
        reference = wav.mean(0)
        standard_deviation = reference.std() + 1e-8
        wav = (wav - reference.mean()) / standard_deviation
        with torch_module.no_grad():
            sources = apply_model(
                model.to(device),
                wav[None].to(device),
                shifts=parameters.shifts,
                split=parameters.split,
                overlap=parameters.overlap,
                segment=parameters.segment,
                progress=False,
                device=device,
            )[0]
        sources = sources * standard_deviation + reference.mean()
        return sources.cpu().numpy().astype(np.float32)

