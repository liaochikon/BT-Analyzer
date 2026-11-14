"""Render monochrome (white+alpha) UI icons into assets/icons/atlas.png + atlas.json."""

from __future__ import annotations

import json
import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "assets" / "icons"
CELL = 64
RENDER = 256  # 4x CELL, then LANCZOS downscale
PAD = 0.14  # inset within the cell
STROKE = 18  # at RENDER scale (~2.8px @64 after downscale)
COLS = 8

# Order matches cpp/apps/bt_app/ui.hpp Icon enum (without Roll/Fall).
ICONS = [
    "play",
    "pause",
    "stop",
    "loop",
    "clear",
    "speaker",
    "speaker_off",
    "solo",
    "reset",
    "gear",
    "metro",
    "pencil",
    "trash",
    "import",
    "cancel",
    "monitor",
    "guitar",
    "bass",
    "positions",
    "suggest",
    "grid",
    "detect",
    "again",
    "rerun",
    "magnet",
    "count",
]


def box(size: int = RENDER) -> tuple[float, float, float, float]:
    m = size * PAD
    return m, m, size - m, size - m


def mid(b: tuple[float, float, float, float]) -> tuple[float, float]:
    return (b[0] + b[2]) * 0.5, (b[1] + b[3]) * 0.5


def wh(b: tuple[float, float, float, float]) -> tuple[float, float]:
    return b[2] - b[0], b[3] - b[1]


def white() -> tuple[int, int, int, int]:
    return (255, 255, 255, 255)


def new_glyph() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGBA", (RENDER, RENDER), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def stroke() -> float:
    return float(STROKE)


def draw_play(d: ImageDraw.ImageDraw) -> None:
    b = box()
    cx, cy = mid(b)
    w, h = wh(b)
    s = min(w, h) * 0.5
    d.polygon(
        [
            (cx - s * 0.55, cy - s),
            (cx - s * 0.55, cy + s),
            (cx + s * 0.95, cy),
        ],
        fill=white(),
    )


def draw_pause(d: ImageDraw.ImageDraw) -> None:
    b = box()
    cx, cy = mid(b)
    w, h = wh(b)
    s = min(w, h) * 0.5
    bw = s * 0.32
    gap = s * 0.22
    d.rounded_rectangle((cx - gap - bw, cy - s, cx - gap, cy + s), radius=bw * 0.35, fill=white())
    d.rounded_rectangle((cx + gap, cy - s, cx + gap + bw, cy + s), radius=bw * 0.35, fill=white())


def draw_stop(d: ImageDraw.ImageDraw) -> None:
    b = box()
    cx, cy = mid(b)
    s = min(wh(b)) * 0.38
    d.rounded_rectangle((cx - s, cy - s, cx + s, cy + s), radius=s * 0.18, fill=white())


def _arc_arrow(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float, start_deg: float, end_deg: float) -> None:
    """~3/4 ring + large chevron — reads as loop at 42px, not a letter O."""
    sw = max(int(stroke() * 0.95), int(r * 0.22))
    half = sw * 0.5
    span = (end_deg - start_deg) % 360.0
    if span < 1.0:
        span = 359.0
    tip_keep = 6.0
    body = max(span - tip_keep, span * 0.92)
    steps = max(40, int(body / 1.5))
    pts: list[tuple[float, float]] = []
    for i in range(steps + 1):
        a = math.radians(start_deg + body * (i / steps))
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    d.line(pts, fill=white(), width=sw, joint="curve")
    x0, y0 = pts[0]
    d.ellipse((x0 - half, y0 - half, x0 + half, y0 + half), fill=white())
    ex, ey = pts[-1]
    px, py = pts[max(0, len(pts) - 4)]
    tx, ty = ex - px, ey - py
    length = math.hypot(tx, ty) or 1.0
    tx, ty = tx / length, ty / length
    nx, ny = -ty, tx
    # Clear triangular head; keep tip short enough to leave a readable gap.
    tip = (ex + tx * half * 3.2, ey + ty * half * 3.2)
    left = (ex - tx * half * 0.9 + nx * half * 2.6, ey - ty * half * 0.9 + ny * half * 2.6)
    right = (ex - tx * half * 0.9 - nx * half * 2.6, ey - ty * half * 0.9 - ny * half * 2.6)
    d.polygon([tip, left, right], fill=white())
    d.ellipse((ex - half, ey - half, ex + half, ey + half), fill=white())


