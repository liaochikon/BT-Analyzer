"""Transport bar: playback controls + the custom drawlist scrubber.

Owns the transport window (play/pause/stop, loop A/B markers, speed, beat-grid trim,
count-in / metronome toggles) and the full-width scrubber track with its beat grid,
waveform overview and per-frame playhead redraw. ``ui_create_transport_bar`` builds the
window; ``update_transport_bar`` is pumped once per frame from the render loop.

Split out of ``ui.ui``; re-exported through ``ui`` so ``from ui import *`` still resolves
``ui_create_transport_bar`` / ``update_transport_bar`` / ``request_play_pause``.
"""

import dearpygui.dearpygui as dpg
import threading
import logging
from core import *
from core import settings
from core.graph_presets import register_globals_hooks
from app_logging.logging import *
from ui import i18n
from ui import icons


logger = logging.getLogger(__name__)


# ============================================================================================
# Transport bar with a custom drawlist scrubber (replaces the native slider) so we can:
#   * scale the track to the full window width (drag resolution grows with the window),
#   * draw A/B loop markers + the loop region + the playhead on the same coordinate system,
#   * reserve an empty layer for a future waveform overview.
# ============================================================================================

# Transport button glyphs from the bundled Sarasa Nerd Font (Font Awesome private-use range,
# verified present in the shipped .ttf). These render as real play/pause/stop icons; if the
# active font ever lacks them they degrade to a blank box, so they stay confined to the
# transport buttons where the meaning is also obvious from position.

# --- track geometry / layout ---
_TIME_CUR_W = 40         # fixed width (px) of each time-readout box (fits "MM:SS"); pins layout
_TRACK_H_MIN = 50        # minimum drawlist height (px) -- the scrubber alone
_TRACK_WAVE_MIN = 40     # track must be at least this tall before the waveform is drawn
_TRACK_MARGIN = 16       # horizontal margin inside the window
_TRACK_BOTTOM_PAD = 50   # gap left below the track inside the window
_TRACK_PAD_Y = 8         # vertical padding of the bar within the drawlist

# --- persistent scrub-drag state (a left-drag seek in progress) ---
_scrub_dragging = False

# Last playback rate actually posted to the transport. The speed slider fires its callback every
# frame while dragged (~60/s); posting a RATE each time would flush the ring + reset the WSOLA
# stretcher every frame (audible jitter), even when the snapped rate didn't change. We only post
# when the snapped 0.1 step actually differs from this.
_last_rate = 1.0

# Master output volume in dB (0 = unity). Mirrored to the engine as a linear gain; part of the
# saved practice state (serialize_globals), so a preset restores the mix level.
_master_db = 0.0

# Metronome/count-in click volume in dB (0 = unity), independent of master. Also part of the
# saved practice state so a preset restores the click balance.
_metro_db = 0.0

# --- persistent marker-drag state (a right-drag on an A/B loop point in progress) ---
# _marker_dragging: which handle the user grabbed ("A" or "B"), or None.
# _marker_pivot: the OTHER end's fraction, held fixed; the dragged handle is combined with it
#   each frame via min/max so dragging one handle past the other swaps roles cleanly (range
#   slider semantics), instead of getting stuck at the crossover.
_marker_dragging = None
_marker_pivot = None

# --- beat grid + count-in ---
# BPM + phase offset now live in TRANSPORT (a Beat Grid node drives them; a shipped default of
# 120/0 stands otherwise). The transport bar only READS them to draw the grid + time the
# count-in, so the controls here are a read-only display, not editors.
_COUNTIN_BEATS = 4
_countin_enabled = False     # toggle: when on, starting playback plays a lead-in first
_metronome_enabled = False   # toggle: overlay clicks on the music while playing
# Grid is redrawn when any of (track length, bpm, offset) changes -- track the last-drawn tuple.
_last_grid_key = None
_last_metronome_key = None
# Total-time text ("/ MM:SS") is change-gated so it only re-renders when the length changes.
_last_total_str = None
# Waveform is redrawn when TRANSPORT publishes a new envelope (its object id changes).
_last_wave_version = None
# Waveform recompute: poll every N frames for a pending recompute; guard so only one worker
# scans the track at a time (a param change during a scan re-flags dirty -> we scan again).
_WAVE_POLL_FRAMES = 30
_wave_poll_counter = 0
_wave_worker_busy = False


def _readout_box(width: int, text: str, tag: str, color=None):
    # A fixed-width, borderless child window wrapping a single text item. The transport bar has four
    # of these (current/total time, BPM, underrun): a fixed box keeps a value whose pixel width
    # wobbles (proportional digits) from reflowing its neighbours. auto_resize_y follows font scale.
    with dpg.child_window(width=width, border=False, no_scrollbar=True, auto_resize_y=True):
        if color is None:
            dpg.add_text(text, tag=tag)
        else:
            dpg.add_text(text, tag=tag, color=color)


