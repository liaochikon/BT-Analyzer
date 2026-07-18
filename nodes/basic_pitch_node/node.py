import logging
import dearpygui.dearpygui as dpg
from core import *
from app_logging.logging import append_log
from nodes.basic_pitch_node.processor import BasicPitchParameters, BasicPitchProcessor

logger = logging.getLogger(__name__)

class BasicPitchNode(BaseNode):
    display_name = "Basic Pitch"
    category = "Analysis"
    config = NodeConfig(
        processing=NodeProcessingMode.BAKED,
        ui=NodeUIMode.INSPECTOR,
    )
    parameter_specs = (
        ParameterSpec("melodia_trick", True, stale=StalePolicy.STRUCTURAL),
        ParameterSpec("onset_threshold", 0.5, stale=StalePolicy.STRUCTURAL, signature_precision=3),
        ParameterSpec("frame_threshold", 0.3, stale=StalePolicy.STRUCTURAL, signature_precision=3),
        ParameterSpec("minimum_note_length", 127.7, stale=StalePolicy.STRUCTURAL, signature_precision=2),
        ParameterSpec("minimum_frequency", 0.0, stale=StalePolicy.STRUCTURAL, signature_precision=1),
        ParameterSpec("maximum_frequency", 4200.0, stale=StalePolicy.STRUCTURAL, signature_precision=1),
    )
    parameter_control_specs = (
        ParameterControlSpec("melodia_trick", CheckboxSpec("Melodia Trick")),
        ParameterControlSpec("onset_threshold", SliderSpec("Onset", 0.05, 0.95)),
        ParameterControlSpec("frame_threshold", SliderSpec("Frame", 0.05, 0.95)),
        ParameterControlSpec("minimum_note_length", SliderSpec("Min note (ms)", 0.0, 500.0)),
        ParameterControlSpec("minimum_frequency", SliderSpec("Min freq", 0.0, 2000.0, format="%.0f Hz")),
        ParameterControlSpec("maximum_frequency", SliderSpec("Max freq", 0.0, 8000.0, format="%.0f Hz")),
    )

    top_bar_spacer_width = 260

    class_node_theme_templates = []
    class_button_theme_templates = []

    def __init__(self, node_tag):
        super().__init__(node_tag)
        self._processor = BasicPitchProcessor()

        self.in_audio = self._register_input("in_audio", DataType.AUDIO)
        self.out_midi = self._register_output("out_midi", DataType.MIDI)

    def get_param_signature(self) -> str:
        return self._declared_param_signature()

    def _draw_node_internal(self):
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input, tag=self.in_audio):
            self._draw_pin(
                "Audio In", is_output=False,
                pin_theme=self.get_global_button_theme("default_input_pin"), label_width=85)
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output, tag=self.out_midi):
            self._draw_pin(
                "MIDI Out", is_output=True,
                pin_theme=self.get_global_button_theme("default_output_pin"), label_width=85)

        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
            dpg.add_text("Model params", color=[150, 180, 210])
            for name in self._param_control_names:
                self._draw_declared_param(name)

    def publish_outputs(self) -> bool:
        return self._publish_cached(self.out_midi)

    def bake(self) -> bool:
        packet = self.input_values.get(self.in_audio)
        if packet is None or packet.data is None:
            append_log(f"[{self.display_name}] Bake failed: no input audio connected (bake the upstream source first).", level="WARNING")
            self.is_baked = False
            return False

        self._report_progress(0.05)
        try:
            model = self._processor.get_model()
        except Exception as e:
            logger.exception("[%s] Failed to load Basic Pitch model", self.display_name)
            append_log(f"[{self.display_name}] Failed to load Basic Pitch model: {e}", level="ERROR")
            self.is_baked = False
            return False

        self._report_progress(0.15)
        append_log(
            f"[{self.display_name}] Transcribing {packet.data.shape[-1]} samples to MIDI...",
            level="INFO")

        try:
            notes = self._processor.transcribe(
                packet.data,
                model=model,
                sample_rate=GLOBAL_SAMPLE_RATE,
                progress=self._report_progress,
                parameters=BasicPitchParameters(
                    melodia_trick=self.melodia_trick,
                    onset_threshold=self.onset_threshold,
                    frame_threshold=self.frame_threshold,
                    minimum_note_length=self.minimum_note_length,
                    minimum_frequency=(self.minimum_frequency
                                       if self.minimum_frequency > 0 else None),
                    maximum_frequency=(self.maximum_frequency
                                       if self.maximum_frequency > 0 else None),
                ),
            )
        except Exception:
            logger.exception("[%s] Transcription crashed", self.display_name)
            append_log(f"[{self.display_name}] Transcription failed.", level="ERROR")
            self.is_baked = False
            return False

        self.cached_outputs[self.out_midi] = notes
        self.is_baked = True
        self._report_progress(1.0)
        append_log(f"[{self.display_name}] Bake succeeded: {len(notes)} notes transcribed.", level="INFO")
        return True
