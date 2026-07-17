"""Tabbed metadata and parameter workspace projected from all runtime nodes."""

import logging

import dearpygui.dearpygui as dpg

from ui import i18n
from core.base_node import NodeUIMode
from ui.surfaces import centered_surface


logger = logging.getLogger(__name__)

WINDOW_TAG = "win_pedal_workspace"
TAB_BAR_TAG = "pedal_workspace_tabs"
EMPTY_TAG = "pedal_workspace_empty"

_rendered_nodes = {}
_rendered_panel_modes = {}
_rendered_aliases = {}
_snapshot_provider = None
_node_resolver = None

def bind_graph_projection(snapshot_provider, node_resolver) -> None:
    global _snapshot_provider, _node_resolver
    _snapshot_provider = snapshot_provider
    _node_resolver = node_resolver
    update_pedal_workspace(force=True)


def inspector_nodes():
    """Resolve projection objects in immutable graph-snapshot order."""
    if _snapshot_provider is None or _node_resolver is None:
        return []
    nodes = []
    for item in _snapshot_provider().nodes:
        node = _node_resolver(item.node_id)
        if node is not None:
            nodes.append(node)
    return nodes


def ui_create_pedal_workspace():
    with dpg.window(
        tag=WINDOW_TAG,
        label=i18n.tr_reg(WINDOW_TAG, "pedal_workspace_title"),
        width=560,
        height=640,
        show=False,
    ):
        dpg.add_text(
            tag=EMPTY_TAG,
            default_value=i18n.tr_reg_value(EMPTY_TAG, "pedal_workspace_empty"),
        )
        dpg.add_tab_bar(tag=TAB_BAR_TAG, show=False)


def _draw_node_tab(node, label):
    tab_tag = f"pedal_tab::{node.node_tag}"
    content_tag = f"pedal_content::{node.node_tag}"
    aliases_before = set(dpg.get_aliases())
    try:
        tab = dpg.add_tab(
            label=label, tag=tab_tag, parent=TAB_BAR_TAG, closable=False)
        content = dpg.add_child_window(
            tag=content_tag, parent=tab, border=False)
        dpg.push_container_stack(content)
        try:
            with dpg.group(horizontal=True):
                node._draw_custom_name_control()
                if node.config.ui is NodeUIMode.INSPECTOR_PANEL:
                    dpg.add_button(
                        tag=f"inspector_open_panel::{node.node_tag}",
                        label=i18n.tr("node_open_panel_btn"),
                        callback=lambda *_args: node.open_panel(),
                        width=180,
                    )
            dpg.add_group(tag=f"pedal_panel_body::{node.node_tag}")
            _render_node_body(node)
        except Exception:
            logger.exception("Failed to draw inspector for %s", node.node_tag)
            dpg.delete_item(content, children_only=True)
            node._clear_ui_resources()
            dpg.add_text(
                i18n.tr("pedal_workspace_build_error").format(name=label),
                color=[220, 100, 100],
            )
            dpg.add_button(
                label=i18n.tr("pedal_workspace_retry"),
                callback=lambda: _retry_inspector(node.node_tag),
            )
        finally:
            dpg.pop_container_stack()
    finally:
        _rendered_aliases[node.node_tag] = set(dpg.get_aliases()) - aliases_before


def _delete_node_tab(node_tag):
    tab_tag = f"pedal_tab::{node_tag}"
    aliases = _rendered_aliases.pop(node_tag, set())
    aliases.add(tab_tag)

    if dpg.does_alias_exist(tab_tag):
        tab_id = dpg.get_alias_id(tab_tag)
        if tab_id and dpg.does_item_exist(tab_id):
            dpg.delete_item(tab_id)
    elif dpg.does_item_exist(tab_tag):
        dpg.delete_item(tab_tag)

    for alias in aliases:
        if not dpg.does_alias_exist(alias):
            continue
        item_id = dpg.get_alias_id(alias)
        if item_id and dpg.does_item_exist(item_id):
            dpg.delete_item(item_id)
        if dpg.does_alias_exist(alias):
            dpg.remove_alias(alias)


