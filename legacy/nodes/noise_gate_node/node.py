import dearpygui.dearpygui as dpg
from core import *
from nodes._shared.level_meter import LevelHistoryMixin
from nodes.noise_gate_node.processor import NoiseGateParameters, NoiseGateProcessor


class NoiseGateNode(LevelHistoryMixin, BaseNode):
    display_name = "Noise Gate"
    category = "Effects"
    config = NodeConfig(
        processing=NodeProcessingMode.STREAMING,
        ui=NodeUIMode.INSPECTOR,
        realtime=True,
    )

    panel_width = 460
    panel_height = 400

    class_node_theme_templates = []
    class_button_theme_templates = []
    parameter_specs = (
        ParameterSpec("threshold_db", -40.0, signature_precision=2),
        ParameterSpec("attack_ms", 1.0, signature_precision=2,
                      coerce=lambda value: max(0.1, float(value))),
        ParameterSpec("hold_ms", 10.0, signature_precision=2,
                      coerce=lambda value: max(0.0, float(value))),
        ParameterSpec("release_ms", 100.0, signature_precision=2,
                      coerce=lambda value: max(1.0, float(value))),
        ParameterSpec("floor_db", -60.0, signature_precision=2),
    )
    parameter_control_specs = (
        ParameterControlSpec("threshold_db", SliderSpec("Threshold", -80.0, 0.0, format="%.1f dB")),
        ParameterControlSpec("attack_ms", SliderSpec("Attack", 0.1, 50.0, format="%.1f")),
        ParameterControlSpec("hold_ms", SliderSpec("Hold", 0.0, 500.0, format="%.0f")),
        ParameterControlSpec("release_ms", SliderSpec("Release", 5.0, 1000.0, format="%.0f")),
        ParameterControlSpec("floor_db", SliderSpec("Floor", -90.0, 0.0, format="%.1f dB")),
    )

    def __init__(self, node_tag):
        super().__init__(node_tag)
        self._processor = NoiseGateProcessor()

        self.in_audio = self._register_input("in_audio", DataType.AUDIO)
        self.out_audio = self._register_output("out_audio", DataType.AUDIO)

        self._init_level_history("gate", db_floor=-80.0, db_ceil=6.0)

    def get_param_signature(self) -> str:
        return (f"thr={self.threshold_db:.2f},atk={self.attack_ms:.2f},hold={self.hold_ms:.2f},"
                f"rel={self.release_ms:.2f},floor={self.floor_db:.2f}")

    def _draw_node_internal(self):
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input, tag=self.in_audio):
            self._draw_pin("In", is_output=False, pin_theme=self.get_global_button_theme("default_input_pin"))
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output, tag=self.out_audio):
            self._draw_pin("Out", is_output=True, pin_theme=self.get_global_button_theme("default_output_pin"))

        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
            dpg.add_text("Noise Gate params", color=[150, 180, 210])
            for name in self._param_control_names:
                self._draw_declared_param(name)

    def _draw_inspector(self):
        dpg.add_text("Level (In vs Out)", color=[150, 180, 210])
        self._draw_level_history(
            height=150, out_color=(90, 200, 140, 255),
            ref_label="Threshold", show_ref2=True, ref2_label="Floor")

        dpg.add_spacer(height=10)
        for name in self._param_control_names:
            self._draw_declared_param(name, secondary=True, width=300)

    def _process_block(self, block):
        out = self._processor.process_block(
            block,
            parameters=NoiseGateParameters(
                threshold_db=self.threshold_db,
                attack_ms=self.attack_ms,
                hold_ms=self.hold_ms,
                release_ms=self.release_ms,
                floor_db=self.floor_db,
            ),
            sample_rate=GLOBAL_SAMPLE_RATE,
        )
        self._push_levels(block, out)
        return out

    def on_gui_frame(self):
        self._update_level_history(ref_db=self.threshold_db, ref2_db=self.floor_db)

    def build_stream_renderer(self, out_pin, input_renderers):
        return self._wrap_single_input(input_renderers, self._process_block)

    def publish_outputs(self) -> bool:
        # Streamable: audio is produced live per block; publish a fingerprint passthrough.
        return self._publish_stream_passthrough(self.out_audio)
