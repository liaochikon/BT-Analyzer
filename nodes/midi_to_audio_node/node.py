import dearpygui.dearpygui as dpg
from core import *
from app_logging.logging import append_log
from nodes.midi_to_audio_node.processor import (
    MidiToAudioProcessor,
    MidiToAudioStatus,
)


class MidiToAudioNode(BaseNode):
    display_name = "MIDI to Audio"
    category = "Input / Output"
    config = NodeConfig(
        processing=NodeProcessingMode.BAKED,
        ui=NodeUIMode.INSPECTOR,
    )

    class_node_theme_templates = []
    class_button_theme_templates = []

    def __init__(self, node_tag):
        super().__init__(node_tag)
        self._processor = MidiToAudioProcessor()

        self.in_midi = self._register_input("in_midi", DataType.MIDI)
        self.out_audio = self._register_output("out_audio", DataType.AUDIO)

    def _draw_node_internal(self):
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input, tag=self.in_midi):
            self._draw_pin(
                "MIDI In", is_output=False,
                pin_theme=self.get_global_button_theme("default_input_pin"), label_width=85)
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output, tag=self.out_audio):
            self._draw_pin(
                "Audio Out", is_output=True,
                pin_theme=self.get_global_button_theme("default_output_pin"), label_width=85)

    def publish_outputs(self) -> bool:
        # Heavy leaf: the synthesized audio lives in cached_outputs (produced by bake). Publish
        # it so fingerprints propagate; the live audio path pulls the same array via build_baked_renderer.
        return self._publish_cached(self.out_audio)

    def bake(self) -> bool:
        packet = self.input_values.get(self.in_midi)
        notes = packet.data if packet is not None else None
        result = self._processor.synthesize(
            notes,
            sample_rate=GLOBAL_SAMPLE_RATE,
            progress=self._report_progress,
        )
        if result.status is MidiToAudioStatus.NO_INPUT:
            append_log(f"[{self.display_name}] Bake failed: no MIDI connected (bake the upstream Basic Pitch node first).", level="WARNING")
            return False
        if result.status is MidiToAudioStatus.SYNTHESIS_FAILED:
            append_log(f"[{self.display_name}] Synthesis crashed: {result.error}", level="ERROR")
            self.is_baked = False
            return False

        mono = result.audio
        self.cached_outputs[self.out_audio] = mono

        self.is_baked = True
        self._report_progress(1.0)
        append_log(f"[{self.display_name}] Bake succeeded: synthesized {mono.shape[1]} samples from {result.notes} notes ({GLOBAL_SAMPLE_RATE}Hz).", level="INFO")
        return True
