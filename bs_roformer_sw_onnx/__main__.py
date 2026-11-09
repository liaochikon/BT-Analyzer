"""Command line for the BS-RoFormer SW ONNX export.

``python -m bs_roformer_sw_onnx export models/bs_roformer_sw.onnx --checkpoint model.ckpt --config model.yaml``
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bs_roformer_sw_onnx.export_model import export_bs_roformer_sw, write_fp16_compute


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bs_roformer_sw_onnx")
    commands = parser.add_subparsers(dest="command", required=True)

    export = commands.add_parser("export", help="export a local checkpoint and write its contract")
    export.add_argument("output", type=Path, help="destination .onnx file or directory")
    export.add_argument("--checkpoint", type=Path, required=True, help="local .ckpt; this command does not download weights")
    export.add_argument("--config", type=Path, required=True, help="yaml that matches the checkpoint")
    export.add_argument(
        "--chunk-samples",
        type=int,
        default=None,
        help="waveform chunk length. Default is audio.chunk_size from the yaml. Must be a multiple of 512.",
    )
    export.add_argument(
        "--fp16",
        type=Path,
        default=None,
        help="also write this path with float16 compute and float32 inputs and outputs",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        model_path, contract_path = export_bs_roformer_sw(
            args.output,
            args.checkpoint,
            args.config,
            chunk_samples=args.chunk_samples,
        )
        fp16_path = None if args.fp16 is None else write_fp16_compute(model_path, args.fp16)
    except (RuntimeError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1
    print(model_path)
    print(contract_path)
    if fp16_path is not None:
        print(fp16_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
