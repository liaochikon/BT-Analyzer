"""Preset dropdown menu + the Save Preset dialog.

Extracted from ui.ui: this is a self-contained slice of the UI (a menu that can live in more than
one place, plus a modal save dialog). ui.ui imports build_preset_menu / rebuild_preset_menus to
populate the node-editor and topbar menus; everything else here is internal.
"""

import re
import dearpygui.dearpygui as dpg
from ui import i18n
from core.graph_presets import (
    list_presets,
    get_startup_preset_name, set_startup_preset_name,
)
from app_logging.logging import append_log


_graph_commands = None
_pending_overwrite = None


def bind_graph_commands(graph_commands) -> None:
    global _graph_commands
    _graph_commands = graph_commands


def _require_graph_commands():
    if _graph_commands is None:
        raise RuntimeError("graph command publisher is not bound")
    return _graph_commands


def _load_preset(name: str):
    return _require_graph_commands().load_preset(name)


# Every place that hosts a preset dropdown, so saving from one refreshes them all. The node
# editor's menu is created inline (its tag is fixed); the topbar's is added in the main window.
_PRESET_MENUS = ("node_editor_preset_menu", "menu_presets_topbar")


def build_preset_menu(menu_tag="node_editor_preset_menu"):
    """(Re)populate a Preset menu: one load entry per saved preset + a Save Preset action.

    ``menu_tag`` lets the same dropdown live in more than one place (node editor + topbar). The
    save item tag is derived from ``menu_tag`` so each menu's item stays unique (DPG tags must be
    globally unique) and can be relabelled on language switch.
    """
    if not dpg.does_item_exist(menu_tag):
        return
    dpg.delete_item(menu_tag, children_only=True)
    for name in list_presets():
        dpg.add_menu_item(
            parent=menu_tag,
            label=name,
            user_data=name,
            callback=lambda s, a, u: _load_preset(u),
        )
    dpg.add_separator(parent=menu_tag)
    dpg.add_menu_item(
        parent=menu_tag,
        tag=f"mi_preset_save__{menu_tag}",
        label=i18n.tr_reg(f"mi_preset_save__{menu_tag}", "node_editor_preset_save_btn"),
        callback=lambda: _open_preset_save(),
    )


def rebuild_preset_menus():
    """Refresh every preset dropdown that currently exists (skips ones not yet built)."""
    for menu_tag in _PRESET_MENUS:
        build_preset_menu(menu_tag)


def _sanitize_preset_name(raw: str) -> str:
    """Make a user-typed preset name safe to use as a filename (no path traversal/invalid chars)."""
    name = re.sub(r'[\\/:*?"<>|]+', "", raw or "").strip()
    return name.strip(". ")  # drop leading/trailing dots and spaces (blocks '.' / '..')


def _open_preset_save():
    """Show the unified Save Preset dialog: name + 'set as startup default' + 'save parameters'.

    Built lazily on first use. The name is prefilled with the current startup preset so the common
    'overwrite what I'm working on' case is one Enter away, while a new name forks a copy.
    """
    if not dpg.does_item_exist("win_preset_save"):
        with dpg.window(
            label=i18n.tr_reg("win_preset_save", "preset_save_as_title"),
            tag="win_preset_save",
            modal=True, show=False, no_resize=True,
            width=360, pos=[340, 220],
        ):
            dpg.add_input_text(
                tag="preset_save_input",
                label=i18n.tr_reg("preset_save_input", "preset_save_as_name_label"),
                width=200, on_enter=True, callback=_on_preset_save_confirm,
            )
            dpg.add_checkbox(
                tag="preset_save_as_default",
                label=i18n.tr_reg("preset_save_as_default", "preset_save_set_default"),
                default_value=False,
            )
            # Off by default: presets are lean workspace TEMPLATES unless the user opts to bake in
            # their current parameters (file paths / slider values / playbar settings).
            dpg.add_checkbox(
                tag="preset_save_params",
                label=i18n.tr_reg("preset_save_params", "preset_save_include_params"),
                default_value=False,
            )
            dpg.add_text(
                "",
                tag="preset_save_overwrite_warning",
                color=[255, 190, 80, 255],
                show=False,
            )
            dpg.add_separator()
            with dpg.group(horizontal=True):
                dpg.add_button(
                    tag="preset_save_confirm",
                    label=i18n.tr_reg("preset_save_confirm", "preset_save_as_confirm"),
                    callback=_on_preset_save_confirm,
                )
                dpg.add_button(
                    tag="preset_save_cancel",
                    label=i18n.tr_reg("preset_save_cancel", "preset_save_as_cancel"),
                    callback=_cancel_preset_save,
                )
    dpg.set_value("preset_save_input", get_startup_preset_name())
    dpg.set_value("preset_save_as_default", False)
    dpg.set_value("preset_save_params", False)
    _reset_overwrite_confirmation()
    dpg.configure_item("win_preset_save", show=True)


