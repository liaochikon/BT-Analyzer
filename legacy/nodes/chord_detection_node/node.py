import numpy as np
import dearpygui.dearpygui as dpg
from core import *
from app_logging.logging import append_log
from nodes.chord_detection_node.processor import (
    ChordDetectionProcessor,
    ChordDetectionStatus,
)


class ChordDetectionNode(BaseNode):
    """Detect a chord progression from an audio track, synchronised to a beat grid.

    Computes a chromagram over the whole signal (heavy -> bake), then collapses it to ONE chroma
    vector per beat-grid unit (median over the frames in that unit) before matching against the 24
    major/minor triad templates. Beat-synchronous aggregation is what keeps the progression from
    fragmenting: chords change on beats, so one label per beat-unit (plus adjacent-equal merging)
    yields clean segments instead of the jitter you get from independent 46 ms frames.

    The beat grid comes from a REQUIRED ``Beat In`` (a Beat/Tempo node's MIDI click track); the
    ``Granularity`` selector chooses how finely to sample it (2 beat / 1 beat / 1/2 / 1/4). While
    playing, the node body shows the chord under the playhead. Feed ``Chord Out`` into the Piano
    Roll / Guitar Fretboard, and ``Key Out`` into anything that wants the song key.
    """

    display_name = "Chord Detection"
    category = "Analysis"
    config = NodeConfig(
        processing=NodeProcessingMode.BAKED,
        ui=NodeUIMode.INSPECTOR,
        realtime=True,
    )

    top_bar_spacer_width = 180

    HOP = ChordDetectionProcessor.HOP
    MIN_CHORD_DUR = ChordDetectionProcessor.MIN_CHORD_DURATION
    MAX_BRIDGE_GAP = ChordDetectionProcessor.MAX_BRIDGE_GAP
    CHORD_OCTAVE_BASE = ChordDetectionProcessor.CHORD_OCTAVE_BASE
    CHORD_VELOCITY = ChordDetectionProcessor.CHORD_VELOCITY

    # Granularity of the beat grid the chroma is aggregated onto, expressed as a factor of one
    # beat. >1 groups beats into coarser units (cleaner, e.g. one chord per half-bar); <1
    # subdivides each beat into finer units (catches chords that change off the beat).
    _BEAT_DIV_CHOICES = {"2 beat": 2.0, "1 beat": 1.0, "1/2 beat": 0.5, "1/4 beat": 0.25}
    parameter_specs = (
        ParameterSpec(
            "beat_div", "1 beat",
            stale=StalePolicy.STRUCTURAL,
        ),
    )
    parameter_control_specs = (
        ParameterControlSpec(
            "beat_div", ComboSpec("Granularity", tuple(_BEAT_DIV_CHOICES), width=120)),
    )

    class_node_theme_templates = []
    class_button_theme_templates = []

    def __init__(self, node_tag):
        super().__init__(node_tag)
        self._processor = ChordDetectionProcessor()

        self.in_audio = self._register_input("in_audio", DataType.AUDIO)
        # Beat grid is REQUIRED: connect a Beat/Tempo node's click track. Its MIDI note starts are
        # the beat times we aggregate the chroma onto.
        self.in_beats = self._register_input("in_beats", DataType.MIDI)
        self.out_midi = self._register_output("out_midi", DataType.MIDI)
        self.out_key = self._register_output("out_key", DataType.TEXT)

        self.chord_text_tag = f"chord_text_{self.node_tag}"
        self.key_text_tag = f"key_text_{self.node_tag}"
        self.inspector_chord_text_tag = f"inspector_chord_text_{self.node_tag}"
        self.inspector_key_text_tag = f"inspector_key_text_{self.node_tag}"

        # Chord segments for the realtime playhead readout (parallel arrays + name list).
        self._seg_starts = np.empty(0, dtype=np.float32)
        self._seg_ends = np.empty(0, dtype=np.float32)
        self._seg_names: list[str] = []
        self._last_chord_shown = None
        # Whole-track key estimate (e.g. "G major"), from the averaged chroma at bake time.
        self._key_name = "--"

    def _div_factor(self) -> float:
        return self._BEAT_DIV_CHOICES.get(self.beat_div, 1.0)

    def get_param_signature(self) -> str:
        # Identity = input audio/beat fingerprints (auto) + the chosen granularity.
        return f"chord|div={self._div_factor():.3f}"

    def _draw_node_internal(self):
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input, tag=self.in_audio):
            self._draw_pin(
                "Audio In", is_output=False,
                pin_theme=self.get_global_button_theme("default_input_pin"), label_width=85)
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input, tag=self.in_beats):
            self._draw_pin(
                "Beat In", is_output=False,
                pin_theme=self.get_global_button_theme("default_input_pin"), label_width=85)
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output, tag=self.out_midi):
            self._draw_pin(
                "Chord Out", is_output=True,
                pin_theme=self.get_global_button_theme("default_output_pin"), label_width=85)
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output, tag=self.out_key):
            self._draw_pin(
                "Key Out", is_output=True,
                pin_theme=self.get_global_button_theme("default_output_pin"), label_width=85)

        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
            self._draw_declared_param("beat_div")
            dpg.add_text("Chord: --", tag=self.chord_text_tag, color=[150, 180, 210])
            dpg.add_text("Key: --", tag=self.key_text_tag, color=[150, 180, 210])

    def _draw_inspector(self):
        super()._draw_inspector()
        dpg.add_separator()
        dpg.add_text("Chord: --", tag=self.inspector_chord_text_tag, color=[150, 180, 210])
        dpg.add_text(f"Key: {self._key_name}", tag=self.inspector_key_text_tag, color=[150, 180, 210])

    def bake(self) -> bool:
        audio_packet = self.input_values.get(self.in_audio)
        beat_packet = self.input_values.get(self.in_beats)
        result = self._processor.process(
            audio_packet.data if audio_packet is not None else None,
            beat_packet.data if beat_packet is not None else None,
            beat_factor=self._div_factor(),
            sample_rate=GLOBAL_SAMPLE_RATE,
            progress=self._report_progress,
        )
        if result.status is ChordDetectionStatus.NO_AUDIO:
            append_log(f"[{self.display_name}] Bake failed: no input audio connected (bake the upstream source first).", level="WARNING")
            return False
        if result.status is ChordDetectionStatus.INVALID_BEATS:
            append_log(f"[{self.display_name}] Bake failed: connect a Beat/Tempo source to Beat In (need >=2 beats).", level="WARNING")
            return False
        if result.status is ChordDetectionStatus.DEPENDENCY_UNAVAILABLE:
            append_log(f"[{self.display_name}] librosa unavailable: {result.error}", level="ERROR")
            return False
        if result.status is ChordDetectionStatus.DETECTION_FAILED:
            append_log(f"[{self.display_name}] Chord detection failed.", level="ERROR")
            self.is_baked = False
            return False

        self.cached_outputs[self.out_midi] = result.notes
        self._key_name = result.key_name
        self._seg_starts = np.asarray(
            [segment.start for segment in result.segments], dtype=np.float32)
        self._seg_ends = np.asarray(
            [segment.end for segment in result.segments], dtype=np.float32)
        self._seg_names = [segment.name for segment in result.segments]
        self.is_baked = True
        self._report_progress(1.0)
        if result.empty_reason == "no_beats_within_audio":
            append_log(f"[{self.display_name}] No beats within the audio; nothing to detect.", level="WARNING")
            return True
        if result.empty_reason == "no_frames":
            return True
        append_log(
            f"[{self.display_name}] Bake succeeded: {len(result.segments)} chord segments, {len(result.notes)} notes.",
            level="INFO")
        return True

    def on_gui_frame(self):
        chord_tags = (self.chord_text_tag, self.inspector_chord_text_tag)
        key_tags = (self.key_text_tag, self.inspector_key_text_tag)
        if not any(dpg.does_item_exist(tag) for tag in chord_tags):
            return
        # Key label: whole-song, set at bake; refresh from the GUI thread (change-gated) so the
        # bake worker never touches DPG. The label text doubles as the shown state, so compare it.
        key_label = f"Key: {self._key_name}"
        for tag in key_tags:
            if dpg.does_item_exist(tag) and dpg.get_value(tag) != key_label:
                dpg.set_value(tag, key_label)

        name = "--"
        starts = self._seg_starts
        if starts.size:
            pos = TRANSPORT.position_seconds
            k = int(np.searchsorted(starts, pos, side="right")) - 1
            if 0 <= k < starts.size and starts[k] <= pos < self._seg_ends[k]:
                name = self._seg_names[k]
        if name != self._last_chord_shown:
            self._last_chord_shown = name
            for tag in chord_tags:
                if dpg.does_item_exist(tag):
                    dpg.set_value(tag, f"Chord: {name}")

    def publish_outputs(self) -> bool:
        # The chord MIDI is the heavy cached output; the key is cheap node state (not cached, so
        # the ndarray-only disk cache stays intact). Publish the key as a TEXT packet regardless,
        # then defer to the cached-MIDI guard for the bake-required check.
        self.output_values[self.out_key] = DataPacket(self._key_name, DataType.TEXT)
        return self._publish_cached(self.out_midi)