def draw_loop(d: ImageDraw.ImageDraw) -> None:
    """Square repeat: chamfered rectangle path + arrow on the top edge."""
    b = box()
    cx, cy = mid(b)
    w = min(wh(b)) * 0.92
    h = min(wh(b)) * 0.70
    left, right = cx - w * 0.5, cx + w * 0.5
    top, bottom = cy - h * 0.5, cy + h * 0.5
    ch = min(w, h) * 0.22  # corner chamfer
    sw = max(int(stroke() * 1.05), int(min(w, h) * 0.14))
    half = sw * 0.5
    gap = w * 0.20

    # Clockwise from left of top gap → left → bottom → right → top → arrow.
    pts = [
        (cx - gap * 0.2, top),
        (left + ch, top),
        (left, top + ch),
        (left, bottom - ch),
        (left + ch, bottom),
        (right - ch, bottom),
        (right, bottom - ch),
        (right, top + ch),
        (right - ch, top),
        (cx + gap * 0.65, top),
    ]
    d.line(pts, fill=white(), width=sw, joint="curve")
    x0, y0 = pts[0]
    d.ellipse((x0 - half, y0 - half, x0 + half, y0 + half), fill=white())
    # Arrow pointing left along the top edge into the gap.
    ex, ey = pts[-1]
    tip = (ex - sw * 2.6, ey)
    wing_a = (ex + sw * 0.4, ey - sw * 1.4)
    wing_b = (ex + sw * 0.4, ey + sw * 1.4)
    d.polygon([tip, wing_a, wing_b], fill=white())
    d.ellipse((ex - half, ey - half, ex + half, ey + half), fill=white())


def draw_clear(d: ImageDraw.ImageDraw) -> None:
    b = box()
    cx, cy = mid(b)
    s = min(wh(b)) * 0.42
    sw = stroke()
    d.line((cx - s, cy - s, cx + s, cy + s), fill=white(), width=int(sw))
    d.line((cx + s, cy - s, cx - s, cy + s), fill=white(), width=int(sw))


def _speaker(d: ImageDraw.ImageDraw, muted: bool = False) -> None:
    b = box()
    cx, cy = mid(b)
    s = min(wh(b)) * 0.5
    # body
    d.rounded_rectangle((cx - s * 0.95, cy - s * 0.28, cx - s * 0.35, cy + s * 0.28), radius=6, fill=white())
    d.polygon(
        [
            (cx - s * 0.35, cy - s * 0.28),
            (cx - s * 0.35, cy + s * 0.28),
            (cx + s * 0.25, cy + s * 0.85),
            (cx + s * 0.25, cy - s * 0.85),
        ],
        fill=white(),
    )
    sw = max(10, int(stroke() * 0.85))
    d.arc((cx - s * 0.05, cy - s * 0.55, cx + s * 0.75, cy + s * 0.55), start=-55, end=55, fill=white(), width=sw)
    d.arc((cx + s * 0.15, cy - s * 0.85, cx + s * 1.05, cy + s * 0.85), start=-55, end=55, fill=white(), width=sw)
    if muted:
        d.line((cx - s * 0.2, cy - s * 0.95, cx + s * 0.95, cy + s * 0.7), fill=white(), width=int(stroke()))


def draw_speaker(d: ImageDraw.ImageDraw) -> None:
    _speaker(d, False)


def draw_speaker_off(d: ImageDraw.ImageDraw) -> None:
    _speaker(d, True)


