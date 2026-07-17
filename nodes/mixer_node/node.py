import dearpygui.dearpygui as dpg
from core import *
from core.graph_contracts import EditPhase, NodeStateAction
from ui.surfaces import centered_surface
from nodes.mixer_node.processor import (
    MixerChannelParameters, MixerParameters, MixerProcessor)


class Mixer8Node(BaseNode):
    display_name = "Mixer"
    category = "Mixing"
    config = NodeConfig(
        processing=NodeProcessingMode.STREAMING,
        ui=NodeUIMode.INSPECTOR_PANEL,
    )

    panel_width = 460
    panel_height = 400

    # Fader range in dB. The bottom is treated as -inf (silence -> linear 0); the top gives a
    # little push headroom (+6 dB ~= 2x). 0 dB = unity.
    MIN_DB = -60.0
    MAX_DB = 6.0

    # Per-channel strip box (child window) size: wide enough for the name field + M/S/R row, tall
    # enough for the name, buttons and the 250px vertical fader.
    STRIP_W = 100
    STRIP_H = 300

    # Max characters for a channel name so it can't overflow the STRIP_W-wide name field.
    LABEL_MAX_CHARS = 10

    class_node_theme_templates = []
    class_button_theme_templates = [
        ButtonThemeTemplate(
            theme_name="ms_off",
            base_color=[70, 70, 70, 180],
            hover_color=[95, 95, 95, 200],
            active_color=[110, 110, 110, 200],
            rounding=3, padding=(4, 2)),
        ButtonThemeTemplate(
            theme_name="solo_on",
            base_color=[200, 170, 60, 220],
            hover_color=[210, 185, 80, 230],
            active_color=[180, 150, 50, 230],
            rounding=3, padding=(4, 2)),
        ButtonThemeTemplate(
            theme_name="mute_on",
            base_color=[190, 70, 70, 220],
            hover_color=[205, 90, 90, 230],
            active_color=[170, 55, 55, 230],
            rounding=3, padding=(4, 2)),
    ]

    def __init__(self, node_tag):
        super().__init__(node_tag)
        self._processor = MixerProcessor()

        self.ch_count = 8

        self.in_chs = [self._register_input(f"in_ch{i}", DataType.AUDIO) for i in range(1, self.ch_count + 1)]
        self.out_master = self._register_output("out_master", DataType.AUDIO)

        # Gain is stored in LINEAR units (the renderer multiplies by these directly). The faders
        # DISPLAY dB (like a DAW): -60 dB..+6 dB, 0 dB = unity = linear 1.0 (the default). The
        # bottom of the range (MIN_DB) means silence -> linear 0. Conversions live in
        # _db_to_lin / _lin_to_db so the linear store and the dB view never drift.
        self.default_gain = 1.0   # linear (0 dB)
        self.vols = [self.default_gain] * self.ch_count
        self.mutes = [False] * self.ch_count
        self.solos = [False] * self.ch_count
        # Per-channel display names (e.g. "Vocals", "Guitar"). Free-text, user-editable in the
        # panel; persisted with the rest of the channel state. Empty falls back to "Ch N".
        self.labels = [self._default_label(i) for i in range(self.ch_count)]

    @staticmethod
    def _default_label(i: int) -> str:
        return f"Ch {i + 1}"

    def get_param_signature(self) -> str:
        # Volumes, mutes and solos all affect the master, so changing any of them changes this
        # node's fingerprint and propagates staleness to downstream consumers.
        return (
            "vols=" + ",".join(f"{v:.4f}" for v in self.vols)
            + "|mutes=" + ",".join("1" if m else "0" for m in self.mutes)
            + "|solos=" + ",".join("1" if s else "0" for s in self.solos)
        )

    def serialize_extra(self) -> dict:
        # The mixer's state is per-channel lists, not the X/X_default scalar convention that
        # serialize_params understands, so it lives here. This is what a preset saved "with
        # parameters" -- and undo/redo -- restore.
        return {
            "vols": [float(v) for v in self.vols],
            "mutes": [bool(m) for m in self.mutes],
            "solos": [bool(s) for s in self.solos],
            "labels": [str(x) for x in self.labels],
        }

    def serialize_template_extra(self) -> dict:
        return {"labels": [str(label) for label in self.labels]}

    def apply_extra(self, data: dict):
        # Restore the channel state, clamped to the current channel count so a preset saved with a
        # different mixer size can't index out of range. Then repaint the panel if it's open.
        vols = data.get("vols") or []
        mutes = data.get("mutes") or []
        solos = data.get("solos") or []
        labels = data.get("labels") or []
        self._apply_extra_state_values(vols, mutes, solos, labels)
        self._refresh_panel_widgets()

    def apply_extra_state(self, data: dict):
        self._apply_extra_state_values(
            data.get("vols") or [],
            data.get("mutes") or [],
            data.get("solos") or [],
            data.get("labels") or [],
        )

    def _apply_extra_state_values(self, vols, mutes, solos, labels):
        for i in range(self.ch_count):
            if i < len(vols):
                self.vols[i] = float(vols[i])
            if i < len(mutes):
                self.mutes[i] = bool(mutes[i])
            if i < len(solos):
                self.solos[i] = bool(solos[i])
            if i < len(labels):
                self.labels[i] = str(labels[i])

    def _db_to_lin(self, db: float) -> float:
        # dB fader value -> linear gain. The very bottom of the range is silence (linear 0), so a
        # channel pulled all the way down is truly muted rather than a faint -60 dB residue.
        return self._processor.db_to_linear(db, minimum_db=self.MIN_DB)

    def _lin_to_db(self, lin: float) -> float:
        # Linear gain -> dB fader value, floored at MIN_DB (also maps linear 0 -> MIN_DB).
        return self._processor.linear_to_db(lin, minimum_db=self.MIN_DB)

    def _parameters(self):
        return MixerParameters(tuple(
            MixerChannelParameters(gain, muted, soloed)
            for gain, muted, soloed in zip(self.vols, self.mutes, self.solos)
        ))

    def _refresh_panel_widgets(self):
        # Push the current channel state into the panel widgets (sliders + M/S button themes), if
        # the panel happens to be open. On a fresh panel the widgets already read the right values
        # from self.vols/mutes/solos at draw time, so this is a no-op then; it matters when the
        # state changed while the panel was already open (rare) or is re-synced on reopen.
        for i in range(self.ch_count):
            vt = f"mixer_vol_{self.node_tag}_{i}"
            if dpg.does_item_exist(vt):
                dpg.set_value(vt, self._lin_to_db(self.vols[i]))
            lt = f"mixer_label_{self.node_tag}_{i}"
            if dpg.does_item_exist(lt):
                dpg.set_value(lt, self.labels[i])
            mt = f"mixer_mute_{self.node_tag}_{i}"
            if dpg.does_item_exist(mt):
                dpg.bind_item_theme(mt, self.get_class_button_theme("mute_on" if self.mutes[i] else "ms_off"))
            st = f"mixer_solo_{self.node_tag}_{i}"
            if dpg.does_item_exist(st):
                dpg.bind_item_theme(st, self.get_class_button_theme("solo_on" if self.solos[i] else "ms_off"))

    def _sync_panel_widgets(self):
        # BaseNode's default only syncs the X/X_default slider convention, which the mixer doesn't
        # use. Override so a reopened panel reflects the live channel state.
        self._refresh_panel_widgets()

    def _draw_node_internal(self):
        for i, in_ch in enumerate(self.in_chs, start=1):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input, tag=in_ch):
                self._draw_pin(f"Ch {i}", is_output=False, pin_theme=self.get_global_button_theme("default_input_pin"))
            
        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output, tag=self.out_master):
            self._draw_pin("Master Out", is_output=True, pin_theme=self.get_global_button_theme("default_output_pin"))

    def _draw_panel(self, sender=None, app_data=None):
        with centered_surface(tag=f"mixer_centered_surface_{self.node_tag}"):
            with dpg.group(tag=f"mixer_content_{self.node_tag}"):
                with dpg.group(horizontal=True):
                    for i in range(self.ch_count):
                        # One fixed-size child window per channel so the strips stay visually
                        # separated (a bordered box each) and don't reflow into one another.
                        with dpg.child_window(width=self.STRIP_W, height=self.STRIP_H,
                                              border=True, no_scrollbar=True):
                            # Editable channel name. Free text (not an i18n string); commits on
                            # enter or focus loss via _on_label. Stable tag so a reopened panel
                            # re-syncs the saved name (see _refresh_panel_widgets).
                            dpg.add_input_text(
                                tag=f"mixer_label_{self.node_tag}_{i}",
                                default_value=self.labels[i], width=-1,
                                on_enter=True, callback=self._on_label, user_data=i)
                            dpg.add_spacer(height=2)
                            with dpg.group(horizontal=True):
                                # M/S buttons carry stable tags + state-driven themes so a panel
                                # (re)opened after a preset/undo restore shows the correct
                                # mute/solo colours, not a blank ms_off. See _sync_panel_widgets.
                                fader_tag = f"mixer_vol_{self.node_tag}_{i}"
                                dpg.add_slider_float(
                                    tag=fader_tag,
                                    default_value=self._lin_to_db(self.vols[i]),
                                    min_value=self.MIN_DB, max_value=self.MAX_DB,
                                    vertical=True, height=250, width=50, format="%.0f dB",
                                    callback=self._on_vol_changed, user_data=i)
                                handler = self._track_ui_resource(
                                    dpg.add_item_handler_registry())
                                dpg.add_item_deactivated_after_edit_handler(
                                    parent=handler,
                                    callback=lambda tag=fader_tag, index=i:
                                        self._parameter_ui.apply_extra(
                                            "volume", dpg.get_value(tag), index=index,
                                            edit_phase=EditPhase.COMMIT))
                                dpg.bind_item_handler_registry(fader_tag, handler)

                                with dpg.group():
                                    m_btn = dpg.add_button(
                                        tag=f"mixer_mute_{self.node_tag}_{i}",
                                        label="M", width=22, callback=self._on_mute, user_data=i)
                                    dpg.bind_item_theme(m_btn, self.get_class_button_theme(
                                        "mute_on" if self.mutes[i] else "ms_off"))
                                    s_btn = dpg.add_button(
                                        tag=f"mixer_solo_{self.node_tag}_{i}",
                                        label="S", width=22, callback=self._on_solo, user_data=i)
                                    dpg.bind_item_theme(s_btn, self.get_class_button_theme(
                                        "solo_on" if self.solos[i] else "ms_off"))
                                    r_btn = dpg.add_button(
                                        label="R", width=22, callback=self._on_reset, user_data=i)
                                    dpg.bind_item_theme(r_btn, self.get_class_button_theme("ms_off"))

    def _on_vol_changed(self, sender, app_data, user_data):
        self._parameter_ui.apply_extra(
            "volume", float(app_data), index=user_data,
            edit_phase=EditPhase.UPDATE)

    def _on_solo(self, sender, app_data, user_data):
        self._parameter_ui.apply_extra(
            "solo", index=user_data, action=NodeStateAction.TOGGLE)

    def _on_mute(self, sender, app_data, user_data):
        self._parameter_ui.apply_extra(
            "mute", index=user_data, action=NodeStateAction.TOGGLE)

    def _on_reset(self, sender, app_data, user_data):
        self._parameter_ui.apply_extra(
            "volume", index=user_data, action=NodeStateAction.RESET)

    def _on_label(self, sender, app_data, user_data):
        # Rename a channel. Purely cosmetic -- it doesn't affect the mix, so no staleness is
        # propagated (and it stays out of get_param_signature). Blank reverts to the "Ch N"
        # default so a strip never shows an empty header; long names are truncated so the text
        # can't overflow the strip's name field.
        i = user_data
        name = (app_data or "").strip()[: self.LABEL_MAX_CHARS] or self._default_label(i)
        self._parameter_ui.apply_extra("label", name, index=i)

    def mutate_extra_state(self, field, value, *, index=None, action=None):
        if not isinstance(index, int) or not 0 <= index < self.ch_count:
            return None
        if field == "volume":
            if action is NodeStateAction.RESET:
                self.vols[index] = self.default_gain
            elif action is NodeStateAction.SET:
                self.vols[index] = self._db_to_lin(float(value))
            else:
                return None
            mark_downstream_heavy_stale(self.node_tag)
            return self.vols[index]
        if field == "mute" and action is NodeStateAction.TOGGLE:
            self.mutes[index] = not self.mutes[index]
            mark_downstream_heavy_stale(self.node_tag)
            return self.mutes[index]
        if field == "solo" and action is NodeStateAction.TOGGLE:
            self.solos[index] = not self.solos[index]
            mark_downstream_heavy_stale(self.node_tag)
            return self.solos[index]
        if field == "label" and action is NodeStateAction.SET:
            name = str(value).strip()[: self.LABEL_MAX_CHARS] or self._default_label(index)
            self.labels[index] = name
            return name
        return None

    def project_extra_state(self, field, value, *, index=None):
        if not isinstance(index, int) or not 0 <= index < self.ch_count:
            return
        if field == "volume":
            tag = f"mixer_vol_{self.node_tag}_{index}"
            if dpg.does_item_exist(tag):
                dpg.set_value(tag, self._lin_to_db(float(value)))
        elif field == "mute":
            tag = f"mixer_mute_{self.node_tag}_{index}"
            if dpg.does_item_exist(tag):
                dpg.bind_item_theme(
                    tag, self.get_class_button_theme("mute_on" if value else "ms_off"))
        elif field == "solo":
            tag = f"mixer_solo_{self.node_tag}_{index}"
            if dpg.does_item_exist(tag):
                dpg.bind_item_theme(
                    tag, self.get_class_button_theme("solo_on" if value else "ms_off"))
        elif field == "label":
            tag = f"mixer_label_{self.node_tag}_{index}"
            if dpg.does_item_exist(tag):
                dpg.set_value(tag, str(value))

    def build_stream_renderer(self, out_pin, input_renderers):
        # Freeze (channel_index, upstream_renderer) pairs at compile time; gains stay live.
        srcs = []
        for in_pin, fn in input_renderers.items():
            try:
                idx = self.in_chs.index(in_pin)
            except ValueError:
                continue
            srcs.append((idx, fn))

        def render(start, frames):
            return self._processor.render(
                srcs, start, frames, self._parameters())

        return render

    def publish_outputs(self) -> bool:
        # Streamable: the master is mixed live per block by build_stream_renderer(). We only
        # publish a fingerprint-carrying packet so staleness still propagates downstream.
        self.output_values[self.out_master] = DataPacket(None, DataType.AUDIO)
        return True