def _on_speed_change(sender, app_data):
    # Fires every frame while the slider is dragged. Snap to the nearest 0.1 step and post a new
    # rate ONLY when that snapped step actually changed -- otherwise a slider wiggle within one
    # step (or none at all) would flush the ring + reset the stretcher 60x/s (audible jitter).
    # We do NOT set_value back onto the slider while dragging (that fights the drag and makes the
    # thumb jump); the "%.1fx" format already displays the snapped value.
    global _last_rate
    try:
        rate = round(float(app_data) * 10.0) / 10.0
    except (TypeError, ValueError):
        return
    rate = min(2.0, max(0.1, rate))
    if rate == _last_rate:
        return
    _last_rate = rate
    TRANSPORT.request_rate(rate)


def _on_speed_reset(sender, app_data):
    # Reset playback speed to normal (1.0x). Not a drag, so snapping the slider value is fine.
    global _last_rate
    dpg.set_value("transport_speed", 1.0)
    if _last_rate != 1.0:
        _last_rate = 1.0
        TRANSPORT.request_rate(1.0)


def _push_master(db: float):
    # dB slider -> linear gain on the engine. Unlike speed, this is a cheap per-block multiply with
    # no state to flush, so there's no need to change-gate it -- push on every callback.
    global _master_db
    _master_db = float(db)
    ENGINE.set_master_gain(10.0 ** (_master_db / 20.0))


def _on_master_changed(sender, app_data):
    _push_master(app_data)


def _on_master_reset(sender, app_data):
    # Reset master output to unity (0 dB).
    dpg.set_value("transport_master", 0.0)
    _push_master(0.0)


def _push_metro_vol(db: float):
    # dB slider -> linear click gain on the engine (metronome overlay + count-in), independent of
    # master. Cheap per-block multiply, no state to flush -> push on every callback.
    global _metro_db
    _metro_db = float(db)
    ENGINE.set_metro_gain(10.0 ** (_metro_db / 20.0))


def _on_metro_vol_changed(sender, app_data):
    _push_metro_vol(app_data)


def _on_metro_vol_reset(sender, app_data):
    # Reset the click (metronome + count-in) volume to unity (0 dB).
    dpg.set_value("transport_metro_vol", 0.0)
    _push_metro_vol(0.0)


def _set_toggle_icon(tag: str, icon: str, enabled: bool):
    """Set a transport toggle button's label to its glyph, bracketed when enabled ("[icon]").
    Shared by the loop / count-in / metronome / snap buttons so the convention lives in one place."""
    if dpg.does_item_exist(tag):
        dpg.configure_item(tag, label=(f"[{icon}]" if enabled else icon))


def _track_width() -> int:
    if not dpg.does_item_exist("transport_track"):
        return 1
    w = dpg.get_item_configuration("transport_track").get("width", 1)
    return max(1, int(w))


def _track_height() -> int:
    # Current drawlist height (adaptive -- set by _resize_track from the window's spare space).
    if not dpg.does_item_exist("transport_track"):
        return _TRACK_H_MIN
    h = dpg.get_item_configuration("transport_track").get("height", _TRACK_H_MIN)
    return max(_TRACK_H_MIN, int(h))


