"""Frozen I/O contract for the htdemucs_6s ONNX graph.

The graph is one fixed segment. Overlap-add, file normalization, and stem
naming stay in the host so the same file can be loaded from ONNX Runtime in
Python or C++.
"""

from __future__ import annotations

import json
from pathlib import Path

MODEL_NAME = "htdemucs_6s"
SAMPLE_RATE = 44100
SEGMENT_SECONDS = 7.8
SEGMENT_SAMPLES = int(SEGMENT_SECONDS * SAMPLE_RATE)  # 343980
DEFAULT_OVERLAP = 0.25
DEFAULT_SHIFTS = 0

INPUT_NAME = "mix"
OUTPUT_NAME = "stems"
STEMS = ("drums", "bass", "other", "vocals", "guitar", "piano")


def contract_document() -> dict:
    return {
        "model_name": MODEL_NAME,
        "sample_rate": SAMPLE_RATE,
        "segment_seconds": SEGMENT_SECONDS,
        "segment_samples": SEGMENT_SAMPLES,
        "default_overlap": DEFAULT_OVERLAP,
        "default_shifts": DEFAULT_SHIFTS,
        "input": {
            "name": INPUT_NAME,
            "dtype": "float32",
            "layout": "batch, channels, samples",
            "shape": [1, 2, SEGMENT_SAMPLES],
        },
        "output": {
            "name": OUTPUT_NAME,
            "dtype": "float32",
            "layout": "batch, stems, channels, samples",
            "shape": [1, len(STEMS), 2, SEGMENT_SAMPLES],
        },
        "stems": list(STEMS),
        "normalization": (
            "Per file, before chunking: subtract the mean of the channel-averaged "
            "waveform and divide by its unbiased standard deviation plus 1e-8. "
            "Invert that affine transform after overlap-add."
        ),
    }


def write_contract(path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(contract_document(), indent=2) + "\n", encoding="utf-8")
    return path