def draw_solo(d: ImageDraw.ImageDraw) -> None:
    b = box()
    cx, cy = mid(b)
    s = min(wh(b)) * 0.5
    sw = stroke()
    # headphones band
    d.arc((cx - s * 0.85, cy - s * 0.95, cx + s * 0.85, cy + s * 0.35), start=200, end=340, fill=white(), width=int(sw))
    # ear cups
    d.rounded_rectangle((cx - s * 0.95, cy - s * 0.15, cx - s * 0.45, cy + s * 0.75), radius=10, fill=white())
    d.rounded_rectangle((cx + s * 0.45, cy - s * 0.15, cx + s * 0.95, cy + s * 0.75), radius=10, fill=white())


def draw_reset(d: ImageDraw.ImageDraw) -> None:
    b = box()
    cx, cy = mid(b)
    r = min(wh(b)) * 0.28
    _arc_arrow(d, cx, cy, r, -150, 170)


def draw_gear(d: ImageDraw.ImageDraw) -> None:
    b = box()
    cx, cy = mid(b)
    s = min(wh(b)) * 0.5
    sw = int(stroke())
    d.ellipse((cx - s * 0.32, cy - s * 0.32, cx + s * 0.32, cy + s * 0.32), outline=white(), width=sw)
    for i in range(8):
        a = i / 8 * math.tau
        x0 = cx + math.cos(a) * s * 0.42
        y0 = cy + math.sin(a) * s * 0.42
        x1 = cx + math.cos(a) * s * 0.92
        y1 = cy + math.sin(a) * s * 0.92
        d.line((x0, y0, x1, y1), fill=white(), width=sw)


def draw_metro(d: ImageDraw.ImageDraw) -> None:
    b = box()
    cx, cy = mid(b)
    s = min(wh(b)) * 0.5
    d.polygon(
        [
            (cx - s * 0.42, cy - s),
            (cx + s * 0.42, cy - s),
            (cx + s * 0.95, cy + s),
            (cx - s * 0.95, cy + s),
        ],
        fill=white(),
    )
    # pendulum as punched hole (draw dark then we can't — use overlay by cutting via separate mask)
    # Instead draw pendulum as thicker white line with ball; punch by drawing after downscale is hard.
    # Draw pendulum in a second pass: black line then white ball — but black would show.
    # Use a cutout: draw pendulum on alpha by compositing.
    # Simpler: draw a white circle ball and a thin "slot" look with a slightly inset darker isn't allowed.
    # Draw the arm as a gap using destination-out later in render_one.


def draw_metro_full(img: Image.Image) -> None:
    d = ImageDraw.Draw(img)
    draw_metro(d)
    b = box()
    cx, cy = mid(b)
    s = min(wh(b)) * 0.5
    # cut pendulum slot
    cut = Image.new("L", img.size, 0)
    cd = ImageDraw.Draw(cut)
    cd.line((cx, cy + s * 0.55, cx + s * 0.5, cy - s * 0.75), fill=255, width=int(stroke() * 1.1))
    cd.ellipse(
        (cx + s * 0.38, cy - s * 0.95, cx + s * 0.72, cy - s * 0.55),
        fill=255,
    )
    r, g, bl, a = img.split()
    a = ImageChops.subtract(a, cut)
    img.putalpha(a)
    d = ImageDraw.Draw(img)
    d.ellipse((cx + s * 0.4, cy - s * 0.92, cx + s * 0.7, cy - s * 0.58), fill=white())


def draw_pencil(d: ImageDraw.ImageDraw) -> None:
    """Line-art diagonal pencil: eraser, ferrule, barrel, tip — reads clearly at small size."""
    b = box()
    cx, cy = mid(b)
    s = min(wh(b)) * 0.5
    sw = max(8, int(stroke() * 0.9))
    ux, uy = 0.7071, -0.7071
    px, py = 0.7071, 0.7071
    half = s * 0.28

    def pt(along: float, across: float = 0.0) -> tuple[float, float]:
        return (cx + ux * along * s + px * across * half, cy + uy * along * s + py * across * half)

    # Outer silhouette outline
    outline = [
        pt(-0.95, 0),  # tip
        pt(-0.45, -1),
        pt(0.55, -1),
        pt(0.55, -1.15),
        pt(0.95, -1.15),  # eraser top
        pt(0.95, 1.15),
        pt(0.55, 1.15),
        pt(0.55, 1),
        pt(-0.45, 1),
    ]
    d.line(outline + [outline[0]], fill=white(), width=sw, joint="curve")
    # Ferrule (two bands near eraser)
    d.line([pt(0.55, -1), pt(0.55, 1)], fill=white(), width=sw)
    d.line([pt(0.72, -1.05), pt(0.72, 1.05)], fill=white(), width=max(6, sw - 2))
    # Tip shoulder
    d.line([pt(-0.45, -1), pt(-0.45, 1)], fill=white(), width=sw)


