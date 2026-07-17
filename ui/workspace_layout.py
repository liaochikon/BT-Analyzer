"""Capture and restore the persistent, preset-owned DearPyGui workspace."""

from pathlib import Path
import tempfile

import dearpygui.dearpygui as dpg

from core.graph_documents import Position, WindowDocument


MANAGED_WINDOW_TAGS = (
    "win_node_editor",
    "win_pedal_workspace",
    "win_transport",
    "win_logger",
    "win_settings",
)


def capture_windows() -> dict[str, WindowDocument]:
    windows = {}
    for tag in MANAGED_WINDOW_TAGS:
        if not dpg.does_item_exist(tag):
            continue
        configuration = dpg.get_item_configuration(tag)
        position = dpg.get_item_pos(tag)
        width, height = dpg.get_item_rect_size(tag)
        if width <= 1 or height <= 1:
            width = configuration.get("width", width)
            height = configuration.get("height", height)
        windows[tag] = WindowDocument(
            Position(float(position[0]), float(position[1])),
            Position(float(width), float(height)),
            bool(configuration.get("show", True)),
        )
    return windows


def capture_dock_layout() -> str | None:
    with tempfile.TemporaryDirectory(prefix="bt_analyzer_layout_") as directory:
        path = Path(directory) / "workspace.ini"
        try:
            dpg.save_init_file(str(path))
        except Exception:
            return None
        if not path.exists():
            return None
        layout = path.read_text(encoding="utf-8")
        return layout or None


def restore_workspace_layout(windows, dock_layout: str | None) -> None:
    if dock_layout:
        with tempfile.TemporaryDirectory(prefix="bt_analyzer_layout_") as directory:
            path = Path(directory) / "workspace.ini"
            path.write_text(dock_layout, encoding="utf-8")
            dpg.configure_app(init_file=str(path), load_init_file=True)

    for tag, window in windows.items():
        if tag not in MANAGED_WINDOW_TAGS or not dpg.does_item_exist(tag):
            continue
        configuration = {"show": window.visible}
        if window.position is not None:
            configuration["pos"] = (window.position.x, window.position.y)
        if window.size is not None:
            configuration["width"] = window.size.x
            configuration["height"] = window.size.y
        dpg.configure_item(tag, **configuration)