def ui_create_transport_bar():
    with dpg.window(
        label=i18n.tr_reg("win_transport", "transport_window_title"),
        tag="win_transport",
        width=640,
        height=160,
        pos=[10, 10],
        # No vertical scroll: the track auto-sizes to fill the window (see _resize_track), so a
        # scrollbar would only appear during a transient resize and let the user scroll the whole
        # bar off-view. no_scrollbar hides it; no_scroll_with_mouse stops the wheel scrolling too.
        no_scrollbar=True,
        no_scroll_with_mouse=True):
        # Row 1 -- transport buttons (play/stop + the snap/loop/clear/count-in/metronome toggles)
        # then, after a gap, the metronome CLICK volume (label + dB slider + reset).
        _GROUP_GAP = 18
        with dpg.group(horizontal=True):
            dpg.add_button(label=icons.PLAY, tag="transport_play_btn", width=40,
                           callback=lambda *a: request_play_pause())
            dpg.add_button(label=icons.STOP, tag="transport_stop_btn", width=40,
                           callback=lambda *a: TRANSPORT.stop())
            dpg.add_button(tag="transport_snap_btn", label=icons.SNAP,
                           width=40, callback=_on_toggle_snap)
            dpg.add_button(tag="transport_loop_btn", label=icons.LOOP,
                           width=40, callback=_on_toggle_loop)
            dpg.add_button(tag="transport_clear_loop_btn", label=icons.CLEAR,
                           width=40, callback=lambda *a: TRANSPORT.clear_loop())
            dpg.add_button(tag="transport_countin_btn", label=icons.COUNTIN,
                           width=40, callback=_on_toggle_countin)
            dpg.add_button(tag="transport_metronome_btn", label=icons.METRONOME,
                           width=40, callback=_on_toggle_metronome)

            dpg.add_spacer(width=_GROUP_GAP)

            dpg.add_text(i18n.tr_reg_value("transport_metro_vol_label", "transport_metro_vol_label"),
                         tag="transport_metro_vol_label")
            dpg.add_slider_float(tag="transport_metro_vol", default_value=0.0,
                                 min_value=-60.0, max_value=6.0, width=100, format="%.0f dB",
                                 callback=_on_metro_vol_changed)
            dpg.add_button(tag="transport_metro_vol_reset_btn", label=icons.UNDO,
                           width=40, callback=_on_metro_vol_reset)

        with dpg.group(horizontal=True):
            with dpg.group():
                dpg.add_text(i18n.tr_reg_value("transport_speed_label", "transport_speed_label"),
                             tag="transport_speed_label")
                dpg.add_text(i18n.tr_reg_value("transport_bpm_trim_label", "transport_bpm_trim_label"),
                             tag="transport_bpm_trim_label")
                
            with dpg.group():
                dpg.add_slider_float(tag="transport_speed", default_value=1.0,
                                     min_value=0.1, max_value=2.0, width=120, format="%.1fx",
                                     callback=_on_speed_change)
                dpg.add_slider_float(tag="transport_bpm_trim", default_value=0.0,
                                     min_value=-5.0, max_value=5.0, width=120, format="%+.2f",
                                     callback=_on_beat_trim_changed)
                
            with dpg.group():
                dpg.add_button(tag="transport_speed_reset_btn", label=icons.UNDO,
                               width=40, callback=_on_speed_reset)
                dpg.add_button(tag="transport_bpm_trim_reset_btn", label=icons.UNDO,
                               width=40, callback=_on_bpm_trim_reset)

            dpg.add_spacer(width=_GROUP_GAP)
            with dpg.group():
                dpg.add_text(i18n.tr_reg_value("transport_master_label", "transport_master_label"),
                             tag="transport_master_label")
                dpg.add_text(i18n.tr_reg_value("transport_offset_trim_label", "transport_offset_trim_label"),
                             tag="transport_offset_trim_label")
                
            with dpg.group():
                dpg.add_slider_float(tag="transport_master", default_value=0.0,
                                     min_value=-60.0, max_value=6.0, width=120, format="%.0f dB",
                                     callback=_on_master_changed)
                dpg.add_slider_float(tag="transport_offset_trim", default_value=0.0,
                                     min_value=-0.1, max_value=0.1, width=120, format="%+.3f s",
                                     callback=_on_beat_trim_changed)
            with dpg.group():
                dpg.add_button(tag="transport_master_reset_btn", label=icons.UNDO,
                               width=40, callback=_on_master_reset)
                dpg.add_button(tag="transport_offset_trim_reset_btn", label=icons.UNDO,
                               width=40, callback=_on_offset_trim_reset)
        with dpg.group(horizontal=True):
            # Time readout as three separate items ("current / total") so the ever-changing current
            # time can't jitter the rest. The font's digits are proportional (a "1" is narrower than
            # a "0"), so each number sits in its OWN fixed-width box: its width wobble stays inside
            # the box and never reflows the slash or the total. The total is also change-gated
            # (re-rendered only on a new length). auto_resize_y follows the font scale.
            _readout_box(_TIME_CUR_W, "00:00", "transport_time")
            dpg.add_text("/", tag="transport_time_sep")
            _readout_box(_TIME_CUR_W, "00:00", "transport_time_total")
            dpg.add_spacer(width=_GROUP_GAP)
            _readout_box(150, "BPM: 120.0", "transport_grid_text", color=[150, 180, 210])
            dpg.add_spacer(width=_GROUP_GAP)
            _readout_box(150, "", "transport_underrun", color=(230, 80, 80))
            
        # Bottom: the full-width scrubber track. Layers, back-to-front: background, beat grid,
        # waveform (reserved / empty for now), loop region + markers + playhead (redrawn/frame).
        with dpg.drawlist(width=608, height=_TRACK_H_MIN, tag="transport_track"):
            dpg.add_draw_layer(tag="transport_track_bg")
            dpg.add_draw_layer(tag="transport_track_grid")   # beat grid (redrawn on bpm/offset)
            dpg.add_draw_layer(tag="transport_track_wave")   # reserved for a future waveform
            dpg.add_draw_layer(tag="transport_track_dyn")

    # Resize the track to fill the window width whenever the window is resized. (The
    # guard/create/bind idiom also appears in mixer_node's panel, but that's in the nodes/ layer
    # with no shared home short of a nodes<->ui dependency, so each keeps its own 3-line copy.)
    if not dpg.does_item_exist("transport_panel_handlers"):
        with dpg.item_handler_registry(tag="transport_panel_handlers"):
            dpg.add_item_resize_handler(callback=_resize_track)
    dpg.bind_item_handler_registry("win_transport", "transport_panel_handlers")

    # Mouse handlers for the scrubber: left = seek / seek-drag, right = set / drag an A/B point.
    if not dpg.does_item_exist("transport_mouse_handlers"):
        with dpg.handler_registry(tag="transport_mouse_handlers"):
            dpg.add_mouse_click_handler(button=dpg.mvMouseButton_Left, callback=_on_track_left_click)
            dpg.add_mouse_click_handler(button=dpg.mvMouseButton_Right, callback=_on_track_right_click)

    _resize_track()
    _draw_track_bg()

    # Let presets saved "with parameters" capture/restore the playbar settings (see graph_presets).
    register_globals_hooks(serialize_globals, apply_globals)


