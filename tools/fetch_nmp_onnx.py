"""Copy Spotify's published nmp.onnx out of the basic-pitch 0.4.0 wheel.

The C++ app loads this file as models/nmp.onnx. It is already ONNX inside the
package; this script only downloads the wheel and extracts that member.

    python tools/fetch_nmp_onnx.py
"""

from __future__ import annotations

import io
import json
import sys
import urllib.request
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "models" / "nmp.onnx"
MEMBER = "basic_pitch/saved_models/icassp_2022/nmp.onnx"
PACKAGE_URL = "https://pypi.org/pypi/basic-pitch/0.4.0/json"


def wheel_url() -> str:
    with urllib.request.urlopen(PACKAGE_URL) as response:
        meta = json.load(response)
    for item in meta["urls"]:
        name = item["filename"]
        if item["packagetype"] == "bdist_wheel" and name.endswith(".whl"):
            return item["url"]
    raise RuntimeError("basic-pitch 0.4.0 has no wheel")


def main() -> int:
    url = wheel_url()
    print(f"downloading {url}")
    with urllib.request.urlopen(url) as response:
        payload = response.read()
    with ZipFile(io.BytesIO(payload)) as archive:
        if MEMBER not in archive.namelist():
            print(f"wheel is missing {MEMBER}", file=sys.stderr)
            return 1
        DESTINATION.parent.mkdir(parents=True, exist_ok=True)
        DESTINATION.write_bytes(archive.read(MEMBER))
    print(DESTINATION)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
