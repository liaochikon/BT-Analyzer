"""Assemble repo-root cuda-ep/ for portable CUDA inference.

1. Downloads ONNX Runtime 1.31.0 GPU package (CUDA EP plugin DLLs).
2. Downloads NVIDIA CUDA / cuDNN redistributable wheels via pip and copies
   every *.dll into the same flat folder (ORT's GPU zip does not ship these).

Default CUDA major is 12 (matches cublasLt64_12.dll). Use --cuda 13 for CUDA 13.

    python tools/fetch_cuda_ep.py
    python tools/fetch_cuda_ep.py --cuda 13 --force
"""

from __future__ import annotations

import argparse
import io
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "cuda-ep"
ORT_VERSION = "1.31.0"
MARKER = "ORT_CUDA.txt"
PROVIDER = "onnxruntime_providers_cuda.dll"

# Pip packages that carry Windows CUDA/cuDNN runtime DLLs.
NVIDIA_PACKAGES = {
    "12": (
        "nvidia-cublas-cu12",
        "nvidia-cuda-runtime-cu12",
        "nvidia-cuda-nvrtc-cu12",
        "nvidia-cudnn-cu12",
        "nvidia-cufft-cu12",
        "nvidia-curand-cu12",
        "nvidia-nvjitlink-cu12",
    ),
    "13": (
        "nvidia-cublas-cu13",
        "nvidia-cuda-runtime-cu13",
        "nvidia-cuda-nvrtc-cu13",
        "nvidia-cudnn-cu13",
        "nvidia-cufft-cu13",
        "nvidia-curand-cu13",
        "nvidia-nvjitlink-cu13",
    ),
}


def package_url(cuda_major: str) -> str:
    name = f"onnxruntime-win-x64-gpu_cuda{cuda_major}-{ORT_VERSION}.zip"
    return f"https://github.com/microsoft/onnxruntime/releases/download/v{ORT_VERSION}/{name}"


def marker_text(cuda_major: str) -> str:
    return f"ort={ORT_VERSION}\ncuda={cuda_major}\nredistrib=nvidia-pip\n"


def already_ready(destination: Path, cuda_major: str) -> bool:
    provider = destination / PROVIDER
    marker = destination / MARKER
    # ORT EP alone is not enough; require a known cublasLt name for the major.
    cublas = destination / f"cublasLt64_{cuda_major}.dll"
    if not provider.is_file() or not marker.is_file() or not cublas.is_file():
        return False
    return marker.read_text(encoding="utf-8") == marker_text(cuda_major)


def download(url: str) -> bytes:
    print(f"downloading {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "bt-analyzer-fetch-cuda"})
    with urllib.request.urlopen(request) as response:
        total = response.headers.get("Content-Length")
        total_n = int(total) if total and total.isdigit() else None
        chunks: list[bytes] = []
        done = 0
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
            done += len(chunk)
            if total_n:
                pct = done * 100.0 / total_n
                print(
                    f"\r  {done / (1024 * 1024):.1f} / {total_n / (1024 * 1024):.1f} MB ({pct:5.1f}%)",
                    end="",
                    flush=True,
                )
        if total_n:
            print()
    return b"".join(chunks)


def extract_dlls_from_zip_bytes(payload: bytes, destination: Path) -> int:
    destination.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = Path(info.filename).name
            if not name.lower().endswith(".dll"):
                continue
            target = destination / name
            with archive.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            count += 1
    return count


def extract_dlls_from_wheel(wheel: Path, destination: Path) -> int:
    count = 0
    with zipfile.ZipFile(wheel) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = Path(info.filename).name
            if not name.lower().endswith(".dll"):
                continue
            target = destination / name
            with archive.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            count += 1
    return count


def fetch_nvidia_dlls(cuda_major: str, destination: Path, work: Path) -> int:
    packages = NVIDIA_PACKAGES[cuda_major]
    wheel_dir = work / "wheels"
    wheel_dir.mkdir(parents=True, exist_ok=True)
    print("downloading NVIDIA CUDA/cuDNN redistributable wheels via pip...")
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "download",
        "--only-binary=:all:",
        "--python-version",
        "3.12",
        "--platform",
        "win_amd64",
        "--dest",
        str(wheel_dir),
        *packages,
    ]
    completed = subprocess.run(cmd, check=False)
    if completed.returncode != 0:
        raise RuntimeError("pip download of NVIDIA packages failed")
    total = 0
    wheels = sorted(wheel_dir.glob("*.whl"))
    if not wheels:
        raise RuntimeError("pip download produced no wheels")
    for wheel in wheels:
        print(f"  extracting {wheel.name}")
        total += extract_dlls_from_wheel(wheel, destination)
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch ORT CUDA EP + NVIDIA runtime DLLs into cuda-ep/")
    parser.add_argument("--cuda", choices=("12", "13"), default="12", help="CUDA major (default: 12)")
    parser.add_argument("--force", action="store_true", help="Re-download even if cuda-ep looks ready")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT, help="Destination (default: <repo>/cuda-ep)")
    args = parser.parse_args()
    destination = args.output_dir.resolve()

    if not args.force and already_ready(destination, args.cuda):
        print(f"cuda-ep already ready (ORT {ORT_VERSION}, CUDA {args.cuda} + NVIDIA redistrib)")
        print(destination / PROVIDER)
        return 0

    url = package_url(args.cuda)
    try:
        ort_payload = download(url)
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        print(f"ORT GPU download failed: {exc}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="bt-cuda-ep-") as tmp:
        work = Path(tmp)
        staging = work / "stage"
        staging.mkdir()
        ort_count = extract_dlls_from_zip_bytes(ort_payload, staging)
        if not (staging / PROVIDER).is_file():
            print(f"ORT zip is missing {PROVIDER}", file=sys.stderr)
            return 1
        try:
            nvidia_count = fetch_nvidia_dlls(args.cuda, staging, work)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        cublas = staging / f"cublasLt64_{args.cuda}.dll"
        if not cublas.is_file():
            print(f"NVIDIA packages did not provide {cublas.name}", file=sys.stderr)
            return 1
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(staging, destination)

    (destination / MARKER).write_text(marker_text(args.cuda), encoding="utf-8")
    print(f"wrote {ort_count} ORT + {nvidia_count} NVIDIA DLLs -> {destination}")
    print(destination / PROVIDER)
    print(destination / f"cublasLt64_{args.cuda}.dll")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
