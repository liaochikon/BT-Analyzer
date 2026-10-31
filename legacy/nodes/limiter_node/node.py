import dearpygui.dearpygui as dpg
from core import *
from nodes._shared.level_meter import LevelHistoryMixin
from nodes.limiter_node.processor import LimiterParameters, LimiterProcessor


class LimiterNode(LevelHistoryMixin, BaseNode):
    display_name = "Limiter"
    category = "Effects"
    config = NodeConfig(
        processing=NodeProcessingMode.STREAMING,
        ui=NodeUIMode.INSPECTOR,
        realtime=True,
    )

    panel_width = 460
    panel_height = 370
    top_bar_spacer_width = 240

    class_node_theme_templates = []
    class_button_theme_templates = []
    parameter_specs = (
        ParameterSpec("pre_gain_db", 0.0, signature_precision=2),
        ParameterSpec("gain_db", 0.0, signature_precision=2),
        ParameterSpec("ceiling_db", -1.0, signature_precision=2),
        ParameterSpec("attack_ms", 1.0, signature_precision=2,
                      coerce=lambda value: max(0.1, float(value))),
        ParameterSpec("release_ms", 50.0, signature_precision=2,
                      coerce=lambda value: max(1.0, float(value))),
    )
    parameter_control_specs = (
        ParameterControlSpec("pre_gain_db", SliderSpec("Pre Gain", -24.0, 24.0, format="%.1f dB")),
        ParameterControlSpec("gain_db", SliderSpec("Output Gain", -24.0, 24.0, format="%.1f dB")),
        ParameterControlSpec("ceiling_db", SliderSpec("Ceiling", -24.0, 0.0, format="%.1f dB")),
        ParameterControlSpec("attack_ms", SliderSpec("Attack", 0.1, 20.0, format="%.1f")),
        ParameterControlSpec("release_ms", SliderSpec("Release", 5.0, 500.0, format="%.0f")),
    )

    def __init__(self, node_tag):
        super().__init__(node_tag)
        self._processor = LimiterProcessor()

        self.in_audio = self._register_input("in_audio", DataType.AUDIO)
        self.out_audio = self._register_output("out_audio", DataType.AUDIO)

        self._init_level_history("lim", db_floor=-60.0, db_ceil=6.0)

    def get_param_signature(self) -> str:
        return (f"pre={self.pre_gain_db:.2f},gain={self.gain_db:.2f},ceiling={self.ceiling_db:.2f},"
            f"atk={self.attack_ms:.2f},rel={self.release_ms:.2f}")

    def _draw_node_internal(self):
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input, tag=self.in_audio):
            self._draw_pin("In", is_output=False, pin_theme=self.get_global_button_theme("default_input_pin"))
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output, tag=self.out_audio):
            self._draw_pin("Out", is_output=True, pin_theme=self.get_global_button_theme("default_output_pin"))

        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
            dpg.add_text("Limiter params", color=[150, 180, 210])
            for name in self._param_control_names:
                self._draw_declared_param(name)

    def _draw_inspector(self):
        self._draw_level_history(height=150)

        dpg.add_spacer(height=10)
        for name in self._param_control_names:
            self._draw_declared_param(name, secondary=True, width=280)

    def _process_block(self, block):
        result = self._processor.process_block(
            block,
            parameters=LimiterParameters(
                pre_gain_db=self.pre_gain_db,
                gain_db=self.gain_db,
                ceiling_db=self.ceiling_db,
                attack_ms=self.attack_ms,
                release_ms=self.release_ms,
            ),
            sample_rate=GLOBAL_SAMPLE_RATE,
        )
        self._push_levels(result.driven_input, result.output)
        return result.output

    def on_gui_frame(self):
        self._update_level_history(ref_db=self.ceiling_db)

    def build_stream_renderer(self, out_pin, input_renderers):
        return self._wrap_single_input(input_renderers, self._process_block)

    def publish_outputs(self) -> bool:
        # Streamable: audio is produced live per block; publish a fingerprint passthrough.
        return self._publish_stream_passthrough(self.out_audio)
