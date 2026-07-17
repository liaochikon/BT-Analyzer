"""Graft an icon font's glyphs (Symbols Nerd Font) into the base CJK font so a single .ttf
carries both Chinese text glyphs AND the full Nerd Font icon set (incl. Material Design Icons
at 0xF0000+).

DearPyGui binds a single font and does not expose ImGui's MergeMode, so the one bound font must
contain everything. This produces that combined font offline.

Why grafting instead of fontTools.merge: Merger strictly compares every shared table's fields
and aborts on the many small differences between a CJK font and an icon font (unitsPerEm, OS/2
sub-fields, vertical-layout tables, ...). Both fonts here are TrueType (glyf-based), so we
instead copy just the missing icon glyphs directly into the base's glyf/hmtx/cmap tables. The
base font is otherwise untouched -- its Chinese + existing Font Awesome glyphs stay exactly as
they are; we only ADD the codepoints it lacks (the MDI range).

Usage (from the project root, all on one line):
    python tools/merge_icon_font.py fonts/sarasa-gothic-tc-regular-nerd-font.ttf fonts/SymbolsNerdFont-Regular.ttf fonts/sarasa-gothic-tc-nerd-merged.ttf
"""

import sys

from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._g_l_y_f import Glyph
from fontTools.ttLib.scaleUpem import scale_upem
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.pens.recordingPen import DecomposingRecordingPen


def graft(base_path: str, icon_path: str, out_path: str) -> None:
    base = TTFont(base_path, fontNumber=0)
    icon = TTFont(icon_path, fontNumber=0)

    base_upem = base["head"].unitsPerEm
    # Scale the icon font to the base's em size so grafted glyph coordinates are consistent.
    if icon["head"].unitsPerEm != base_upem:
        print(f"Scaling icon glyphs {icon['head'].unitsPerEm} -> {base_upem} em.")
        scale_upem(icon, base_upem)

    base_cmap = base.getBestCmap()
    icon_cmap = icon.getBestCmap()
    fill = sorted(set(icon_cmap) - set(base_cmap))
    if not fill:
        print("Icon font adds no new codepoints over the base; nothing to do.")
        return
    print(f"Base has {len(base_cmap)} codepoints; grafting {len(fill)} new icon glyphs.")
    print(f"  new range spans {hex(fill[0])}..{hex(fill[-1])}")

    base_glyf = base["glyf"]
    base_hmtx = base["hmtx"]
    icon_glyf = icon["glyf"]
    icon_hmtx = icon["hmtx"]
    icon_glyph_set = icon.getGlyphSet()

    # Add each icon glyph under a collision-free name, decomposing composites so we don't have to
    # also import their component glyphs. Then point new cmap entries at them.
    added = {}  # codepoint -> new glyph name
    for cp in fill:
        src_name = icon_cmap[cp]
        new_name = f"icon_{cp:05X}"
        if new_name in base_glyf.glyphs:
            continue
        # Re-draw the source glyph (flattening any composite) into a fresh simple glyph.
        pen = TTGlyphPen(None)
        rec = DecomposingRecordingPen(icon_glyph_set)
        icon_glyph_set[src_name].draw(rec)
        rec.replay(pen)
        base_glyf[new_name] = pen.glyph()
        base_hmtx[new_name] = icon_hmtx[src_name]
        added[cp] = new_name

    # Register the new glyphs in the font's glyph order + maxp count.
    for name in added.values():
        if name not in base.getGlyphOrder():
            base.glyphOrder.append(name)
    base["maxp"].numGlyphs = len(base.getGlyphOrder())

    # Add cmap entries. A format-4 subtable is 16-bit and cannot hold codepoints > 0xFFFF (the
    # MDI range), so only put those into format-12 subtables; BMP codepoints go into all Unicode
    # subtables. If no format-12 subtable exists, the >0xFFFF glyphs can't be addressed -- warn.
    has_fmt12 = any(getattr(sub, "format", None) == 12 and sub.isUnicode()
                    for sub in base["cmap"].tables)
    if not has_fmt12 and any(cp > 0xFFFF for cp in added):
        print("WARNING: base font has no format-12 cmap subtable; >0xFFFF icons won't map.")
    for sub in base["cmap"].tables:
        if not sub.isUnicode():
            continue
        fmt = getattr(sub, "format", None)
        for cp, name in added.items():
            if cp > 0xFFFF and fmt != 12:
                continue  # can't store supplementary-plane codepoints in a 16-bit subtable
            sub.cmap[cp] = name

    base.save(out_path)
    base.close()
    icon.close()

    out_cps = set(TTFont(out_path, fontNumber=0, lazy=True).getBestCmap().keys())
    print(f"Merged font written to {out_path}")
    print(f"  total codepoints: {len(out_cps)}  (added {len(added)})")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)
    graft(sys.argv[1], sys.argv[2], sys.argv[3])
