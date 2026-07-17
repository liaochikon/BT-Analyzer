import logging
import numpy as np
import dearpygui.dearpygui as dpg
from core import *
from core import settings
from app_logging.logging import append_log
from nodes.demucs_node.processor import DemucsParameters, DemucsProcessor

logger = logging.getLogger(__name__)

class DemucsNode(BaseNode):
    display_name = "Demucs v6"
    category = "Effects"
    config = NodeConfig(
        processing=NodeProcessingMode.BAKED,
        ui=NodeUIMode.INSPECTOR,
    )
    parameter_specs = (
        ParameterSpec("split", True, stale=StalePolicy.STRUCTURAL),
        ParameterSpec("shifts", 0, stale=StalePolicy.STRUCTURAL, coerce=float),
        ParameterSpec("overlap", 0.25, stale=StalePolicy.STRUCTURAL, signature_precision=3),
        ParameterSpec("segment", 0.0, stale=StalePolicy.STRUCTURAL, signature_precision=3),
    )
    parameter_control_specs = (
        ParameterControlSpec("split", CheckboxSpec("Split")),
        ParameterControlSpec("shifts", SliderSpec("Shifts", 0, 10, format="%.0f")),
        ParameterControlSpec("overlap", SliderSpec("Overlap", 0.0, 0.95, format="%.2f")),
        ParameterControlSpec("segment", SliderSpec("Segment (s)", 0.0, 7.8, format="%.1f")),
    )

    top_bar_spacer_width = 240

    MODEL_NAME = "htdemucs_6s"
    _model_cache = {}

    class_node_theme_templates = []
    class_button_theme_templates = [
        ButtonThemeTemplate(
            theme_name="out_vocal_pin",
            base_color=[100, 75, 150, 180], 
            hover_color=[100, 75, 150, 180], 
            active_color=[100, 75, 150, 180], 
            rounding=4, padding=(6, 3)),
        ButtonThemeTemplate(
            theme_name="out_drums_pin",
            base_color=[150, 75, 75, 180], 
            hover_color=[150, 75, 75, 180], 
            active_color=[150, 75, 75, 180], 
            rounding=4, padding=(6, 3)),
        ButtonThemeTemplate(
            theme_name="out_guitar_pin",
            base_color=[75, 150, 90, 180], 
            hover_color=[75, 150, 90, 180], 
            active_color=[75, 150, 90, 180], 
            rounding=4, padding=(6, 3)),
        ButtonThemeTemplate(
            theme_name="out_other_pin",
            base_color=[150, 140, 75, 180], 
            hover_color=[150, 140, 75, 180], 
            active_color=[150, 140, 75, 180], 
            rounding=4, padding=(6, 3)),
        ButtonThemeTemplate(
            theme_name="out_bass_pin",
            base_color=[80, 110, 150, 180], 
            hover_color=[80, 110, 150, 180], 
            active_color=[80, 110, 150, 180], 
            rounding=4, padding=(6, 3)),
        ButtonThemeTemplate(
            theme_name="out_piano_pin",
            base_color=[150, 100, 130, 180], 
            hover_color=[150, 100, 130, 180], 
            active_color=[150, 100, 130, 180], 
            rounding=4, padding=(6, 3))]

    def __init__(self, node_tag):
        super().__init__(node_tag)
        self._processor = DemucsProcessor(model_cache=self._model_cache)

        self.in_mix = self._register_input("in_mix", DataType.AUDIO)

        self.out_drums = self._register_output("out_drums", DataType.AUDIO)
        self.out_bass = self._register_output("out_bass", DataType.AUDIO)
        self.out_other = self._register_output("out_other", DataType.AUDIO)
        self.out_vocal = self._register_output("out_vocal", DataType.AUDIO)
        self.out_guitar = self._register_output("out_guitar", DataType.AUDIO)
        self.out_piano = self._register_output("out_piano", DataType.AUDIO)

        self._stem_pins = {
            "drums": self.out_drums,
            "bass": self.out_bass,
            "other": self.out_other,
            "vocals": self.out_vocal,
            "guitar": self.out_guitar,
            "piano": self.out_piano,
        }

    def get_param_signature(self) -> str:
        return f"{self.MODEL_NAME}|{self._declared_param_signature()}"

    def _draw_node_internal(self):
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input, tag=self.in_mix):
            self._draw_pin("Mix", is_output=False, pin_theme=self.get_global_button_theme("default_input_pin"))
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output, tag=self.out_drums):
            self._draw_pin("Drums", is_output=True, pin_theme=self.get_class_button_theme("out_drums_pin"))
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output, tag=self.out_bass):
            self._draw_pin("Bass", is_output=True, pin_theme=self.get_class_button_theme("out_bass_pin"))
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output, tag=self.out_other):
            self._draw_pin("Other", is_output=True, pin_theme=self.get_class_button_theme("out_other_pin"))
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output, tag=self.out_vocal):
            self._draw_pin("Vocal", is_output=True, pin_theme=self.get_class_button_theme("out_vocal_pin"))
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output, tag=self.out_guitar):
            self._draw_pin("Guitar", is_output=True, pin_theme=self.get_class_button_theme("out_guitar_pin"))
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output, tag=self.out_piano):
            self._draw_pin("Piano", is_output=True, pin_theme=self.get_class_button_theme("out_piano_pin"))

        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
            dpg.add_text("Model params", color=[150, 180, 210])
            for name in self._param_control_names:
                self._draw_declared_param(name)
            
    def publish_outputs(self) -> bool:
        return self._publish_cached(*self._stem_pins.values())

    def bake(self) -> bool:
        mix_packet = self.input_values.get(self.in_mix)
        if mix_packet is None or mix_packet.data is None:
            append_log(f"[{self.display_name}] Bake failed: no input mix connected (bake the upstream source first).", level="WARNING")
            return False

        segment = self.segment if self.segment > 0 else None
        requested_device = str(
            settings.get("inference_device", INFERENCE_DEVICE)).lower()
        append_log(
            f"[{self.display_name}] Separating {mix_packet.data.shape[-1]} samples "
            f"({self.MODEL_NAME}; shifts={self.shifts}, overlap={self.overlap:.2f}, "
            f"segment={'auto' if segment is None else f'{segment:.1f}s'}, split={self.split})...",
            level="INFO")

        result = self._processor.separate(
            mix_packet.data,
            model_name=self.MODEL_NAME,
            parameters=DemucsParameters(
                shifts=self.shifts,
                split=self.split,
                overlap=self.overlap,
                segment=segment,
            ),
            requested_device=requested_device,
            progress=self._report_progress,
        )
        for warning in result.warnings:
            append_log(f"[{self.display_name}] {warning}", level="WARNING")
        if not result.ok:
            logger.error(
                "[%s] %s: %s",
                self.display_name, result.error.message, result.error.detail)
            append_log(
                f"[{self.display_name}] {result.error.message}: {result.error.detail}",
                level="ERROR")
            self.is_baked = False
            return False

        zero = np.zeros_like(next(iter(result.stems.values())))
        for stem_name, pin in self._stem_pins.items():
            stem = result.stems.get(stem_name, zero)
            self.cached_outputs[pin] = np.ascontiguousarray(stem, dtype=np.float32)

        self.is_baked = True
        self._report_progress(1.0)
        append_log(f"[{self.display_name}] Bake succeeded: {len(self._stem_pins)} stems x {zero.shape[1]} samples ({GLOBAL_SAMPLE_RATE}Hz).", level="INFO")
        return True