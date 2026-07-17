import dearpygui.dearpygui as dpg
from core import *
from app_logging.logging import append_log
from nodes.audio_velocity_map_node.processor import (
    AudioVelocityMapParameters,
    AudioVelocityMapProcessor,
    AudioVelocityMapStatus,
)


class AudioVelocityMapNode(BaseNode):
    """Re-weight a MIDI stream's velocities from a reference audio track.

    For every incoming note we measure the energy of the reference track inside that
    note's frequency band (fundamental +/- a few semitones, optionally summed over a
    handful of harmonics) over the note's time window, then normalise those energies
    across all notes into MIDI velocities 1..127. The pitches/timings are untouched;
    only the velocity field is rewritten.
    """

    display_name = "Audio Velocity Map"
    category = "Analysis"
    config = NodeConfig(
        processing=NodeProcessingMode.BAKED,
        ui=NodeUIMode.INSPECTOR,
    )
    parameter_specs = (
        ParameterSpec("band_semitones", 1.0, stale=StalePolicy.STRUCTURAL, signature_precision=3),
        ParameterSpec("num_harmonics", 1.0, stale=StalePolicy.STRUCTURAL, signature_precision=0),
        ParameterSpec("dynamic_range_db", 48.0, stale=StalePolicy.STRUCTURAL, signature_precision=1),
        ParameterSpec("gain_db", 0.0, stale=StalePolicy.STRUCTURAL, signature_precision=1),
    )
    parameter_control_specs = (
        ParameterControlSpec("band_semitones", SliderSpec("Band (semitones)", 0.25, 6.0)),
        ParameterControlSpec("num_harmonics", SliderSpec("Harmonics", 1.0, 8.0, format="%.0f")),
        ParameterControlSpec("dynamic_range_db", SliderSpec("Dynamic range", 12.0, 96.0, format="%.0f dB")),
        ParameterControlSpec("gain_db", SliderSpec("Gain", -24.0, 24.0, format="%.1f dB")),
    )

    top_bar_spacer_width = 220

    N_FFT = AudioVelocityMapProcessor.N_FFT
    HOP = AudioVelocityMapProcessor.HOP

    class_node_theme_templates = []
    class_button_theme_templates = []

    def __init__(self, node_tag):
        super().__init__(node_tag)
        self._processor = AudioVelocityMapProcessor()

        self.in_midi = self._register_input("in_midi", DataType.MIDI)
        self.in_audio = self._register_input("in_audio", DataType.AUDIO)
        self.out_midi = self._register_output("out_midi", DataType.MIDI)

    def get_param_signature(self) -> str:
        return self._declared_param_signature()

    def _draw_node_internal(self):
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input, tag=self.in_midi):
            self._draw_pin(
                "MIDI In", is_output=False,
                pin_theme=self.get_global_button_theme("default_input_pin"), label_width=85)
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input, tag=self.in_audio):
            self._draw_pin(
                "Audio In", is_output=False,
                pin_theme=self.get_global_button_theme("default_input_pin"), label_width=85)
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output, tag=self.out_midi):
            self._draw_pin(
                "MIDI Out", is_output=True,
                pin_theme=self.get_global_button_theme("default_output_pin"), label_width=85)

        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
            dpg.add_text("Mapping params", color=[150, 180, 210])
            for name in self._param_control_names:
                self._draw_declared_param(name)

    def bake(self) -> bool:
        midi_packet = self.input_values.get(self.in_midi)
        audio_packet = self.input_values.get(self.in_audio)
        notes = midi_packet.data if midi_packet is not None else None
        audio = audio_packet.data if audio_packet is not None else None
        result = self._processor.map_velocities(
            notes,
            audio,
            parameters=AudioVelocityMapParameters(
                band_semitones=self.band_semitones,
                num_harmonics=self.num_harmonics,
                dynamic_range_db=self.dynamic_range_db,
                gain_db=self.gain_db,
            ),
            sample_rate=GLOBAL_SAMPLE_RATE,
            progress=self._report_progress,
        )
        if result.status is AudioVelocityMapStatus.NO_MIDI:
            append_log(f"[{self.display_name}] Bake failed: no MIDI connected.", level="WARNING")
            return False
        if result.status is AudioVelocityMapStatus.NO_AUDIO:
            append_log(f"[{self.display_name}] Bake failed: no reference audio connected (bake the upstream source first).", level="WARNING")
            return False
        if result.status is AudioVelocityMapStatus.MAPPING_FAILED:
            append_log(f"[{self.display_name}] Mapping failed: {result.error}", level="ERROR")
            self.is_baked = False
            return False

        self.cached_outputs[self.out_midi] = result.notes
        self.is_baked = True
        if len(result.notes) == 0:
            return True
        self._report_progress(1.0)
        append_log(f"[{self.display_name}] Bake succeeded: remapped {len(result.notes)} note velocities.", level="INFO")
        return True

    def publish_outputs(self) -> bool:
        return self._publish_cached(self.out_midi)
