import dearpygui.dearpygui as dpg
import os
from application.compile_events import CompileBusyChanged, CompileTopic
from ui.control import (
    global_key_press_handler,
    global_key_release_handler,
    init_shortcuts,
    set_node_interactions_enabled,
)
from ui.locale import LocaleCode
from ui import i18n
from ui.preset_menu import bind_graph_commands as bind_preset_graph_commands
from ui.preset_menu import build_preset_menu
from ui.analyzer_status import build_analyzer_status
from ui.pedal_workspace import show_pedal_workspace
from ui.node_controls import bind_graph_commands
from ui.graph_projection import commit_moved_node_positions
# Settings window lives in its own module now; re-exported so `from ui import *` and main.py's
# ui_create_settings_window() call keep working unchanged.
from ui.settings_window import ui_create_settings_window
from core import *
from core import settings
from app_logging.logging import *
from app_logging.gui_actions import register_gui_action_handlers


# Project root (one level above this ui/ package). Relative resource paths from settings are
# resolved against it so the app runs regardless of the launch directory.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve_path(path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(_PROJECT_ROOT, path)


class UIConfig:
    def __init__(self):
        name = settings.get("locale")
        self.locale = LocaleCode[name] if name in LocaleCode.__members__ else LocaleCode.ZH_TW
        self.default_layout_file = _resolve_path(settings.get("default_layout_file"))
        self.font_file = _resolve_path(settings.get("font_file"))
        self.font_size = settings.get("font_size")
        self.viewport_title = settings.get("viewport_title")
        self.viewport_width = settings.get("viewport_width")
        self.viewport_height = settings.get("viewport_height")

# The dim ("greyed out") theme bound to the node editor while a compile runs.
_EDITOR_DIM_THEME = None
_DISABLED_GRAPH_ITEMS = set()
_FROZEN_NODE_DRAGGABLE = {}
_HIDDEN_GRAPH_DIALOGS = set()

_INTERACTIVE_ITEM_TYPES = {
    "mvButton", "mvImageButton", "mvCheckbox", "mvCombo", "mvListbox",
    "mvMenu", "mvMenuItem", "mvRadioButton", "mvSelectable", "mvTabButton",
    "mvDatePicker", "mvTimePicker", "mvKnobFloat",
}


def _build_editor_dim_theme():
    global _EDITOR_DIM_THEME
    with dpg.theme() as _EDITOR_DIM_THEME:
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_style(dpg.mvStyleVar_Alpha, 0.5)


def _item_children(tag):
    children = dpg.get_item_children(tag) or {}
    if isinstance(children, dict):
        return [child for slot in children.values() for child in (slot or [])]
    return list(children)


def _supports_enabled_state(tag) -> bool:
    item_type = dpg.get_item_type(tag).rsplit("::", 1)[-1]
    return (
        item_type in _INTERACTIVE_ITEM_TYPES
        or item_type.startswith(("mvDrag", "mvInput", "mvSlider", "mvColor"))
    )


def _set_descendant_controls_enabled(root_tag, enabled: bool) -> None:
    pending = [root_tag]
    while pending:
        tag = pending.pop()
        pending.extend(_item_children(tag))
        item_type = dpg.get_item_type(tag).rsplit("::", 1)[-1]
        if item_type == "mvNode":
            if enabled:
                if tag in _FROZEN_NODE_DRAGGABLE:
                    dpg.configure_item(
                        tag, draggable=_FROZEN_NODE_DRAGGABLE.pop(tag))
            elif tag not in _FROZEN_NODE_DRAGGABLE:
                draggable = dpg.get_item_configuration(tag).get("draggable", True)
                _FROZEN_NODE_DRAGGABLE[tag] = draggable
                dpg.configure_item(tag, draggable=False)
        if not _supports_enabled_state(tag):
            continue
        if enabled:
            if tag in _DISABLED_GRAPH_ITEMS and dpg.does_item_exist(tag):
                dpg.configure_item(tag, enabled=True)
            _DISABLED_GRAPH_ITEMS.discard(tag)
        elif dpg.get_item_configuration(tag).get("enabled", True):
            dpg.configure_item(tag, enabled=False)
            _DISABLED_GRAPH_ITEMS.add(tag)