def _resize_track(*args):
    # Track spans the window width minus a margin (wider window = finer drag resolution) and its
    # HEIGHT grows to fill the window's spare space below the control rows, so pulling the window
    # taller grows the waveform area. min() keeps at least the thin scrubber; the track's own top
    # (y position) marks where the controls end.
    if not (dpg.does_item_exist("win_transport") and dpg.does_item_exist("transport_track")):
        return
    win_w, win_h = dpg.get_item_rect_size("win_transport")   # rect_size = the window's CONTENT area
    dpg.set_item_width("transport_track", max(120, int(win_w) - _TRACK_MARGIN))
    # Track top, measured within the content area (so it subtracts off cleanly from win_h, which is
    # also content-space). get_item_pos returns the item's *set* position (0 -- the track is
    # auto-laid-out, never positioned), so it's useless here; the laid-out screen y is in rect_min.
    # A window has no rect_min, but its FIRST child's rect_min marks the content top, so
    # track.rect_min.y - first_child.rect_min.y is the control-rows height in content space. Any
    # missing rect_min (before the first layout pass) falls back to 0.
    track_top = 0
    try:
        kids = dpg.get_item_children("win_transport", 1) or []
        if kids:
            content_top = dpg.get_item_rect_min(kids[0])[1]
            track_top = max(0, int(dpg.get_item_rect_min("transport_track")[1] - content_top))
    except Exception:
        track_top = 0
    avail = int(win_h) - track_top - _TRACK_BOTTOM_PAD
    dpg.set_item_height("transport_track", max(_TRACK_H_MIN, avail))
    _draw_track_bg()
    _draw_beat_grid()
    _draw_waveform()


def _draw_track_bg():
    # Static background: the track groove. Redrawn on resize (width changes).
    if not dpg.does_item_exist("transport_track_bg"):
        return
    dpg.delete_item("transport_track_bg", children_only=True)
    w = _track_width()
    y0, y1 = _TRACK_PAD_Y, _track_height() - _TRACK_PAD_Y
    dpg.draw_rectangle((0, y0), (w, y1), color=(70, 70, 80, 255),
                       fill=(40, 40, 48, 255), rounding=3, parent="transport_track_bg")


def _beat_times():
    """Beat time positions (SOURCE seconds) across the loaded track for the current BPM + offset.

    beat n = offset + n * (60 / BPM). Beats live on the source (original) timeline -- they are
    bpm/offset in source seconds -- so they DON'T move when the playback rate changes. Bounded by
    the source length. Returns [] when nothing is loaded or BPM is invalid.
    """
    snapshot = TRANSPORT.snapshot()
    length_s = snapshot.source_length_seconds
    bpm = snapshot.bpm
    if length_s <= 0 or bpm <= 0:
        return []
    interval = 60.0 / bpm
    times = []
    t = snapshot.beat_offset
    # Cap the count so an extreme BPM can't spray thousands of lines.
    while t <= length_s and len(times) < 4096:
        if t >= 0:
            times.append(t)
        t += interval
    return times


def _draw_beat_grid():
    # Draw the beat grid: a faint line per beat, brighter + thicker every 4th beat (downbeat).
    # Beats are SOURCE-timeline positions, so map them with the SOURCE length -- using the
    # (rate-stretched) output length here made the grid drift whenever the speed wasn't 1.0.
    if not dpg.does_item_exist("transport_track_grid"):
        return
    dpg.delete_item("transport_track_grid", children_only=True)
    snapshot = TRANSPORT.snapshot()
    length_s = snapshot.source_length_seconds
    if length_s <= 0:
        return
    y0, y1 = _TRACK_PAD_Y, _track_height() - _TRACK_PAD_Y
    for i, t in enumerate(_beat_times()):
        x = (t / length_s) * _track_width()
        downbeat = (i % 4 == 0)
        color = (150, 170, 210, 160) if downbeat else (110, 120, 140, 90)
        dpg.draw_line((x, y0), (x, y1), color=color,
                      thickness=(2 if downbeat else 1), parent="transport_track_grid")


def _draw_waveform():
    # Draw the whole-track min/max envelope, filling the track width, vertically centered. Only
    # shown once the track is tall enough (the scrubber alone stays clean at small heights). The
    # envelope spans the SOURCE timeline (same x-mapping as the beat grid), so waveform, grid,
    # markers and playhead all line up. Redrawn on resize / when a new envelope is published.
    if not dpg.does_item_exist("transport_track_wave"):
        return
    dpg.delete_item("transport_track_wave", children_only=True)
    snapshot = TRANSPORT.snapshot()
    env = snapshot.waveform
    h = _track_height()
    if env is None or h < _TRACK_WAVE_MIN:
        return
    mn, mx = env
    bins = len(mx)
    if bins <= 0:
        return
    w = _track_width()
    y0, y1 = _TRACK_PAD_Y, h - _TRACK_PAD_Y
    mid = (y0 + y1) * 0.5
    half = (y1 - y0) * 0.5
    # One filled vertical bar per bin (min..max around the center line).
    step = w / bins
    col = (90, 150, 170, 200)
    for i in range(bins):
        x = i * step
        top = mid - mx[i] * half
        bot = mid - mn[i] * half   # mn is negative -> below center
        dpg.draw_line((x, top), (x, bot), color=col, thickness=max(1.0, step),
                      parent="transport_track_wave")


