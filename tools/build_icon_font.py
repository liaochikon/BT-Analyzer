"""Build fonts/bt-icons.ttf from assets/icons/*.png for ImGui glyph icons."""
from __future__ import annotations
from pathlib import Path
from PIL import Image
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

ROOT = Path(__file__).resolve().parents[1]
ICON_DIR = ROOT / "assets" / "icons"
OUT = ROOT / "fonts" / "bt-icons.ttf"
ICONS = [
    "play","pause","stop","loop","clear","speaker","speaker_off","solo","reset","gear",
    "metro","pencil","trash","import","cancel","monitor","guitar","bass","positions","suggest",
    "grid","detect","again","rerun","magnet","undo","redo","tab_up","count",
]
UPM, BOX, BASELINE, THRESHOLD = 1000, 800, 100, 48

def png_to_glyph(path: Path):
    img = Image.open(path).convert("RGBA")
    w, h = img.size
    px = img.load()
    scale = BOX / float(max(w, h))
    ox = (BOX - w * scale) * 0.5
    oy = BASELINE + (BOX - h * scale) * 0.5
    pen = TTGlyphPen(None)
    for row in range(h):
        col = 0
        while col < w:
            while col < w and px[col, row][3] < THRESHOLD:
                col += 1
            if col >= w:
                break
            start = col
            while col < w and px[col, row][3] >= THRESHOLD:
                col += 1
            y0 = oy + (h - 1 - row) * scale
            y1 = oy + (h - row) * scale
            x0 = ox + start * scale
            x1 = ox + col * scale
            pen.moveTo((x0, y0)); pen.lineTo((x1, y0)); pen.lineTo((x1, y1)); pen.lineTo((x0, y1)); pen.closePath()
    return pen.glyph()

def main():
    glyphs = {".notdef": TTGlyphPen(None).glyph()}
    cmap = {}
    metrics = {".notdef": (BOX, 0)}
    names = [n.replace("-", "_") for n in ICONS]
    for index, name in enumerate(ICONS):
        path = ICON_DIR / f"{name}.png"
        if not path.is_file():
            raise SystemExit(f"missing {path}")
        g = names[index]
        glyphs[g] = png_to_glyph(path)
        cmap[0xE000 + index] = g
        metrics[g] = (BOX, 0)
    fb = FontBuilder(UPM, isTTF=True)
    fb.setupGlyphOrder([".notdef", *names])
    fb.setupCharacterMap(cmap)
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=BASELINE + BOX, descent=-BASELINE)
    fb.setupOS2(sTypoAscender=BASELINE + BOX, sTypoDescender=-BASELINE, usWinAscent=BASELINE + BOX, usWinDescent=BASELINE)
    fb.setupPost()
    fb.setupNameTable({"familyName":"BT Icons","styleName":"Regular","uniqueFontIdentifier":"BT Icons Regular","fullName":"BT Icons Regular","psName":"BTIcons-Regular","version":"Version 0.1"})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fb.save(OUT)
    print(f"wrote {OUT}")

if __name__ == "__main__":
    main()
