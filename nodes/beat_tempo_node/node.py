import logging
import numpy as np
import dearpygui.dearpygui as dpg
from core import *
from app_logging.logging import append_log
from app_logging.dispatch import run_on_gui
from nodes.beat_tempo_node.processor import BeatTempoProcessor

logger = logging.getLogger(__name__)


class BeatTempoNode(BaseNode):
    """Detect tempo (BPM) and beat positions from an audio track.

    Runs ``librosa.beat.beat_track`` over the whole signal (heavy -> bake), reports the
    estimated tempo, and emits a MIDI "click track": one short note on every detected beat.
    Feed the MIDI out into the Piano Roll / Fretboard to see the beat grid, or export it.
    """

    display_name = "Beat / Tempo"
    category = "Analysis"
    config = NodeConfig(
        processing=NodeProcessingMode.BAKED,
        ui=NodeUIMode.INSPECTOR,
    )

    top_bar_spacer_width = 180

    # Click-track note: a short percussive blip per beat.
    CLICK_PITCH = BeatTempoProcessor.CLICK_PITCH
    CLICK_VELOCITY = BeatTempoProcessor.CLICK_VELOCITY
    CLICK_DURATION = BeatTempoProcessor.CLICK_DURATION

    class_node_theme_templates = []
    class_button_theme_templates = []

    def __init__(self, node_tag):
        super().__init__(node_tag)
        self._processor = BeatTempoProcessor()

        self.in_audio = self._register_input("in_audio", DataType.AUDIO)
        self.out_midi = self._register_output("out_midi", DataType.MIDI)
        # Scalar outputs for the beat grid: detected tempo (BPM) and the phase of beat 1 (the
        # first detected beat time, seconds). Feed these into a Beat Grid node to drive the
        # transport's grid + count-in.
        self.out_tempo = self._register_output("out_tempo", DataType.TEMPO)
        self.out_offset = self._register_output("out_offset", DataType.TIME)

        self.tempo = 0.0
        self.beat_offset = 0.0
        self.bpm_text_tag = f"beat_bpm_{self.node_tag}"
        self.inspector_bpm_text_tag = f"inspector_beat_bpm_{self.node_tag}"
        self.inspector_offset_text_tag = f"inspector_beat_offset_{self.node_tag}"

    def get_param_signature(self) -> str:
        # No tunable parameters; identity comes from the input audio fingerprint.
        return "beat"

    def _draw_node_internal(self):
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input, tag=self.in_audio):
            self._draw_pin(
                "Audio In", is_output=False,
                pin_theme=self.get_global_button_theme("default_input_pin"), label_width=85)
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output, tag=self.out_midi):
            self._draw_pin(
                "Click Out", is_output=True,
                pin_theme=self.get_global_button_theme("default_output_pin"), label_width=85)
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output, tag=self.out_tempo):
            self._draw_pin(
                "Tempo Out", is_output=True,
                pin_theme=self.get_global_button_theme("default_output_pin"), label_width=85)
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output, tag=self.out_offset):
            self._draw_pin(
                "Offset Out", is_output=True,
                pin_theme=self.get_global_button_theme("default_output_pin"), label_width=85)

        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
            label = f"BPM: {self.tempo:.1f}" if self.tempo > 0 else "BPM: --"
            dpg.add_text(label, tag=self.bpm_text_tag, color=[150, 180, 210])

    def _set_bpm_text(self, text: str):
        def _do():
            for tag in (self.bpm_text_tag, self.inspector_bpm_text_tag):
                if dpg.does_item_exist(tag):
                    dpg.set_value(tag, text)
            if dpg.does_item_exist(self.inspector_offset_text_tag):
                dpg.set_value(self.inspector_offset_text_tag, f"First beat: {self.beat_offset:.3f} s")
        run_on_gui(_do)

    def _draw_inspector(self):
        super()._draw_inspector()
        dpg.add_separator()
        label = f"BPM: {self.tempo:.1f}" if self.tempo > 0 else "BPM: --"
        dpg.add_text(label, tag=self.inspector_bpm_text_tag, color=[150, 180, 210])
        dpg.add_text(
            f"First beat: {self.beat_offset:.3f} s" if self.tempo > 0 else "First beat: --",
            tag=self.inspector_offset_text_tag,
            color=[150, 180, 210],
        )

    def bake(self) -> bool:
        audio_packet = self.input_values.get(self.in_audio)
        if audio_packet is None or audio_packet.data is None:
            append_log(f"[{self.display_name}] Bake failed: no input audio connected (bake the upstream source first).", level="WARNING")
            return False

        try:
            result = self._processor.process(
                audio_packet.data,
                sample_rate=GLOBAL_SAMPLE_RATE,
                progress=self._report_progress,
            )
        except ImportError as e:
            logger.exception("[%s] librosa unavailable", self.display_name)
            append_log(f"[{self.display_name}] librosa unavailable: {e}", level="ERROR")
            return False
        except Exception:
            logger.exception("[%s] Beat tracking crashed", self.display_name)
            append_log(f"[{self.display_name}] Beat tracking failed.", level="ERROR")
            self.is_baked = False
            return False

        self.tempo = result.tempo
        self.beat_offset = result.beat_offset
        notes = result.notes

        self.cached_outputs[self.out_midi] = notes
        # Scalar outputs as 0-d float32 arrays so they ride the same cache/fingerprint path.
        self.cached_outputs[self.out_tempo] = np.asarray(self.tempo, dtype=np.float32)
        self.cached_outputs[self.out_offset] = np.asarray(self.beat_offset, dtype=np.float32)
        self.is_baked = True
        self._set_bpm_text(f"BPM: {self.tempo:.1f}  ({len(notes)} beats)")
        self._report_progress(1.0)
        append_log(
            f"[{self.display_name}] Bake succeeded: {self.tempo:.1f} BPM, {len(notes)} beats.",
            level="INFO")
        return True

    def publish_outputs(self) -> bool:
        # Each pin's DataPacket type comes from its registered output_spec (MIDI / TEMPO / TIME).
        return self._publish_cached(self.out_midi, self.out_tempo, self.out_offset)