def _poll_waveform_recompute():
    """Every _WAVE_POLL_FRAMES frames, if a live param changed the audible chain (waveform dirty)
    and no scan is already running, recompute the envelope on a worker thread. The scan pulls the
    whole track through the render closure (which reads live params), so it reflects the current
    gain/EQ/mix. Single-flight: only one worker at a time; if a param changes DURING the scan the
    dirty flag is re-set and the next poll scans again -> converges to the latest params.
    """
    global _wave_poll_counter, _wave_worker_busy
    _wave_poll_counter += 1
    if _wave_poll_counter < _WAVE_POLL_FRAMES:
        return
    _wave_poll_counter = 0
    if _wave_worker_busy:
        return
    pending = TRANSPORT.take_waveform_recompute()   # (render, length) or None; clears the flag
    if pending is None:
        return
    render, length = pending
    _wave_worker_busy = True

    def _work():
        global _wave_worker_busy
        try:
            # Scan under the engine's render lock so the whole-track scan never traverses the
            # shared stateful effect nodes at the same instant as playback (would corrupt live
            # filter/envelope state -> audible glitches).
            env = compute_waveform_envelope(render, length, lock=ENGINE.render_lock)
            # Only publish if this render chain is still the current one. A recompile / disconnect
            # may have swapped or cleared the source while we scanned; publishing a stale envelope
            # would leave a ghost waveform over now-different (or absent) audio.
            if TRANSPORT.waveform_source_is(render):
                TRANSPORT.set_waveform(env)
        except Exception:
            logger.exception("Waveform recompute failed")
        finally:
            _wave_worker_busy = False

    threading.Thread(target=_work, daemon=True).start()


def _frac_to_x(frac: float) -> float:
    return max(0.0, min(1.0, frac)) * _track_width()


def _on_toggle_loop(sender, app_data):
    TRANSPORT.set_loop_enabled(not TRANSPORT.snapshot().loop_enabled)


def _on_toggle_countin(sender, app_data):
    # Count-in is a mode toggle (like loop), not an immediate action. When enabled, starting
    # playback plays the lead-in first (see request_play_pause).
    global _countin_enabled
    _countin_enabled = not _countin_enabled


def _on_toggle_metronome(sender, app_data):
    # Metronome overlay toggle (independent of count-in). The actual enable + current grid are
    # pushed to the engine every frame in update_transport_bar, so it tracks BPM/offset changes.
    global _metronome_enabled
    _metronome_enabled = not _metronome_enabled
    # On the off->on edge, re-anchor the engine's metronome/render clock to the playhead (a
    # no-move resync -- the same realignment a manual seek does). Enabling the metronome
    # mid-playback otherwise left it computing beats against a drifted render clock, so no clicks
    # sounded until the user happened to seek. Only meaningful while audio is actually playing.
    if _metronome_enabled and TRANSPORT.snapshot().state == PlaybackState.PLAYING:
        TRANSPORT.request_resync()


def _on_toggle_snap(sender, app_data):
    # Snap-to-beat toggle: when on, seeks + A/B loop points pull to the nearest beat. The state
    # lives in TRANSPORT (read inside _apply_seek / _apply_loop_*), so just flip it there.
    TRANSPORT.set_snap_enabled(not TRANSPORT.snapshot().snap_enabled)


def _on_beat_trim_changed(sender, app_data):
    # Push both trim sliders into TRANSPORT; bpm/beat_offset then read base + trim everywhere.
    TRANSPORT.set_beat_trim(
        float(dpg.get_value("transport_bpm_trim")),
        float(dpg.get_value("transport_offset_trim")))


def _on_bpm_trim_reset(sender, app_data):
    # Reset only the BPM trim; leave the offset trim untouched.
    dpg.set_value("transport_bpm_trim", 0.0)
    _on_beat_trim_changed(sender, None)


def _on_offset_trim_reset(sender, app_data):
    # Reset only the offset trim; leave the BPM trim untouched.
    dpg.set_value("transport_offset_trim", 0.0)
    _on_beat_trim_changed(sender, None)


# ===== Global (transport-bar) settings for presets =====
# The "playbar settings": toggles + beat trim + playback speed + the A/B loop. Captured/restored as
# a bundle so a preset saved with "parameters" reopens the same practice setup -- which, for this
# app, IS "saving a song" (the loaded file lives in the Audio Input node's serialize_extra). The
# loop A/B are stored as fractions of the OUTPUT timeline (same units TRANSPORT.loop_a/b use).
# Deliberately excludes the live playhead position and the beat-grid BASE (a Beat Grid node drives
# that; the trim is the user's manual correction, which we DO store).

def serialize_globals() -> dict:
    """Snapshot the transport-bar settings a preset should restore."""
    snapshot = TRANSPORT.snapshot()
    data = {
        "speed": round(float(_last_rate), 4),
        "master_db": round(float(_master_db), 2),
        "metro_db": round(float(_metro_db), 2),
        "bpm_trim": round(float(snapshot.bpm_trim), 4),
        "offset_trim": round(float(snapshot.offset_trim), 4),
        "countin": bool(_countin_enabled),
        "metronome": bool(_metronome_enabled),
        "snap": bool(snapshot.snap_enabled),
        "loop_enabled": bool(snapshot.loop_enabled),
    }
    # Only store the A/B markers when both are set (otherwise there's no loop to restore).
    if snapshot.loop_a is not None and snapshot.loop_b is not None:
        data["loop_a"] = round(float(snapshot.loop_a), 6)
        data["loop_b"] = round(float(snapshot.loop_b), 6)
    return data