def draw_trash(d: ImageDraw.ImageDraw) -> None:
    b = box()
    cx, cy = mid(b)
    s = min(wh(b)) * 0.5
    sw = int(stroke())
    d.line((cx - s * 0.95, cy - s * 0.45, cx + s * 0.95, cy - s * 0.45), fill=white(), width=sw)
    d.line((cx - s * 0.35, cy - s * 0.45, cx - s * 0.35, cy - s * 0.85), fill=white(), width=sw)
    d.line((cx + s * 0.35, cy - s * 0.45, cx + s * 0.35, cy - s * 0.85), fill=white(), width=sw)
    d.line((cx - s * 0.35, cy - s * 0.85, cx + s * 0.35, cy - s * 0.85), fill=white(), width=sw)
    d.line((cx - s * 0.7, cy - s * 0.25, cx - s * 0.55, cy + s * 0.9), fill=white(), width=sw)
    d.line((cx + s * 0.7, cy - s * 0.25, cx + s * 0.55, cy + s * 0.9), fill=white(), width=sw)
    d.line((cx - s * 0.55, cy + s * 0.9, cx + s * 0.55, cy + s * 0.9), fill=white(), width=sw)
    d.line((cx, cy - s * 0.2, cx, cy + s * 0.7), fill=white(), width=max(8, sw - 4))


def draw_import(d: ImageDraw.ImageDraw) -> None:
    b = box()
    cx, cy = mid(b)
    s = min(wh(b)) * 0.42
    sw = max(int(stroke() * 1.15), int(s * 0.28))
    d.line((cx - s, cy, cx + s, cy), fill=white(), width=sw)
    d.line((cx, cy - s, cx, cy + s), fill=white(), width=sw)


def draw_cancel(d: ImageDraw.ImageDraw) -> None:
    b = box()
    cx, cy = mid(b)
    s = min(wh(b)) * 0.48
    sw = int(stroke())
    d.ellipse((cx - s, cy - s, cx + s, cy + s), outline=white(), width=sw)
    t = s * 0.42
    d.line((cx - t, cy - t, cx + t, cy + t), fill=white(), width=sw)
    d.line((cx + t, cy - t, cx - t, cy + t), fill=white(), width=sw)


def draw_monitor(d: ImageDraw.ImageDraw) -> None:
    b = box()
    cx, cy = mid(b)
    s = min(wh(b)) * 0.5
    sw = int(stroke())
    d.rounded_rectangle((cx - s, cy - s * 0.72, cx + s, cy + s * 0.55), radius=14, outline=white(), width=sw)
    # waveform
    pts = [
        (cx - s * 0.7, cy),
        (cx - s * 0.35, cy),
        (cx - s * 0.1, cy - s * 0.38),
        (cx + s * 0.2, cy + s * 0.38),
        (cx + s * 0.4, cy),
        (cx + s * 0.7, cy),
    ]
    d.line(pts, fill=white(), width=sw, joint="curve")


def draw_guitar(d: ImageDraw.ImageDraw) -> None:
    b = box()
    cx, cy = mid(b)
    s = min(wh(b)) * 0.5
    sw = int(stroke())
    d.ellipse((cx - s * 0.55, cy + s * 0.0, cx + s * 0.55, cy + s * 0.95), outline=white(), width=sw)
    d.ellipse((cx - s * 0.16, cy + s * 0.35, cx + s * 0.16, cy + s * 0.65), outline=white(), width=max(8, sw - 4))
    d.line((cx, cy + s * 0.05, cx, cy - s * 0.85), fill=white(), width=sw)
    d.line((cx - s * 0.28, cy - s * 0.85, cx + s * 0.28, cy - s * 0.85), fill=white(), width=sw)


