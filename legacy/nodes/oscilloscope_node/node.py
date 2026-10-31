import dearpygui.dearpygui as dpg
from core import *
from ui import i18n
from ui.surfaces import canvas_surface, centered_surface
from nodes.oscilloscope_node.processor import OscilloscopeProcessor


class OscilloscopeNode(BaseNode):
    display_name = "Oscilloscope"
    category = "Display"
    config = NodeConfig(
        processing=NodeProcessingMode.STREAMING,
        ui=NodeUIMode.INSPECTOR_PANEL,
        realtime=True,
    )

    panel_width = 480
    panel_height = 260

    # Engine block size; the producer hands us this many frames per render call.
    WINDOW = 512

    class_node_theme_templates = []
    class_button_theme_templates = []

    def __init__(self, node_tag):
        super().__init__(node_tag)
        self._processor = OscilloscopeProcessor()

        self.in_audio = self._register_input("in_audio", DataType.AUDIO)
        self.out_audio = self._register_output("out_audio", DataType.AUDIO)

        self.plot_tag = f"scope_plot_{self.node_tag}"
        self.x_axis_tag = f"scope_x_{self.node_tag}"
        self.y_axis_tag = f"scope_y_{self.node_tag}"
        self.series_tag = f"scope_series_{self.node_tag}"
        self.canvas_tag = f"scope_canvas_{self.node_tag}"
        self.empty_surface_tag = f"scope_empty_{self.node_tag}"

    def _draw_node_internal(self):
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input, tag=self.in_audio):
            self._draw_pin("In", is_output=False, pin_theme=self.get_global_button_theme("default_input_pin"))
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output, tag=self.out_audio):
            self._draw_pin("Out", is_output=True, pin_theme=self.get_global_button_theme("default_output_pin"))

    def _draw_panel(self):
        with canvas_surface(tag=self.canvas_tag):
            with dpg.plot(
                height=-1, width=-1, tag=self.plot_tag, show=False,
                no_menus=True, no_box_select=True, no_mouse_pos=True):
                dpg.add_plot_axis(
                    dpg.mvXAxis, no_tick_labels=True, no_gridlines=True, tag=self.x_axis_tag)
                dpg.set_axis_limits(self.x_axis_tag, 0, self.WINDOW)
                dpg.add_plot_axis(
                    dpg.mvYAxis, label="Amplitude", no_tick_labels=False, no_gridlines=True, tag=self.y_axis_tag)
                dpg.set_axis_limits(self.y_axis_tag, -1.0, 1.0)
                dpg.add_line_series([], [], parent=self.y_axis_tag, tag=self.series_tag)
            with centered_surface(tag=self.empty_surface_tag):
                dpg.add_text(i18n.tr("oscilloscope_empty"), color=[150, 150, 150])

    def build_stream_renderer(self, out_pin, input_renderers):
        return self._wrap_single_input_tap(
            input_renderers, self._processor.capture)

    def on_gui_frame(self):
        snapshot = self._processor.snapshot
        if not dpg.does_item_exist(self.series_tag):
            return
        has_audio = snapshot is not None and len(snapshot.samples) > 0
        dpg.configure_item(self.plot_tag, show=has_audio)
        dpg.configure_item(self.empty_surface_tag, show=not has_audio)
        if not has_audio:
            return
        samples = snapshot.samples
        dpg.set_value(self.series_tag, [list(range(len(samples))), list(samples)])

    def publish_outputs(self) -> bool:
        # Streamable passthrough: audio is produced live per block; publish a fingerprint
        # passthrough so staleness propagates downstream.
        return self._publish_stream_passthrough(self.out_audio)