def _set_graph_interactions_enabled(enabled: bool) -> None:
    set_node_interactions_enabled(enabled)
    aliases = set(dpg.get_aliases())
    roots = {"win_node_editor", "win_pedal_workspace"}
    roots.update(tag for tag in aliases if tag.startswith("panel_"))
    dialog_prefixes = ("file_dialog_", "export_dialog_", "midi_export_dialog_")
    dialogs = {tag for tag in aliases if tag.startswith(dialog_prefixes)}

    if enabled:
        for dialog_tag in tuple(_HIDDEN_GRAPH_DIALOGS):
            if dpg.does_item_exist(dialog_tag):
                dpg.configure_item(dialog_tag, show=True)
            _HIDDEN_GRAPH_DIALOGS.discard(dialog_tag)
    else:
        for dialog_tag in dialogs:
            if dpg.does_item_exist(dialog_tag) and dpg.is_item_shown(dialog_tag):
                dpg.configure_item(dialog_tag, show=False)
                _HIDDEN_GRAPH_DIALOGS.add(dialog_tag)

    for root_tag in roots:
        if dpg.does_item_exist(root_tag):
            _set_descendant_controls_enabled(root_tag, enabled)

    for tag in (
            "menu_presets_topbar", "mi_global_analyze_topbar",
            "mi_show_node_editor", "mi_show_pedal_workspace"):
        if not dpg.does_item_exist(tag):
            continue
        if enabled:
            if tag in _DISABLED_GRAPH_ITEMS:
                dpg.configure_item(tag, enabled=True)
            _DISABLED_GRAPH_ITEMS.discard(tag)
        elif dpg.get_item_configuration(tag).get("enabled", True):
            dpg.configure_item(tag, enabled=False)
            _DISABLED_GRAPH_ITEMS.add(tag)


def handle_node_editor_message(message) -> None:
    if message.topic != CompileTopic.BUSY_CHANGED:
        logger.warning("Node Editor ignored unsupported topic: %s", message.topic)
        return
    event = message.payload
    if not isinstance(event, CompileBusyChanged):
        raise TypeError("compile.busy.changed requires CompileBusyChanged payload")
    if dpg.does_item_exist("win_node_editor"):
        dpg.bind_item_theme("win_node_editor", _EDITOR_DIM_THEME if event.busy else 0)
    _set_graph_interactions_enabled(not event.busy)


def ui_init(ui_config: UIConfig, graph_commands):
    if graph_commands is None:
        raise ValueError("graph_commands is required")
    # Attach action handlers to their ids before the key handler can fire.
    init_shortcuts(graph_commands)
    bind_graph_commands(graph_commands)
    bind_preset_graph_commands(graph_commands)
    with dpg.handler_registry(tag="global_input_handlers"):
        dpg.add_key_press_handler(callback=global_key_press_handler)
        dpg.add_key_release_handler(callback=global_key_release_handler)
        dpg.add_mouse_release_handler(
            button=dpg.mvMouseButton_Left,
            callback=commit_moved_node_positions,
        )
        register_gui_action_handlers()

    scan_and_register_nodes()
    build_all_node_themes()  # themes must be built after the context exists
    _build_editor_dim_theme()

    if ui_config.default_layout_file and os.path.exists(ui_config.default_layout_file):
        dpg.configure_app(docking=True, docking_space=True, init_file=ui_config.default_layout_file)
    else:
        dpg.configure_app(docking=True, docking_space=True)

    with dpg.font_registry():
        sarasa_font = dpg.add_font(ui_config.font_file, ui_config.font_size)
    dpg.bind_font(sarasa_font)

    dpg.create_viewport(
        title=ui_config.viewport_title,
        width=ui_config.viewport_width,
        height=ui_config.viewport_height)

