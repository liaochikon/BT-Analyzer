import logging
import os

import dearpygui.dearpygui as dpg

from core import *
from core import settings, disk_cache
from app_logging.logging import append_log
from nodes.bs_roformer_node.processor import BSRoformerProcessor

logger = logging.getLogger(__name__)

_FFMPEG_CACHE_DIR = os.path.join(disk_cache.CACHE_ROOT, "ffmpeg")


class BSRoformerNode(BaseNode):
    display_name = "BS-RoFormer"
    category = "Effects"
    config = NodeConfig(
        processing=NodeProcessingMode.BAKED,
        ui=NodeUIMode.INSPECTOR,
    )

    top_bar_spacer_width = 280

    # Friendly label -> audio-separator model checkpoint filename (auto-downloaded on first use).
    # All listed models are vocal BS-RoFormer checkpoints that split into Vocals + Instrumental.
    MODELS = {
        "BS-RoFormer 317 (Vocals)": "model_bs_roformer_ep_317_sdr_12.9755.ckpt",
        "BS-RoFormer 368 (Vocals)": "model_bs_roformer_ep_368_sdr_12.9628.ckpt",
    }
    parameter_specs = (
        ParameterSpec(
            "model_label", next(iter(MODELS)),
            stale=StalePolicy.STRUCTURAL,
        ),
    )
    parameter_control_specs = (
        ParameterControlSpec(
            "model_label", ComboSpec("Model", tuple(MODELS), width=220)),
    )

    class_node_theme_templates = []
    class_button_theme_templates = [
        ButtonThemeTemplate(
            theme_name="out_vocal_pin",
            base_color=[100, 75, 150, 180],
            hover_color=[100, 75, 150, 180],
            active_color=[100, 75, 150, 180],
            rounding=4, padding=(6, 3)),
        ButtonThemeTemplate(
            theme_name="out_inst_pin",
            base_color=[75, 150, 90, 180],
            hover_color=[75, 150, 90, 180],
            active_color=[75, 150, 90, 180],
            rounding=4, padding=(6, 3)),
    ]

    def __init__(self, node_tag):
        super().__init__(node_tag)
        self._processor = BSRoformerProcessor()

        self.in_mix = self._register_input("in_mix", DataType.AUDIO)

        self.out_vocal = self._register_output("out_vocal", DataType.AUDIO)
        self.out_inst = self._register_output("out_inst", DataType.AUDIO)

        self._stem_pins = {
            "vocals": self.out_vocal,
            "instrumental": self.out_inst,
        }

    @property
    def model_name(self) -> str:
        return self.MODELS[self.model_label]

    def get_param_signature(self) -> str:
        return self.model_name

    def _draw_node_internal(self):
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input, tag=self.in_mix):
            self._draw_pin("Mix", is_output=False, pin_theme=self.get_global_button_theme("default_input_pin"))
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output, tag=self.out_vocal):
            self._draw_pin("Vocal", is_output=True, pin_theme=self.get_class_button_theme("out_vocal_pin"))
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output, tag=self.out_inst):
            self._draw_pin("Instrumental", is_output=True, pin_theme=self.get_class_button_theme("out_inst_pin"))

        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
            dpg.add_text("Model params", color=[150, 180, 210])
            self._draw_declared_param("model_label")

    def publish_outputs(self) -> bool:
        return self._publish_cached(*self._stem_pins.values())

    def bake(self) -> bool:
        mix_packet = self.input_values.get(self.in_mix)
        if mix_packet is None or mix_packet.data is None:
            append_log(f"[{self.display_name}] Bake failed: no input mix connected (bake the upstream source first).", level="WARNING")
            return False

        requested = str(settings.get("inference_device", INFERENCE_DEVICE)).lower()
        append_log(
            f"[{self.display_name}] Separating {mix_packet.data.shape[-1]} samples "
            f"({self.model_label}; first run downloads the model)...",
            level="INFO")
        result = self._processor.separate(
            mix_packet.data,
            model_name=self.model_name,
            requested_device=requested,
            sample_rate=GLOBAL_SAMPLE_RATE,
            ffmpeg_cache_dir=_FFMPEG_CACHE_DIR,
            progress=self._report_progress,
        )
        if not result.ok:
            logger.error(
                "[%s] %s: %s",
                self.display_name, result.error.message, result.error.detail)
            append_log(
                f"[{self.display_name}] {result.error.message}: {result.error.detail}",
                level="ERROR")
            self.is_baked = False
            return False

        for stem_name, pin in self._stem_pins.items():
            self.cached_outputs[pin] = result.stems[stem_name]

        self.is_baked = True
        self._report_progress(1.0)
        n_samples = self.cached_outputs[self.out_vocal].shape[1]
        append_log(f"[{self.display_name}] Bake succeeded: {len(self._stem_pins)} stems x {n_samples} samples ({GLOBAL_SAMPLE_RATE}Hz).", level="INFO")
        return True