def apply_globals(data: dict):
    """Restore a bundle produced by :func:`serialize_globals` (missing keys keep current value)."""
    global _last_rate, _countin_enabled, _metronome_enabled
    if not isinstance(data, dict):
        return

    if "speed" in data:
        try:
            _last_rate = min(2.0, max(0.1, round(float(data["speed"]) * 10.0) / 10.0))
        except (TypeError, ValueError):
            _last_rate = 1.0
        if dpg.does_item_exist("transport_speed"):
            dpg.set_value("transport_speed", _last_rate)
        TRANSPORT.request_rate(_last_rate)

    if "master_db" in data:
        try:
            db = max(-60.0, min(6.0, float(data["master_db"])))
        except (TypeError, ValueError):
            db = 0.0
        if dpg.does_item_exist("transport_master"):
            dpg.set_value("transport_master", db)
        _push_master(db)

    if "metro_db" in data:
        try:
            mdb = max(-60.0, min(6.0, float(data["metro_db"])))
        except (TypeError, ValueError):
            mdb = 0.0
        if dpg.does_item_exist("transport_metro_vol"):
            dpg.set_value("transport_metro_vol", mdb)
        _push_metro_vol(mdb)

    if "bpm_trim" in data or "offset_trim" in data:
        snapshot = TRANSPORT.snapshot()
        bpm_trim = float(data.get("bpm_trim", snapshot.bpm_trim))
        offset_trim = float(data.get("offset_trim", snapshot.offset_trim))
        if dpg.does_item_exist("transport_bpm_trim"):
            dpg.set_value("transport_bpm_trim", bpm_trim)
        if dpg.does_item_exist("transport_offset_trim"):
            dpg.set_value("transport_offset_trim", offset_trim)
        TRANSPORT.set_beat_trim(bpm_trim, offset_trim)

    if "countin" in data:
        _countin_enabled = bool(data["countin"])
    if "metronome" in data:
        _metronome_enabled = bool(data["metronome"])
    if "snap" in data:
        TRANSPORT.set_snap_enabled(bool(data["snap"]))

    # A/B loop: set the range atomically (auto-ordered), then the enabled flag. Clear first so a
    # preset with no stored loop resets any loop left over from the previous graph.
    if "loop_a" in data and "loop_b" in data:
        try:
            TRANSPORT.set_loop_range(float(data["loop_a"]), float(data["loop_b"]))
        except (TypeError, ValueError):
            pass
    else:
        TRANSPORT.clear_loop()
    if "loop_enabled" in data:
        TRANSPORT.set_loop_enabled(bool(data["loop_enabled"]))
    # Toggle button glyphs + the engine metronome are refreshed by update_transport_bar next tick.
    # The loop region redraws on the scrubber via _redraw_track_dynamic next tick.


def request_play_pause():
    """Play/pause entry point shared by the play button and the Space shortcut.

    Honours the count-in toggle: starting playback from a stopped/paused state with count-in on
    plays a 4-beat lead-in first, then resumes from the current playhead. If a count-in is
    already running, this cancels it (a second press aborts). While playing, it just pauses.
    """
    if ENGINE.is_counting_in():
        TRANSPORT.cancel_countin()   # abort the lead-in, leaving the playhead where it is
        return
    snapshot = TRANSPORT.snapshot()
    if snapshot.state == PlaybackState.PLAYING:
        TRANSPORT.toggle()        # pause
        return
    # Starting playback. With count-in enabled + a valid tempo, play the lead-in; else play now.
    # Divide by the playback rate so the lead-in ticks at the tempo the music will ACTUALLY sound
    # at: the grid is source-tempo (bpm), but at rate r the music is heard at bpm*r, so the count
    # spacing must stretch by 1/r to match (otherwise a 0.5x slow-down counts you in at full speed
    # then the music enters at half — misleading).
    bpm = snapshot.bpm
    rate = snapshot.rate if snapshot.rate > 1e-6 else 1.0
    if _countin_enabled and snapshot.length > 0 and bpm > 0:
        interval_frames = int((60.0 / bpm) * snapshot.sample_rate / rate)
        ENGINE.start_countin(interval_frames, _COUNTIN_BEATS)
    else:
        TRANSPORT.toggle()        # play


def _track_local_x():
    """Mouse x within the track in [0, width], or None if the mouse isn't over the track."""
    if not dpg.does_item_exist("transport_track") or not dpg.is_item_hovered("transport_track"):
        return None
    lx = dpg.get_drawing_mouse_pos()[0]
    return max(0.0, min(float(_track_width()), lx))


def _on_track_left_click(sender, app_data):
    # Begin a seek-drag if the click landed on the track and audio is loaded. The continuous
    # drag is handled per-frame in update_transport_bar (mouse x -> seek).
    global _scrub_dragging, _marker_dragging, _marker_pivot
    lx = _track_local_x()
    snapshot = TRANSPORT.snapshot()
    if lx is None or snapshot.length <= 0:
        return
    # A seek drag and a marker drag are mutually exclusive: starting one cancels the other.
    _marker_dragging = None
    _marker_pivot = None
    _scrub_dragging = True
    TRANSPORT.request_seek(int((lx / _track_width()) * snapshot.length))


