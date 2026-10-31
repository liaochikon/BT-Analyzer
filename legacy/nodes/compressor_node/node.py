import numpy as np
import dearpygui.dearpygui as dpg
from core import *
from nodes._shared.level_meter import LevelHistoryMixin
from nodes.compressor_node.processor import (
    CompressorParameters, CompressorProcessor, CompressorTelemetry)


class CompressorNode(LevelHistoryMixin, BaseNode):
    display_name = "Compressor"
    category = "Effects"
    config = NodeConfig(
        processing=NodeProcessingMode.STREAMING,
        ui=NodeUIMode.INSPECTOR,
        realtime=True,
    )

    panel_width = 460
    panel_height = 380

    GR_METER_MAX = 24.0

    class_node_theme_templates = []
    class_button_theme_templates = []
    parameter_specs = (
        ParameterSpec("threshold_db", -18.0, signature_precision=2),
        ParameterSpec("ratio", 4.0, signature_precision=2,
                      coerce=lambda value: max(1.0, float(value))),
        ParameterSpec("makeup_db", 0.0, signature_precision=2),
        ParameterSpec("attack_ms", 10.0, signature_precision=2,
                      coerce=lambda value: max(0.1, float(value))),
        ParameterSpec("release_ms", 100.0, signature_precision=2,
                      coerce=lambda value: max(1.0, float(value))),
    )
    parameter_control_specs = (
        ParameterControlSpec("threshold_db", SliderSpec("Threshold", -60.0, 0.0, format="%.1f dB")),
        ParameterControlSpec("ratio", SliderSpec("Ratio", 1.0, 20.0)),
        ParameterControlSpec("makeup_db", SliderSpec("Makeup", 0.0, 24.0, format="%.1f dB")),
        ParameterControlSpec("attack_ms", SliderSpec("Attack", 0.1, 100.0)),
        ParameterControlSpec("release_ms", SliderSpec("Release", 5.0, 500.0)),
    )

    def __init__(self, node_tag):
        super().__init__(node_tag)
        self._processor = CompressorProcessor()

        self.in_audio = self._register_input("in_audio", DataType.AUDIO)
        self.out_audio = self._register_output("out_audio", DataType.AUDIO)

        self._telemetry = CompressorTelemetry()

        self.curve_plot_tag = f"comp_curve_plot_{self.node_tag}"
        self.curve_x_tag = f"comp_curve_x_{self.node_tag}"
        self.curve_y_tag = f"comp_curve_y_{self.node_tag}"
        self.ref_line_tag = f"comp_ref_line_{self.node_tag}"
        self.curve_line_tag = f"comp_curve_line_{self.node_tag}"
        self.point_tag = f"comp_point_{self.node_tag}"

        self.meter_bar_tag = f"comp_meter_bar_{self.node_tag}"
        self.meter_text_tag = f"comp_meter_text_{self.node_tag}"
        self._init_level_history("comp", db_floor=-60.0, db_ceil=6.0)

    def get_param_signature(self) -> str:
        return (f"thr={self.threshold_db:.2f},ratio={self.ratio:.2f},makeup={self.makeup_db:.2f},"
                f"atk={self.attack_ms:.2f},rel={self.release_ms:.2f}")

    @property
    def _latest_gr_db(self):
        return self._telemetry.gain_reduction_db

    @property
    def _latest_in_db(self):
        return self._telemetry.input_level_db

    def _parameters(self):
        return CompressorParameters(
            threshold_db=self.threshold_db,
            ratio=self.ratio,
            makeup_db=self.makeup_db,
            attack_ms=self.attack_ms,
            release_ms=self.release_ms,
        )

    def _draw_node_internal(self):
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input, tag=self.in_audio):
            self._draw_pin("In", is_output=False, pin_theme=self.get_global_button_theme("default_input_pin"))
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output, tag=self.out_audio):
            self._draw_pin("Out", is_output=True, pin_theme=self.get_global_button_theme("default_output_pin"))

        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
            dpg.add_text("Compressor params", color=[150, 180, 210])
            for name in self._param_control_names:
                self._draw_declared_param(name)

    def _draw_inspector(self):
        # Transfer curve: input dBFS -> output dBFS, with a 1:1 reference, the live
        # compression characteristic, and a moving dot marking the current operating point.
        # with dpg.plot(
        #         height=200, width=-1, tag=self.curve_plot_tag,
        #         no_menus=True, no_box_select=True, no_mouse_pos=True, equal_aspects=True):
        #     dpg.add_plot_axis(dpg.mvXAxis, label="Input (dBFS)", tag=self.curve_x_tag)
        #     dpg.set_axis_limits(self.curve_x_tag, -60.0, 0.0)
        #     dpg.add_plot_axis(dpg.mvYAxis, label="Output (dBFS)", tag=self.curve_y_tag)
        #     dpg.set_axis_limits(self.curve_y_tag, -60.0, 6.0)

        #     dpg.add_line_series([-60.0, 0.0], [-60.0, 0.0], parent=self.curve_y_tag, tag=self.ref_line_tag)
        #     dpg.add_line_series([], [], parent=self.curve_y_tag, tag=self.curve_line_tag)
        #     dpg.add_scatter_series([], [], parent=self.curve_y_tag, tag=self.point_tag)

        #     with dpg.theme() as ref_theme:
        #         with dpg.theme_component(dpg.mvLineSeries):
        #             dpg.add_theme_color(dpg.mvPlotCol_Line, (90, 90, 90, 255), category=dpg.mvThemeCat_Plots)
        #     dpg.bind_item_theme(self.ref_line_tag, ref_theme)
        #     with dpg.theme() as curve_theme:
        #         with dpg.theme_component(dpg.mvLineSeries):
        #             dpg.add_theme_color(dpg.mvPlotCol_Line, (200, 140, 90, 255), category=dpg.mvThemeCat_Plots)
        #             dpg.add_theme_style(dpg.mvPlotStyleVar_LineWeight, 2.0, category=dpg.mvThemeCat_Plots)
        #     dpg.bind_item_theme(self.curve_line_tag, curve_theme)
        #     with dpg.theme() as point_theme:
        #         with dpg.theme_component(dpg.mvScatterSeries):
        #             dpg.add_theme_color(dpg.mvPlotCol_MarkerFill, (255, 220, 90, 255), category=dpg.mvThemeCat_Plots)
        #             dpg.add_theme_color(dpg.mvPlotCol_MarkerOutline, (255, 220, 90, 255), category=dpg.mvThemeCat_Plots)
        #             dpg.add_theme_style(dpg.mvPlotStyleVar_MarkerSize, 5, category=dpg.mvThemeCat_Plots)
        #     dpg.bind_item_theme(self.point_tag, point_theme)

        # dpg.add_spacer(height=6)
        # Gain-reduction meter: a progress bar that grows with the live reduction amount.
        # dpg.add_text("Gain Reduction", tag=self.meter_text_tag, color=[200, 140, 90])
        # dpg.add_progress_bar(
        #     default_value=0.0, overlay="0.0 dB", width=-1, tag=self.meter_bar_tag)

        # dpg.add_spacer(height=6)
        # Before/after level history: input vs compressed output level over time.
        # dpg.add_text("Level (In vs Out)", color=[150, 180, 210])
        self._draw_level_history(height=150, out_color=(90, 200, 140, 255), ref_label="Threshold")
        dpg.add_spacer(height=15)
        for name in self._param_control_names:
            self._draw_declared_param(name, secondary=True, width=300)

    def _process_block(self, block):
        result = self._processor.process_block(
            block, parameters=self._parameters(), sample_rate=GLOBAL_SAMPLE_RATE)
        self._telemetry = result.telemetry
        self._push_levels(block, result.output)
        return result.output

    def _transfer_curve(self):
        return self._processor.transfer_curve(self._parameters())

    def on_gui_frame(self):
        # The transfer-curve plot + GR meter are currently commented out in _draw_panel, so their
        # tags don't exist. Update each optional widget only if it's present -- but ALWAYS refresh
        # the level history (which IS drawn). Previously an early-return on the missing curve tag
        # froze the level history too.
        if dpg.does_item_exist(self.curve_line_tag):
            x, y = self._transfer_curve()
            dpg.set_value(self.curve_line_tag, [x.tolist(), y.tolist()])
            in_db = float(np.clip(self._latest_in_db, -60.0, 0.0))
            out_db = in_db - self._latest_gr_db + self.makeup_db
            dpg.set_value(self.point_tag, [[in_db], [out_db]])

        gr = float(np.clip(self._latest_gr_db, 0.0, self.GR_METER_MAX))
        if dpg.does_item_exist(self.meter_bar_tag):
            dpg.set_value(self.meter_bar_tag, gr / self.GR_METER_MAX)
            dpg.configure_item(self.meter_bar_tag, overlay=f"{self._latest_gr_db:.1f} dB")

        self._update_level_history(ref_db=self.threshold_db)

    def build_stream_renderer(self, out_pin, input_renderers):
        return self._wrap_single_input(input_renderers, self._process_block)

    def publish_outputs(self) -> bool:
        return self._publish_stream_passthrough(self.out_audio)
