import dearpygui.dearpygui as dpg
from core import *
from nodes.gain_node.processor import GainProcessor


class GainNode(BaseNode):
    display_name = "Gain"
    category = "Effects"
    config = NodeConfig(
        processing=NodeProcessingMode.STREAMING,
        ui=NodeUIMode.INSPECTOR,
    )
    parameter_specs = (
        ParameterSpec(
            name="gain_db",
            default=0.0,
            signature_precision=3,
        ),
        ParameterSpec(
            name="bypass",
            default=False,
        ),
    )
    parameter_control_specs = (
        ParameterControlSpec(
            parameter="gain_db",
            control=SliderSpec(
                label="Gain",
                minimum=-60.0,
                maximum=12.0,
                format="%.1f dB",
            ),
        ),
        ParameterControlSpec(
            parameter="bypass",
            control=CheckboxSpec(label="Bypass"),
        ),
    )

    class_node_theme_templates = []
    class_button_theme_templates = []

    def __init__(self, node_tag):
        super().__init__(node_tag)
        self._processor = GainProcessor()

        self.in_audio = self._register_input("in_audio", DataType.AUDIO)
        self.out_audio = self._register_output("out_audio", DataType.AUDIO)

    def get_param_signature(self) -> str:
        return self._declared_param_signature()

    def _draw_node_internal(self):
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input, tag=self.in_audio):
            self._draw_pin("In", is_output=False, pin_theme=self.get_global_button_theme("default_input_pin"))
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output, tag=self.out_audio):
            self._draw_pin("Out", is_output=True, pin_theme=self.get_global_button_theme("default_output_pin"))

        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
            dpg.add_text("Gain params", color=[150, 180, 210])
            self._draw_declared_param("gain_db")
            self._draw_declared_param("bypass")

    def _draw_inspector(self):
        dpg.add_text("Gain", color=[150, 180, 210])
        self._draw_declared_param("gain_db", secondary=True, width=360)
        self._draw_declared_param("bypass", secondary=True)

    def _process_block(self, block):
        return self._processor.process_block(
            block, gain_db=self.gain_db, bypass=self.bypass)

    def build_stream_renderer(self, out_pin, input_renderers):
        return self._wrap_single_input(input_renderers, self._process_block)

    def publish_outputs(self) -> bool:
        # Streamable: audio is produced live per block; publish a fingerprint passthrough.
        return self._publish_stream_passthrough(self.out_audio)
