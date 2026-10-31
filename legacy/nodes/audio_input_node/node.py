import os
import logging
import dearpygui.dearpygui as dpg
from core import *
from app_logging.logging import append_log
from nodes._shared.file_path_state import FilePathStateMixin
from nodes.audio_input_node.processor import AudioInputProcessor

logger = logging.getLogger(__name__)

class AudioInputNode(FilePathStateMixin, BaseNode):
    display_name = "Audio Input"
    category = "Input / Output"
    config = NodeConfig(
        processing=NodeProcessingMode.BAKED,
        ui=NodeUIMode.INSPECTOR,
    )

    class_node_theme_templates = []
    class_button_theme_templates = []
    
    def __init__(self, node_tag):
        super().__init__(node_tag)
        self._processor = AudioInputProcessor()

        self.max_file_text_length = 15
        
        self.out_audio = self._register_output("out_audio", DataType.AUDIO)

        self.file_path = "" 
        self.file_dialog_tag = f"file_dialog_{self.node_tag}"
        self.file_text_tag = f"file_text_{self.node_tag}"
        self.inspector_file_text_tag = f"inspector_file_text_{self.node_tag}"

    def get_param_signature(self) -> str:
        if not self.file_path or not os.path.exists(self.file_path):
            return self.file_path or ""
        st = os.stat(self.file_path)
        return f"{self.file_path}|{st.st_size}|{st.st_mtime_ns}"

    def _file_selected_callback(self, sender, app_data):
        self._parameter_ui.apply_extra(
            "file_path", app_data.get("file_path_name", ""))

    def mutate_extra_state(self, field, value, *, index=None, action=None):
        result = super().mutate_extra_state(
            field, value, index=index, action=action)
        if field == "file_path" and result is not None:
            mark_structural_heavy_stale(self.node_tag)
        return result

    def project_extra_state(self, field, value, *, index=None):
        if field == "file_path":
            self._refresh_file_text()

    def _refresh_file_text(self):
        # Mirror the current file_path into the node-body label (green = a file is set). Safe to
        # call before the widget exists (e.g. during apply_extra ordering); guarded by does_item.
        if self.file_path:
            for tag in (self.file_text_tag, self.inspector_file_text_tag):
                if dpg.does_item_exist(tag):
                    dpg.set_value(tag, self._file_display_name())
                    dpg.configure_item(tag, color=[100, 255, 100])
        else:
            # No file yet: clear the label. The "needs attention" signal is the node's warning skin
            # (applied by GraphState.failed_nodes when a bake with no file fails), not per-text
            # colour here -- so we don't duplicate/second-guess the shared theme.
            for tag in (self.file_text_tag, self.inspector_file_text_tag):
                if dpg.does_item_exist(tag):
                    dpg.set_value(tag, "")

    def _file_display_name(self) -> str:
        if not self.file_path:
            return ""
        file_name = os.path.basename(self.file_path)
        suffix = "..." if len(file_name) > self.max_file_text_length else ""
        return file_name[:self.max_file_text_length] + suffix

    def apply_extra(self, data: dict):
        # Restore the path (via the shared mixin), then repaint the node-body filename label.
        super().apply_extra(data)
        self._refresh_file_text()

    def _draw_node_internal(self):
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
            with dpg.group(horizontal=True):
                dpg.add_spacer(width=25)
                dpg.add_button(
                    label="Browse Audio File", 
                    callback=lambda: dpg.show_item(self.file_dialog_tag),
                    width=160
                )
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
            with dpg.group(horizontal=True):
                dpg.add_spacer(width=25)
                # Filename label; _refresh_file_text sets it green when a file is chosen, empty
                # otherwise. The no-file WARNING is shown via the node's warning skin, not here.
                dpg.add_text(
                    self._file_display_name(),
                    tag=self.file_text_tag,
                    color=[100, 255, 100],
                )
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
            dpg.add_spacer(height=5)

        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output, tag=self.out_audio):
            self._draw_pin(
                pin_label="Audio Out", is_output=True,
                pin_theme=self.get_global_button_theme("default_output_pin"),
                label_width=85)

    def _draw_node_additional(self):
        with dpg.file_dialog(
            directory_selector=False, 
            show=False, 
            tag=self._track_ui_resource(self.file_dialog_tag),
            callback=self._file_selected_callback,
            width=600, 
            height=400):

            dpg.add_file_extension(".mp3", color=[0, 255, 255, 255])
            dpg.add_file_extension(".*")

    def _draw_inspector(self):
        super()._draw_inspector()
        dpg.add_button(
            label="Browse Audio File",
            callback=lambda: dpg.show_item(self.file_dialog_tag),
            width=180,
        )
        dpg.add_text(
            os.path.basename(self.file_path) if self.file_path else "No file selected",
            tag=self.inspector_file_text_tag,
            color=[100, 255, 100] if self.file_path else [150, 150, 150],
        )

    def bake(self) -> bool:
        if not self.file_path or not os.path.exists(self.file_path):
            # Local failure (this source has no data), not a system error: just report failure and
            # let GraphState.failed_nodes mark it YELLOW + surface it in Analyzer Status. Do NOT
            # self-highlight red -- red is reserved for whole-graph problems (a wiring loop).
            append_log(f"[{self.display_name}] Bake failed: no file selected or file path does not exist.", level="WARNING")
            return False

        try:
            append_log(f"[{self.display_name}] Starting heavy audio decoding and sample rate alignment...", level="INFO")
            data = self._processor.decode(
                self.file_path,
                sample_rate=GLOBAL_SAMPLE_RATE,
                progress=self._report_progress,
            )
                
            self.cached_outputs[self.out_audio] = data
            self.is_baked = True
            self._report_progress(0.8)
            
            append_log(f"[{self.display_name}] Bake succeeded! Channels: {data.shape[0]}, Total samples: {data.shape[1]} ({GLOBAL_SAMPLE_RATE}Hz)", level="INFO")
            self._report_progress(1.0)
            return True
            
        except Exception:
            logger.exception("[%s] Audio decoding crashed", self.display_name)
            append_log(f"[{self.display_name}] Audio decoding failed.", level="ERROR")
            self.is_baked = False
            return False

    def publish_outputs(self) -> bool:
        return self._publish_cached(self.out_audio)
        
    