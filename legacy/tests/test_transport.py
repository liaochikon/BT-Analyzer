"""Transport command / loop / rate / snap math -- the module most likely to break silently.

Drives the pure state machine directly via the _apply_* handlers (what the producer thread calls
after draining the command queue), so no audio device or engine is involved.
"""

from types import SimpleNamespace

import pytest

from core.bus import BusMessageResult
from core.transport import Transport, PlaybackState
from core.transport_contracts import TransportEndpoint
from core.transport_contracts import (
    ConfigureMetronome,
    Seek,
    SetBeatTrim,
    SetLoopPoint,
    SetMasterGain,
    SetMetronomeGain,
    SetRate,
    SetSnapEnabled,
    StartCountIn,
)

SR = 44100


@pytest.fixture
def t():
    tr = Transport(SR)
    tr._apply_length(SR * 10)   # 10 s source
    return tr


# ----- clamp / length -----

def test_length_sets_output_length_at_rate_1(t):
    assert t.length == SR * 10
    assert t.length_seconds == pytest.approx(10.0)
    assert t.source_length_seconds == pytest.approx(10.0)


def test_seek_clamps_into_range(t):
    t._apply_seek(SR * 999)
    assert t.current_index == t.length          # clamped to end
    t._apply_seek(-5000)
    assert t.current_index == 0                 # clamped to start


def test_seek_zero_length_stays_zero():
    tr = Transport(SR)                           # nothing loaded
    tr._apply_seek(1234)
    assert tr.current_index == 0


def test_command_queue_preserves_fifo_order():
    tr = Transport(SR)
    tr.request_seek(100)
    tr.request_rate(0.5)
    tr.set_loop_a(0.25)

    assert tr.drain_commands() == [
        Seek(100),
        SetRate(0.5),
        SetLoopPoint("A", 0.25),
    ]


def test_bound_transport_publishes_typed_commands_to_bus_fifo():
    from core.bus import Bus

    bus = Bus()
    mailbox = bus.register(TransportEndpoint.SERVICE, maxsize=2)
    tr = Transport(SR)
    tr.bind_control_bus(bus)

    assert tr.request_seek(100) is BusMessageResult.ENQUEUED
    assert tr.request_rate(0.5) is BusMessageResult.ENQUEUED

    assert mailbox.get_nowait().payload == Seek(100)
    assert mailbox.get_nowait().payload == SetRate(0.5)


def test_bound_transport_overflow_preserves_accepted_command_order():
    from core.bus import Bus

    bus = Bus()
    mailbox = bus.register(TransportEndpoint.SERVICE, maxsize=3)
    tr = Transport(SR)
    tr.bind_control_bus(bus)

    results = [
        tr.request_seek(100),
        tr.request_rate(0.5),
        tr.request_seek(200),
        tr.request_rate(1.5),
    ]

    assert results == [
        BusMessageResult.ENQUEUED,
        BusMessageResult.ENQUEUED,
        BusMessageResult.ENQUEUED,
        BusMessageResult.MAILBOX_FULL,
    ]
    assert [mailbox.get_nowait().payload for _ in range(3)] == [
        Seek(100),
        SetRate(0.5),
        Seek(200),
    ]


def test_transport_unbind_restores_local_fifo_without_publishing():
    from core.bus import Bus

    bus = Bus()
    mailbox = bus.register(TransportEndpoint.SERVICE, maxsize=2)
    tr = Transport(SR)
    tr.bind_control_bus(bus)
    assert tr.request_seek(100) == BusMessageResult.ENQUEUED
    assert mailbox.get_nowait().payload == Seek(100)

    tr.bind_control_bus(None)
    bus.unregister(TransportEndpoint.SERVICE)

    assert tr.request_seek(200) == BusMessageResult.ENQUEUED
    assert tr.drain_commands() == [Seek(200)]


def test_metronome_frame_sync_is_change_gated(monkeypatch):
    from ui import transport_bar

    calls = []
    monkeypatch.setattr(transport_bar.ENGINE, "set_metronome", lambda *args: calls.append(args))
    monkeypatch.setattr(transport_bar, "_last_metronome_key", None)

    transport_bar._sync_engine_from_ui()
    transport_bar._sync_engine_from_ui()

    assert len(calls) == 1


