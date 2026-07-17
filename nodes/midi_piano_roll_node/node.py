import math
import dearpygui.dearpygui as dpg
from core import *
from nodes._shared.midi_draw import note_name
from ui import i18n
from ui.surfaces import canvas_surface, centered_surface


class MidiPianoRollNode(BaseNode):
    display_name = "MIDI Piano Roll"
    category = "Display"
    config = NodeConfig(ui=NodeUIMode.INSPECTOR_PANEL, realtime=True)

    panel_width = 640
    panel_height = 360

    LINE_WEIGHT = 6  # note-bar thickness in pixels

    class_node_theme_templates = []
    class_button_theme_templates = []

    def __init__(self, node_tag):
        super().__init__(node_tag)

        self.in_midi = self._register_input("in_midi", DataType.MIDI)

        self.plot_tag = f"piano_roll_plot_{self.node_tag}"
        self.x_axis_tag = f"piano_roll_x_{self.node_tag}"
        self.y_axis_tag = f"piano_roll_y_{self.node_tag}"
        self.series_tag = f"piano_roll_series_{self.node_tag}"
        self.series_theme_tag = f"piano_roll_theme_{self.node_tag}"
        self.canvas_tag = f"piano_roll_canvas_{self.node_tag}"
        self.empty_surface_tag = f"piano_roll_empty_{self.node_tag}"

        self._last_fp = None  # fingerprint of the last-drawn note set; refresh only on change

    def _draw_node_internal(self):
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input, tag=self.in_midi):
            self._draw_pin(
                "MIDI In", is_output=False,
                pin_theme=self.get_global_button_theme("default_input_pin"), label_width=85)

    def _draw_panel(self):
        # A plot fills the panel (width/height = -1, so it follows window resize) and provides
        # native pan (drag) and zoom (scroll; per-axis when hovering an axis). Notes are drawn
        # as thick horizontal line segments, broken apart by NaN gaps in a single series.
        with canvas_surface(tag=self.canvas_tag):
            with dpg.plot(height=-1, width=-1, tag=self.plot_tag, show=False):
                dpg.add_plot_axis(dpg.mvXAxis, label="Time (s)", tag=self.x_axis_tag)
                dpg.add_plot_axis(dpg.mvYAxis, label="Pitch", tag=self.y_axis_tag)
                dpg.add_line_series([], [], parent=self.y_axis_tag, tag=self.series_tag)
            with centered_surface(tag=self.empty_surface_tag):
                dpg.add_text(i18n.tr("piano_roll_empty"), color=[150, 150, 150])

        # Bar thickness + colour live on a persistent top-level theme (not a child of the panel
        # window), created once and reused across panel open/close.
        if not dpg.does_item_exist(self.series_theme_tag):
            with dpg.theme(tag=self._track_ui_resource(self.series_theme_tag)):
                with dpg.theme_component(dpg.mvLineSeries):
                    dpg.add_theme_style(
                        dpg.mvPlotStyleVar_LineWeight, self.LINE_WEIGHT,
                        category=dpg.mvThemeCat_Plots)
                    dpg.add_theme_color(
                        dpg.mvPlotCol_Line, (90, 160, 220, 255),
                        category=dpg.mvThemeCat_Plots)
        dpg.bind_item_theme(self.series_tag, self.series_theme_tag)

        # Force a refresh on the next frame now that the series exists again.
        self._last_fp = None

    def on_gui_frame(self):
        # GUI thread: refresh the series only when the panel is open and the notes changed.
        if not dpg.does_item_exist(self.series_tag):
            return
        packet = self.input_values.get(self.in_midi)
        fp = packet.fingerprint if packet is not None else None
        if fp == self._last_fp:
            return
        self._last_fp = fp
        notes = packet.data if packet is not None else None
        self._update_series(notes)

    def _update_series(self, notes):
        # Each note becomes a horizontal segment [start, end] at y=pitch; a NaN point between
        # segments breaks the line so notes do not connect to one another.
        has_notes = notes is not None and len(notes) > 0
        dpg.configure_item(self.plot_tag, show=has_notes)
        dpg.configure_item(self.empty_surface_tag, show=not has_notes)
        xs, ys = [], []
        if has_notes:
            for n in notes:
                pitch = float(n["pitch"])
                xs.append(float(n["start"])); ys.append(pitch)
                xs.append(float(n["end"]));   ys.append(pitch)
                xs.append(math.nan);          ys.append(math.nan)
        dpg.set_value(self.series_tag, [xs, ys])

        # Pitch axis ticks: label each octave C across the (padded) note range.
        if has_notes:
            p_min = int(min(int(n["pitch"]) for n in notes)) - 2
            p_max = int(max(int(n["pitch"]) for n in notes)) + 2
        else:
            p_min, p_max = 48, 72
        ticks = tuple((note_name(p), p) for p in range(p_min, p_max + 1) if p % 12 == 0)
        if ticks:
            dpg.set_axis_ticks(self.y_axis_tag, ticks)

        # Frame the data once on (re)load; the user can then freely pan/zoom from there.
        dpg.fit_axis_data(self.x_axis_tag)
        dpg.fit_axis_data(self.y_axis_tag)

    def publish_outputs(self) -> bool:
        # Pure sink: visualizes the upstream MIDI; nothing flows downstream.
        return True
