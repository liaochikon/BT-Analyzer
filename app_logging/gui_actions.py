"""Low-volume, privacy-conscious records of raw GUI input actions.

Semantic callbacks continue to log their domain outcomes normally. This module adds enough raw
input context to reconstruct what the user did immediately before an error without recording
text-field contents, selected file paths, or mouse movement on every frame.
"""

import logging

import dearpygui.dearpygui as dpg


logger = logging.getLogger(__name__)
# GUI actions are part of the session audit trail even when terminal verbosity is raised to
# WARNING/ERROR. Propagated records still use the root console and file handlers.
logger.setLevel(logging.INFO)


def _constant_names(prefix: str) -> dict[int, str]:
    return {
        value: name.removeprefix(prefix)
        for name in dir(dpg)
        if name.startswith(prefix)
        and isinstance((value := getattr(dpg, name)), int)
    }


_KEY_NAMES = _constant_names("mvKey_")
_MOUSE_BUTTON_NAMES = _constant_names("mvMouseButton_")


def _safe_call(operation, default=None):
    try:
        return operation()
    except Exception:
        return default


def _item_description(item) -> str:
    if not item:
        return "none"
    alias = _safe_call(lambda: dpg.get_item_alias(item), "")
    label = _safe_call(lambda: dpg.get_item_label(item), "")
    item_type = _safe_call(lambda: dpg.get_item_type(item), "unknown")
    identity = alias or str(item)
    if label and label != identity:
        return f"{identity!r} label={label!r} type={item_type}"
    return f"{identity!r} type={item_type}"


def _input_context() -> str:
    focused = _safe_call(dpg.get_focused_item)
    active_window = _safe_call(dpg.get_active_window)
    mouse_pos = _safe_call(lambda: dpg.get_mouse_pos(local=False), (None, None))
    return (
        f"focused={_item_description(focused)} "
        f"window={_item_description(active_window)} mouse={tuple(mouse_pos)}"
    )


def log_mouse_click(_sender, app_data) -> None:
    button = _MOUSE_BUTTON_NAMES.get(app_data, str(app_data))
    logger.info("GUI action mouse_click button=%s %s", button, _input_context())


def log_mouse_double_click(_sender, app_data) -> None:
    button = _MOUSE_BUTTON_NAMES.get(app_data, str(app_data))
    logger.info("GUI action mouse_double_click button=%s %s", button, _input_context())


def log_mouse_release(_sender, app_data) -> None:
    button = _MOUSE_BUTTON_NAMES.get(app_data, str(app_data))
    logger.info("GUI action mouse_release button=%s %s", button, _input_context())


def log_mouse_wheel(_sender, app_data) -> None:
    logger.info("GUI action mouse_wheel delta=%s %s", app_data, _input_context())


def log_key_press(_sender, app_data) -> None:
    key = _KEY_NAMES.get(app_data, str(app_data))
    logger.info("GUI action key_press key=%s %s", key, _input_context())


def register_gui_action_handlers() -> None:
    """Add raw action handlers to the currently open DearPyGui handler registry."""
    dpg.add_mouse_click_handler(callback=log_mouse_click)
    dpg.add_mouse_double_click_handler(callback=log_mouse_double_click)
    dpg.add_mouse_release_handler(callback=log_mouse_release)
    dpg.add_mouse_wheel_handler(callback=log_mouse_wheel)
    dpg.add_key_press_handler(callback=log_key_press)
