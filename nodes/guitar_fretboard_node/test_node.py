"""Guitar Fretboard node: fret-position maths, natural harmonics, and the 3-layer Auto Fingering
engine (window pick, per-pitch emphasis that never hides a note, one-per-string hand shape)."""

from nodes.guitar_fretboard_node.processor import (
    FretboardParameters,
    GuitarFretboardProcessor,
)


def _parameters(*, prefer_position=0.0, allow_open=False):
    return FretboardParameters(22, prefer_position, allow_open)


# ===== Position math + natural harmonics =====

def test_fretboard_press_positions():
    processor = GuitarFretboardProcessor()
    # B3 (MIDI 59): fretted on the low-E string (open 40) at fret 19, and open on the B string.
    pos = processor.press_positions(59, _parameters(), allow_open=True)
    assert (5, 19) in pos and (1, 0) in pos


def test_fretboard_harmonic_touch_frets():
    processor = GuitarFretboardProcessor()
    # B3 (59) is the 3rd harmonic of low-E (59-40=19) -> touch the 7th fret on the low-E string.
    assert (5, 7) in processor.harmonic_positions(59, _parameters())
    # E4 (64) is the 4th harmonic of low-E (64-40=24) -> touch the 5th fret.
    assert (5, 5) in processor.harmonic_positions(64, _parameters())


# ===== Auto Fingering: 3-layer assignment (assist, never hides a pitch) =====

# The real chord from the reported case: A2 F3 A3 E4 A4 E5.
_CHORD = {45: 100, 53: 100, 57: 100, 64: 100, 69: 100, 76: 100}


def test_layers_show_every_unique_pitch_in_L2():
    layers = GuitarFretboardProcessor().assign_layers(
        _CHORD, _parameters(prefer_position=9.0))
    # Every sounding pitch gets exactly one emphasised (L2) position -- nothing is dropped.
    assert sorted(p for p, _, _ in layers.emphasized) == sorted(_CHORD)
    assert len({p for p, _, _ in layers.emphasized}) == len(_CHORD)


def test_layers_prefer_position_sets_window():
    layers = GuitarFretboardProcessor().assign_layers(
        _CHORD, _parameters(prefer_position=9.0))
    # Window low fret sits just below the preferred position (room for the index finger).
    assert layers.anchor == 8
    # A2 (only playable at low-E 5) can't reach the window, so it is NOT in the hand shape (L3)...
    l3_pitches = {p for p, _, _ in layers.hand_shape}
    assert 45 not in l3_pitches
    # ...but it is still emphasised in L2 (never hidden).
    assert 45 in {p for p, _, _ in layers.emphasized}


def test_layers_hand_shape_in_window_and_one_per_string():
    processor = GuitarFretboardProcessor()
    layers = processor.assign_layers(_CHORD, _parameters(prefer_position=9.0))
    strings = [i for _, i, _ in layers.hand_shape]
    assert len(strings) == len(set(strings))              # one note per string in the hand shape
    for _p, _i, fret in layers.hand_shape:
        assert layers.anchor <= fret <= layers.anchor + processor.HAND_SPAN


def test_layers_L1_covers_all_playable_positions():
    processor = GuitarFretboardProcessor()
    layers = processor.assign_layers({64: 100}, _parameters())
    # L1 backdrop = every fretted spot for E4 (incl. open high-E), regardless of allow_open.
    assert set((i, f) for _p, i, f in layers.all_positions) == set(
        processor.press_positions(64, _parameters(), allow_open=True))


def test_layers_window_follows_hand_when_no_preference():
    processor = GuitarFretboardProcessor()
    processor.hand_center = 12.0
    layers = processor.assign_layers(_CHORD, _parameters())
    assert layers.anchor == 12


def test_layers_cold_start_maximises_coverage():
    processor = GuitarFretboardProcessor()
    processor.hand_center = 0.0
    layers = processor.assign_layers(_CHORD, _parameters())
    assert layers.hand_shape


def test_allow_open_excludes_open_from_press():
    processor = GuitarFretboardProcessor()
    # E4 (64) = open high-E (index 0, fret 0). Excluded when opens are off, back when on.
    assert (0, 0) not in processor.press_positions(
        64, _parameters(), allow_open=False)
    assert (0, 0) in processor.press_positions(
        64, _parameters(), allow_open=True)


def test_allow_open_keeps_hand_shape_fretted():
    layers = GuitarFretboardProcessor().assign_layers(
        _CHORD, _parameters(prefer_position=5.0))
    assert all(fret > 0 for _, _, fret in layers.hand_shape)


# The classic open E major voicing (0 2 2 1 0 0): E2 B2 E3 G#3 B3 E4.
_E_OPEN = {40: 100, 47: 100, 52: 100, 56: 100, 59: 100, 64: 100}


def test_allow_open_on_lets_window_start_at_nut():
    processor = GuitarFretboardProcessor()
    processor.hand_center = 0.0
    layers = processor.assign_layers(_E_OPEN, _parameters(allow_open=True))
    assert layers.anchor == 0
    assert any(fret == 0 for _, _, fret in layers.hand_shape)


def test_allow_open_off_never_uses_open_in_shape():
    processor = GuitarFretboardProcessor()
    processor.hand_center = 0.0
    layers = processor.assign_layers(_E_OPEN, _parameters())
    assert layers.anchor >= 1
    assert all(fret > 0 for _, _, fret in layers.hand_shape)


def test_fretboard_param_signature_includes_fingering(make_node):
    fb = make_node("Guitar Fretboard")
    sig = fb.get_param_signature()
    fb.fingering_mode = 1.0
    assert fb.get_param_signature() != sig


