"""Shared helpers in core.constants.

``format_mmss`` was hoisted out of three copy-pasted ``_fmt_time`` definitions (transport bar,
audio-output node, chord-chart node). The padded/un-padded split is a real behaviour difference
(the chord-chart scrolling label intentionally drops the leading zero on minutes), so both branches
are locked here.
"""

from core.constants import format_mmss


def test_format_mmss_pads_minutes_by_default():
    assert format_mmss(0) == "00:00"
    assert format_mmss(7) == "00:07"
    assert format_mmss(65) == "01:05"
    assert format_mmss(600) == "10:00"


def test_format_mmss_unpadded_minutes():
    # The chord-chart timeline label uses this form: no leading zero on the minutes field.
    assert format_mmss(0, pad_minutes=False) == "0:00"
    assert format_mmss(65, pad_minutes=False) == "1:05"
    assert format_mmss(600, pad_minutes=False) == "10:00"


def test_format_mmss_truncates_fractional_seconds():
    # divmod(int(seconds), 60): sub-second parts are dropped, not rounded.
    assert format_mmss(59.9) == "00:59"


def test_format_mmss_clamps_negative_to_zero():
    # A duration is never negative; a transient sub-zero position must not render as "-1:57".
    assert format_mmss(-3) == "00:00"
    assert format_mmss(-3, pad_minutes=False) == "0:00"
