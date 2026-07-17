"""Verify a font covers what BT Analyzer needs: Chinese text + the icon codepoints we use.

Run after merging (or on any candidate font) to confirm before wiring it into the app:
    python tools/check_font_icons.py fonts/sarasa-gothic-tc-nerd-merged.ttf

Reports whether representative Chinese characters, the current Font Awesome icons, and any
extra codepoints you pass as further args (e.g. 0xF0199) are present.
"""

import sys

from fontTools.ttLib import TTFont


# Codepoints the app already uses (Font Awesome range) + a couple of CJK samples.
CHECKS = {
    "CJK 繁 (U+7E41)": 0x7E41,
    "CJK 中 (U+4E2D)": 0x4E2D,
    "CJK 節 (U+7BC0)": 0x7BC0,
    "fa-play (U+F04B)": 0xF04B,
    "fa-pause (U+F04C)": 0xF04C,
    "fa-stop (U+F04D)": 0xF04D,
    "fa-repeat (U+F01E)": 0xF01E,
    "fa-times (U+F00D)": 0xF00D,
    "fa-clock-o (U+F017)": 0xF017,
    "fa-music (U+F001)": 0xF001,
    "fa-undo (U+F0E2)": 0xF0E2,
    # MDI candidates the user wants for count-in / metronome:
    "MDI (U+F0199)": 0xF0199,
}


def main(path: str, extra: list) -> None:
    with TTFont(path, fontNumber=0, lazy=True) as f:
        cmap = set(f.getBestCmap().keys())

    checks = dict(CHECKS)
    for cp in extra:
        checks[f"extra (U+{cp:05X})"] = cp

    print(f"Font: {path}")
    print(f"Total codepoints: {len(cmap)}")
    print(f"Highest codepoint: {hex(max(cmap))}")
    mdi_present = any(0xF0000 <= cp <= 0xF1AFF for cp in cmap)
    print(f"MDI segment (0xF0000+) present: {mdi_present}")
    print("-" * 40)
    for name, cp in checks.items():
        print(("YES " if cp in cmap else "no  ") + name)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    extra = [int(a, 16) if a.lower().startswith("0x") else int(a) for a in sys.argv[2:]]
    main(sys.argv[1], extra)
