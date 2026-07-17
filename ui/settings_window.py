"""The Settings window and its handlers.

Extracted from ui.ui: a self-contained slice covering language / log level / inference device /
audio device / sample rate / font scale / viewport size / mem-cache / disk-cache / keyboard
shortcuts. ui.ui exposes it from the System menu via ``ui_create_settings_window`` (imported and
re-exported so ``from ui.ui import *`` / main.py keep working). Depends only on core, i18n, keymap,
control, and settings -- not on any ui.ui internals.
"""

import logging
import dearpygui.dearpygui as dpg

from ui import i18n
from ui import keymap
from ui.control import DEFAULT_SEEK_STEPS
from core import *
from core import settings, disk_cache, mem_cache
from app_logging.logging import *

logger = logging.getLogger(__name__)


# Output-device combo label -> sounddevice index (None = system default). Rebuilt each time the
# Settings window is created so it reflects devices present at that moment.
_AUDIO_DEVICE_LABELS: dict = {}


def ui_create_settings_window():
    """Settings window (hidden until opened from the System menu)."""
    with dpg.window(
        label=i18n.tr_reg("win_settings", "settings_window_title"),
        tag="win_settings",
        width=420,
        height=340,
        pos=[320, 160],
        show=False,
    ):
        # Language
        lang_labels = list(i18n.LOCALE_LABELS.values())
        current_lang = i18n.LOCALE_LABELS.get(i18n.current_code(), lang_labels[0])
        dpg.add_combo(
            lang_labels,
            tag="settings_language_combo",
            default_value=current_lang,
            label=i18n.tr_reg("settings_language_combo", "settings_language_label"),
            width=180,
            callback=_on_language_changed,
        )

        # Terminal log verbosity (applied live).
        dpg.add_combo(
            ["DEBUG", "INFO", "WARNING", "ERROR"],
            tag="settings_loglevel_combo",
            default_value=settings.get("log_level", "INFO"),
            label=i18n.tr_reg("settings_loglevel_combo", "settings_loglevel_label"),
            width=180,
            callback=_on_log_level_changed,
        )

        # AI inference device (takes effect on the next bake), with a probe button beside it.
        with dpg.group(horizontal=True):
            dpg.add_combo(
                ["auto", "cuda", "cpu"],
                tag="settings_device_combo",
                default_value=settings.get("inference_device", INFERENCE_DEVICE),
                label=i18n.tr_reg("settings_device_combo", "settings_device_label"),
                width=140,
                callback=_on_device_changed,
            )
            dpg.add_button(
                tag="settings_detect_device_btn",
                label=i18n.tr_reg("settings_detect_device_btn", "settings_detect_device_btn"),
                callback=_on_detect_devices,
            )
        dpg.add_text("", tag="settings_device_status", color=(140, 180, 140))

        dpg.add_separator()

        # Audio output device (applied live: switching restarts the playback stream in place).
        # The setting persists the device LABEL (not its index -- indices are unstable across
        # unplug/replug/reboot), and the label maps to the current index via this dict.
        devices = list_output_devices()
        _AUDIO_DEVICE_LABELS.clear()
        _AUDIO_DEVICE_LABELS["System Default"] = None
        for idx, label in devices:
            _AUDIO_DEVICE_LABELS[label] = idx
        saved_label = settings.get("output_device", None)
        # Show the saved label if that device is currently present, else fall back to default.
        current_dev_label = saved_label if saved_label in _AUDIO_DEVICE_LABELS else "System Default"
        dpg.add_combo(
            list(_AUDIO_DEVICE_LABELS.keys()),
            tag="settings_audio_device_combo",
            default_value=current_dev_label,
            label=i18n.tr_reg("settings_audio_device_combo", "settings_audio_device_label"),
            width=260,
            callback=_on_audio_device_changed,
        )

        # Internal sample rate (applied on next launch; see the note below the combo).
        dpg.add_combo(
            [str(r) for r in SUPPORTED_SAMPLE_RATES],
            tag="settings_sample_rate_combo",
            default_value=str(settings.get("sample_rate", GLOBAL_SAMPLE_RATE)),
            label=i18n.tr_reg("settings_sample_rate_combo", "settings_sample_rate_label"),
            width=140,
            callback=_on_sample_rate_changed,
        )
        dpg.add_text(
            i18n.tr_reg_value("settings_sample_rate_note", "settings_sample_rate_note"),
            tag="settings_sample_rate_note",
            color=(190, 190, 120),
        )

        # Font size (applied live via global font scale), with a reset-to-default button.
        with dpg.group(horizontal=True):
            dpg.add_button(
                tag="settings_fontscale_reset_btn",
                label=i18n.tr_reg("settings_fontscale_reset_btn", "settings_reset_btn"),
                callback=_on_font_scale_reset,
            )
            dpg.add_slider_float(
                tag="settings_fontscale_slider",
                default_value=float(settings.get("font_scale", 1.0)),
                min_value=0.5,
                max_value=2.0,
                format="%.2f",
                width=180,
                label=i18n.tr_reg("settings_fontscale_slider", "settings_fontscale_label"),
                callback=_on_font_scale_changed,
            )

        dpg.add_separator()

        # Default viewport size (remembered for the next launch), with a reset-to-default button.
        with dpg.group(horizontal=True):
            dpg.add_button(
                tag="settings_viewport_reset_btn",
                label=i18n.tr_reg("settings_viewport_reset_btn", "settings_reset_btn"),
                callback=_on_viewport_reset,
            )
            with dpg.group():
                dpg.add_input_int(
                    tag="settings_viewport_w",
                    default_value=int(settings.get("viewport_width")),
                    width=120,
                    label=i18n.tr_reg("settings_viewport_w", "settings_viewport_w_label"),
                    callback=_on_viewport_size_changed,
                )
                dpg.add_input_int(
                    tag="settings_viewport_h",
                    default_value=int(settings.get("viewport_height")),
                    width=120,
                    label=i18n.tr_reg("settings_viewport_h", "settings_viewport_h_label"),
                    callback=_on_viewport_size_changed,
                )
        # Maximise on launch: when on, the window opens maximised (the width/height above are
        # ignored at startup). Applied at next launch, like the size fields.
        dpg.add_checkbox(
            tag="settings_start_maximized",
            default_value=bool(settings.get("start_maximized", False)),
            label=i18n.tr_reg("settings_start_maximized", "settings_start_maximized_label"),
            callback=_on_start_maximized_changed,
        )
        dpg.add_text(
            i18n.tr_reg_value("settings_restart_note", "settings_restart_note"),
            tag="settings_restart_note",
            color=(190, 190, 120),
        )

        dpg.add_separator()

        # In-memory heavy-output cache budget in MiB (applied live; bounds how many baked
        # node outputs stay resident before the LRU spills the oldest to the disk cache).
        dpg.add_input_int(
            tag="settings_mem_cache_mb",
            default_value=int(settings.get("mem_cache_mb", 512)),
            min_value=1,
            min_clamped=True,
            step=64,
            width=120,
            label=i18n.tr_reg("settings_mem_cache_mb", "settings_mem_cache_label"),
            callback=_on_mem_cache_changed,
        )

        # Clear the on-disk render cache now.
        dpg.add_button(
            tag="settings_clear_cache_btn",
            label=i18n.tr_reg("settings_clear_cache_btn", "settings_clear_cache_btn"),
            callback=_on_clear_cache,
        )

        dpg.add_separator()

        # ---- Keyboard shortcuts (collapsible; remap + step sizes) ----
        with dpg.collapsing_header(
                label=i18n.tr_reg("settings_shortcuts_header", "settings_shortcuts_label"),
                tag="settings_shortcuts_header"):

            # Relative-seek step sizes (seconds). These feed seek_back_1..4 / seek_fwd_1..4.
            dpg.add_text(i18n.tr_reg_value("settings_seek_steps_text", "settings_seek_steps_label"),
                         tag="settings_seek_steps_text", color=[150, 180, 210])
            with dpg.group(horizontal=True):
                for i in (1, 2, 3, 4):
                    dpg.add_input_float(
                        tag=f"settings_seek_step_{i}",
                        default_value=float(settings.get(f"seek_step_{i}", DEFAULT_SEEK_STEPS[i])),
                        min_value=0.5, min_clamped=True, max_value=600.0, max_clamped=True,
                        step=0, width=70, format="%.0f",
                        user_data=i, callback=_on_seek_step_changed)

            dpg.add_spacer(height=6)

            # One rebind row per action: label | current binding | Rebind button.
            for action in keymap.ACTIONS:
                with dpg.group(horizontal=True):
                    dpg.add_text(
                        i18n.tr_reg_value(f"kmlabel_{action.action_id}", action.label_key),
                        tag=f"kmlabel_{action.action_id}")
                    dpg.add_text(
                        keymap.binding_label(action.action_id),
                        tag=f"kmbind_{action.action_id}", color=[180, 210, 150])
                    dpg.add_button(
                        label=i18n.tr_reg(f"kmbtn_{action.action_id}", "settings_rebind_btn"),
                        user_data=action.action_id, callback=_on_rebind_clicked)

            dpg.add_spacer(height=6)
            dpg.add_button(
                tag="settings_reset_keys_btn",
                label=i18n.tr_reg("settings_reset_keys_btn", "settings_reset_keys_btn"),
                callback=_on_reset_all_keys)