def _render_node_body(node, *, detached=None):
    body_tag = f"pedal_panel_body::{node.node_tag}"
    if not dpg.does_item_exist(body_tag):
        return
    dpg.delete_item(body_tag, children_only=True)
    if node.config.ui is not NodeUIMode.INSPECTOR_PANEL:
        with centered_surface(
                tag=f"pedal_fixed_layout::{node.node_tag}", parent=body_tag):
            node._draw_inspector()
        node._sync_control_widgets()
        return

    if detached is None:
        detached = dpg.does_item_exist(node.panel_window_tag)
    action_tag = f"inspector_open_panel::{node.node_tag}"
    if detached:
        dpg.configure_item(
            action_tag,
            label=i18n.tr("node_return_to_workspace_btn"),
            callback=lambda *_args: node._panel_controller.recall(),
        )
    else:
        dpg.configure_item(
            action_tag,
            label=i18n.tr("node_open_panel_btn"),
            callback=lambda *_args: node.open_panel(),
        )
        with dpg.group(parent=body_tag):
            node._draw_panel()
        node._sync_panel_widgets()
    _rendered_panel_modes[node.node_tag] = bool(detached)


def prepare_panel_detach(node_tag) -> None:
    node = _rendered_nodes.get(node_tag)
    if node is not None:
        _render_node_body(node, detached=True)


def restore_embedded_panel(node_tag) -> None:
    node = _rendered_nodes.get(node_tag)
    if node is not None:
        _render_node_body(node, detached=False)


def _retry_inspector(node_tag):
    node = _rendered_nodes.pop(node_tag, None)
    if node is not None:
        node._clear_ui_resources()
    _delete_node_tab(node_tag)
    update_pedal_workspace()


def update_pedal_workspace(force=False):
    """Incrementally synchronize tabs with inspector node membership."""
    if not dpg.does_item_exist(TAB_BAR_TAG):
        return
    nodes = inspector_nodes()
    current = {node.node_tag: node for node in nodes}

    for node_tag in list(_rendered_nodes):
        if node_tag not in current:
            old_node = _rendered_nodes.pop(node_tag)
            old_node._clear_ui_resources()
            _rendered_panel_modes.pop(node_tag, None)
            _delete_node_tab(node_tag)

    dpg.configure_item(EMPTY_TAG, show=not nodes)
    dpg.configure_item(TAB_BAR_TAG, show=bool(nodes))

    class_counts = {}
    for node in nodes:
        class_counts[type(node)] = class_counts.get(type(node), 0) + 1
    class_seen = {}

    for node in nodes:
        cls = type(node)
        class_seen[cls] = class_seen.get(cls, 0) + 1
        label = node.title
        if not node.custom_name and class_counts[cls] > 1:
            label = f"{label} {class_seen[cls]}"
        tab_tag = f"pedal_tab::{node.node_tag}"
        rendered = _rendered_nodes.get(node.node_tag)
        if rendered is not None and rendered is not node:
            _rendered_nodes.pop(node.node_tag, None)
            _rendered_panel_modes.pop(node.node_tag, None)
            rendered._clear_ui_resources()
            _delete_node_tab(node.node_tag)
        if node.node_tag in _rendered_nodes and dpg.does_item_exist(tab_tag):
            dpg.configure_item(tab_tag, label=label)
            if node.config.ui is NodeUIMode.INSPECTOR_PANEL:
                detached = dpg.does_item_exist(node.panel_window_tag)
                if _rendered_panel_modes.get(node.node_tag) != detached:
                    _render_node_body(node, detached=detached)
            continue
        _draw_node_tab(node, label)
        _rendered_nodes[node.node_tag] = node


def show_pedal_workspace(node_tag=None):
    update_pedal_workspace()
    if not dpg.does_item_exist(WINDOW_TAG):
        return
    dpg.configure_item(WINDOW_TAG, show=True)
    dpg.focus_item(WINDOW_TAG)
    tab_tag = f"pedal_tab::{node_tag}" if node_tag else None
    if tab_tag and dpg.does_item_exist(tab_tag):
        dpg.set_value(TAB_BAR_TAG, tab_tag)