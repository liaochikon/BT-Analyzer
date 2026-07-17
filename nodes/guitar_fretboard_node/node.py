import dearpygui.dearpygui as dpg
from core import *
from nodes._shared.midi_draw import note_name
from nodes.guitar_fretboard_node.processor import (
    FretboardParameters,
    GuitarFretboardProcessor,
)
from ui.surfaces import canvas_surface, centered_surface


def _coerce_guitar_pitch(value):
    return max(40.0, min(88.0, float(value)))


class GuitarFretboardNode(BaseNode):
    display_name = "Guitar Fretboard"
    category = "Display"
    config = NodeConfig(ui=NodeUIMode.INSPECTOR_PANEL, realtime=True)

    panel_width = 1400
    panel_height = 400

    # Standard tuning, drawn TOP -> BOTTOM: high-E, B, G, D, A, low-E (MIDI numbers).
    OPEN_STRINGS = (64, 59, 55, 50, 45, 40)
    INLAY_SINGLE = (3, 5, 7, 9, 15, 17, 19, 21)
    INLAY_DOUBLE = (12, 24)

    # Natural harmonics that land on real fret positions with accurate pitch: the semitone
    # interval above the open string -> the fret you touch to sound it. The 2nd harmonic (12
    # semitones) is at the 12th fret; the 3rd (19) at the 7th; the 4th (24) at the 5th. (Each
    # also repeats higher up -- 19/24 -- but the low touch fret is the useful one.)
    HARMONIC_INTERVAL_TO_FRET = {12: 12, 19: 7, 24: 5}

    # Fingering-mode radio labels (single source of truth so the widget + the label<->mode mapping
    # can't drift). ALL = every playable position of every note; SUGGEST = one recommended hand
    # shape/position, with the rest shown faintly.
    MODE_ALL = "All Positions"
    MODE_SUGGEST = "Suggested Shape"
    GUITAR_PITCH_MIN = 40
    GUITAR_PITCH_MAX = 88
    MIN_PITCH_LINE_COLOR = (70, 210, 255, 255)
    MAX_PITCH_LINE_COLOR = (255, 170, 70, 255)

    # ----- Auto Fingering model (internal; not user-facing) ----- #
    # Auto Fingering is an ASSIST, not a filter: it never hides a note. It picks a recommended
    # HAND WINDOW (a hand-width fret span) and emphasises, per sounding pitch, one position -- the
    # one inside that window when reachable. Positions in the window that form a one-per-string
    # hand shape get a fingering mark (a white "U"). See _assign_layers.
    HAND_SPAN = 4           # width (frets) of the recommended hand window (index..index+HAND_SPAN)
    HAND_SMOOTH = 0.30      # _hand_center += HAND_SMOOTH * (new - old); lower = steadier follow
    L1_ALPHA = 55           # fixed opacity for the "all positions" backdrop (NOT velocity-scaled,
                            #   so a faint dot always means "other place to play it", never "quiet")

    # Drawing margins (pixels).
    PAD_L = 100   # room for the string-name labels + open-string markers (left of the nut)
    PAD_R = 30
    PAD_T = 30
    PAD_B = 30   # room for the fret-number labels under the board

    # One distinct hue per pitch class, C -> B (chromatic colour wheel). Owned by this node -- the
    # chord chart keeps its own (muted) palette, so the two views are not coupled.
    PITCH_COLORS = (
        (255, 60, 60),    # C
        (255, 130, 40),   # C#
        (255, 200, 40),   # D
        (200, 230, 50),   # D#
        (90, 220, 70),    # E
        (60, 220, 150),   # F
        (60, 210, 220),   # F#
        (70, 150, 240),   # G
        (110, 100, 240),  # G#
        (170, 90, 240),   # A
        (230, 80, 220),   # A#
        (240, 70, 150),   # B
    )
    parameter_specs = (
        ParameterSpec(
            "fingering_mode", 0.0, stale=StalePolicy.NONE,
            signature=False, coerce=float,
            on_change="_on_fingering_mode_changed"),
        ParameterSpec(
            "num_frets", 22.0, stale=StalePolicy.NONE,
            signature=False, on_change="_on_frets_changed"),
        ParameterSpec(
            "bright_threshold", 100.0, stale=StalePolicy.NONE,
            signature=False, on_change="_invalidate_dynamic"),
        ParameterSpec(
            "pitch_min", 40.0, stale=StalePolicy.NONE,
            signature=False, coerce=_coerce_guitar_pitch,
            on_change="_invalidate_dynamic"),
        ParameterSpec(
            "pitch_max", 88.0, stale=StalePolicy.NONE,
            signature=False, coerce=_coerce_guitar_pitch,
            on_change="_invalidate_dynamic"),
        ParameterSpec(
            "prefer_position", 0.0, stale=StalePolicy.NONE,
            signature=False, on_change="_invalidate_dynamic"),
        ParameterSpec(
            "show_all_positions", True, stale=StalePolicy.NONE,
            signature=False, on_change="_invalidate_dynamic"),
        ParameterSpec(
            "allow_open", False, stale=StalePolicy.NONE,
            signature=False, on_change="_invalidate_dynamic"),
        ParameterSpec(
            "harmonic_mode", False, stale=StalePolicy.NONE,
            signature=False, on_change="_invalidate_dynamic"),
    )
    parameter_control_specs = (
        ParameterControlSpec(
            "num_frets", SliderSpec("Frets", 12.0, 24.0, format="%.0f")),
        ParameterControlSpec(
            "bright_threshold",
            SliderSpec("Brightest Velocity", 1.0, 127.0, format="%.0f")),
        ParameterControlSpec(
            "pitch_min", SliderSpec("Min Pitch", 40.0, 88.0, format="%.0f")),
        ParameterControlSpec(
            "pitch_max", SliderSpec("Max Pitch", 40.0, 88.0, format="%.0f")),
        ParameterControlSpec(
            "prefer_position",
            SliderSpec("Preferred Position", 0.0, 22.0, format="%.0f")),
        ParameterControlSpec("show_all_positions", CheckboxSpec("Show Alternatives")),
        ParameterControlSpec("allow_open", CheckboxSpec("Allow Open Strings")),
        ParameterControlSpec("harmonic_mode", CheckboxSpec("Harmonic Positions")),
    )

    class_node_theme_templates = []
    class_button_theme_templates = []

    def __init__(self, node_tag):
        super().__init__(node_tag)
        self._processor = GuitarFretboardProcessor()

        self.in_midi = self._register_input("in_midi", DataType.MIDI)

        self.drawlist_tag = f"fretboard_draw_{self.node_tag}"
        self.static_layer_tag = f"fretboard_static_{self.node_tag}"
        self.dynamic_layer_tag = f"fretboard_dynamic_{self.node_tag}"
        self.canvas_tag = f"fretboard_canvas_{self.node_tag}"

        # Derived geometry inputs (kept in sync with the params above).
        self._open_strings = self.OPEN_STRINGS
        self._num_frets = int(round(self.num_frets))

        # Cached geometry (computed by _layout); per-frame drawing reads these.
        self._string_y: list[float] = []
        self._fret_x: list[float] = []      # fret_x[f] = x of fret line f (0 = nut)
        self._spacing = 0.0
        self._draw_w = self.panel_width
        self._draw_h = self.panel_height

        # Refresh bookkeeping: redraw the dynamic layer only when something actually changed.
        self._last_fp = None        # upstream MIDI fingerprint
        self._last_pos = -1.0       # last drawn playhead position (seconds)
        self._canvas_size = (0, 0)

    def get_param_signature(self) -> str:
        # Pure sink (nothing flows downstream), but a signature keeps presets/identity tidy.
        return (f"frets={int(round(self.num_frets))}|thr={int(round(self.bright_threshold))}|"
            f"pitch={int(round(self.pitch_min))}-{int(round(self.pitch_max))}|"
                f"fmode={int(round(self.fingering_mode))}|harm={int(self.harmonic_mode)}|"
                f"pref={int(round(self.prefer_position))}|allpos={int(self.show_all_positions)}|"
                f"open={int(self.allow_open)}")

    def apply_params(self, params: dict):
        # Restore preset values, then re-derive geometry and rebuild the board if it is open.
        super().apply_params(params)
        self._num_frets = max(1, int(round(self.num_frets)))
        self._processor.reset_hand()
        # The base mirror pushes each numeric value into "<name>_panel_slider_tag"; that works for
        # the sliders/checkbox, but the fingering-mode widget is a radio whose value is a LABEL, so
        # sync it by hand from the restored numeric mode.
        self._sync_fingering_mode_control()
        self._rebuild_board()

    # ----- Parameter callbacks ----- #

    def _invalidate_dynamic(self):
        self._last_pos = -1.0  # force a marker redraw

    def _on_fingering_mode(self, sender, value):
        mode = 1.0 if value == self.MODE_SUGGEST else 0.0
        self._parameter_ui.apply_declared("fingering_mode", mode)

    def _on_fingering_mode_changed(self):
        self._processor.reset_hand()
        self._last_pos = -1.0

    def _sync_fingering_mode_control(self):
        if dpg.does_item_exist(self.fingering_mode_panel_slider_tag):
            dpg.set_value(
                self.fingering_mode_panel_slider_tag,
                self.MODE_SUGGEST if self._auto_mode() else self.MODE_ALL,
            )

    def _project_declared_param(self, name: str, value, *, notify=True) -> bool:
        projected = super()._project_declared_param(name, value, notify=notify)
        if projected and name == "fingering_mode":
            self._sync_fingering_mode_control()
        return projected

    def _sync_control_widgets(self):
        super()._sync_control_widgets()
        self._sync_fingering_mode_control()

    def _auto_mode(self) -> bool:
        return int(round(self.fingering_mode)) == 1

    def _on_frets_changed(self):
        self._num_frets = max(1, int(round(self.num_frets)))
        self._rebuild_board()

    def _fretboard_parameters(self):
        return FretboardParameters(
            num_frets=self._num_frets,
            prefer_position=self.prefer_position,
            allow_open=self.allow_open,
        )

    def _rebuild_board(self):
        # Recompute geometry + repaint the static board after a geometry/reference change.
        if not dpg.does_item_exist(self.drawlist_tag):
            return
        self._layout()
        self._draw_static()
        self._last_pos = -1.0

    # ----- Node body ----- #

    def _draw_node_internal(self):
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input, tag=self.in_midi):
            self._draw_pin(
                "MIDI In", is_output=False,
                pin_theme=self.get_global_button_theme("default_input_pin"), label_width=85)

    # ----- Panel ----- #

    # Gap between logical control groups (matches the transport bar's grouping style).
    _GROUP_GAP = 24

    def _draw_panel(self):
        with dpg.child_window(width=-1, height=100, border=False, no_scrollbar=True):
            with centered_surface(tag=f"fretboard_controls_surface_{self.node_tag}"):
                self._draw_controls()
        with canvas_surface(tag=self.canvas_tag):
            with dpg.drawlist(width=self._draw_w, height=self._draw_h, tag=self.drawlist_tag):
                dpg.add_draw_layer(tag=self.static_layer_tag)
                dpg.add_draw_layer(tag=self.dynamic_layer_tag)

        self._layout()
        self._draw_static()

        # Force a full refresh on the next frame now that the layers exist again.
        self._last_fp = None
        self._last_pos = -1.0

    def _draw_controls(self):
        # Four control groups laid out left-to-right, separated by a fixed gap:
        #   1) board geometry (fret count) + note brightness
        #   2) fingering mode (all positions vs one suggested hand shape)
        #   3) suggested-shape options (preferred position, allow open strings)
        #   4) note overlays (harmonic hints, show alternatives)
        with dpg.group(horizontal=True):
            dpg.add_spacer(width=self._GROUP_GAP)

            with dpg.group():
                self._draw_declared_param("num_frets", scope="panel")
                self._draw_declared_param("bright_threshold", scope="panel")

            dpg.add_spacer(width=self._GROUP_GAP)

            with dpg.group():
                self._draw_declared_param("pitch_min", scope="panel")
                self._draw_declared_param("pitch_max", scope="panel")

            dpg.add_spacer(width=self._GROUP_GAP)

            with dpg.group():
                dpg.add_radio_button(
                    (self.MODE_ALL, self.MODE_SUGGEST),
                    default_value=self.MODE_SUGGEST if self._auto_mode() else self.MODE_ALL,
                    tag=self.fingering_mode_panel_slider_tag, callback=self._on_fingering_mode)

            dpg.add_spacer(width=self._GROUP_GAP)

            with dpg.group():
                self._draw_declared_param("prefer_position", scope="panel")
                self._draw_declared_param("allow_open", scope="panel")

            dpg.add_spacer(width=self._GROUP_GAP)

            with dpg.group():
                self._draw_declared_param("harmonic_mode", scope="panel")
                self._draw_declared_param("show_all_positions", scope="panel")

    def _layout(self):
        # Recompute string/fret pixel positions for the current drawlist size.
        w, h = self._draw_w, self._draw_h
        nut_x = self.PAD_L
        board_right = w - self.PAD_R
        self._spacing = max(8.0, (board_right - nut_x) / self._num_frets)
        self._fret_x = [nut_x + f * self._spacing for f in range(self._num_frets + 1)]

        n = len(self._open_strings)
        usable_h = max(1.0, h - self.PAD_T - self.PAD_B)
        self._string_y = [self.PAD_T + i * (usable_h / (n - 1)) for i in range(n)]

    def _fret_center_x(self, fret: int) -> float:
        # Where a pressed note sits: open (0) just left of the nut; fretted between two frets.
        if fret <= 0:
            return self._fret_x[0] - self._spacing * 0.42
        return (self._fret_x[fret - 1] + self._fret_x[fret]) * 0.5

    def _draw_static(self):
        layer = self.static_layer_tag
        dpg.delete_item(layer, children_only=True)

        y_top = self._string_y[0]
        y_bot = self._string_y[-1]
        board_left = self._fret_x[0]
        board_right = self._fret_x[-1]
        mid_y = (y_top + y_bot) * 0.5

        # Board background.
        dpg.draw_rectangle(
            (board_left, y_top - 8), (board_right, y_bot + 8),
            color=(60, 45, 35, 255), fill=(46, 34, 26, 255), parent=layer)

        # Inlay dots (single centred; double split above/below centre).
        dot_color = (110, 110, 120, 200)
        for f in self.INLAY_SINGLE:
            if f <= self._num_frets:
                dpg.draw_circle((self._fret_center_x(f), mid_y), 5, color=dot_color, fill=dot_color, parent=layer)
        for f in self.INLAY_DOUBLE:
            if f <= self._num_frets:
                cx = self._fret_center_x(f)
                dpg.draw_circle((cx, mid_y - (y_bot - y_top) * 0.22), 5, color=dot_color, fill=dot_color, parent=layer)
                dpg.draw_circle((cx, mid_y + (y_bot - y_top) * 0.22), 5, color=dot_color, fill=dot_color, parent=layer)

        # Frets: the nut (fret 0) is thick; the rest are thin.
        for f, x in enumerate(self._fret_x):
            dpg.draw_line((x, y_top - 8), (x, y_bot + 8),
                          color=(200, 200, 210, 255) if f == 0 else (120, 120, 130, 255),
                          thickness=4 if f == 0 else 1, parent=layer)
            # Fret numbers under the board at the inlay positions.
            if f in self.INLAY_SINGLE or f in self.INLAY_DOUBLE:
                dpg.draw_text((self._fret_center_x(f) - 4, y_bot + 12), str(f),
                              size=13, color=(150, 150, 160, 255), parent=layer)

        # Strings + open-string name labels.
        for i, y in enumerate(self._string_y):
            dpg.draw_line((board_left, y), (board_right, y), color=(170, 170, 180, 255), thickness=1.5, parent=layer)
            name = note_name(self._open_strings[i])
            dpg.draw_text((8, y - 8), name, size=15, color=(200, 200, 210, 255), parent=layer)

    # ----- Per-frame update ----- #

    def on_gui_frame(self):
        if not dpg.does_item_exist(self.drawlist_tag):
            return

        # Resize: if the panel window changed size, rebuild the drawlist + static board.
        if dpg.does_item_exist(self.canvas_tag):
            w, h = dpg.get_item_rect_size(self.canvas_tag)
            if (w, h) != self._canvas_size and w > 0 and h > 0:
                self._canvas_size = (w, h)
                self._draw_w = max(360, int(w))
                self._draw_h = max(160, int(h))
                dpg.configure_item(self.drawlist_tag, width=self._draw_w, height=self._draw_h)
                self._layout()
                self._draw_static()
                self._last_pos = -1.0  # force a marker redraw against the new geometry

        packet = self.input_values.get(self.in_midi)
        fp = packet.fingerprint if packet is not None else None
        # Use SOURCE-timeline position (what is actually being heard) so the markers stay in
        # sync with the audio at any playback rate; position_seconds follows the stretched
        # OUTPUT timeline and would drift whenever the playback speed is not 1.0x.
        pos = TRANSPORT.source_position_seconds

        # Nothing moved and the notes are unchanged -> keep the current frame.
        if fp == self._last_fp and abs(pos - self._last_pos) < 1e-4:
            return
        self._last_fp = fp
        self._last_pos = pos

        notes = packet.data if packet is not None else None
        self._draw_active(notes, pos)

    def _draw_active(self, notes, t: float):
        layer = self.dynamic_layer_tag
        dpg.delete_item(layer, children_only=True)
        if len(self._string_y) < 2:
            return

        pitch_min = int(round(self.pitch_min))
        pitch_max = int(round(self.pitch_max))
        self._draw_pitch_filter_overlay(layer, pitch_min, pitch_max)
        if notes is None or len(notes) == 0:
            return

        active = self._processor.active_notes(notes, t)
        active = {
            pitch: velocity
            for pitch, velocity in active.items()
            if pitch_min <= pitch <= pitch_max
        }
        if not active:
            return

        radius = max(3.0, min(self._spacing, self._string_y[1] - self._string_y[0]) * 0.30)
        if self._auto_mode():
            self._draw_auto(layer, active, radius)
        else:
            self._draw_all_positions(layer, active, radius)

    # ----- Rendering primitives ----- #

    def _pitch_boundary_x(self, pitch: int, open_pitch: int, *, upper: bool):
        fret = pitch - open_pitch
        if fret <= 0:
            return self._fret_x[0]
        if fret > self._num_frets:
            return self._fret_x[-1]
        return self._fret_x[fret if upper else fret - 1]

    def _draw_pitch_filter_overlay(self, layer, pitch_min: int, pitch_max: int):
        string_gap = self._string_y[1] - self._string_y[0]
        board_top = self._string_y[0] - 8
        board_bottom = self._string_y[-1] + 8
        for string_index, open_pitch in enumerate(self._open_strings):
            y1 = max(board_top, self._string_y[string_index] - string_gap * 0.5)
            y2 = min(board_bottom, self._string_y[string_index] + string_gap * 0.5)
            for fret in range(1, self._num_frets + 1):
                pitch = open_pitch + fret
                if pitch_min <= pitch <= pitch_max:
                    continue
                dpg.draw_rectangle(
                    (self._fret_x[fret - 1], y1),
                    (self._fret_x[fret], y2),
                    color=(0, 0, 0, 0), fill=(0, 0, 0, 145), parent=layer)
            min_x = self._pitch_boundary_x(pitch_min, open_pitch, upper=False)
            max_x = self._pitch_boundary_x(pitch_max, open_pitch, upper=True)
            dpg.draw_line(
                (min_x, y1), (min_x, y2), color=self.MIN_PITCH_LINE_COLOR,
                thickness=3, parent=layer)
            dpg.draw_line(
                (max_x, y1), (max_x, y2), color=self.MAX_PITCH_LINE_COLOR,
                thickness=3, parent=layer)

    def _note_rgba(self, pitch: int, velocity: int):
        r, g, b = self.PITCH_COLORS[pitch % 12]
        # Velocity -> opacity: notes at/above the brightness threshold are fully bright; softer
        # notes scale down linearly (threshold is a velocity value 1..127).
        norm = min(1.0, max(0.0, velocity / max(1.0, self.bright_threshold)))
        return r, g, b, int(round(12 + norm * 243))

    def _position_passes_pitch_filter(self, string_index: int, fret: int) -> bool:
        position_pitch = self._open_strings[string_index] + fret
        return int(round(self.pitch_min)) <= position_pitch <= int(round(self.pitch_max))

    def _draw_press(self, layer, i: int, fret: int, rgba, label: str, radius: float):
        if not self._position_passes_pitch_filter(i, fret):
            return
        # Filled dot (a fretted/played note). No outline: border colour == fill.
        cx = self._fret_center_x(fret)
        cy = self._string_y[i]
        dpg.draw_circle((cx, cy), radius, color=rgba, fill=rgba, parent=layer)
        if label:
            dpg.draw_text((cx - len(label) * 3.6, cy - 7), label, size=13,
                          color=(255, 255, 255, rgba[3]), parent=layer)

    def _draw_ring(self, layer, i: int, fret: int, rgba, radius: float):
        if not self._position_passes_pitch_filter(i, fret):
            return
        # Hollow circle (a playable position that isn't the emphasised one -- the L1 backdrop).
        cx = self._fret_center_x(fret)
        cy = self._string_y[i]
        dpg.draw_circle((cx, cy), radius, color=rgba, fill=(0, 0, 0, 0), thickness=2, parent=layer)

    def _draw_harmonic(self, layer, i: int, fret: int, rgba, radius: float):
        if not self._position_passes_pitch_filter(i, fret):
            return
        # Hollow diamond (a natural-harmonic position).
        cx = self._fret_center_x(fret)
        cy = self._string_y[i]
        hr = radius * 1.6
        # Repeat the first vertex at the end: draw_polygon connects consecutive points but does
        # NOT close the loop, so without this the diamond is missing one edge.
        pts = [(cx, cy - hr), (cx + hr, cy), (cx, cy + hr), (cx - hr, cy), (cx, cy - hr)]
        dpg.draw_polygon(pts, color=rgba, fill=(0, 0, 0, 0), thickness=2, parent=layer)

    def _draw_hand_mark(self, layer, i: int, fret: int, radius: float):
        if not self._position_passes_pitch_filter(i, fret):
            return
        # A white "U" opening upward, cradling the dot from below = a finger pressing the string.
        cx = self._fret_center_x(fret)
        cy = self._string_y[i]
        r = radius * 1.5
        # Left wall up -> round the bottom -> right wall up (open at the top).
        pts = [(cx - r, cy - r), (cx - r, cy + r * 0.4), (cx - r * 0.5, cy + r),
               (cx + r * 0.5, cy + r), (cx + r, cy + r * 0.4), (cx + r, cy - r)]
        dpg.draw_polyline(pts, color=(255, 255, 255, 255), thickness=2, parent=layer)

    def _draw_harmonics_for(self, layer, pitch: int, rgba, radius: float):
        # Draw the natural-harmonic diamonds for a pitch, if the harmonic overlay is on.
        if self.harmonic_mode:
            for i, fret in self._processor.harmonic_positions(
                    pitch, self._fretboard_parameters()):
                self._draw_harmonic(layer, i, fret, rgba, radius)

    # ----- All Positions: mark every playable spot for every sounding note ----- #

    def _draw_all_positions(self, layer, active: dict, radius: float):
        for pitch, vel in active.items():
            rgba = self._note_rgba(pitch, vel)
            label = note_name(pitch)
            for i, fret in self._processor.press_positions(
                    pitch, self._fretboard_parameters(), allow_open=True):
                self._draw_press(layer, i, fret, rgba, label, radius)
            self._draw_harmonics_for(layer, pitch, rgba, radius)

    # ----- Auto Fingering: emphasise a hand window; never hide a sounding pitch ----- #

    def _draw_auto(self, layer, active: dict, radius: float):
        layers = self._processor.assign_layers(active, self._fretboard_parameters())

        # Advance the smoothed hand position toward the recommended window.
        self._processor.advance_hand(layers.anchor)

        # L1: faint hollow backdrop of every playable position (fixed alpha, not velocity-scaled).
        if self.show_all_positions:
            for pitch, i, fret in layers.all_positions:
                r, g, b = self.PITCH_COLORS[pitch % 12]
                self._draw_ring(layer, i, fret, (r, g, b, self.L1_ALPHA), radius)

        # L2: one emphasised solid dot per unique pitch (velocity-scaled alpha), with its name.
        for pitch, i, fret in layers.emphasized:
            rgba = self._note_rgba(pitch, active.get(pitch, 127))
            self._draw_press(layer, i, fret, rgba, note_name(pitch), radius)
            self._draw_harmonics_for(layer, pitch, rgba, radius)

        # L3: white finger "U" on the emphasised positions that form the recommended hand shape.
        for pitch, i, fret in layers.hand_shape:
            self._draw_hand_mark(layer, i, fret, radius)

    def publish_outputs(self) -> bool:
        # Pure sink: visualizes the upstream MIDI; nothing flows downstream.
        return True
