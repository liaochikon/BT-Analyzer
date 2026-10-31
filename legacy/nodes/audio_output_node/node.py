import dearpygui.dearpygui as dpg
from core import *
from app_logging.logging import append_log
from nodes._shared.status_label import (
    StatusLabelMixin, STATUS_IDLE, STATUS_BUSY, STATUS_OK)
from nodes.audio_output_node.processor import AudioOutputProcessor


class AudioOutputNode(StatusLabelMixin, BaseNode):
    display_name = "Audio Output"
    category = "Input / Output"
    config = NodeConfig(realtime=True, singleton=True)

    class_node_theme_templates = []
    class_button_theme_templates = []

    def __init__(self, node_tag):
        super().__init__(node_tag)
        self._processor = AudioOutputProcessor()

        self.in_audio = self._register_input("in_audio", DataType.AUDIO)

        self.has_signal = False
        self.status_text_tag = f"output_status_{self.node_tag}"

    def _draw_node_internal(self):
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input, tag=self.in_audio):
            self._draw_pin(
                pin_label="Audio In", is_output=False,
                pin_theme=self.get_global_button_theme("default_input_pin"),
                label_width=85)

        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
            with dpg.group(horizontal=True):
                dpg.add_spacer(width=15)
                # No signal = nothing wired yet: a normal mid-editing state, not an error -> grey.
                dpg.add_text("No signal", tag=self.status_text_tag, color=STATUS_IDLE)

    def on_gui_frame(self):
        if not self.has_signal:
            self._set_status("No signal", STATUS_IDLE)
            return
        pos = f"{format_mmss(TRANSPORT.position_seconds)} / {format_mmss(TRANSPORT.length_seconds)}"
        if TRANSPORT.state == PlaybackState.PLAYING:
            self._set_status(f"Playing {pos}", STATUS_OK)
        elif TRANSPORT.state == PlaybackState.PAUSED:
            self._set_status(f"Paused {pos}", STATUS_BUSY)
        else:
            self._set_status(f"Ready {pos}", STATUS_OK)

    def publish_outputs(self) -> bool:
        # Runs on the compile worker thread (or the GUI thread for light passes): only touch
        # data/engine/transport here, never DearPyGui. The status text is drawn by on_gui_frame().
        #
        # Build a frozen pull-chain for whatever feeds this sink. The whole upstream wiring is
        # captured here on the compile thread; the producer then pulls per-block render() calls.
        # Leaf arrays are bound by value and live params are read inside the closures, so a
        # later recompile can never change the audio under the producer's feet mid-block.
        self.has_signal = self._processor.bind_source(
            self.in_audio,
            build_source=build_stream_source,
            engine=ENGINE,
            transport=TRANSPORT,
        )
        if not self.has_signal:
            append_log(f"[{self.display_name}] No input audio connected.", level="WARNING")
            return False

        return True
