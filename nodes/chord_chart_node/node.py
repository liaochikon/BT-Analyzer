import dearpygui.dearpygui as dpg
from core import *
from nodes._shared.midi_draw import chord_root_pitch_class
from nodes.chord_chart_node.processor import ChordChartProcessor
from ui import i18n
from ui.surfaces import canvas_surface, centered_surface


class ChordChartNode(BaseNode):
    """Scrolling chord timeline, like a video tab/karaoke scroller.

    Reads a chord MIDI stream (each chord = a few notes sharing a start/end, e.g. from Chord
    Detection) and draws the chords on a horizontal band. The playhead is FIXED near the left; the
    chord blocks scroll past it as playback advances, so you read what's coming. An adjustable
    window (seconds visible) is the zoom -- a fit-the-whole-song view made every chord a few pixels
    wide and unreadable when the detection is fragmented; a narrow window gives each chord room.

    A pure sink like the Guitar Fretboard. Reads MIDI (a cached/preset-persisted output) rather
    than a detector's scratch state, so the chart survives a preset/undo restore without re-baking.
    """

    display_name = "Chord Chart"
    category = "Display"
    config = NodeConfig(ui=NodeUIMode.INSPECTOR_PANEL, realtime=True)

    panel_width = 760
    panel_height = 180

    # Drawing margins (pixels).
    PAD_L = 12
    PAD_R = 12
    PAD_T = 12
    PAD_B = 12   # room for the start-time labels under the blocks

    # Playhead sits this fraction from the left of the band: 1/3 past, 2/3 upcoming.
    PLAYHEAD_FRAC = 0.33
    # Don't draw a chord name unless its visible block is at least this wide (px).
    MIN_LABEL_W = 26

    # One hue per chord root (C -> B), used to tint the blocks. This node's OWN palette (not shared
    # with the fretboard): muted -- mixed ~45% toward mid-grey -- because a wall of large filled
    # blocks at full saturation is harsh on the eyes, whereas the fretboard's small dots aren't.
    ROOT_COLORS = (
        (213, 133, 133),  # C
        (213, 150, 128),  # C#
        (213, 180, 128),  # D
        (185, 200, 130),  # D#
        (128, 194, 133),  # E
        (114, 194, 156),  # F
        (114, 188, 188),  # F#
        (117, 156, 208),  # G
        (137, 131, 208),  # G#
        (166, 128, 208),  # A
        (200, 123, 194),  # A#
        (206, 120, 156),  # B
    )
    parameter_specs = (
        ParameterSpec(
            "window_seconds", 8.0,
            stale=StalePolicy.NONE,
            signature=False,
            on_change="_on_window_changed",
        ),
    )
    parameter_control_specs = (
        ParameterControlSpec(
            "window_seconds",
            SliderSpec("Window (s)", 2.0, 30.0, format="%.0f", width=200)),
    )

    class_node_theme_templates = []
    class_button_theme_templates = []

    def __init__(self, node_tag):
        super().__init__(node_tag)
        self._processor = ChordChartProcessor()

        self.in_midi = self._register_input("in_midi", DataType.MIDI)

        self.drawlist_tag = f"chordchart_draw_{self.node_tag}"
        self.content_layer_tag = f"chordchart_content_{self.node_tag}"
        self.canvas_tag = f"chordchart_canvas_{self.node_tag}"
        self.empty_surface_tag = f"chordchart_empty_{self.node_tag}"

        # Merged chord segments derived from the MIDI (parallel arrays + name list).
        self._seg_starts = []
        self._seg_ends = []
        self._seg_names = []
        self._song_len = 0.0

        self._draw_w = self.panel_width
        self._draw_h = self.panel_height

        self._last_fp = None        # upstream MIDI fingerprint (rebuild segments on change)
        self._last_pos = -1.0       # last drawn playhead position (seconds)
        self._canvas_size = (0, 0)

    def get_param_signature(self) -> str:
        # Pure sink; identity comes from the input MIDI fingerprint. (Window is a view setting.)
        return "chordchart"

    # ----- Node body ----- #

    def _draw_node_internal(self):
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input, tag=self.in_midi):
            self._draw_pin(
                "Chord In", is_output=False,
                pin_theme=self.get_global_button_theme("default_input_pin"), label_width=85)

    # ----- Panel ----- #

    def _draw_panel(self):
        with dpg.child_window(width=-1, height=56, border=False, no_scrollbar=True):
            with centered_surface(tag=f"chordchart_controls_surface_{self.node_tag}"):
                with dpg.group(horizontal=True):
                    self._draw_declared_param(
                        "window_seconds", width=200, scope="panel")

        has_chords = bool(self._seg_names)
        with canvas_surface(tag=self.canvas_tag):
            with dpg.drawlist(
                width=self._draw_w, height=self._draw_h,
                tag=self.drawlist_tag, show=has_chords,
            ):
                dpg.add_draw_layer(tag=self.content_layer_tag)
            with centered_surface(tag=self.empty_surface_tag, show=not has_chords):
                dpg.add_text(i18n.tr("chord_chart_empty"), color=[150, 150, 150])

        # Force a redraw on the next frame now that the layer exists again.
        self._last_pos = -1.0

    def _on_window_changed(self):
        # Redraw immediately so zooming while stopped updates the view without waiting for motion.
        self._redraw(TRANSPORT.source_position_seconds)

    def _block_colors(self, name: str, is_current: bool):
        """(fill, edge) for a chord block, tinted by the chord's ROOT pitch class (this node's muted
        ROOT_COLORS). The current chord is a saturated fill with a bright edge; upcoming/past chords
        are a dim wash so the playhead's chord stands out. Unnamed ('--') falls back to a neutral
        grey."""
        pc = chord_root_pitch_class(name)
        base = self.ROOT_COLORS[pc] if pc is not None else (120, 130, 145)
        r, g, b = base
        if is_current:
            fill = (r, g, b, 235)
            edge = (255, 255, 255, 255)
        else:
            # Dim wash: darken toward the panel background so past/upcoming blocks recede.
            fill = (r // 3, g // 3, b // 3, 200)
            edge = (r, g, b, 180)
        return fill, edge

    def _segments_from_notes(self, notes):
        snapshot = self._processor.build_snapshot(notes)
        self._seg_starts = [segment.start for segment in snapshot.segments]
        self._seg_ends = [segment.end for segment in snapshot.segments]
        self._seg_names = [segment.name for segment in snapshot.segments]
        self._song_len = snapshot.song_length

    # ----- Per-frame update ----- #

    def on_gui_frame(self):
        if not dpg.does_item_exist(self.drawlist_tag):
            return

        # Resize: match the drawlist to the panel window.
        if dpg.does_item_exist(self.canvas_tag):
            w, h = dpg.get_item_rect_size(self.canvas_tag)
            if (w, h) != self._canvas_size and w > 0 and h > 0:
                self._canvas_size = (w, h)
                self._draw_w = max(320, int(w))
                self._draw_h = max(80, int(h))
                dpg.configure_item(self.drawlist_tag, width=self._draw_w, height=self._draw_h)
                self._last_pos = -1.0  # force a redraw against the new geometry

        packet = self.input_values.get(self.in_midi)
        fp = packet.fingerprint if packet is not None else None
        # SOURCE-timeline position so the scroll tracks the music at any playback rate.
        pos = TRANSPORT.source_position_seconds

        if fp != self._last_fp:
            self._last_fp = fp
            self._segments_from_notes(packet.data if packet is not None else None)
            has_chords = bool(self._seg_names)
            dpg.configure_item(self.drawlist_tag, show=has_chords)
            dpg.configure_item(self.empty_surface_tag, show=not has_chords)
            self._last_pos = -1.0  # geometry-independent rebuild -> force redraw

        # Scroll: redraw whenever the playhead moved (every frame while playing).
        if abs(pos - self._last_pos) >= 1e-4:
            self._last_pos = pos
            self._redraw(pos)

    def _redraw(self, pos: float):
        layer = self.content_layer_tag
        if not dpg.does_item_exist(layer):
            return
        dpg.delete_item(layer, children_only=True)

        y_top = self.PAD_T
        y_bot = self._draw_h - self.PAD_B
        band_l = self.PAD_L
        band_r = self._draw_w - self.PAD_R
        span = max(1.0, band_r - band_l)

        win = max(1.0, self.window_seconds)
        px_per_sec = span / win
        playhead_x = band_l + self.PLAYHEAD_FRAC * span
        t_left = pos - self.PLAYHEAD_FRAC * win
        t_right = pos + (1.0 - self.PLAYHEAD_FRAC) * win

        def x_for(t):
            return playhead_x + (t - pos) * px_per_sec

        for i, name in enumerate(self._seg_names):
            s, e = self._seg_starts[i], self._seg_ends[i]
            if e < t_left or s > t_right:
                continue   # fully outside the visible window
            true_x0 = x_for(s)
            x0 = max(band_l, true_x0)
            x1 = min(band_r, x_for(e))
            if x1 <= x0:
                continue
            is_current = (s <= pos < e)
            fill, edge = self._block_colors(name, is_current)
            dpg.draw_rectangle((x0, y_top), (x1, y_bot),
                               color=edge, fill=fill, thickness=2 if is_current else 1, parent=layer)
            # Chord name: centre it in the VISIBLE span so the current chord's label stays on
            # screen even as its block scrolls off the left. Skip if the block's too narrow.
            if (x1 - x0) >= self.MIN_LABEL_W:
                cx = (x0 + x1) * 0.5
                col = (255, 255, 255, 255) if is_current else (225, 230, 238, 235)
                dpg.draw_text((cx - len(name) * 5.0, (y_top + y_bot) * 0.5 - 10),
                              name, size=20, color=col, parent=layer)
            # Start-time label under the block's true left edge, only when that edge is on screen.
            if band_l <= true_x0 <= band_r:
                dpg.draw_text((true_x0 + 2, y_bot + 3), format_mmss(s, pad_minutes=False),
                              size=11, color=(150, 160, 175, 255), parent=layer)

        # Fixed playhead line.
        dpg.draw_line((playhead_x, y_top - 2), (playhead_x, y_bot + 2),
                      color=(230, 230, 240, 255), thickness=2, parent=layer)

    def publish_outputs(self) -> bool:
        # Pure sink: visualises the upstream chord MIDI; nothing flows downstream.
        return True
