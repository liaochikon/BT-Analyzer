import os
import logging
import dearpygui.dearpygui as dpg
from core import *
from app_logging.logging import append_log
from nodes._shared.status_label import (
    StatusLabelMixin, STATUS_IDLE, STATUS_BUSY, STATUS_OK, STATUS_WARN)
from nodes._shared.file_path_state import FilePathStateMixin
from nodes.midi_export_node.processor import MidiExportProcessor, MidiExportStatus


logger = logging.getLogger(__name__)


class MidiExportNode(FilePathStateMixin, StatusLabelMixin, BaseNode):
    display_name = "MIDI Export"
    category = "Input / Output"
    config = NodeConfig(
        processing=NodeProcessingMode.BAKED,
        ui=NodeUIMode.INSPECTOR,
    )

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
        self._processor = MidiExportProcessor()

        self.in_midi = self._register_input("in_midi", DataType.MIDI)

        self.file_path = ""
        self.max_file_text_length = 15
        self.file_dialog_tag = f"midi_export_dialog_{self.node_tag}"
        self.file_text_tag = f"midi_export_file_text_{self.node_tag}"
        self.inspector_file_text_tag = f"inspector_midi_export_file_text_{self.node_tag}"
        self.status_text_tag = f"midi_export_status_{self.node_tag}"

    def _draw_node_internal(self):
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input, tag=self.in_midi):
            self._draw_pin(
                pin_label="MIDI In", is_output=False,
                pin_theme=self.get_global_button_theme("default_input_pin"),
                label_width=85)

        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
            dpg.add_spacer(height=5)

        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
            with dpg.group(horizontal=True):
                dpg.add_spacer(width=25)
                dpg.add_button(
                    label="Browse MIDI File",
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
            dpg.add_file_extension(".mid", color=[0, 255, 255, 255])
            dpg.add_file_extension(".*")

    def _file_selected_callback(self, sender, app_data):
        # GUI thread: capture the chosen path, then hand the write to a worker thread so the
        # render loop never blocks.
        path = app_data.get("file_path_name", "")
        if not path:
            return
        if not path.lower().endswith(".mid"):
            path += ".mid"
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
            label="Browse MIDI File",
            callback=lambda: dpg.show_item(self.file_dialog_tag),
            width=180,
        )
        dpg.add_text(
            self._file_display_name() if self.file_path else "No file selected",
            tag=self.inspector_file_text_tag,
            color=[100, 255, 100] if self.file_path else [150, 150, 150],
        )

    def export(self) -> bool:
        # Runs on the export worker thread (single-flight with the compile engine). Read the
        # transcribed notes published by the upstream MIDI node and write a standard .mid.
        if not self.file_path:
            append_log(f"[{self.display_name}] No output path selected.", level="WARNING")
            return False

        packet = self.input_values.get(self.in_midi)
        notes = packet.data if packet is not None else None
        if notes is not None and len(notes) > 0:
            self._set_status("Exporting...", STATUS_BUSY)

        result = self._processor.export(notes, self.file_path)
        if result.status is MidiExportStatus.NO_INPUT:
            self._set_status("No input", STATUS_IDLE)  # nothing wired: not an error
            append_log(f"[{self.display_name}] No MIDI connected (bake the upstream Basic Pitch node first).", level="WARNING")
            return False
        if result.status is MidiExportStatus.WRITE_FAILED:
            self._set_status("Write failed", STATUS_WARN)  # local failure: yellow, not system red
            logger.error(
                "[%s] Failed to write MIDI to %s: %s",
                self.display_name, self.file_path, result.error)
            append_log(f"[{self.display_name}] Failed to write MIDI: {result.error}", level="ERROR")
            return False

        self._set_status(f"Saved {os.path.basename(self.file_path)}", STATUS_OK)
        append_log(f"[{self.display_name}] Exported {result.notes} notes to {self.file_path}.", level="INFO")
        return True

    def bake(self) -> bool:
        self.is_baked = self.export()
        self._report_progress(1.0 if self.is_baked else 0.0)
        return self.is_baked

    def publish_outputs(self) -> bool:
        # Pure sink: configured paths are written by the unified heavy compile.
        return True