def ui_create_main_window(ui_config: UIConfig, graph_commands):
    if graph_commands is None:
        raise ValueError("graph_commands is required")
    with dpg.window(tag="win_main"):
        with dpg.viewport_menu_bar():
            with dpg.menu(tag="menu_system", label=i18n.tr_reg("menu_system", "top_bar_system")):
                dpg.add_menu_item(tag="mi_settings", label=i18n.tr_reg("mi_settings", "settings_open_btn"),
                                  callback=lambda: dpg.configure_item("win_settings", show=True))
                dpg.add_separator()
                dpg.add_menu_item(tag="mi_exit", label=i18n.tr_reg("mi_exit", "top_bar_file_exit"),
                                  callback=lambda: dpg.stop_dearpygui())
            with dpg.menu(tag="menu_window", label=i18n.tr_reg("menu_window", "top_bar_window")):
                dpg.add_menu_item(tag="mi_show_node_editor", label=i18n.tr_reg("mi_show_node_editor", "top_bar_window_show_node_editor"), callback=lambda: dpg.show_item("win_node_editor"))
                dpg.add_menu_item(tag="mi_show_logger", label=i18n.tr_reg("mi_show_logger", "top_bar_window_show_logger"), callback=lambda: dpg.show_item("win_logger"))
                dpg.add_menu_item(tag="mi_show_transport", label=i18n.tr_reg("mi_show_transport", "top_bar_window_show_transport"), callback=lambda: dpg.show_item("win_transport"))
                dpg.add_menu_item(tag="mi_show_pedal_workspace", label=i18n.tr_reg("mi_show_pedal_workspace", "top_bar_window_show_pedal_workspace"), callback=show_pedal_workspace)
            # Preset dropdown mirrored in the topbar so loading/saving a workspace (nodes + panel
            # layout) is reachable without opening the node editor. Shares build_preset_menu with
            # the node-editor copy; saving from either refreshes both via rebuild_preset_menus().
            dpg.add_menu(tag="menu_presets_topbar", label=i18n.tr_reg("menu_presets_topbar", "node_editor_preset_btn"))
            build_preset_menu("menu_presets_topbar")
            dpg.add_menu_item(
                tag="mi_global_analyze_topbar",
                label=i18n.tr_reg(
                    "mi_global_analyze_topbar", "analyzer_status_bake_btn"),
                callback=graph_commands.analyze_callback,
            )


# Per-view node-editor window geometry: (width, height, min_width, min_height). Simple is a compact
# status panel; Advanced needs room for the graph. Applied on every view switch so the window both
# resizes to a sensible size and cannot be shrunk below what that view needs to stay usable.
_NODE_ADVANCED_ENTER_SIZE = (720, 480)
_NODE_ADVANCED_EXIT_SIZE = (660, 420)
_node_editor_advanced = None


def ui_create_node_editor(graph_commands):
    if graph_commands is None:
        raise ValueError("graph_commands is required")
    connect_callback = graph_commands.connect_callback
    with dpg.window(label=i18n.tr_reg("win_node_editor", "node_editor_window_title"), tag="win_node_editor"):
        with dpg.menu_bar(tag="node_editor_menubar"):
            with dpg.menu(tag="menu_add_node", label=i18n.tr_reg("menu_add_node", "node_editor_add_node_btn")):
                _build_add_node_menu(graph_commands)
            dpg.add_menu(tag="node_editor_preset_menu", label=i18n.tr_reg("node_editor_preset_menu", "node_editor_preset_btn"))
            build_preset_menu()
            # "Analyze" == the same global heavy compile the Simple view's big button runs, so both
            # faces use one shared label (analyzer_status_bake_btn) instead of "Bake" vs "Analyze".
            dpg.add_menu_item(
                tag="mi_global_bake",
                label=i18n.tr_reg("mi_global_bake", "analyzer_status_bake_btn"),
                callback=graph_commands.analyze_callback,
            )
        # Advanced (node-graph) body wrapped in a group: hiding the GROUP reliably hides the node
        # editor, because a node_editor's own `show` flag is ignored at render time in this DPG
        # build (it stays visible). width/height=-1 keeps it filling the window as before.
        with dpg.group(tag="node_advanced_body"):
            dpg.add_separator()
            dpg.add_node_editor(tag="node_editor", callback=connect_callback,
                                minimap=True, minimap_location=dpg.mvNodeMiniMap_Location_BottomRight,
                                width=-1, height=-1)
        # Simple (musician-facing) view: engine health + a big Analyze button, no node graph. Lives
        # in the same window so Simple/Advanced are two faces of one editor, not two peer windows.
        with dpg.group(tag="node_simple_body", show=False):
            build_analyzer_status(
                on_analyze=graph_commands.analyze_callback,
            )
    # Default to Simple so a non-node user is never dropped straight into the graph.
    set_node_editor_view(advanced=False)