def test_draw_waveform_reads_transport_snapshot(monkeypatch):
    from ui import transport_bar

    monkeypatch.setattr(transport_bar.dpg, "does_item_exist", lambda _tag: True)
    monkeypatch.setattr(transport_bar.dpg, "delete_item", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(transport_bar, "_track_height", lambda: 100)
    monkeypatch.setattr(
        transport_bar.TRANSPORT,
        "snapshot",
        lambda: SimpleNamespace(waveform=None),
    )

    transport_bar._draw_waveform()


def test_transport_command_contracts_are_typed_and_immutable():
    assert Seek(100).sample_index == 100
    assert SetRate(0.5).rate == 0.5
    assert SetLoopPoint("A", 0.25).fraction == 0.25
    with pytest.raises(ValueError, match="point must be"):
        SetLoopPoint("C", 0.5)
    with pytest.raises((AttributeError, TypeError)):
        Seek(100).sample_index = 200


def test_snapshot_changes_only_when_owner_publishes(t):
    initial = t.snapshot()

    t._apply_seek(SR * 2)

    assert t.snapshot() is initial
    t.publish_snapshot()
    snapshot = t.snapshot()
    assert snapshot.current_index == SR * 2
    assert snapshot.length == SR * 10
    assert snapshot.rate == 1.0


def test_snapshot_contains_coherent_transport_bar_read_model(t):
    t._apply_seek(SR * 2)
    t._apply_loop_range(0.2, 0.5)
    t._apply_loop_enable(True)
    t.set_beat_grid(128.0, 0.25)
    t._apply_beat_trim(0.5, 0.05)
    t._apply_snap_enabled(True)
    t.publish_snapshot()

    snapshot = t.snapshot()
    assert snapshot.position_seconds == pytest.approx(2.0)
    assert snapshot.source_length_seconds == pytest.approx(10.0)
    assert snapshot.loop_active
    assert snapshot.bpm == pytest.approx(128.5)
    assert snapshot.beat_offset == pytest.approx(0.3)
    assert snapshot.snap_enabled


def test_engine_applies_scalar_controls_on_producer_boundary():
    from core.playback_engine import ENGINE
    from core.transport import TRANSPORT

    ENGINE._apply_cmd(SetBeatTrim(0.5, 0.05))
    ENGINE._apply_cmd(SetSnapEnabled(True))
    ENGINE._apply_cmd(SetMasterGain(0.25))
    ENGINE._apply_cmd(SetMetronomeGain(0.5))
    ENGINE._apply_cmd(ConfigureMetronome(True, 128.0, 0.2))

    snapshot = TRANSPORT.snapshot()
    assert snapshot.bpm_trim == pytest.approx(0.5)
    assert snapshot.offset_trim == pytest.approx(0.05)
    assert snapshot.snap_enabled
    assert ENGINE._master_gain == pytest.approx(0.25)
    assert ENGINE._metro_gain == pytest.approx(0.5)
    assert ENGINE._metronome_on

    ENGINE._apply_cmd(SetBeatTrim(0.0, 0.0))
    ENGINE._apply_cmd(SetSnapEnabled(False))
    ENGINE._apply_cmd(ConfigureMetronome(False, 120.0, 0.0))


def test_engine_applies_typed_command_and_publishes_snapshot():
    from core.playback_engine import ENGINE
    from core.transport import TRANSPORT

    TRANSPORT._apply_length(SR * 10)
    ENGINE._apply_cmd(Seek(SR * 3))

    assert TRANSPORT.snapshot().current_index == SR * 3


# ----- play / stop / consumed -----

def test_play_requires_loaded():
    tr = Transport(SR)                           # length 0
    tr._apply_play()
    assert tr.state == PlaybackState.STOPPED     # can't play with nothing loaded


def test_consumed_advances_only_when_playing(t):
    t._apply_seek(0)
    t._apply_consumed(1000)                      # not playing -> ignored
    assert t.current_index == 0
    t._apply_play()
    t._apply_consumed(1000)
    assert t.current_index == 1000


def test_consumed_stops_at_end(t):
    t._apply_play()
    t._apply_seek(t.length - 100)
    t._apply_consumed(500)                       # crosses the end
    assert t.state == PlaybackState.STOPPED


# ----- rate remap (source position preserved) -----

def test_rate_change_preserves_source_position(t):
    t._apply_play()
    t._apply_seek(SR * 3)                        # 3 s (source == output at 1.0x)
    src_before = t.current_index * t.rate
    t._apply_rate(0.5)                           # slow to half speed
    src_after = t.current_index * t.rate
    assert src_after == pytest.approx(src_before, abs=2)   # same source sample (±rounding)
    assert t.length == pytest.approx(SR * 20, abs=2)       # output length doubled
    assert t.source_length_seconds == pytest.approx(10.0)  # source length unchanged


def test_rate_clamped_positive(t):
    t._apply_rate(0.0)
    assert t.rate >= 0.05


# ----- A/B loop wrap (fractions of the OUTPUT timeline) -----

def test_loop_wrap_rewinds_to_a(t):
    t._apply_play()
    t._apply_loop_a(0.2)
    t._apply_loop_b(0.5)
    t._apply_loop_enable(True)
    assert t.loop_is_active()
    t._apply_seek(int(0.49 * t.length))
    wrapped = t._apply_consumed(int(0.05 * t.length))    # crosses B
    assert wrapped is True
    assert t.current_index == pytest.approx(int(0.2 * t.length), abs=200)
    assert t.state == PlaybackState.PLAYING              # loop beats the STOP branch


def test_loop_inactive_when_disabled_or_out_of_order(t):
    t._apply_loop_a(0.5)
    t._apply_loop_b(0.2)                                 # b < a -> rejected, b stays None
    assert t.loop_b is None
    t._apply_loop_a(0.2); t._apply_loop_b(0.5)
    assert not t.loop_is_active()                        # not enabled yet
    t._apply_loop_enable(True)
    assert t.loop_is_active()


def test_loop_range_orders_and_clamps(t):
    t._apply_loop_range(0.8, 0.3)                        # given out of order
    assert t.loop_a == pytest.approx(0.3)
    assert t.loop_b == pytest.approx(0.8)
    t._apply_loop_range(-1.0, 2.0)
    assert t.loop_a == 0.0 and t.loop_b == 1.0


def test_no_wrap_when_disabled(t):
    t._apply_play()
    t._apply_loop_a(0.2); t._apply_loop_b(0.5); t._apply_loop_enable(False)
    t._apply_seek(int(0.49 * t.length))
    assert t._apply_consumed(int(0.05 * t.length)) is False


# ----- beat grid: base + trim -----

def test_bpm_offset_are_base_plus_trim():
    tr = Transport(SR)
    tr.set_beat_grid(128.0, 0.30)      # base (from a node)
    tr.set_beat_trim(0.5, 0.02)        # manual nudge
    assert tr.bpm == pytest.approx(128.5)
    assert tr.beat_offset == pytest.approx(0.32)


def test_reset_beat_grid_keeps_trim():
    tr = Transport(SR)
    tr.set_beat_trim(1.0, 0.1)
    tr.set_beat_grid(140.0, 0.5)
    tr.reset_beat_grid()               # node removed -> base back to default
    assert tr.bpm == pytest.approx(121.0)      # 120 default + 1.0 trim
    assert tr.beat_offset == pytest.approx(0.1)
    assert tr.beat_grid_from_node is False


def test_bpm_clamped_positive():
    tr = Transport(SR)
    tr.set_beat_trim(-500.0, -10.0)    # absurd negative trim
    assert tr.bpm >= 1.0
    assert tr.beat_offset >= 0.0


# ----- snap-to-beat (rate-invariant, source timeline) -----

def test_snap_off_is_identity(t):
    t.set_snap_enabled(False)
    t._apply_seek(int(2.30 * SR))
    assert t.current_index / SR == pytest.approx(2.30, abs=0.001)


def test_seek_snaps_to_nearest_beat(t):
    t.set_snap_enabled(True)           # 120 bpm default -> beats every 0.5 s
    t._apply_seek(int(2.30 * SR))
    assert t.current_index / SR == pytest.approx(2.5, abs=0.01)
    t._apply_seek(int(2.10 * SR))
    assert t.current_index / SR == pytest.approx(2.0, abs=0.01)


def test_seek_snap_rate_invariant(t):
    t.set_snap_enabled(True)
    t._apply_rate(0.5)                 # output timeline doubled
    t._apply_seek(int(4.60 * SR))      # output 4.6 s == source 2.3 s
    src_s = (t.current_index / SR) * t.rate
    assert src_s == pytest.approx(2.5, abs=0.02)   # snapped in source time


def test_loop_point_snaps(t):
    t.set_snap_enabled(True)
    t._apply_loop_a(0.23)              # 2.3 s -> 2.5 s -> frac 0.25
    assert t.loop_a == pytest.approx(0.25, abs=0.005)


def test_snap_follows_offset(t):
    t.set_snap_enabled(True)
    t.set_beat_trim(0.0, 0.1)          # grid = 0.1 + n*0.5
    t._apply_seek(int(2.30 * SR))
    assert t.current_index / SR == pytest.approx(2.10, abs=0.01)


# ----- waveform version -----

def test_waveform_version_monotonic():
    tr = Transport(SR)
    v0 = tr.waveform_version
    tr.set_waveform(([1], [2]))
    tr.set_waveform(None)
    assert tr.waveform_version > v0
    assert tr.waveform_version == v0 + 2


# ----- count-in cancel (regression: must NOT reset the playhead) -----

def _drain_global():
    # Apply queued commands through the real engine dispatch, like the producer loop.
    from core.transport import TRANSPORT
    from core.playback_engine import ENGINE
    for cmd in TRANSPORT.drain_commands():
        ENGINE._apply_cmd(cmd)


def test_cancel_countin_keeps_playhead():
    # Repro: with count-in on, pressing Space DURING the lead-in used to call stop(), which reset
    # the position to 0. cancel_countin() must abort the lead-in but leave the playhead put.
    from core.transport import TRANSPORT
    from core.playback_engine import ENGINE
    TRANSPORT.set_length(SR * 10)
    TRANSPORT.request_seek(int(4.0 * SR))     # user parked the playhead at 4 s
    _drain_global()
    here = TRANSPORT.current_index
    assert here > 0

    TRANSPORT.submit(StartCountIn(__import__("numpy").zeros((16, 2), dtype="float32")))
    _drain_global()
    assert ENGINE.is_counting_in()

    TRANSPORT.cancel_countin()
    _drain_global()
    assert not ENGINE.is_counting_in()        # lead-in aborted
    assert TRANSPORT.current_index == here    # ...and the playhead did NOT jump to 0


def test_stop_still_resets_playhead():
    # Contrast: with NO active loop, stop() zeroes the playhead (intentional).
    from core.transport import TRANSPORT
    from core.playback_engine import ENGINE
    TRANSPORT.set_length(SR * 10)
    TRANSPORT.clear_loop()                       # ensure no loop leaked in from a prior test
    TRANSPORT.request_seek(int(4.0 * SR))
    _drain_global()
    TRANSPORT.stop()
    _drain_global()
    assert TRANSPORT.current_index == 0


# ----- active loop makes A the "home" for stop / play (loop-enable binds home=A) -----

def test_loop_active_stop_homes_to_a(t):
    t._apply_loop_range(0.3, 0.7)
    t._apply_loop_enable(True)
    t._apply_seek(int(0.5 * t.length))
    t._apply_stop()
    assert t.state == PlaybackState.STOPPED
    assert t.current_index == pytest.approx(int(0.3 * t.length), abs=200)   # home = A, not 0


def test_loop_inactive_stop_homes_to_zero(t):
    t._apply_loop_range(0.3, 0.7)                # set but NOT enabled -> not active
    t._apply_seek(int(0.5 * t.length))
    t._apply_stop()
    assert t.current_index == 0                  # no active loop -> absolute start


def test_loop_clear_restores_stop_to_zero(t):
    t._apply_loop_range(0.3, 0.7)
    t._apply_loop_enable(True)
    t._apply_loop_clear()                        # the escape hatch: clearing the loop frees home
    t._apply_seek(int(0.5 * t.length))
    t._apply_stop()
    assert t.current_index == 0


def test_loop_active_play_from_before_a_snaps_to_a(t):
    t._apply_loop_range(0.3, 0.7)
    t._apply_loop_enable(True)
    t._apply_seek(int(0.1 * t.length))           # parked BEFORE A
    t._apply_play()
    assert t.state == PlaybackState.PLAYING
    assert t.current_index == pytest.approx(int(0.3 * t.length), abs=200)   # pulled up to A


def test_loop_active_play_from_after_b_snaps_to_a(t):
    t._apply_loop_range(0.3, 0.7)
    t._apply_loop_enable(True)
    t._apply_seek(int(0.9 * t.length))           # parked PAST B
    t._apply_play()
    assert t.current_index == pytest.approx(int(0.3 * t.length), abs=200)


def test_loop_active_play_inside_does_not_move(t):
    t._apply_loop_range(0.3, 0.7)
    t._apply_loop_enable(True)
    inside = int(0.5 * t.length)
    t._apply_seek(inside)
    t._apply_play()
    assert t.current_index == inside             # already in the section -> no jarring jump
