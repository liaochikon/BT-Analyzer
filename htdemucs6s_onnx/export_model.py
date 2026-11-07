"""Export pretrained htdemucs_6s to one ONNX file plus a JSON contract.

Export is a development step. It needs PyTorch, demucs, and demucs-onnx.
Inference in ``separate.py`` needs only ONNX Runtime.
"""

from __future__ import annotations

from pathlib import Path

from htdemucs6s_onnx.contract import MODEL_NAME, write_contract


def export_htdemucs_6s(output: Path | str) -> tuple[Path, Path]:
    """Write ``output`` and a sibling ``.contract.json``. Return both paths."""
    try:
        from demucs_onnx.export import export_to_onnx
    except ImportError as exc:
        raise RuntimeError(
            "Export needs PyTorch, demucs==4.0.1, onnx, and demucs-onnx. "
            "See requirements-htdemucs-onnx.txt."
        ) from exc

    destination = Path(output)
    if destination.suffix.lower() != ".onnx":
        destination = destination / f"{MODEL_NAME}.onnx"
    exported = export_to_onnx(MODEL_NAME, destination)
    if MODEL_NAME in exported:
        model_path = Path(exported[MODEL_NAME])
    elif len(exported) == 1:
        model_path = Path(next(iter(exported.values())))
    else:
        raise RuntimeError(f"export returned unexpected files: {exported}")
    contract_path = write_contract(model_path.with_suffix(".contract.json"))
    return model_path, contract_path
