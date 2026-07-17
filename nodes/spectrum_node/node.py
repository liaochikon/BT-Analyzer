import dearpygui.dearpygui as dpg
from core import *
from nodes._shared.spectrum import RollingSpectrum
from ui import i18n
from ui.surfaces import canvas_surface, centered_surface


class SpectrumNode(BaseNode):
    display_name = "Spectrum"
    category = "Display"
    config = NodeConfig(
        processing=NodeProcessingMode.STREAMING,
        ui=NodeUIMode.INSPECTOR_PANEL,
        realtime=True,
    )

    panel_width = 1200
    panel_height = 400

    DB_FLOOR = -90.0  # bottom of the magnitude axis, in dBFS

    class_node_theme_templates = []
    class_button_theme_templates = []

    def __init__(self, node_tag):
        super().__init__(node_tag)

        self.in_audio = self._register_input("in_audio", DataType.AUDIO)
        self.out_audio = self._register_output("out_audio", DataType.AUDIO)

        # Rolling-window FFT shared with the EQ node (see nodes._shared.spectrum). A large window
        # keeps low-frequency resolution fine (44100/8192 ~= 5.4 Hz/bin) even with short blocks.
        self._spectrum = RollingSpectrum(GLOBAL_SAMPLE_RATE, fft_size=8192, db_floor=self.DB_FLOOR)

        self.plot_tag = f"spec_plot_{self.node_tag}"
        self.x_axis_tag = f"spec_x_{self.node_tag}"
        self.y_axis_tag = f"spec_y_{self.node_tag}"
        self.series_tag = f"spec_series_{self.node_tag}"
        self.spec_line_tag = f"spec_line_{self.node_tag}"
        self.octave_lines_tag = f"spec_octave_lines_{self.node_tag}"
        self.canvas_tag = f"spec_canvas_{self.node_tag}"
        self.empty_surface_tag = f"spec_empty_{self.node_tag}"
        self.fill_theme_tag = f"spec_fill_theme_{self.node_tag}"
        self.line_theme_tag = f"spec_line_theme_{self.node_tag}"
        self.octave_theme_tag = f"spec_octave_theme_{self.node_tag}"

    def _draw_node_internal(self):
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input, tag=self.in_audio):
            self._draw_pin("In", is_output=False, pin_theme=self.get_global_button_theme("default_input_pin"))
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output, tag=self.out_audio):
            self._draw_pin("Out", is_output=True, pin_theme=self.get_global_button_theme("default_output_pin"))

    def _draw_panel(self):
        # C note frequencies used as x-axis tick labels and guide lines
        _C_NOTES = [
            ("C1", 32.70), ("C2", 65.41), ("C3", 130.81), ("C4", 261.63),
            ("C5", 523.25), ("C6", 1046.50), ("C7", 2093.00), ("C8", 4186.01),
            ("C9", 8372.02), ("C10", 16744.04),
        ]
        visible = [(lbl, f) for lbl, f in _C_NOTES if 20.0 <= f <= 20000.0]
        _hz_label = lambda f: f"{int(round(f))}Hz" if f < 1000.0 else f"{f / 1000.0:.1f}kHz"
        axis_ticks = tuple((f"{lbl} ({_hz_label(f)})", f if f < 10000.0 else (f - 1500)) for lbl, f in visible) # add a small offset to lables above 10kHz to prevent overlap with the right edge of the plot

        with canvas_surface(tag=self.canvas_tag):
            with dpg.plot(
                height=-1, width=-1, tag=self.plot_tag, show=False,
                no_menus=True, no_box_select=True, no_mouse_pos=True):
                dpg.add_plot_axis(
                    dpg.mvXAxis, scale=dpg.mvPlotScale_Log10, no_gridlines=True, tag=self.x_axis_tag)
                dpg.set_axis_limits(self.x_axis_tag, 20.0, 20000.0)
                dpg.set_axis_ticks(self.x_axis_tag, axis_ticks)
                dpg.add_plot_axis(
                    dpg.mvYAxis, label="Magnitude (dBFS)", tag=self.y_axis_tag)
                dpg.set_axis_limits(self.y_axis_tag, self.DB_FLOOR, 0.0)
                dpg.add_shade_series(
                    [20.0, 20000.0], [-90.0, -90.0], y2=[-90.0, -90.0],
                    parent=self.y_axis_tag, tag=self.series_tag)
                if not dpg.does_item_exist(self.fill_theme_tag):
                    with dpg.theme(tag=self._track_ui_resource(self.fill_theme_tag)):
                        with dpg.theme_component(dpg.mvShadeSeries):
                            dpg.add_theme_color(
                                dpg.mvPlotCol_Fill, (90, 160, 160, 55),
                                category=dpg.mvThemeCat_Plots)
                dpg.bind_item_theme(self.series_tag, self.fill_theme_tag)

                # Bright outline line on top of the fill (matches eq_node curve style)
                dpg.add_line_series([], [], parent=self.y_axis_tag, tag=self.spec_line_tag)
                if not dpg.does_item_exist(self.line_theme_tag):
                    with dpg.theme(tag=self._track_ui_resource(self.line_theme_tag)):
                        with dpg.theme_component(dpg.mvLineSeries):
                            dpg.add_theme_color(
                                dpg.mvPlotCol_Line, (120, 210, 255, 255),
                                category=dpg.mvThemeCat_Plots)
                            dpg.add_theme_style(
                                dpg.mvPlotStyleVar_LineWeight, 1.0,
                                category=dpg.mvThemeCat_Plots)
                dpg.bind_item_theme(self.spec_line_tag, self.line_theme_tag)

                # Faint vertical guide lines at each C note
                dpg.add_inf_line_series(
                    [f for _, f in visible], parent=self.y_axis_tag, tag=self.octave_lines_tag)
                if not dpg.does_item_exist(self.octave_theme_tag):
                    with dpg.theme(tag=self._track_ui_resource(self.octave_theme_tag)):
                        with dpg.theme_component(dpg.mvInfLineSeries):
                            dpg.add_theme_color(
                                dpg.mvPlotCol_Line, (200, 200, 200, 75),
                                category=dpg.mvThemeCat_Plots)
                            dpg.add_theme_style(
                                dpg.mvPlotStyleVar_LineWeight, 1.0,
                                category=dpg.mvThemeCat_Plots)
                dpg.bind_item_theme(self.octave_lines_tag, self.octave_theme_tag)
            with centered_surface(tag=self.empty_surface_tag):
                dpg.add_text(i18n.tr("spectrum_empty"), color=[150, 150, 150])

    def build_stream_renderer(self, out_pin, input_renderers):
        # Transparent tap: roll each block into the FFT buffer, pass it through unchanged.
        return self._wrap_single_input_tap(input_renderers, self._spectrum.push)

    def on_gui_frame(self):
        # GUI thread: compute the log-frequency spectrum over the rolling buffer and update the
        # shade series. Every octave carries equal visual weight on the log-scale x-axis.
        if not dpg.does_item_exist(self.series_tag):
            return
        snapshot = self._spectrum.snapshot(n_points=200, f_min=20.0, f_max=20000.0)
        if snapshot is None:
            dpg.configure_item(self.plot_tag, show=False)
            dpg.configure_item(self.empty_surface_tag, show=True)
            return
        dpg.configure_item(self.plot_tag, show=True)
        dpg.configure_item(self.empty_surface_tag, show=False)
        fx = list(snapshot.frequencies)
        fy_list = list(snapshot.levels_db)
        dpg.set_value(self.series_tag, [fx, fy_list, [self.DB_FLOOR] * len(fx)])
        if dpg.does_item_exist(self.spec_line_tag):
            dpg.set_value(self.spec_line_tag, [fx, fy_list])

    def publish_outputs(self) -> bool:
        # Streamable passthrough: audio is produced live per block; publish a fingerprint
        # passthrough so staleness propagates downstream.
        return self._publish_stream_passthrough(self.out_audio)
