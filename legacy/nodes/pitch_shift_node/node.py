import dearpygui.dearpygui as dpg
from core import *
from app_logging.logging import append_log
from nodes._shared.librosa_effect import AudioEffectStatus
from nodes.pitch_shift_node.processor import PitchShiftProcessor


class PitchShiftNode(BaseNode):
    """Shift an audio track's pitch up/down by a number of semitones, tempo preserved.

    Uses a phase vocoder (librosa.effects.pitch_shift) over the whole signal, so the
    transposition is heavy (full STFT) and runs on a bake. The duration/tempo is kept
    unchanged; only the pitch moves.
    """

    display_name = "Pitch Shift"
    category = "Effects"
    config = NodeConfig(
        processing=NodeProcessingMode.BAKED,
        ui=NodeUIMode.INSPECTOR,
    )
    parameter_specs = (
        ParameterSpec(
            "semitones", 0.0,
            stale=StalePolicy.STRUCTURAL,
            signature_precision=3,
        ),
    )
    parameter_control_specs = (
        ParameterControlSpec(
            "semitones", SliderSpec("Semitones", -12.0, 12.0, format="%.1f")),
    )

    top_bar_spacer_width = 230

    class_node_theme_templates = []
    class_button_theme_templates = []

    def __init__(self, node_tag):
        super().__init__(node_tag)
        self._processor = PitchShiftProcessor()

        self.in_audio = self._register_input("in_audio", DataType.AUDIO)
        self.out_audio = self._register_output("out_audio", DataType.AUDIO)

    def get_param_signature(self) -> str:
        return self._declared_param_signature()

    def _draw_node_internal(self):
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input, tag=self.in_audio):
            self._draw_pin(
                "In", is_output=False,
                pin_theme=self.get_global_button_theme("default_input_pin"))
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output, tag=self.out_audio):
            self._draw_pin(
                "Out", is_output=True,
                pin_theme=self.get_global_button_theme("default_output_pin"))

        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
            dpg.add_text("Pitch params", color=[150, 180, 210])
            self._draw_declared_param("semitones")

    def bake(self) -> bool:
        semitones = float(self.semitones)
        packet = self.input_values.get(self.in_audio)
        result = self._processor.process(
            packet.data if packet is not None else None,
            semitones=semitones,
            sample_rate=GLOBAL_SAMPLE_RATE,
            progress=self._report_progress,
        )
        if result.status is AudioEffectStatus.NO_INPUT:
            append_log(f"[{self.display_name}] Bake failed: no input audio connected (bake the upstream source first).", level="ERROR")
            return False
        if result.status is AudioEffectStatus.DEPENDENCY_UNAVAILABLE:
            append_log(f"[{self.display_name}] librosa unavailable: {result.error}", level="ERROR")
            return False
        if result.status is AudioEffectStatus.EFFECT_FAILED:
            append_log(f"[{self.display_name}] Effect failed.", level="ERROR")
            self.is_baked = False
            return False

        self.cached_outputs[self.out_audio] = result.audio
        self.is_baked = True
        self._report_progress(1.0)
        if abs(semitones) < 1e-6:
            message = "Bake succeeded: passthrough (0 semitones)."
        else:
            message = (f"Bake succeeded: shifted {result.audio.shape[0]}ch x "
                       f"{result.input_samples} samples by {semitones:+.1f} semitones.")
        append_log(f"[{self.display_name}] {message}", level="INFO")
        return True

    def publish_outputs(self) -> bool:
        return self._publish_cached(self.out_audio)