def test_fretboard_param_signature_includes_allow_open(make_node):
    fb = make_node("Guitar Fretboard")
    sig = fb.get_param_signature()
    fb.allow_open = True
    assert fb.get_param_signature() != sig


def test_fretboard_pitch_filter_crossed_bounds_cover_everything(
        make_node, monkeypatch):
    import dearpygui.dearpygui as dpg

    fb = make_node("Guitar Fretboard")
    fb._string_y = [0.0, 10.0]
    fb._spacing = 10.0
    fb.pitch_min = 70.0
    fb.pitch_max = 50.0
    rendered = []
    monkeypatch.setattr(dpg, "delete_item", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        fb, "_draw_pitch_filter_overlay", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        fb._processor,
        "active_notes",
        lambda _notes, _time: {49: 90, 50: 91, 60: 92, 70: 93, 71: 94},
    )
    monkeypatch.setattr(
        fb,
        "_draw_all_positions",
        lambda _layer, active, _radius: rendered.append(active),
    )

    fb._draw_active([object()], 0.0)

    assert rendered == []


def test_fretboard_pitch_filter_dims_out_of_range_positions(make_node, monkeypatch):
    import dearpygui.dearpygui as dpg

    fb = make_node("Guitar Fretboard")
    fb._open_strings = (60,)
    fb._num_frets = 3
    fb._string_y = [10.0, 20.0]
    fb._fret_x = [0.0, 10.0, 20.0, 30.0]
    rectangles = []
    lines = []
    monkeypatch.setattr(
        dpg, "draw_rectangle",
        lambda point1, point2, **kwargs: rectangles.append((point1, point2, kwargs)),
    )
    monkeypatch.setattr(
        dpg, "draw_line",
        lambda point1, point2, **kwargs: lines.append((point1, point2, kwargs)),
    )

    fb._draw_pitch_filter_overlay("layer", 62, 62)

    assert [(point1, point2) for point1, point2, _kwargs in rectangles] == [
        ((0.0, 5.0), (10.0, 15.0)),
        ((20.0, 5.0), (30.0, 15.0)),
    ]
    assert all(kwargs["fill"][3] == 145 for _, _, kwargs in rectangles)
    assert [(point1, point2) for point1, point2, _kwargs in lines] == [
        ((10.0, 5.0), (10.0, 15.0)),
        ((20.0, 5.0), (20.0, 15.0)),
    ]
    assert lines[0][2]["color"] == fb.MIN_PITCH_LINE_COLOR
    assert lines[1][2]["color"] == fb.MAX_PITCH_LINE_COLOR


def test_fretboard_pitch_filter_crossed_bounds_dim_every_position(
        make_node, monkeypatch):
    import dearpygui.dearpygui as dpg

    fb = make_node("Guitar Fretboard")
    fb._open_strings = (60,)
    fb._num_frets = 3
    fb._string_y = [10.0, 20.0]
    fb._fret_x = [0.0, 10.0, 20.0, 30.0]
    rectangles = []
    monkeypatch.setattr(
        dpg, "draw_rectangle",
        lambda point1, point2, **kwargs: rectangles.append((point1, point2)),
    )
    monkeypatch.setattr(dpg, "draw_line", lambda *_args, **_kwargs: None)

    fb._draw_pitch_filter_overlay("layer", 63, 61)

    assert len(rectangles) == 3


def test_fretboard_pitch_filter_excludes_harmonics_in_dimmed_positions(
        make_node, monkeypatch):
    import dearpygui.dearpygui as dpg

    fb = make_node("Guitar Fretboard")
    fb._string_y = [float(index * 10) for index in range(6)]
    fb._fret_x = [float(index * 10) for index in range(23)]
    fb._spacing = 10.0
    fb.pitch_min = 50.0
    fb.pitch_max = 70.0
    polygons = []
    monkeypatch.setattr(
        dpg, "draw_polygon",
        lambda points, **kwargs: polygons.append((points, kwargs)),
    )

    # B3 sounds at MIDI 59, but its low-E natural-harmonic touch point is fret 7
    # (position pitch 47), which lies in the dimmed area and must not render.
    fb._draw_harmonic("layer", 5, 7, (255, 255, 255, 255), 5.0)
    fb._draw_harmonic("layer", 5, 12, (255, 255, 255, 255), 5.0)

    assert len(polygons) == 1


def test_every_fretboard_marker_respects_position_filter(make_node, monkeypatch):
    import dearpygui.dearpygui as dpg

    fb = make_node("Guitar Fretboard")
    fb._string_y = [float(index * 10) for index in range(6)]
    fb._fret_x = [float(index * 10) for index in range(23)]
    fb._spacing = 10.0
    fb.pitch_min = 50.0
    fb.pitch_max = 70.0
    calls = []
    monkeypatch.setattr(dpg, "draw_circle", lambda *_args, **_kwargs: calls.append("circle"))
    monkeypatch.setattr(dpg, "draw_text", lambda *_args, **_kwargs: calls.append("text"))
    monkeypatch.setattr(dpg, "draw_polygon", lambda *_args, **_kwargs: calls.append("polygon"))
    monkeypatch.setattr(dpg, "draw_polyline", lambda *_args, **_kwargs: calls.append("polyline"))

    fb._draw_press("layer", 5, 7, (255, 255, 255, 255), "B3", 5.0)
    fb._draw_ring("layer", 5, 7, (255, 255, 255, 255), 5.0)
    fb._draw_harmonic("layer", 5, 7, (255, 255, 255, 255), 5.0)
    fb._draw_hand_mark("layer", 5, 7, 5.0)

    assert calls == []