def draw_bass(d: ImageDraw.ImageDraw) -> None:
    b = box()
    cx, cy = mid(b)
    s = min(wh(b)) * 0.5
    sw = int(stroke())
    d.ellipse((cx - s * 0.42, cy + s * 0.2, cx + s * 0.42, cy + s * 0.95), outline=white(), width=sw)
    d.line((cx, cy + s * 0.25, cx, cy - s * 0.65), fill=white(), width=int(sw * 1.25))
    d.line((cx - s * 0.4, cy - s * 0.78, cx + s * 0.4, cy - s * 0.78), fill=white(), width=sw)
    d.ellipse((cx - s * 0.32, cy - s * 0.9, cx - s * 0.12, cy - s * 0.7), fill=white())
    d.ellipse((cx + s * 0.12, cy - s * 0.9, cx + s * 0.32, cy - s * 0.7), fill=white())


def draw_positions(d: ImageDraw.ImageDraw) -> None:
    b = box()
    cx, cy = mid(b)
    s = min(wh(b)) * 0.5
    cell = s * 0.38
    gap = s * 0.22
    for row in range(2):
        for col in range(3):
            x = cx - (cell * 1.5 + gap) + col * (cell + gap)
            y = cy - (cell + gap * 0.5) + row * (cell + gap)
            d.rounded_rectangle((x, y, x + cell, y + cell), radius=6, fill=white())


def draw_suggest(d: ImageDraw.ImageDraw) -> None:
    b = box()
    cx, cy = mid(b)
    s = min(wh(b)) * 0.5
    sw = int(max(10, stroke() * 0.85))
    bw = s * 0.32
    gap = s * 0.12
    for i in range(4):
        x = cx - s + i * (bw + gap)
        box_r = (x, cy - s * 0.28, x + bw, cy + s * 0.28)
        if i == 1:
            d.rounded_rectangle(box_r, radius=6, fill=white())
        else:
            d.rounded_rectangle(box_r, radius=6, outline=white(), width=sw)


def draw_grid(d: ImageDraw.ImageDraw) -> None:
    b = box()
    cx, cy = mid(b)
    s = min(wh(b)) * 0.5
    sw = int(stroke())
    base = cy + s * 0.7
    d.line((cx - s, base, cx + s, base), fill=white(), width=sw)
    for i in range(4):
        x = cx - s * 0.75 + i * s * 0.5
        top = cy - s if i == 0 else cy - s * 0.2
        d.line((x, top, x, base), fill=white(), width=sw)


def draw_detect(d: ImageDraw.ImageDraw) -> None:
    b = box()
    cx, cy = mid(b)
    s = min(wh(b)) * 0.5
    sw = int(stroke())
    base = cy + s * 0.7
    d.line((cx - s, base, cx + s, base), fill=white(), width=sw)
    marks = (-0.85, -0.2, 0.2, 0.7)
    for i, m in enumerate(marks):
        x = cx + s * m
        top = cy - s if i == 0 else cy - s * 0.2
        d.line((x, top, x, base), fill=white(), width=sw)


def draw_again(d: ImageDraw.ImageDraw) -> None:
    b = box()
    cx, cy = mid(b)
    r = min(wh(b)) * 0.30
    _arc_arrow(d, cx, cy, r, 95, 360)


def draw_rerun(d: ImageDraw.ImageDraw) -> None:
    b = box()
    cx, cy = mid(b)
    r = min(wh(b)) * 0.4
    sw = int(stroke())
    d.arc((cx - r, cy - r, cx + r, cy + r), start=-130, end=120, fill=white(), width=sw)
    # play triangle in center
    s = r * 0.55
    d.polygon([(cx - s * 0.35, cy - s), (cx - s * 0.35, cy + s), (cx + s * 0.85, cy)], fill=white())