def _pick_loop_handle(frac, a, b) -> str:
    """Decide which loop end a right-click at ``frac`` grabs. Reused for click and drag-start.

    Right of B -> B; left of A -> A; inside [A,B] -> the nearer end. With only one end set,
    the click position picks the missing end; with none set, the half of the track clicked.
    """
    if b is not None and frac > b:
        return "B"
    if a is not None and frac < a:
        return "A"
    if a is not None and b is not None:
        return "A" if abs(frac - a) <= abs(frac - b) else "B"
    if a is not None:
        return "B"          # only A set, click is right of it -> that's B
    if b is not None:
        return "A"          # only B set, click is left of it -> that's A
    return "A" if frac < 0.5 else "B"


def _on_track_right_click(sender, app_data):
    # Right-click grabs an A/B loop handle and begins a drag. A click without movement just sets
    # that point where clicked (drag of length 0), so the old set-point behaviour is preserved.
    # The continuous drag is handled per-frame in update_transport_bar.
    global _marker_dragging, _marker_pivot, _scrub_dragging
    lx = _track_local_x()
    snapshot = TRANSPORT.snapshot()
    if lx is None or snapshot.length <= 0:
        return
    frac = lx / _track_width()
    a, b = snapshot.loop_a, snapshot.loop_b

    handle = _pick_loop_handle(frac, a, b)
    # The pivot is the OTHER end, held fixed during the drag (None if it doesn't exist yet).
    _marker_pivot = b if handle == "A" else a
    _marker_dragging = handle
    _scrub_dragging = False   # a marker drag and a seek drag are mutually exclusive
    _apply_marker_drag(frac)


def _apply_marker_drag(frac: float):
    # Combine the dragged handle with the fixed pivot into an ordered range. When there is no
    # pivot (the other end is unset), just move the single point. min/max ordering gives clean
    # swap: drag A past B and A becomes the right end, B the left -- no sticking at the crossover.
    frac = max(0.0, min(1.0, frac))
    if _marker_pivot is None:
        (TRANSPORT.set_loop_a if _marker_dragging == "A" else TRANSPORT.set_loop_b)(frac)
    else:
        TRANSPORT.set_loop_range(frac, _marker_pivot)


def _redraw_track_dynamic():
    # Redraw the loop region, A/B markers and playhead every frame on the dynamic layer.
    if not dpg.does_item_exist("transport_track_dyn"):
        return
    dpg.delete_item("transport_track_dyn", children_only=True)
    w = _track_width()
    y0, y1 = _TRACK_PAD_Y, _track_height() - _TRACK_PAD_Y
    layer = "transport_track_dyn"

    snapshot = TRANSPORT.snapshot()
    a, b = snapshot.loop_a, snapshot.loop_b
    active = snapshot.loop_active

    # Loop region shading between A and B.
    if a is not None and b is not None and a < b:
        fill = (110, 170, 90, 70) if active else (110, 110, 130, 45)
        dpg.draw_rectangle((_frac_to_x(a), y0), (_frac_to_x(b), y1),
                           color=(0, 0, 0, 0), fill=fill, parent=layer)
    # A / B marker lines.
    if a is not None:
        ax = _frac_to_x(a)
        dpg.draw_line((ax, y0), (ax, y1), color=(120, 220, 120, 230), thickness=2, parent=layer)
        dpg.draw_text((ax + 2, y0), "A", size=13, color=(150, 240, 150, 255), parent=layer)
    if b is not None:
        bx = _frac_to_x(b)
        dpg.draw_line((bx, y0), (bx, y1), color=(220, 180, 90, 230), thickness=2, parent=layer)
        dpg.draw_text((bx - 12, y0), "B", size=13, color=(240, 200, 120, 255), parent=layer)

    # Playhead.
    px = _frac_to_x(snapshot.progress)
    dpg.draw_line((px, y0 - 2), (px, y1 + 2), color=(230, 230, 240, 255), thickness=2, parent=layer)


def _drag_frac() -> float:
    # Current mouse x as a track fraction in [0,1], clamped, using screen coords so a drag that
    # leaves the track (past either edge) pins to 0 / 1 instead of going dead.
    track_left = dpg.get_item_rect_min("transport_track")[0]
    mx = dpg.get_mouse_pos(local=False)[0]
    return max(0.0, min(1.0, (mx - track_left) / _track_width()))


def _sync_engine_from_ui():
    # Push the metronome state to the engine every frame. It's a cheap scalar write done
    # unconditionally so the engine's metronome can never get stuck out of sync with the UI, and
    # kept as the FIRST thing update_transport_bar does so a later per-frame draw error can't
    # starve it (which would leave the click sounding once then dying).
    global _last_metronome_key
    snapshot = TRANSPORT.snapshot()
    key = (_metronome_enabled, round(snapshot.bpm, 6), round(snapshot.beat_offset, 6))
    if key != _last_metronome_key:
        _last_metronome_key = key
        ENGINE.set_metronome(*key)


