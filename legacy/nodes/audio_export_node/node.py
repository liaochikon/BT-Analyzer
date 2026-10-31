import os
import logging
import dearpygui.dearpygui as dpg
from core import *
from app_logging.logging import append_log
from nodes._shared.status_label import (
    StatusLabelMixin, STATUS_IDLE, STATUS_BUSY, STATUS_OK, STATUS_WARN)
from nodes._shared.file_path_state import FilePathStateMixin
from nodes.audio_export_node.processor import AudioExportProcessor, AudioExportStatus


logger = logging.getLogger(__name__)


class AudioExportNode(FilePathStateMixin, StatusLabelMixin, BaseNode):
    display_name = "Audio Export"
    category = "Input / Output"
    config = NodeConfig(
        processing=NodeProcessingMode.BAKED,
        ui=NodeUIMode.INSPECTOR,
    )

    # How many frames to pull per render() call while writing the file.
    EXPORT_BLOCK = 8192

    class_node_theme_templates = []
    class_button_theme_templates = [
        ButtonThemeTemplate(
            theme_name="export_btn",
            base_color=[150, 120, 80, 100],
            hover_color=[150, 120, 80, 200],
            active_color=[110, 85, 50, 200],
            rounding=4, padding=(6, 3)),
    ]

    def __init__(self, node_tag):
        super().__init__(node_tag)
        self._processor = AudioExportProcessor()

        self.in_audio = self._register_input("in_audio", DataType.AUDIO)

        self.file_path = ""
        self.max_file_text_length = 15
        self.file_dialog_tag = f"export_dialog_{self.node_tag}"
        self.file_text_tag = f"export_file_text_{self.node_tag}"
        self.inspector_file_text_tag = f"inspector_export_file_text_{self.node_tag}"
        self.status_text_tag = f"export_status_{self.node_tag}"
        self.export_progress_tag = f"export_progress_{self.node_tag}"

    def _draw_node_internal(self):
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input, tag=self.in_audio):
            self._draw_pin(
                pin_label="Audio In", is_output=False,
                pin_theme=self.get_global_button_theme("default_input_pin"),
                label_width=85)

        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
            dpg.add_spacer(height=5)

        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
            with dpg.group(horizontal=True):
                dpg.add_spacer(width=25)
                dpg.add_button(
                    label="Browse WAV File",
                    callback=lambda: dpg.show_item(self.file_dialog_tag),
                    width=160
                )

        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
            with dpg.group(horizontal=True):
                dpg.add_spacer(width=25)
                dpg.add_text(
                    self._file_display_name(),
                    tag=self.file_text_tag,
                    color=[100, 255, 100],
                )

    def _draw_node_additional(self):
        with dpg.file_dialog(
            directory_selector=False,
            show=False,
            tag=self._track_ui_resource(self.file_dialog_tag),
            callback=self._file_selected_callback,
            default_filename="export",
            width=600, height=400,
        ):
            dpg.add_file_extension(".wav", color=[0, 255, 255, 255])
            dpg.add_file_extension(".*")

    def _file_selected_callback(self, sender, app_data):
        # GUI thread: capture the chosen path, then hand the heavy render+write to a worker
        # thread so the render loop never blocks while a long file is written.
        path = app_data.get("file_path_name", "")
        if not path:
            return
        if not path.lower().endswith(".wav"):
            path += ".wav"
        self._parameter_ui.apply_extra("file_path", path)

    def _file_display_name(self) -> str:
        filename = os.path.basename(self.file_path)
        suffix = "..." if len(filename) > self.max_file_text_length else ""
        return filename[:self.max_file_text_length] + suffix

    def project_extra_state(self, field, value, *, index=None):
        if field == "file_path":
            self._refresh_file_text()

    def _refresh_file_text(self):
        for tag in (self.file_text_tag, self.inspector_file_text_tag):
            if dpg.does_item_exist(tag):
                dpg.set_value(
                    tag, self._file_display_name() if self.file_path else "No file selected")
                dpg.configure_item(
                    tag,
                    color=[100, 255, 100] if self.file_path else [150, 150, 150],
                )

    def mutate_extra_state(self, field, value, *, index=None, action=None):
        result = super().mutate_extra_state(
            field, value, index=index, action=action)
        if field == "file_path" and result is not None:
            mark_structural_heavy_stale(self.node_tag)
        return result

    def apply_extra(self, data: dict):
        super().apply_extra(data)
        self._refresh_file_text()

    def _draw_inspector(self):
        super()._draw_inspector()
        dpg.add_button(
            label="Browse WAV File",
            callback=lambda: dpg.show_item(self.file_dialog_tag),
            width=180,
        )
        dpg.add_text(
            self._file_display_name() if self.file_path else "No file selected",
            tag=self.inspector_file_text_tag,
            color=[100, 255, 100] if self.file_path else [150, 150, 150],
        )

    def export(self) -> bool:
        # Runs on the export worker thread (single-flight with the compile engine). Build the
        # same frozen pull-chain the playback sink uses, render the whole length block by
        # block, and write it to a WAV. Reusing build_stream_source means the exported file
        # captures the exact effect/mix chain the user hears.
        if not self.file_path:
            append_log(f"[{self.display_name}] No output path selected.", level="WARNING")
            return False

        render, length = build_stream_source(self.in_audio)
        if render is not None and length > 0:
            self._set_status("Exporting...", STATUS_BUSY)

        result = self._processor.export(
            render,
            length,
            self.file_path,
            render_lock=ENGINE.render_lock,
            sample_rate=GLOBAL_SAMPLE_RATE,
            block_size=self.EXPORT_BLOCK,
            # progress=self._report_export_progress,
        )
        if result.status is AudioExportStatus.NO_INPUT:
            self._set_status("No input", STATUS_IDLE)  # nothing wired: not an error
            append_log(f"[{self.display_name}] No input audio connected; nothing to export.", level="WARNING")
            return False
        if result.status is AudioExportStatus.EMPTY:
            self._set_status("Empty", STATUS_WARN)  # local failure: yellow, not system red
            append_log(f"[{self.display_name}] Render produced no audio.", level="WARNING")
            return False
        if result.status is AudioExportStatus.WRITE_FAILED:
            self._set_status("Write failed", STATUS_WARN)  # local failure: yellow, not system red
            logger.error(
                "[%s] Failed to write WAV to %s: %s",
                self.display_name, self.file_path, result.error)
            append_log(f"[{self.display_name}] Failed to write WAV: {result.error}", level="ERROR")
            return False

        self._set_status(f"Saved {os.path.basename(self.file_path)}", STATUS_OK)
        append_log(
            f"[{self.display_name}] Exported {result.frames} frames "
            f"({GLOBAL_SAMPLE_RATE}Hz) to {self.file_path}.", level="INFO")
        return True

    def bake(self) -> bool:
        self.is_baked = self.export()
        self._report_progress(1.0 if self.is_baked else 0.0)
        return self.is_baked

    def publish_outputs(self) -> bool:
        # Pure sink: configured paths are written by the unified heavy compile.
        return True
