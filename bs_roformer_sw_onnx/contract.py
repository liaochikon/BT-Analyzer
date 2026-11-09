"""I/O contract for one fixed BS-RoFormer SW ONNX chunk.

The graph takes a complex STFT and returns six masked spectrograms.
Overlap-add and the inverse STFT stay in the host.
"""

from __future__ import annotations

import json
from pathlib import Path

MODEL_NAME = "bs_roformer_sw"
SAMPLE_RATE = 44100
N_FFT = 2048
HOP_LENGTH = 512
FREQ_BINS = N_FFT // 2 + 1
DEFAULT_CHUNK_SAMPLES = 588800
DEFAULT_OVERLAP = 2

INPUT_REAL = "spec_real"
INPUT_IMAG = "spec_imag"
OUTPUT_REAL = "out_spec_real"
OUTPUT_IMAG = "out_spec_imag"
STEMS = ("bass", "drums", "other", "vocals", "guitar", "piano")


def time_frames(chunk_samples: int) -> int:
    """Match the C++ host: T = samples / hop + 1, with samples a multiple of hop."""
    if chunk_samples < HOP_LENGTH or chunk_samples % HOP_LENGTH != 0:
        raise ValueError(
            f"chunk length must be a positive multiple of {HOP_LENGTH}, got {chunk_samples}"
        )
    return chunk_samples // HOP_LENGTH + 1


def contract_document(chunk_samples: int = DEFAULT_CHUNK_SAMPLES) -> dict:
    frames = time_frames(chunk_samples)
    return {
        "model_name": MODEL_NAME,
        "sample_rate": SAMPLE_RATE,
        "n_fft": N_FFT,
        "hop_length": HOP_LENGTH,
        "chunk_samples": chunk_samples,
        "time_frames": frames,
        "default_overlap": DEFAULT_OVERLAP,
        "input": {
            "names": [INPUT_REAL, INPUT_IMAG],
            "dtype": "float32",
            "layout": "batch, channels, frequency, time",
            "shape": [1, 2, FREQ_BINS, frames],
        },
        "output": {
            "names": [OUTPUT_REAL, OUTPUT_IMAG],
            "dtype": "float32",
            "layout": "batch, stems, channels, frequency, time",
            "shape": [1, len(STEMS), 2, FREQ_BINS, frames],
        },
        "stems": list(STEMS),
        "normalization": "None. The host does not apply file-level mean and standard deviation.",
    }


def write_contract(path: Path, chunk_samples: int) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(contract_document(chunk_samples), indent=2) + "\n",
        encoding="utf-8",
    )
    return path
