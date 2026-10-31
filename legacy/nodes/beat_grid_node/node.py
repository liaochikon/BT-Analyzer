import dearpygui.dearpygui as dpg
from core import *


class BeatGridNode(BaseNode):
    """Sink that drives the transport's beat grid + count-in from a tempo source.

    Feed a Beat/Tempo node's Tempo/Offset outputs in here; on each pipeline pass this pushes the
    values into the shared TRANSPORT, where the transport bar reads them to draw the grid and to
    time the count-in. Like Audio Output it is a singleton sink: it produces nothing downstream,
    it just publishes into a core singleton.

    When an input is unconnected the transport keeps its shipped default (120 BPM / 0 s offset),
    so a grid is always present -- this node only overrides it with detected/wired values.
    """

    display_name = "Beat Grid Output"
    category = "Input / Output"
    config = NodeConfig(singleton=True)

    top_bar_spacer_width = 180

    class_node_theme_templates = []
    class_button_theme_templates = []

    def __init__(self, node_tag):
        super().__init__(node_tag)
        self.in_tempo = self._register_input("in_tempo", DataType.TEMPO)
        self.in_offset = self._register_input("in_offset", DataType.TIME)
        self.status_text_tag = f"beatgrid_status_{self.node_tag}"

    def get_param_signature(self) -> str:
        return "beatgrid"

    def _draw_node_internal(self):
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input, tag=self.in_tempo):
            self._draw_pin(
                "Tempo In", is_output=False,
                pin_theme=self.get_global_button_theme("default_input_pin"), label_width=85)
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input, tag=self.in_offset):
            self._draw_pin(
                "Offset In", is_output=False,
                pin_theme=self.get_global_button_theme("default_input_pin"), label_width=85)
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
            dpg.add_text("Drives transport grid", tag=self.status_text_tag, color=[150, 180, 210])

    @staticmethod
    def _scalar(packet, default: float) -> float:
        # Unwrap a scalar packet (stored as a 0-d / (1,) float array). None or empty -> default.
        if packet is None or packet.data is None:
            return default
        try:
            return float(packet.data.reshape(-1)[0]) if packet.data.ndim else float(packet.data)
        except (ValueError, IndexError, TypeError):
            return default

    def publish_outputs(self) -> bool:
        # Pipeline thread: push the (connected) tempo/offset into the shared transport. An
        # unconnected input keeps the transport's current value, so partial wiring is fine.
        bpm = self._scalar(self.input_values.get(self.in_tempo), TRANSPORT.bpm)
        offset = self._scalar(self.input_values.get(self.in_offset), TRANSPORT.beat_offset)
        TRANSPORT.set_beat_grid(bpm, offset)
        return True
