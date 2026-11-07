"""Compare a local htdemucs_6s ONNX graph with Demucs apply_model.

Skipped unless models/htdemucs_6s.onnx and the export stack are installed.
"""

from pathlib import Path

import numpy as np
import pytest

from htdemucs6s_onnx.contract import STEMS
from htdemucs6s_onnx.separate import OnnxSegmentModel, separate

MODEL_PATH = Path("models/htdemucs_6s.onnx")


def test_onnx_overlap_add_matches_demucs_apply_model():
    pytest.importorskip("onnxruntime")
    torch = pytest.importorskip("torch")
    pytest.importorskip("demucs")
    if not MODEL_PATH.is_file():
        pytest.skip("export models/htdemucs_6s.onnx first")

    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    mix = np.random.default_rng(0).standard_normal((2, 12 * 44100)).astype(np.float32) * 0.1
    onnx_stems = separate(mix, OnnxSegmentModel(MODEL_PATH))

    loaded = get_model("htdemucs_6s")
    assert tuple(loaded.sources) == STEMS
    wav = torch.from_numpy(mix)
    reference = wav.mean(0)
    deviation = reference.std() + 1e-8
    normalized = (wav - reference.mean()) / deviation
    with torch.no_grad():
        sources = apply_model(
            loaded, normalized[None], shifts=0, split=True, overlap=0.25,
            segment=None, progress=False, device="cpu")[0]
    reference_stems = (sources * deviation + reference.mean()).numpy()

    for index, name in enumerate(STEMS):
        np.testing.assert_allclose(onnx_stems[name], reference_stems[index], atol=1e-3, rtol=0)