def draw_magnet(d: ImageDraw.ImageDraw) -> None:
    b = box()
    cx, cy = mid(b)
    s = min(wh(b)) * 0.5
    sw = int(stroke() * 1.35)
    # U shape
    d.arc((cx - s * 0.7, cy - s * 0.85, cx + s * 0.7, cy + s * 0.55), start=180, end=0, fill=white(), width=sw)
    d.line((cx - s * 0.7, cy - s * 0.05, cx - s * 0.7, cy + s * 0.85), fill=white(), width=sw)
    d.line((cx + s * 0.7, cy - s * 0.05, cx + s * 0.7, cy + s * 0.85), fill=white(), width=sw)
    # poles
    pw = sw * 1.2
    d.rectangle((cx - s * 0.7 - pw * 0.5, cy + s * 0.55, cx - s * 0.7 + pw * 0.5, cy + s * 0.95), fill=white())
    d.rectangle((cx + s * 0.7 - pw * 0.5, cy + s * 0.55, cx + s * 0.7 + pw * 0.5, cy + s * 0.95), fill=white())


def draw_count(d: ImageDraw.ImageDraw) -> None:
    b = box()
    cx, cy = mid(b)
    s = min(wh(b)) * 0.48
    sw = int(stroke())
    d.ellipse((cx - s, cy - s, cx + s, cy + s), outline=white(), width=sw)
    d.line((cx, cy, cx, cy - s * 0.55), fill=white(), width=sw)
    d.line((cx, cy, cx + s * 0.42, cy + s * 0.18), fill=white(), width=sw)
    d.ellipse((cx - s * 0.1, cy - s * 0.1, cx + s * 0.1, cy + s * 0.1), fill=white())


DRAWERS = {
    "play": draw_play,
    "pause": draw_pause,
    "stop": draw_stop,
    "loop": draw_loop,
    "clear": draw_clear,
    "speaker": draw_speaker,
    "speaker_off": draw_speaker_off,
    "solo": draw_solo,
    "reset": draw_reset,
    "gear": draw_gear,
    "pencil": draw_pencil,
    "trash": draw_trash,
    "import": draw_import,
    "cancel": draw_cancel,
    "monitor": draw_monitor,
    "guitar": draw_guitar,
    "bass": draw_bass,
    "positions": draw_positions,
    "suggest": draw_suggest,
    "grid": draw_grid,
    "detect": draw_detect,
    "again": draw_again,
    "rerun": draw_rerun,
    "magnet": draw_magnet,
    "count": draw_count,
}


def render_one(name: str) -> Image.Image:
    if name == "metro":
        img = Image.new("RGBA", (RENDER, RENDER), (0, 0, 0, 0))
        draw_metro_full(img)
    else:
        img, d = new_glyph()
        DRAWERS[name](d)
    # LANCZOS then lightly harden alpha (keep soft tips from vanishing at 42px).
    out = img.resize((CELL, CELL), Image.Resampling.LANCZOS)
    r, g, b, a = out.split()
    a = a.point(lambda p: 255 if p >= 48 else 0)
    out = Image.merge("RGBA", (r, g, b, a))
    return out


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    n = len(ICONS)
    rows = (n + COLS - 1) // COLS
    atlas_w = COLS * CELL
    atlas_h = rows * CELL
    atlas = Image.new("RGBA", (atlas_w, atlas_h), (0, 0, 0, 0))
    icons_meta: dict[str, dict[str, int]] = {}
    for index, name in enumerate(ICONS):
        glyph = render_one(name)
        col = index % COLS
        row = index // COLS
        x = col * CELL
        y = row * CELL
        atlas.paste(glyph, (x, y), glyph)
        icons_meta[name] = {"x": x, "y": y, "w": CELL, "h": CELL}
        glyph.save(OUT_DIR / f"{name}.png")

    atlas_path = OUT_DIR / "atlas.png"
    json_path = OUT_DIR / "atlas.json"
    atlas.save(atlas_path)
    payload = {
        "size": CELL,
        "cols": COLS,
        "rows": rows,
        "atlas_w": atlas_w,
        "atlas_h": atlas_h,
        "icons": icons_meta,
    }
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {atlas_path}")
    print(f"wrote {json_path}")
    print(f"{n} icons @ {CELL}px, atlas {atlas_w}x{atlas_h}")


if __name__ == "__main__":
    main()
