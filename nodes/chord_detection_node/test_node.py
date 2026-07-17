"""Chord Detection node: Krumhansl-Schmuckler key estimate, and the beat-synchronous chord
engine (grid granularity, per-unit aggregation, segment post-processing, beat-required bake)."""

import numpy as np
import pytest

from core.base_node import DataType, DataPacket, NOTE_DTYPE
from nodes.chord_detection_node.processor import ChordDetectionProcessor


# ===== Key detection (Krumhansl-Schmuckler on the chromagram) =====

def test_detect_key_major():
    # A C-major-weighted chroma (diatonic notes present, tonic C strongest) -> C major.
    weights = np.array([1.0, 0.1, 0.7, 0.1, 0.9, 0.6, 0.1, 0.9, 0.1, 0.6, 0.1, 0.5])
    chroma = weights[:, None] * np.ones((12, 40))
    assert ChordDetectionProcessor.detect_key(chroma) == "C major"


def test_detect_key_minor():
    # Same diatonic set but centred on A -> A minor.
    weights = np.array([0.6, 0.1, 0.6, 0.1, 0.7, 0.5, 0.1, 0.6, 0.1, 1.0, 0.1, 0.5])
    chroma = weights[:, None] * np.ones((12, 40))
    assert ChordDetectionProcessor.detect_key(chroma) == "A minor"


def test_detect_key_silence():
    assert ChordDetectionProcessor.detect_key(np.zeros((12, 10))) == "--"


def test_chord_detection_publishes_key_as_text(make_node):
    cd = make_node("Chord Detection")
    cd._key_name = "G major"
    cd.is_baked = True
    cd.cached_outputs = {cd.out_midi: np.zeros(0, dtype=NOTE_DTYPE)}
    assert cd.publish_outputs() is True
    pkt = cd.output_values[cd.out_key]
    assert pkt.data == "G major" and pkt.data_type == DataType.TEXT


# ===== Beat-synchronous chord detection: grid, aggregation, post-processing =====

def test_beat_grid_one_beat_is_beats_plus_tail():
    beats = np.array([0.0, 1.0, 2.0, 3.0])
    grid = ChordDetectionProcessor.grid_from_beats(beats, 1.0, tail=3.5)
    # 1 beat = the beats themselves, closed with the tail.
    assert list(grid) == [0.0, 1.0, 2.0, 3.0, 3.5]


def test_beat_grid_coarser_groups_beats():
    beats = np.array([0.0, 1.0, 2.0, 3.0, 4.0])
    grid = ChordDetectionProcessor.grid_from_beats(beats, 2.0, tail=4.5)
    # 2 beat = every 2nd beat (0, 2, 4) + tail.
    assert list(grid) == [0.0, 2.0, 4.0, 4.5]


def test_beat_grid_finer_subdivides():
    beats = np.array([0.0, 1.0, 2.0])
    grid = ChordDetectionProcessor.grid_from_beats(beats, 0.5, tail=2.0)
    # 1/2 beat = each beat split in two: 0, .5, 1, 1.5, 2.
    assert list(grid) == pytest.approx([0.0, 0.5, 1.0, 1.5, 2.0])


def test_beat_grid_brackets_intro_and_tail():
    # First beat at 2.0 (a 2s intro) -> the grid must open at head=0 so the intro gets a unit,
    # and close at the tail past the last beat.
    beats = np.array([2.0, 3.0, 4.0])
    grid = ChordDetectionProcessor.grid_from_beats(
        beats, 1.0, tail=5.0, head=0.0)
    assert list(grid) == [0.0, 2.0, 3.0, 4.0, 5.0]


def test_beat_grid_factor_in_signature(make_node):
    cd = make_node("Chord Detection")
    sig1 = cd.get_param_signature()
    cd.beat_div = "1/4 beat"
    assert cd.get_param_signature() != sig1     # granularity must change the cache identity