def set_node_editor_view(advanced: bool):
    """Project the responsive Simple or Advanced node-editor face without resizing its window.

    Both faces live in win_node_editor. The node graph and its menu bar do NOT honour a `show`
    flag at render time in this DPG build, so we hide the graph by hiding its wrapping GROUP and
    hide the toolbar via the window's `menubar` flag (both verified to work). The ``node_editor``
    container always exists for the pipeline to build into, even while its group is hidden.

    :func:`update_node_editor_view` owns selection from the current content size; this function
    only changes visibility and the menubar so floating and docked geometry remain user-owned.
    """
    global _node_editor_advanced
    _node_editor_advanced = bool(advanced)
    if dpg.does_item_exist("win_node_editor"):
        try:
            dpg.configure_item("win_node_editor", menubar=advanced)
        except Exception:
            pass
    for tag, show in (("node_advanced_body", advanced), ("node_simple_body", not advanced)):
        if dpg.does_item_exist(tag):
            dpg.configure_item(tag, show=show)


def update_node_editor_view():
    """Select Simple/Advanced from current content size with resize hysteresis."""
    if not dpg.does_item_exist("win_node_editor"):
        return
    width, height = dpg.get_item_rect_size("win_node_editor")
    if width <= 1 or height <= 1:
        return
    if _node_editor_advanced:
        if width < _NODE_ADVANCED_EXIT_SIZE[0] or height < _NODE_ADVANCED_EXIT_SIZE[1]:
            set_node_editor_view(False)
    elif width >= _NODE_ADVANCED_ENTER_SIZE[0] and height >= _NODE_ADVANCED_ENTER_SIZE[1]:
        set_node_editor_view(True)


# Preferred ordering of node categories in the "Add Node" dropdown; any category not
# listed here is appended afterwards in alphabetical order.
NODE_CATEGORY_ORDER = ["Input / Output", "Effects", "Mixing", "Analysis", "Display", "Other"]


def _build_add_node_menu(graph_commands=None):
    """Populate the Add Node menu, grouping nodes into a submenu per category."""
    grouped = {}
    for node_name, node_class in NODE_REGISTRY.items():
        category = getattr(node_class, "category", "Other")
        grouped.setdefault(category, []).append((node_name, node_class))

    ordered = [c for c in NODE_CATEGORY_ORDER if c in grouped]
    ordered += [c for c in sorted(grouped) if c not in NODE_CATEGORY_ORDER]

    for category in ordered:
        with dpg.menu(label=category):
            for node_name, node_class in grouped[category]:
                dpg.add_menu_item(
                    label=node_name,
                    callback=graph_commands.spawn_callback if graph_commands else spawn_node_callback,
                    user_data=node_name if graph_commands else node_class,
                )


def ui_create_log_window():
    with dpg.window(
        label=i18n.tr_reg("win_logger", "log_window_title"),
        tag="win_logger",
        width=1080,
        height=200,
        pos=[10, 520]):
        dpg.add_button(tag="btn_clear_log", label=i18n.tr_reg("btn_clear_log", "log_window_clear_btn"),
                       callback=clear_log_canvas, width=100)
        dpg.add_separator()
        dpg.add_child_window(tag="log_scroll_region", width=-1, height=-1, border=False)