def _refresh_readouts():
    # Change-gated repaints: the beat grid + BPM text, the waveform, and the button glyphs. Each
    # only touches DPG when its underlying state actually changed, so this is cheap most frames.
    global _last_grid_key, _last_wave_version

    # Snap the speed slider thumb to its 0.1 step once the drag is released (we deliberately do
    # NOT write it back mid-drag, which would fight the drag). Only touch it when not active and
    # the raw value is off-step, so this is a no-op the rest of the time.
    if dpg.does_item_exist("transport_speed") and not dpg.is_item_active("transport_speed"):
        if abs(dpg.get_value("transport_speed") - _last_rate) > 1e-6:
            dpg.set_value("transport_speed", _last_rate)

    # If a Beat Grid node had pushed values but is now gone (deleted), fall back to the shipped
    # default so the grid doesn't stay frozen on a stale tempo. Checked every frame (no event
    # wiring): removing the node just makes this true on the next tick.
    snapshot = TRANSPORT.snapshot()
    if snapshot.beat_grid_from_node and not has_node_of_class("BeatGridNode"):
        TRANSPORT.reset_beat_grid()
        snapshot = TRANSPORT.snapshot()

    # Redraw the beat grid only when what it depends on changes -- (source length, bpm, offset).
    # Uses the SOURCE length (rate-independent) to match _draw_beat_grid, so a speed change alone
    # never redraws it. Otherwise static, so this avoids repainting dozens of lines every frame.
    grid_key = (round(snapshot.source_length_seconds, 4),
                round(snapshot.bpm, 4), round(snapshot.beat_offset, 4))
    if grid_key != _last_grid_key:
        _last_grid_key = grid_key
        _draw_beat_grid()
        # Show the effective BPM, plus the trim in parentheses when it's non-zero.
        trim = snapshot.bpm_trim
        trim_str = f" {trim:+.2f}" if abs(trim) > 1e-6 else ""
        dpg.set_value("transport_grid_text", f"BPM: {snapshot.bpm:.1f}{trim_str}")

    # Recompute the waveform on a worker thread when a live param dirtied the audible chain
    # (polled, single-flight -- see _poll_waveform_recompute), then redraw when a new envelope
    # has been published (waveform_version bumps -- reliable, unlike an id() that can collide).
    _poll_waveform_recompute()
    wave_ver = snapshot.waveform_version
    if wave_ver != _last_wave_version:
        _last_wave_version = wave_ver
        _draw_waveform()

    # Play/pause button label reflects the current state.
    playing = snapshot.state == PlaybackState.PLAYING
    dpg.configure_item("transport_play_btn", label=icons.PAUSE if playing else icons.PLAY)

    # Toggle icon buttons show enabled state by bracketing the glyph ("[icon]" when on).
    _set_toggle_icon("transport_loop_btn", icons.LOOP, snapshot.loop_enabled)
    _set_toggle_icon("transport_countin_btn", icons.COUNTIN, _countin_enabled)
    _set_toggle_icon("transport_metronome_btn", icons.METRONOME, _metronome_enabled)
    _set_toggle_icon("transport_snap_btn", icons.SNAP, snapshot.snap_enabled)


def _handle_track_drags():
    # Continuous seek / marker drags started by the mouse-click handlers. Each maps the live mouse
    # x to a position and ends when the button is released. Seek and marker drags are mutually
    # exclusive (only one flag is ever set at a time -- see the click handlers).
    global _scrub_dragging, _marker_dragging, _marker_pivot

    # Seek-drag (left button): map mouse x directly to a seek position.
    if _scrub_dragging:
        if dpg.is_mouse_button_down(dpg.mvMouseButton_Left):
            TRANSPORT.request_seek(int(_drag_frac() * TRANSPORT.snapshot().length))
        else:
            _scrub_dragging = False   # button released -> end the drag

    # Marker-drag (right button): move the grabbed A/B handle, swapping cleanly if dragged past
    # the fixed pivot. When the drag ends, drop the pivot so a subsequent grab re-reads the ends.
    if _marker_dragging is not None:
        if dpg.is_mouse_button_down(dpg.mvMouseButton_Right):
            _apply_marker_drag(_drag_frac())
        else:
            _marker_dragging = None
            _marker_pivot = None


def _refresh_time_readout():
    # Current time every frame; total only when it changes (a new song length), so the static text
    # never re-renders. Each number lives in its own fixed-width box, so a digit's proportional
    # width can't reflow the layout and jitter its neighbours.
    global _last_total_str
    snapshot = TRANSPORT.snapshot()
    dpg.set_value("transport_time", format_mmss(snapshot.position_seconds))
    total_str = format_mmss(snapshot.length_seconds)
    if total_str != _last_total_str:
        _last_total_str = total_str
        dpg.set_value("transport_time_total", total_str)


def update_transport_bar():
    """Per-frame transport tick, pumped from the render loop. Ordered: sync engine FIRST (so a
    later draw error can't starve the metronome), then repaint readouts, handle drags, update the
    time, and finally repaint the playhead/markers on the scrubber."""
    _sync_engine_from_ui()
    _refresh_readouts()
    _handle_track_drags()
    _refresh_time_readout()
    _redraw_track_dynamic()   # playhead / markers / loop region