def test_aggregate_units_medians_frames_into_units():
    # 4 frames at t=0,1,2,3; two units [0,2) and [2,4). Frame chroma differs per unit.
    chroma = np.zeros((12, 4))
    chroma[0, 0:2] = 1.0        # unit 0: pitch class C
    chroma[7, 2:4] = 1.0        # unit 1: pitch class G
    rms = np.ones(4)
    ftimes = np.array([0.0, 1.0, 2.0, 3.0])
    bounds = np.array([0.0, 2.0, 4.0])
    seg_chroma, seg_rms = ChordDetectionProcessor.aggregate_units(
        chroma, rms, ftimes, bounds)
    assert seg_chroma.shape == (12, 2)
    assert seg_chroma[0, 0] == 1.0 and seg_chroma[7, 1] == 1.0


def test_aggregate_empty_unit_falls_back_to_nearest_frame():
    # Two frames, but three units -> the middle unit captures no frame centre.
    chroma = np.tile(np.arange(12.0)[:, None], (1, 2))
    rms = np.ones(2)
    ftimes = np.array([0.0, 3.0])
    bounds = np.array([0.0, 1.0, 2.0, 3.0])     # unit [1,2) has no frame
    seg_chroma, seg_rms = ChordDetectionProcessor.aggregate_units(
        chroma, rms, ftimes, bounds)
    assert seg_chroma.shape[1] == 3             # no crash, one column per unit


def test_postprocess_merges_adjacent_equal():
    labels = np.array([0, 0, 7, 7, 7])          # C, C, G, G, G
    starts = np.array([0.0, 1.0, 2.0, 3.0, 4.0])
    ends = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    segs = ChordDetectionProcessor.postprocess_units(labels, starts, ends)
    assert [s[2] for s in segs] == [0, 7]
    assert segs[0][:2] == [0.0, 2.0] and segs[1][:2] == [2.0, 5.0]


def test_postprocess_bridges_short_dropout():
    # C, <short gap>, C  -> the -1 dropout gets filled with C and the whole thing merges.
    labels = np.array([0, -1, 0])
    starts = np.array([0.0, 1.0, 1.3])
    ends = np.array([1.0, 1.3, 3.0])            # gap 0.3s < MAX_BRIDGE_GAP
    segs = ChordDetectionProcessor.postprocess_units(labels, starts, ends)
    assert len(segs) == 1 and segs[0][2] == 0
    assert segs[0][:2] == [0.0, 3.0]


def test_postprocess_keeps_long_gap_and_differing_flanks():
    # Long gap between DIFFERENT chords must stay a hole (no bridging).
    labels = np.array([0, -1, 7])
    starts = np.array([0.0, 1.0, 4.0])
    ends = np.array([1.0, 4.0, 6.0])            # gap 3s > MAX_BRIDGE_GAP, and flanks differ
    segs = ChordDetectionProcessor.postprocess_units(labels, starts, ends)
    assert [s[2] for s in segs] == [0, -1, 7]


def test_beat_times_from_notes_needs_two():
    assert ChordDetectionProcessor.beat_times_from_notes(None) is None
    one = np.zeros(1, dtype=NOTE_DTYPE); one["start"][0] = 0.5
    assert ChordDetectionProcessor.beat_times_from_notes(one) is None
    two = np.zeros(2, dtype=NOTE_DTYPE); two["start"] = [1.0, 0.5]   # unsorted on purpose
    got = ChordDetectionProcessor.beat_times_from_notes(two)
    assert list(got) == [0.5, 1.0]


def test_bake_fails_without_beats(make_node):
    cd = make_node("Chord Detection")
    audio = (np.random.default_rng(4).standard_normal((2, 4096)) * 0.2).astype(np.float32)
    cd.input_values[cd.in_audio] = DataPacket(audio, DataType.AUDIO)
    # No beat packet -> bake must refuse (beats are a required input, no fallback).
    assert cd.bake() is False
