"""DearPyGui projection state for stable domain graph connections."""

import dearpygui.dearpygui as dpg
import logging

from core.graph_contracts import (
    CompileMetadataChanged,
    ConnectionAdded,
    ConnectionRemoved,
    GraphReplaced,
    PracticeSettingsChanged,
)
from core.graph_state import GraphConnection
from core.graph_documents import PanelDocument, Position


_runtime_node_view = lambda _node_tag: None
_projected_node_ids: set[str] = set()
_committed_node_positions: dict[str, tuple[float, float]] = {}
logger = logging.getLogger(__name__)


def _delete_ui_item(tag, *, children_only=False) -> bool:
    """Best-effort teardown for DPG items whose registry state may already be stale."""
    if not dpg.does_item_exist(tag):
        if isinstance(tag, str) and dpg.does_alias_exist(tag):
            dpg.remove_alias(tag)
        return False
    try:
        dpg.delete_item(tag, children_only=children_only)
        return True
    except Exception:
        logger.warning("Unable to delete stale DearPyGui item %r", tag, exc_info=True)
        if isinstance(tag, str) and dpg.does_alias_exist(tag):
            dpg.remove_alias(tag)
        return False


def _unbind_old_node_handlers(node_ids) -> None:
    for node_id in node_ids:
        input_tag = f"custom_name_input::{node_id}"
        if not dpg.does_item_exist(input_tag):
            continue
        try:
            dpg.bind_item_handler_registry(input_tag, 0)
        except Exception:
            logger.warning(
                "Unable to unbind stale DearPyGui handler from %r",
                input_tag,
                exc_info=True,
            )


def bind_graph_node_projection(runtime_node_view) -> None:
    global _runtime_node_view
    _runtime_node_view = runtime_node_view


class NodeEditorLinkProjection:
    def __init__(self) -> None:
        self._items_by_connection: dict[str, object] = {}
        self._connections_by_item: dict[object, str] = {}

    def add(self, editor_tag: str, connection: GraphConnection) -> object:
        existing = self._items_by_connection.get(connection.connection_id)
        if existing is not None:
            return existing
        item_id = dpg.add_node_link(
            connection.output_pin,
            connection.input_pin,
            parent=editor_tag,
        )
        dpg.set_item_user_data(item_id, connection.connection_id)
        self._items_by_connection[connection.connection_id] = item_id
        self._connections_by_item[item_id] = connection.connection_id
        return item_id

    def remove(self, connection_id: str) -> None:
        item_id = self._items_by_connection.pop(connection_id, None)
        if item_id is None:
            return
        self._connections_by_item.pop(item_id, None)
        if dpg.does_item_exist(item_id):
            dpg.delete_item(item_id)

    def connection_id_for_item(self, item_id: object) -> str | None:
        return self._connections_by_item.get(item_id)

    def clear(self) -> None:
        for connection_id in tuple(self._items_by_connection):
            self.remove(connection_id)

    def __len__(self) -> int:
        return len(self._items_by_connection)


NODE_EDITOR_LINKS = NodeEditorLinkProjection()


def project_connection_added(event: ConnectionAdded) -> None:
    NODE_EDITOR_LINKS.add(
        event.editor_tag,
        GraphConnection(
            event.connection_id,
            event.output_pin,
            event.input_pin,
        ),
    )


def project_connection_removed(event: ConnectionRemoved) -> None:
    NODE_EDITOR_LINKS.remove(event.connection_id)


def project_node_parameter(node_tag: str, name: str, value) -> None:
    node = _runtime_node_view(node_tag)
    if node is None or name not in node._param_specs:
        return
    node._project_declared_param(name, value)


def project_node_state(node_tag: str, field: str, value, index=None) -> None:
    node = _runtime_node_view(node_tag)
    if node is None:
        return
    node.project_extra_state(field, value, index=index)


def project_node_rename(node_tag: str, name: str) -> None:
    node = _runtime_node_view(node_tag)
    if node is None or node.custom_name != name:
        return
    node.sync_custom_name_ui()


def project_node_spawned(event) -> None:
    node = _runtime_node_view(event.node_tag)
    if node is None:
        return
    node.draw_node(parent="node_editor")
    dpg.set_item_pos(event.node_tag, event.position)
    _projected_node_ids.add(event.node_tag)
    _committed_node_positions[event.node_tag] = tuple(event.position)


def project_node_deleted(event) -> None:
    _projected_node_ids.discard(event.node_tag)
    _committed_node_positions.pop(event.node_tag, None)
    for connection in event.removed_connections:
        NODE_EDITOR_LINKS.remove(connection.connection_id)
    for tag in event.ui_resource_tags:
        if dpg.does_item_exist(tag):
            dpg.delete_item(tag)
    if dpg.does_item_exist(event.panel_tag):
        dpg.delete_item(event.panel_tag)
    if dpg.does_item_exist(event.node_tag):
        dpg.delete_item(event.node_tag)


def project_compile_metadata(event: CompileMetadataChanged) -> None:
    for node_id in event.node_ids:
        node = _runtime_node_view(node_id)
        if node is None:
            continue
        if node_id in event.cycle_node_ids:
            node._draw_highlighted_node()
        elif node_id in event.stale_node_ids or node_id in event.failed_node_ids:
            node._draw_warning_node()
        else:
            node._draw_default_node()


