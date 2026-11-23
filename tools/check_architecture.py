#!/usr/bin/env python3
"""Fail if architecture boundaries in ARCHITECTURE.md are violated."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

INCLUDE_RE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]')

UI_MARKERS = (
    "imgui",
    "d3d11",
    "d3d12",
    "dxgi",
    "apps/bt_app/",
    "bt_app/",
)


def sources_under(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return [p for p in root.rglob("*") if p.suffix.lower() in {".hpp", ".h", ".cpp", ".cc", ".cxx"}]


def scan(label: str, files: list[Path], forbidden: tuple[str, ...]) -> list[str]:
    errors: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        for line_no, line in enumerate(text.splitlines(), 1):
            match = INCLUDE_RE.match(line)
            if not match:
                continue
            include = match.group(1).replace("\\", "/").lower()
            for marker in forbidden:
                if marker.lower() in include:
                    rel = path.relative_to(ROOT).as_posix()
                    errors.append(f"{label}: {rel}:{line_no}: forbidden include '{match.group(1)}'")
                    break
    return errors


def main() -> int:
    include_bt = ROOT / "cpp" / "include" / "bt"
    src = ROOT / "cpp" / "src"

    stem_files = [
        p
        for p in sources_under(include_bt) + sources_under(src)
        if "bt/core" not in p.as_posix() and "/core/" not in p.as_posix()
    ]
    core_files = sources_under(include_bt / "core") + sources_under(src / "core")

    errors: list[str] = []
    # bt_stem must stay UI-free and must not include bt_core (layering).
    errors += scan("bt_stem", stem_files, UI_MARKERS + ("bt/core/",))
    # bt_core may use bt_stem headers but never UI.
    errors += scan("bt_core", core_files, UI_MARKERS)

    if errors:
        print("architecture boundary violations:")
        for item in errors:
            print(f"  {item}")
        return 1
    print(f"architecture boundaries ok ({len(stem_files)} stem files, {len(core_files)} core files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
