"""Headless BS-RoFormer source-separation processor."""

from dataclasses import dataclass
import logging
import os
import shutil
import tempfile

import numpy as np
import soundfile as sf

from core.inference_contracts import InferenceError, InferenceErrorCode


@dataclass(frozen=True, slots=True)
class BSRoformerResult:
    stems: dict[str, np.ndarray] | None = None
    error: InferenceError | None = None

    def __post_init__(self):
        if (self.stems is None) == (self.error is None):
            raise ValueError("BSRoformerResult requires exactly one of stems or error")

    @property
    def ok(self) -> bool:
        return self.error is None


class BSRoformerProcessor:
    """Run audio-separator in an isolated temporary workspace."""

    def separate(self, mix, *, model_name: str, requested_device: str,
                 sample_rate: int, ffmpeg_cache_dir: str, progress=None,
                 separator_factory=None, ensure_ffmpeg=None, write=sf.write,
                 read=sf.read, resample=None, make_temp_dir=None,
                 remove_tree=shutil.rmtree, exists=os.path.exists,
                 environ=None) -> BSRoformerResult:
        if separator_factory is None:
            try:
                from audio_separator.separator import Separator
            except ImportError as exc:
                return self._failure(
                    InferenceErrorCode.DEPENDENCY_UNAVAILABLE,
                    "audio-separator unavailable",
                    exc,
                )
            separator_factory = Separator

        normalized = self.normalize_mix(mix)
        environment = os.environ if environ is None else environ
        temporary_dir = (make_temp_dir or tempfile.mkdtemp)(prefix="bsrof_")
        force_cpu = str(requested_device).lower() == "cpu"
        previous_cuda = environment.get("CUDA_VISIBLE_DEVICES")

        try:
            if force_cpu:
                environment["CUDA_VISIBLE_DEVICES"] = ""
            input_path = os.path.join(temporary_dir, "input.wav")
            try:
                write(input_path, normalized.T, sample_rate)
                (ensure_ffmpeg or self.ensure_ffmpeg)(ffmpeg_cache_dir, environment)
                separator = separator_factory(
                    log_level=logging.WARNING,
                    output_dir=temporary_dir,
                    output_format="WAV",
                    sample_rate=sample_rate,
                    use_soundfile=True,
                )
            except Exception as exc:
                return self._failure(
                    InferenceErrorCode.DEPENDENCY_UNAVAILABLE,
                    "BS-RoFormer runtime unavailable",
                    exc,
                )

            if progress is not None:
                progress(0.05)
            try:
                separator.load_model(model_filename=model_name)
            except Exception as exc:
                return self._failure(
                    InferenceErrorCode.MODEL_LOAD_FAILED,
                    f"Failed to load BS-RoFormer model '{model_name}'",
                    exc,
                )

            if progress is not None:
                progress(0.15)
            try:
                output_files = separator.separate(
                    input_path,
                    custom_output_names={
                        "Vocals": os.path.join(temporary_dir, "vocals"),
                        "Instrumental": os.path.join(temporary_dir, "instrumental"),
                    },
                )
            except Exception as exc:
                return self._failure(
                    InferenceErrorCode.EXECUTION_FAILED,
                    "BS-RoFormer separation failed",
                    exc,
                )

            if progress is not None:
                progress(0.9)
            try:
                stems = self._read_stems(
                    output_files,
                    temporary_dir,
                    sample_rate,
                    read,
                    resample,
                    exists,
                )
            except Exception as exc:
                return self._failure(
                    InferenceErrorCode.OUTPUT_INVALID,
                    "BS-RoFormer returned invalid output",
                    exc,
                )
            return BSRoformerResult(stems=stems)
        finally:
            if force_cpu:
                if previous_cuda is None:
                    environment.pop("CUDA_VISIBLE_DEVICES", None)
                else:
                    environment["CUDA_VISIBLE_DEVICES"] = previous_cuda
            remove_tree(temporary_dir, ignore_errors=True)

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
    def ensure_ffmpeg(cache_dir: str, environment) -> None:
        if shutil.which("ffmpeg", path=environment.get("PATH")):
            return
        try:
            import imageio_ffmpeg
        except ImportError as exc:
            raise RuntimeError(
                "FFmpeg not found. Install ffmpeg or imageio-ffmpeg.") from exc

        source = imageio_ffmpeg.get_ffmpeg_exe()
        executable = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
        destination = os.path.join(cache_dir, executable)
        if not os.path.exists(destination):
            os.makedirs(cache_dir, exist_ok=True)
            shutil.copy2(source, destination)
        environment["PATH"] = cache_dir + os.pathsep + environment.get("PATH", "")

    @classmethod
    def _read_stems(
            cls, output_files, temporary_dir, sample_rate, read, resample, exists):
        paths = {
            "vocals": cls._find_output(output_files, "vocal", temporary_dir),
            "instrumental": cls._find_output(output_files, "instrument", temporary_dir),
        }
        stems = {}
        for name, path in paths.items():
            if not path or not exists(path):
                raise FileNotFoundError(f"missing '{name}' output")
            data, source_rate = read(path, dtype="float32", always_2d=True)
            stem = np.ascontiguousarray(data.T, dtype=np.float32)
            if source_rate != sample_rate:
                if resample is None:
                    import librosa
                    resample = librosa.resample
                stem = np.ascontiguousarray(
                    resample(stem, orig_sr=source_rate, target_sr=sample_rate),
                    dtype=np.float32,
                )
            stems[name] = stem
        return stems

    @staticmethod
    def _find_output(output_files, keyword, temporary_dir):
        for path in output_files:
            if keyword in os.path.basename(path).lower():
                if os.path.isabs(path):
                    return path
                return os.path.join(temporary_dir, os.path.basename(path))
        return None

    @staticmethod
    def _failure(code, message, exception):
        return BSRoformerResult(error=InferenceError(code, message, str(exception)))