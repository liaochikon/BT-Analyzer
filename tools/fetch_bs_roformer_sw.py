"""Download BS-RoFormer SW Fixed checkpoint + yaml for ONNX export.

Weights are not shipped with this repo. Default host is the public mirror
``enerjazzer/BS-ROFO-SW-Fixed`` (byte-identical to the historical jarredou release).
SHA-256 is verified before the files are kept.

    python tools/fetch_bs_roformer_sw.py
    python tools/fetch_bs_roformer_sw.py --output-dir weights
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Primary: enerjazzer mirror after jarredou/BS-ROFO-SW-Fixed was deleted.
# Fallback: FractalSurfer mirror of the same bytes.
_MIRRORS = (
    {
        "ckpt": "https://huggingface.co/enerjazzer/BS-ROFO-SW-Fixed/resolve/main/BS-Rofo-SW-Fixed.ckpt",
        "yaml": "https://huggingface.co/enerjazzer/BS-ROFO-SW-Fixed/resolve/main/BS-Rofo-SW-Fixed.yaml",
    },
    {
        "ckpt": "https://huggingface.co/FractalSurfer/BS-RoFormer-SW-6stem/resolve/main/BS-Rofo-SW-Fixed.ckpt",
        "yaml": "https://huggingface.co/FractalSurfer/BS-RoFormer-SW-6stem/resolve/main/BS-Rofo-SW-Fixed.yaml",
    },
)

CKPT_NAME = "BS-Rofo-SW-Fixed.ckpt"
YAML_NAME = "BS-Rofo-SW-Fixed.yaml"
CKPT_SHA256 = "24e7d35ee9c64415673d3fd33e06a67cac2c103c5df6267ba1576459c775916e"
YAML_SHA256 = "f9fada9f94e5ba2d2e4600196299459294bc5f532b314c209cc156ac63e4329b"
CKPT_BYTES = 699_412_152


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def valid(path: Path, expected_sha: str, expected_size: int | None = None) -> bool:
    if not path.is_file():
        return False
    if expected_size is not None and path.stat().st_size != expected_size:
        return False
    return sha256_file(path) == expected_sha


def download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".partial")
    if partial.exists():
        partial.unlink()
    print(f"downloading {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "bt-analyzer-export-onnx"})
    with urllib.request.urlopen(request) as response, partial.open("wb") as out:
        total = response.headers.get("Content-Length")
        total_n = int(total) if total and total.isdigit() else None
        done = 0
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
            done += len(chunk)
            if total_n:
                pct = done * 100.0 / total_n
                print(f"\r  {done / (1024 * 1024):.1f} / {total_n / (1024 * 1024):.1f} MB ({pct:5.1f}%)", end="", flush=True)
            else:
                print(f"\r  {done / (1024 * 1024):.1f} MB", end="", flush=True)
    print()
    partial.replace(destination)


def ensure(path: Path, urls: list[str], expected_sha: str, expected_size: int | None = None) -> None:
    if valid(path, expected_sha, expected_size):
        print(f"OK  {path} (sha256 verified)")
        return
    if path.exists():
        print(f"removing bad or incomplete file: {path}", file=sys.stderr)
        path.unlink()
    last_error: Exception | None = None
    for url in urls:
        try:
            download(url, path)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            last_error = exc
            print(f"failed: {exc}", file=sys.stderr)
            if path.exists():
                path.unlink()
            partial = path.with_suffix(path.suffix + ".partial")
            if partial.exists():
                partial.unlink()
            continue
        if valid(path, expected_sha, expected_size):
            print(f"OK  {path} (sha256 verified)")
            return
        print(f"checksum mismatch for {path}", file=sys.stderr)
        path.unlink(missing_ok=True)
    raise RuntimeError(f"could not download {path.name}" + (f": {last_error}" if last_error else ""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT,
        help="directory for BS-Rofo-SW-Fixed.ckpt / .yaml (default: repo root)",
    )
    args = parser.parse_args(argv)
    out = args.output_dir
    try:
        ensure(
            out / CKPT_NAME,
            [mirror["ckpt"] for mirror in _MIRRORS],
            CKPT_SHA256,
            CKPT_BYTES,
        )
        ensure(
            out / YAML_NAME,
            [mirror["yaml"] for mirror in _MIRRORS],
            YAML_SHA256,
        )
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(out / CKPT_NAME)
    print(out / YAML_NAME)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