def _on_preset_save_confirm(*_):
    name = _sanitize_preset_name(dpg.get_value("preset_save_input"))
    if not name:
        append_log("Preset name is empty or invalid.", level="WARNING")
        return
    include_params = bool(dpg.get_value("preset_save_params"))
    as_default = bool(dpg.get_value("preset_save_as_default"))
    request = (name, include_params, as_default)
    # DearPyGui does not support stacking a second modal reliably over the save modal. Confirm
    # destructive replacement inline: the first click warns, the second identical request saves.
    if name in list_presets():
        if _pending_overwrite != request:
            _request_overwrite_confirmation(request)
            return
        _reset_overwrite_confirmation()
        append_log(f"Overwriting existing preset '{name}'.", level="INFO")
        _do_save(name, include_params, as_default)
        return
    _reset_overwrite_confirmation()
    _do_save(name, include_params, as_default)


def _request_overwrite_confirmation(request) -> None:
    global _pending_overwrite
    _pending_overwrite = request
    name = request[0]
    message = i18n.tr("preset_overwrite_message").format(name=name)
    append_log(message, level="WARNING")
    dpg.set_value("preset_save_overwrite_warning", message)
    dpg.configure_item("preset_save_overwrite_warning", show=True)
    dpg.configure_item(
        "preset_save_confirm",
        label=i18n.tr("preset_overwrite_confirm"),
    )


def _reset_overwrite_confirmation() -> None:
    global _pending_overwrite
    _pending_overwrite = None
    if dpg.does_item_exist("preset_save_overwrite_warning"):
        dpg.set_value("preset_save_overwrite_warning", "")
        dpg.configure_item("preset_save_overwrite_warning", show=False)
    if dpg.does_item_exist("preset_save_confirm"):
        dpg.configure_item(
            "preset_save_confirm",
            label=i18n.tr("preset_save_as_confirm"),
        )


def _cancel_preset_save() -> None:
    _reset_overwrite_confirmation()
    dpg.configure_item("win_preset_save", show=False)


def _do_save(name, include_params, as_default):
    # Only advance the UI once the write actually succeeded: a failed save must leave the dialog
    # open and the startup preset untouched (save_preset already logged the failure).
    result = _require_graph_commands().save_preset(
        name, include_params, as_default)
    if result.ok:
        dpg.configure_item("preset_save_confirm", enabled=False)
    else:
        append_log("Preset save queue is unavailable; please try again.", level="ERROR")


def handle_preset_save_completed(name: str, as_default: bool, node_count: int) -> None:
    append_log(f"Saved node preset '{name}' ({node_count} nodes).", level="INFO")
    if as_default:
        set_startup_preset_name(name)
        append_log(f"Preset '{name}' set as startup default.", level="INFO")
    rebuild_preset_menus()
    _reset_overwrite_confirmation()
    if dpg.does_item_exist("preset_save_confirm"):
        dpg.configure_item("preset_save_confirm", enabled=True)
    if dpg.does_item_exist("win_preset_save"):
        dpg.configure_item("win_preset_save", show=False)


def handle_preset_save_failed(name: str, message: str) -> None:
    append_log(f"Failed to save preset '{name}': {message}.", level="ERROR")
    if dpg.does_item_exist("preset_save_confirm"):
        dpg.configure_item("preset_save_confirm", enabled=True)
    _reset_overwrite_confirmation()

