import dearpygui.dearpygui as dpg
from core import *
from nodes._shared.spectrum import RollingSpectrum
from nodes.eq_node.processor import EqParameters, EqProcessor


class EqNode(BaseNode):
    display_name = "EQ"
    category = "Effects"
    config = NodeConfig(
        processing=NodeProcessingMode.STREAMING,
        ui=NodeUIMode.INSPECTOR,
        realtime=True,
    )

    panel_width = 460
    panel_height = 600

    class_node_theme_templates = []
    class_button_theme_templates = []
    parameter_specs = (
        ParameterSpec("low_gain", 0.0, signature_precision=2, on_change="_refresh_curve"),
        ParameterSpec("low_freq", 100.0, signature_precision=1, on_change="_refresh_curve"),
        ParameterSpec("mid_gain", 0.0, signature_precision=2, on_change="_refresh_curve"),
        ParameterSpec("mid_freq", 1000.0, signature_precision=1, on_change="_refresh_curve"),
        ParameterSpec("mid_q", 1.0, signature_precision=2,
                      coerce=lambda value: max(0.1, float(value)),
                      on_change="_refresh_curve"),
        ParameterSpec("high_gain", 0.0, signature_precision=2, on_change="_refresh_curve"),
        ParameterSpec("high_freq", 8000.0, signature_precision=1, on_change="_refresh_curve"),
    )
    parameter_control_specs = (
        ParameterControlSpec("low_gain", SliderSpec("Gain", -24.0, 24.0, format="%.1f dB")),
        ParameterControlSpec("low_freq", SliderSpec("Freq", 20.0, 500.0, format="%.0f Hz")),
        ParameterControlSpec("mid_gain", SliderSpec("Gain", -24.0, 24.0, format="%.1f dB")),
        ParameterControlSpec("mid_freq", SliderSpec("Freq", 200.0, 5000.0, format="%.0f Hz")),
        ParameterControlSpec("mid_q", SliderSpec("Q", 0.3, 8.0, format="%.2f")),
        ParameterControlSpec("high_gain", SliderSpec("Gain", -24.0, 24.0, format="%.1f dB")),
        ParameterControlSpec("high_freq", SliderSpec("Freq", 2000.0, 16000.0, format="%.0f Hz")),
    )

    def __init__(self, node_tag):
        super().__init__(node_tag)
        self._processor = EqProcessor()

        self.in_audio = self._register_input("in_audio", DataType.AUDIO)
        self.out_audio = self._register_output("out_audio", DataType.AUDIO)

        # Rolling-window FFT for the live output spectrum shaded behind the EQ curve, shared with
        # the Spectrum node (see nodes._shared.spectrum). db_floor matches the spectrum y-axis.
        self._spectrum = RollingSpectrum(GLOBAL_SAMPLE_RATE, fft_size=8192, db_floor=-90.0)

        self.resp_plot_tag = f"eq_resp_plot_{self.node_tag}"
        self.resp_x_tag = f"eq_resp_x_{self.node_tag}"
        self.resp_y_eq_tag = f"eq_resp_y_eq_{self.node_tag}"
        self.resp_y_spec_tag = f"eq_resp_y_spec_{self.node_tag}"
        self.curve_line_tag = f"eq_curve_line_{self.node_tag}"
        self.curve_fill_tag = f"eq_curve_fill_{self.node_tag}"
        self.markers_tag = f"eq_markers_{self.node_tag}"
        self.spec_series_tag = f"eq_spec_{self.node_tag}"

    def get_param_signature(self) -> str:
        return (f"low={self.low_gain:.2f}@{self.low_freq:.1f},"
                f"mid={self.mid_gain:.2f}@{self.mid_freq:.1f}/{self.mid_q:.2f},"
                f"high={self.high_gain:.2f}@{self.high_freq:.1f}")

    def _parameters(self):
        return EqParameters(
            low_gain=self.low_gain,
            low_freq=self.low_freq,
            mid_gain=self.mid_gain,
            mid_freq=self.mid_freq,
            mid_q=self.mid_q,
            high_gain=self.high_gain,
            high_freq=self.high_freq,
        )

    def _draw_node_internal(self):
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input, tag=self.in_audio):
            self._draw_pin("In", is_output=False, pin_theme=self.get_global_button_theme("default_input_pin"))
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output, tag=self.out_audio):
            self._draw_pin("Out", is_output=True, pin_theme=self.get_global_button_theme("default_output_pin"))

        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
            dpg.add_text("Low shelf", color=[150, 180, 210])
            self._draw_declared_param("low_gain")
            self._draw_declared_param("low_freq")

            dpg.add_text("Mid peak", color=[150, 180, 210])
            self._draw_declared_param("mid_gain")
            self._draw_declared_param("mid_freq")
            self._draw_declared_param("mid_q")

            dpg.add_text("High shelf", color=[150, 180, 210])
            self._draw_declared_param("high_gain")
            self._draw_declared_param("high_freq")

    def _draw_inspector(self):
        # FL-style response view: log-frequency x-axis, a filled EQ magnitude curve with band
        # markers on the front gain axis, and a live output spectrum shaded behind it.
        with dpg.plot(
                height=240, width=-1, tag=self.resp_plot_tag,
                no_menus=True, no_box_select=True, no_mouse_pos=True):
            dpg.add_plot_axis(
                dpg.mvXAxis, scale=dpg.mvPlotScale_Log10, label="Frequency (Hz)", tag=self.resp_x_tag)
            dpg.set_axis_limits(self.resp_x_tag, 20.0, 20000.0)

            dpg.add_plot_axis(dpg.mvYAxis, label="Gain (dB)", tag=self.resp_y_eq_tag)
            dpg.set_axis_limits(self.resp_y_eq_tag, -24.0, 24.0)

            dpg.add_plot_axis(
                dpg.mvYAxis, no_tick_labels=True, no_gridlines=True, tag=self.resp_y_spec_tag)
            dpg.set_axis_limits(self.resp_y_spec_tag, -90.0, 0.0)
            dpg.add_shade_series(
                [20.0, 20000.0], [-90.0, -90.0], y2=[-90.0, -90.0],
                parent=self.resp_y_spec_tag, tag=self.spec_series_tag)

            dpg.add_shade_series([], [], y2=[], parent=self.resp_y_eq_tag, tag=self.curve_fill_tag)
            dpg.add_line_series([], [], parent=self.resp_y_eq_tag, tag=self.curve_line_tag)
            dpg.add_scatter_series([], [], parent=self.resp_y_eq_tag, tag=self.markers_tag)

            with dpg.theme() as spec_theme:
                with dpg.theme_component(dpg.mvShadeSeries):
                    dpg.add_theme_color(dpg.mvPlotCol_Fill, (90, 160, 160, 55), category=dpg.mvThemeCat_Plots)
            dpg.bind_item_theme(self.spec_series_tag, self._track_ui_resource(spec_theme))
            with dpg.theme() as fill_theme:
                with dpg.theme_component(dpg.mvShadeSeries):
                    dpg.add_theme_color(dpg.mvPlotCol_Fill, (90, 200, 255, 55), category=dpg.mvThemeCat_Plots)
            dpg.bind_item_theme(self.curve_fill_tag, self._track_ui_resource(fill_theme))
            with dpg.theme() as line_theme:
                with dpg.theme_component(dpg.mvLineSeries):
                    dpg.add_theme_color(dpg.mvPlotCol_Line, (120, 210, 255, 255), category=dpg.mvThemeCat_Plots)
                    dpg.add_theme_style(dpg.mvPlotStyleVar_LineWeight, 2.0, category=dpg.mvThemeCat_Plots)
            dpg.bind_item_theme(self.curve_line_tag, self._track_ui_resource(line_theme))
            with dpg.theme() as marker_theme:
                with dpg.theme_component(dpg.mvScatterSeries):
                    dpg.add_theme_color(dpg.mvPlotCol_MarkerFill, (255, 220, 90, 255), category=dpg.mvThemeCat_Plots)
                    dpg.add_theme_color(dpg.mvPlotCol_MarkerOutline, (255, 220, 90, 255), category=dpg.mvThemeCat_Plots)
                    dpg.add_theme_style(dpg.mvPlotStyleVar_MarkerSize, 5, category=dpg.mvThemeCat_Plots)
            dpg.bind_item_theme(self.markers_tag, self._track_ui_resource(marker_theme))

        dpg.add_spacer(height=8)
        dpg.add_text("Low shelf", color=[150, 180, 210])
        self._draw_declared_param("low_gain", secondary=True, width=320)
        self._draw_declared_param("low_freq", secondary=True, width=320)

        dpg.add_text("Mid peak", color=[150, 180, 210])
        self._draw_declared_param("mid_gain", secondary=True, width=320)
        self._draw_declared_param("mid_freq", secondary=True, width=320)
        self._draw_declared_param("mid_q", secondary=True, width=320)

        dpg.add_text("High shelf", color=[150, 180, 210])
        self._draw_declared_param("high_gain", secondary=True, width=320)
        self._draw_declared_param("high_freq", secondary=True, width=320)

        self._refresh_curve()

    def _compute_response(self):
        return self._processor.response(
            parameters=self._parameters(), sample_rate=GLOBAL_SAMPLE_RATE)

    def _refresh_curve(self):
        if not dpg.does_item_exist(self.curve_line_tag):
            return
        freqs, db = self._compute_response()
        fx, fy = freqs.tolist(), db.tolist()
        dpg.set_value(self.curve_line_tag, [fx, fy])
        dpg.set_value(self.curve_fill_tag, [fx, fy, [0.0] * len(fx)])
        dpg.set_value(self.markers_tag, [
            [self.low_freq, self.mid_freq, self.high_freq],
            [self.low_gain, self.mid_gain, self.high_gain]])

    def on_gui_frame(self):
        # Live output spectrum shaded behind the EQ curve, over a large rolling window so the low
        # end (20-100 Hz) has enough resolution. Log-resampled to match the plot's log x-axis.
        if not dpg.does_item_exist(self.spec_series_tag):
            return
        snapshot = self._spectrum.snapshot(n_points=200, f_min=20.0, f_max=20000.0)
        if snapshot is None:
            return
        fx = list(snapshot.frequencies)
        dpg.set_value(
            self.spec_series_tag,
            [fx, list(snapshot.levels_db), [-90.0] * len(fx)],
        )

    def _process_block(self, block):
        out = self._processor.process_block(
            block, parameters=self._parameters(), sample_rate=GLOBAL_SAMPLE_RATE)
        self._spectrum.push(out)
        return out

    def build_stream_renderer(self, out_pin, input_renderers):
        return self._wrap_single_input(input_renderers, self._process_block)

    def publish_outputs(self) -> bool:
        # Streamable: audio is produced live per block; publish a fingerprint passthrough.
        return self._publish_stream_passthrough(self.out_audio)
