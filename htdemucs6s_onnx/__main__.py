"""Command line for the htdemucs_6s ONNX slice.

``python -m htdemucs6s_onnx export models/htdemucs_6s.onnx``
``python -m htdemucs6s_onnx separate song.mp3 stems/ --model models/htdemucs_6s.onnx``
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from htdemucs6s_onnx.contract import DEFAULT_OVERLAP
from htdemucs6s_onnx.export_model import export_htdemucs_6s
from htdemucs6s_onnx.separate import OnnxSegmentModel, load_wav, separate, write_stems


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="htdemucs6s_onnx")
    commands = parser.add_subparsers(dest="command", required=True)

    export = commands.add_parser("export", help="export htdemucs_6s and write its contract")
    export.add_argument("output", type=Path, help="destination .onnx file or directory")

    run = commands.add_parser("separate", help="separate a wav, flac, or mp3 into six stems")
    run.add_argument("audio", type=Path)
    run.add_argument("output_dir", type=Path)
    run.add_argument("--model", type=Path, required=True)
    run.add_argument("--overlap", type=float, default=DEFAULT_OVERLAP)
    run.add_argument(
        "--provider",
        action="append",
        dest="providers",
        help="ONNX Runtime execution provider. Repeat to set a fallback list. "
             "Default is CPUExecutionProvider.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "export":
        try:
            model_path, contract_path = export_htdemucs_6s(args.output)
        except RuntimeError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(model_path)
        print(contract_path)
        return 0

    mix = load_wav(args.audio)
    model = OnnxSegmentModel(args.model, providers=args.providers)

    def _progress(fraction: float) -> None:
        print(f"\r{fraction * 100:5.1f}%", end="", flush=True)

    stems = separate(mix, model, overlap=args.overlap, progress=_progress)
    print()
    for path in write_stems(stems, args.output_dir):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
