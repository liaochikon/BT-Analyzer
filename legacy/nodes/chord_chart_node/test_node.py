"""Chord Chart node: segment merge from chord MIDI, and per-root block colouring."""

import numpy as np
import pytest

from core.base_node import NOTE_DTYPE
from nodes.chord_chart_node.processor import ChordChartProcessor


def _triad(root_pc, minor, start, end):
    base = 48 + root_pc
    ivs = (0, 3, 7) if minor else (0, 4, 7)
    return [(base + iv, start, end, 90) for iv in ivs]


def _notes(rows):
    arr = np.zeros(len(rows), dtype=NOTE_DTYPE)
    for i, (p, s, e, v) in enumerate(rows):
        arr["pitch"][i] = p; arr["start"][i] = s; arr["end"][i] = e; arr["velocity"][i] = v
    return arr


def test_chord_chart_merges_adjacent_same_chord():
    # Three contiguous C slices then a G, then a C after a real gap -> C(0-2), G(2-3), C(5-6).
    rows = (_triad(0, False, 0.0, 0.5) + _triad(0, False, 0.5, 1.0) + _triad(0, False, 1.0, 2.0)
            + _triad(7, False, 2.0, 3.0)
            + _triad(0, False, 5.0, 6.0))
    snapshot = ChordChartProcessor().build_snapshot(_notes(rows))
    assert [segment.name for segment in snapshot.segments] == ["C", "G", "C"]
    assert [segment.start for segment in snapshot.segments] == pytest.approx([0.0, 2.0, 5.0])
    assert [segment.end for segment in snapshot.segments] == pytest.approx([2.0, 3.0, 6.0])
    assert snapshot.song_length == pytest.approx(6.0)


def test_chord_chart_empty_notes():
    snapshot = ChordChartProcessor().build_snapshot(None)
    assert snapshot.segments == () and snapshot.song_length == 0.0


def test_chord_chart_block_colors_by_root(make_node):
    cc = make_node("Chord Chart")
    # A block is tinted by its chord ROOT from the chord chart's own (muted) palette.
    fill_c, _edge = cc._block_colors("C", is_current=True)
    assert fill_c[:3] == cc.ROOT_COLORS[0]
    fill_g, _ = cc._block_colors("G", is_current=True)
    assert fill_g[:3] == cc.ROOT_COLORS[7]
    # Different roots -> different colours; current is not the dimmed past wash.
    cur, _ = cc._block_colors("C", is_current=True)
    past, _ = cc._block_colors("C", is_current=False)
    assert cur[:3] != past[:3]
    # Unnamed segment falls back to a neutral grey, not a crash.
    fill_none, _ = cc._block_colors("--", is_current=False)
    assert len(fill_none) == 4


def test_chord_chart_palette_is_independent_of_fretboard(make_node):
    # The two views must NOT share a palette object (decoupled: each owns its own).
    cc = make_node("Chord Chart")
    fb = make_node("Guitar Fretboard")
    assert cc.ROOT_COLORS is not fb.PITCH_COLORS
    assert cc.ROOT_COLORS != fb.PITCH_COLORS          # chord chart is the muted variant