def _on_language_changed(sender, app_data):
    # app_data is the selected display label; map it back to its LocaleCode and switch live.
    for code, label in i18n.LOCALE_LABELS.items():
        if label == app_data:
            i18n.set_locale(code)
            break


def _on_log_level_changed(sender, app_data):
    set_log_level(getattr(logging, app_data, logging.INFO))
    settings.set("log_level", app_data)


def _on_device_changed(sender, app_data):
    settings.set("inference_device", app_data)


def _on_audio_device_changed(sender, app_data):
    # Applied live: switch the playback output device, restarting the stream in place. If the
    # device can't be opened the engine reverts, so reflect the real state back into the combo.
    # We persist the LABEL (app_data), not the index, so it survives index renumbering.
    device = _AUDIO_DEVICE_LABELS.get(app_data, None)
    if ENGINE.set_device(device):
        # "System Default" persists as None so the app tracks the OS default rather than pinning.
        settings.set("output_device", None if app_data == "System Default" else app_data)
        append_log(f"[Settings] Audio output device set to '{app_data}'.", level="INFO")
    else:
        append_log(f"[Settings] Could not open '{app_data}'; kept the previous device.", level="ERROR")
        # Revert the combo to whatever device is actually active now.
        active = ENGINE.current_device
        for lbl, idx in _AUDIO_DEVICE_LABELS.items():
            if idx == active:
                dpg.set_value("settings_audio_device_combo", lbl)
                break