def project_graph_replaced(event: GraphReplaced) -> None:
    logger.debug(
        "Graph projection replace start old_nodes=%d nodes=%d connections=%d "
        "resources=%d panels=%d",
        len(event.old_node_ids), len(event.node_positions), len(event.connections),
        len(event.old_ui_resource_tags), len(event.panel_geometries),
    )
    resolved_nodes = {
        position.node_id: _runtime_node_view(position.node_id)
        for position in event.node_positions
    }
    missing_node_ids = tuple(
        node_id for node_id, node in resolved_nodes.items() if node is None)
    if missing_node_ids:
        logger.error(
            "Graph projection preflight failed missing_runtime_nodes=%s",
            missing_node_ids,
        )
        raise RuntimeError(
            f"Graph projection is missing runtime nodes: {missing_node_ids!r}")

    _projected_node_ids.clear()
    _projected_node_ids.update(position.node_id for position in event.node_positions)
    _committed_node_positions.clear()
    NODE_EDITOR_LINKS.clear()
    _unbind_old_node_handlers(event.old_node_ids)
    _delete_ui_item("node_editor", children_only=True)
    for node_id in event.old_node_ids:
        _delete_ui_item(f"panel_{node_id}")
    for tag in event.old_ui_resource_tags:
        _delete_ui_item(tag)
    logger.debug("Graph projection cleanup complete")

    for position in event.node_positions:
        node = resolved_nodes[position.node_id]
        try:
            node.draw_node(parent="node_editor")
        except Exception:
            logger.exception(
                "Graph projection failed phase=draw-node node_id=%s projected=%d expected=%d",
                position.node_id, len(_committed_node_positions),
                len(event.node_positions),
            )
            raise
        dpg.set_item_pos(position.node_id, (position.x, position.y))
        _committed_node_positions[position.node_id] = (position.x, position.y)
    logger.debug(
        "Graph projection nodes complete projected=%d expected=%d",
        len(_committed_node_positions), len(event.node_positions),
    )
    for connection in event.connections:
        try:
            NODE_EDITOR_LINKS.add("node_editor", connection)
        except Exception:
            logger.exception(
                "Graph projection failed phase=draw-link connection_id=%s projected=%d "
                "expected=%d",
                connection.connection_id,
                len(NODE_EDITOR_LINKS), len(event.connections),
            )
            raise
    logger.debug(
        "Graph projection links complete projected=%d expected=%d",
        len(NODE_EDITOR_LINKS), len(event.connections),
    )
    if event.render_state is not None:
        try:
            state = event.render_state
            for node_id in state.node_ids:
                node = resolved_nodes.get(node_id)
                if node is None or not dpg.does_item_exist(node.progress_bar_tag):
                    continue
                dpg.set_value(
                    node.progress_bar_tag,
                    1.0 if node_id in state.baked_node_ids else 0.0,
                )
            project_compile_metadata(CompileMetadataChanged(
                node_ids=state.node_ids,
                stale_node_ids=state.stale_node_ids,
                failed_node_ids=state.failed_node_ids,
                cycle_node_ids=state.cycle_node_ids,
            ))
            logger.debug(
                "Graph projection render state baked=%d stale=%d failed=%d cycle=%d",
                len(state.baked_node_ids), len(state.stale_node_ids),
                len(state.failed_node_ids), len(state.cycle_node_ids),
            )
        except Exception:
            logger.exception("Graph projection failed phase=render-state")
            raise
    try:
        for geometry in event.panel_geometries:
            node = resolved_nodes.get(geometry.node_id)
            if node is not None:
                node.open_panel(pos=geometry.position, size=geometry.size)
    except Exception:
        logger.exception(
            "Graph projection failed phase=restore-panels expected=%d",
            len(event.panel_geometries),
        )
        raise
    if event.window_states or event.dock_layout is not None:
        from core.graph_documents import Position, WindowDocument
        from ui.workspace_layout import restore_workspace_layout

        windows = {
            state.tag: WindowDocument(
                None if state.position is None else Position(*state.position),
                None if state.size is None else Position(*state.size),
                state.visible,
            )
            for state in event.window_states
        }
        try:
            restore_workspace_layout(windows, event.dock_layout)
        except Exception:
            logger.exception(
                "Graph projection failed phase=restore-workspace windows=%d dock_layout=%s",
                len(windows), event.dock_layout is not None,
            )
            raise
    logger.debug(
        "Graph projection replace complete nodes=%d connections=%d",
        len(_committed_node_positions), len(NODE_EDITOR_LINKS),
    )


def sync_live_graph_workspace() -> None:
    from core.pipeline import sync_history_workspace

    positions = {}
    panels = {}
    for node_id in tuple(_projected_node_ids):
        node = _runtime_node_view(node_id)
        if node is None:
            continue
        if dpg.does_item_exist(node.node_tag):
            positions[node.node_tag] = dpg.get_item_pos(node.node_tag)
        panel = node.serialize_panel()
        if panel is None:
            continue
        panel_position = panel.get("pos")
        panel_size = panel.get("size")
        panels[node.node_tag] = PanelDocument(
            None if panel_position is None else Position(*panel_position),
            None if panel_size is None else Position(*panel_size),
        )
    sync_history_workspace(positions, panels)


def commit_moved_node_positions(*_args) -> None:
    current = {
        node_id: tuple(dpg.get_item_pos(node_id))
        for node_id in _projected_node_ids
        if dpg.does_item_exist(node_id)
    }
    if current == _committed_node_positions:
        return
    sync_live_graph_workspace()
    _committed_node_positions.clear()
    _committed_node_positions.update(current)
    from ui.node_controls import commit_workspace_gesture
    commit_workspace_gesture()


def project_practice_settings(event: PracticeSettingsChanged) -> None:
    from ui.transport_bar import apply_globals
    apply_globals(dict(event.practice.settings))