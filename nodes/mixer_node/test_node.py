"""Mixer node: dB<->linear fader maths, channel state serialize_extra, labels, mute/solo render."""

import numpy as np
import pytest


def test_mixer_db_lin_roundtrip(make_node):
    m = make_node("Mixer")
    for db in (-60.0, -24.0, -6.0, 0.0, 6.0):
        lin = m._db_to_lin(db)
        if db <= m.MIN_DB:
            assert lin == 0.0                       # bottom of the range = true mute
        else:
            assert m._lin_to_db(lin) == pytest.approx(db, abs=1e-6)


def test_mixer_unity_is_0db(make_node):
    m = make_node("Mixer")
    assert m._db_to_lin(0.0) == pytest.approx(1.0)
    assert m.default_gain == pytest.approx(1.0)      # default fader = 0 dB


def test_mixer_serialize_extra_roundtrip(make_node):
    m = make_node("Mixer")
    m.vols[0] = m._db_to_lin(-6.0)
    m.mutes[1] = True
    m.solos[2] = True
    m.labels[0] = "Vocals"
    extra = m.serialize_extra()
    m2 = make_node("Mixer")
    m2.apply_extra(extra)                            # touches guarded dpg (no-op without widgets)
    assert m2.vols[0] == pytest.approx(m.vols[0])
    assert m2.mutes[1] is True and m2.solos[2] is True
    assert m2.mutes[0] is False
    assert m2.labels[0] == "Vocals"
    assert m2.labels[1] == "Ch 2"                    # untouched channels keep the default name


def test_mixer_default_labels(make_node):
    m = make_node("Mixer")
    assert m.labels == [f"Ch {i + 1}" for i in range(m.ch_count)]


def test_mixer_label_truncated_and_blank_reverts(make_node):
    from ui import node_controls

    m = make_node("Mixer")

    class Commands:
        @staticmethod
        def set_node_state(
                node_tag, field, value=None, *, index=None,
                action=None, edit_phase=None):
            assert node_tag == m.node_tag
            return m.mutate_extra_state(
                field, value, index=index, action=action)

    node_controls.bind_graph_commands(Commands())
    try:
        m._on_label(sender="", app_data="X" * 50, user_data=0)      # too long -> capped
        assert m.labels[0] == "X" * m.LABEL_MAX_CHARS
        m._on_label(sender="", app_data="   ", user_data=1)          # blank -> default
        assert m.labels[1] == "Ch 2"
        m._on_label(sender="", app_data="  Guitar  ", user_data=2)   # trimmed
        assert m.labels[2] == "Guitar"
    finally:
        node_controls.bind_graph_commands(None)


def test_mixer_apply_extra_without_labels_is_safe(make_node):
    # A preset saved before channel names existed has no "labels" key -> defaults must survive.
    m = make_node("Mixer")
    m.apply_extra({"vols": [1.0] * m.ch_count, "mutes": [False] * m.ch_count,
                   "solos": [False] * m.ch_count})
    assert m.labels[0] == "Ch 1"


def test_mixer_render_respects_mute_and_solo(make_node):
    m = make_node("Mixer")
    # Two live channels; solo ch0 -> only ch0 audible.
    def src_ch(val):
        return lambda start, frames: np.full((frames, 2), val, dtype=np.float32)
    renderers = {m.in_chs[0]: src_ch(0.5), m.in_chs[1]: src_ch(0.5)}
    m.solos[0] = True
    render = m.build_stream_renderer(m.out_master, renderers)
    out = render(0, 64)
    assert float(out.max()) == pytest.approx(0.5, abs=1e-3)   # only ch0 (unity) passed