def _on_sample_rate_changed(sender, app_data):
    # Persist only; the active rate is copied by value across the app at import time, so the
    # new value takes effect on the next launch (the note beside the combo says so).
    try:
        rate = int(app_data)
    except (TypeError, ValueError):
        return
    if rate in SUPPORTED_SAMPLE_RATES:
        settings.set("sample_rate", rate)
        append_log(f"[Settings] Sample rate set to {rate} Hz (applies after restart).", level="INFO")


def _on_mem_cache_changed(sender, app_data):
    mb = max(1, int(app_data))
    mem_cache.MAX_BYTES = mb * 1024 * 1024
    settings.set("mem_cache_mb", mb)


def _on_detect_devices(sender, app_data):
    try:
        import onnxruntime
    except Exception:
        msg = i18n.tr("settings_device_no_onnxruntime")
        dpg.set_value("settings_device_status", msg)
        append_log("[Settings] Device detection: ONNX Runtime not available.", level="WARNING")
        return
    if "CUDAExecutionProvider" in onnxruntime.get_available_providers():
        msg = i18n.tr("settings_device_cuda_available")
    else:
        msg = i18n.tr("settings_device_cpu_only")
    dpg.set_value("settings_device_status", msg)
    append_log("[Settings] Device detection: " + msg, level="INFO")


def _on_font_scale_changed(sender, app_data):
    dpg.set_global_font_scale(float(app_data))
    settings.set("font_scale", float(app_data))


def _on_font_scale_reset(sender, app_data):
    default = 1.0
    dpg.set_value("settings_fontscale_slider", default)
    dpg.set_global_font_scale(default)
    settings.set("font_scale", default)


def _on_viewport_size_changed(sender, app_data):
    settings.set("viewport_width", int(dpg.get_value("settings_viewport_w")))
    settings.set("viewport_height", int(dpg.get_value("settings_viewport_h")))


def _on_start_maximized_changed(sender, app_data):
    settings.set("start_maximized", bool(app_data))


def _on_viewport_reset(sender, app_data):
    w = int(settings.DEFAULTS["viewport_width"])
    h = int(settings.DEFAULTS["viewport_height"])
    dpg.set_value("settings_viewport_w", w)
    dpg.set_value("settings_viewport_h", h)
    settings.set("viewport_width", w)
    settings.set("viewport_height", h)


def _on_clear_cache(sender, app_data):
    disk_cache.clear()
    append_log("Disk cache cleared.", level="INFO")


def _on_seek_step_changed(sender, app_data, user_data):
    # user_data is the step index (1..4); persist the seconds for that relative-seek pair.
    settings.set(f"seek_step_{user_data}", float(app_data))


def _refresh_binding_label(action_id: str):
    tag = f"kmbind_{action_id}"
    if dpg.does_item_exist(tag):
        dpg.set_value(tag, keymap.binding_label(action_id))


def _on_rebind_clicked(sender, app_data, user_data):
    # Enter capture mode: the next key press (in keymap.dispatch) becomes this action's binding.
    action_id = user_data
    tag = f"kmbind_{action_id}"
    if dpg.does_item_exist(tag):
        dpg.set_value(tag, i18n.tr("settings_press_key"))

    def _done(result):
        # result is (key, mods) on success or None if cancelled; refresh the shown binding.
        _refresh_binding_label(action_id)
        if result is not None:
            conflict = keymap.find_conflict(action_id, result[0], result[1])
            if conflict:
                append_log(
                    f"[Settings] '{keymap.binding_label(action_id)}' is also bound to "
                    f"'{conflict}'.", level="WARNING")

    keymap.begin_capture(action_id, _done)


def _on_reset_all_keys(sender, app_data):
    keymap.reset_all()
    for action in keymap.ACTIONS:
        _refresh_binding_label(action.action_id)
    append_log("[Settings] All shortcuts reset to defaults.", level="INFO")
