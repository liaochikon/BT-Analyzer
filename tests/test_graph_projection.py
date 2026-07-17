from core.graph_state import GraphConnection
from ui.graph_projection import NodeEditorLinkProjection


def test_link_projection_maps_domain_ids_to_gui_items(monkeypatch):
    created = []
    user_data = {}
    deleted = []
    existing = set()

    def add_node_link(output_pin, input_pin, *, parent):
        item_id = len(created) + 100
        created.append((item_id, output_pin, input_pin, parent))
        existing.add(item_id)
        return item_id

    monkeypatch.setattr("ui.graph_projection.dpg.add_node_link", add_node_link)
    monkeypatch.setattr(
        "ui.graph_projection.dpg.set_item_user_data",
        lambda item_id, value: user_data.__setitem__(item_id, value),
    )
    monkeypatch.setattr(
        "ui.graph_projection.dpg.does_item_exist",
        lambda item_id: item_id in existing,
    )

    def delete_item(item_id):
        existing.discard(item_id)
        deleted.append(item_id)

    monkeypatch.setattr("ui.graph_projection.dpg.delete_item", delete_item)

    projection = NodeEditorLinkProjection()
    first = GraphConnection("connection_1", "out_1", "in_1")
    second = GraphConnection("connection_2", "out_2", "in_2")

    first_item = projection.add("node_editor", first)
    assert projection.add("node_editor", first) == first_item
    second_item = projection.add("node_editor", second)

    assert created == [
        (100, "out_1", "in_1", "node_editor"),
        (101, "out_2", "in_2", "node_editor"),
    ]
    assert user_data == {100: "connection_1", 101: "connection_2"}
    assert projection.connection_id_for_item(100) == "connection_1"
    assert projection.connection_id_for_item(101) == "connection_2"

    projection.remove("connection_1")
    assert projection.connection_id_for_item(100) is None
    assert deleted == [100]

    projection.clear()
    assert projection.connection_id_for_item(101) is None
    assert deleted == [100, 101]


def test_graph_replacement_rebuilds_nodes_positions_and_links(monkeypatch):
    from core.graph_contracts import (
        GraphNodePosition,
        GraphPanelGeometry,
        GraphReplaced,
        GraphWindowState,
    )
    from ui import graph_projection
    from ui import workspace_layout

    calls = []

    class Node:
        def __init__(self, node_id):
            self.node_id = node_id

        def draw_node(self, *, parent=None):
            calls.append(("draw", self.node_id, parent))

        def open_panel(self, *, pos, size):
            calls.append(("panel", self.node_id, pos, size))

    nodes = {"node_1": Node("node_1"), "node_2": Node("node_2")}
    graph_projection.bind_graph_node_projection(nodes.get)
    monkeypatch.setattr(graph_projection.NODE_EDITOR_LINKS, "clear", lambda: calls.append(("clear",)))
    monkeypatch.setattr(
        graph_projection.NODE_EDITOR_LINKS, "add",
        lambda editor, connection: calls.append(("link", editor, connection.connection_id)),
    )
    monkeypatch.setattr(
        graph_projection.dpg,
        "does_item_exist",
        lambda tag: tag in {"node_editor", "handler::old"},
    )
    monkeypatch.setattr(
        graph_projection.dpg,
        "does_alias_exist",
        lambda _tag: False,
    )
    monkeypatch.setattr(
        graph_projection.dpg, "delete_item",
        lambda tag, **kwargs: calls.append(("delete", tag, kwargs)),
    )
    monkeypatch.setattr(
        graph_projection.dpg, "set_item_pos",
        lambda tag, pos: calls.append(("position", tag, pos)),
    )
    monkeypatch.setattr(
        workspace_layout,
        "restore_workspace_layout",
        lambda windows, dock: calls.append(("workspace", windows, dock)),
    )
    event = GraphReplaced(
        old_node_ids=("node_old",),
        node_positions=(
            GraphNodePosition("node_1", 10.0, 20.0),
            GraphNodePosition("node_2", 30.0, 40.0),
        ),
        connections=(GraphConnection("connection_1", "out", "in"),),
        panel_geometries=(GraphPanelGeometry(
            "node_2", (100.0, 200.0), (400.0, 300.0)),),
        window_states=(GraphWindowState(
            "win_node_editor", (50.0, 60.0), (800.0, 600.0), False),),
        dock_layout="[Docking][Data]",
        old_ui_resource_tags=("handler::old",),
    )

    graph_projection.project_graph_replaced(event)

    assert calls[:-1] == [
        ("clear",),
        ("delete", "node_editor", {"children_only": True}),
        ("delete", "handler::old", {"children_only": False}),
        ("draw", "node_1", "node_editor"),
        ("position", "node_1", (10.0, 20.0)),
        ("draw", "node_2", "node_editor"),
        ("position", "node_2", (30.0, 40.0)),
        ("link", "node_editor", "connection_1"),
        ("panel", "node_2", (100.0, 200.0), (400.0, 300.0)),
    ]
    workspace_call = calls[-1]
    assert workspace_call[0] == "workspace"
    assert workspace_call[1]["win_node_editor"].visible is False
    assert workspace_call[2] == "[Docking][Data]"


def test_graph_replacement_continues_after_stale_resource_delete(monkeypatch):
    from core.graph_contracts import GraphNodePosition, GraphReplaced
    from ui import graph_projection

    calls = []

    class Node:
        def draw_node(self, *, parent=None):
            calls.append(("draw", parent))

    graph_projection.bind_graph_node_projection(lambda _node_id: Node())
    monkeypatch.setattr(graph_projection.NODE_EDITOR_LINKS, "clear", lambda: None)
    monkeypatch.setattr(graph_projection.dpg, "does_item_exist", lambda _tag: True)
    monkeypatch.setattr(graph_projection.dpg, "does_alias_exist", lambda _tag: False)
    monkeypatch.setattr(
        graph_projection.dpg, "bind_item_handler_registry", lambda *_args: None)

    def delete_item(tag, **_kwargs):
        calls.append(("delete", tag))
        if tag == "stale-handler":
            raise SystemError("stale DPG registry")

    monkeypatch.setattr(graph_projection.dpg, "delete_item", delete_item)
    monkeypatch.setattr(graph_projection.dpg, "set_item_pos", lambda *_args: None)

    graph_projection.project_graph_replaced(GraphReplaced(
        old_node_ids=("old",),
        node_positions=(GraphNodePosition("new", 1.0, 2.0),),
        connections=(),
        old_ui_resource_tags=("stale-handler",),
    ))

    assert calls[-1] == ("draw", "node_editor")


def test_graph_replacement_unbinds_custom_name_handlers_before_cleanup(monkeypatch):
    from core.graph_contracts import GraphReplaced
    from ui import graph_projection

    calls = []
    input_tag = "custom_name_input::node_old"
    monkeypatch.setattr(graph_projection.NODE_EDITOR_LINKS, "clear", lambda: None)
    monkeypatch.setattr(
        graph_projection.dpg,
        "does_item_exist",
        lambda tag: tag in {"node_editor", input_tag},
    )
    monkeypatch.setattr(
        graph_projection.dpg,
        "does_alias_exist",
        lambda _tag: False,
    )
    monkeypatch.setattr(
        graph_projection.dpg,
        "bind_item_handler_registry",
        lambda tag, registry: calls.append(("unbind", tag, registry)),
    )
    monkeypatch.setattr(
        graph_projection.dpg,
        "delete_item",
        lambda tag, **_kwargs: calls.append(("delete", tag)),
    )

    graph_projection.project_graph_replaced(GraphReplaced(
        old_node_ids=("node_old",),
        node_positions=(),
        connections=(),
    ))

    assert calls[:2] == [
        ("unbind", input_tag, 0),
        ("delete", "node_editor"),
    ]


def test_graph_replacement_missing_runtime_node_preserves_existing_projection(monkeypatch):
    import pytest

    from core.graph_contracts import GraphNodePosition, GraphReplaced
    from ui import graph_projection

    destructive_calls = []
    graph_projection.bind_graph_node_projection(lambda _node_id: None)
    monkeypatch.setattr(
        graph_projection.NODE_EDITOR_LINKS, "clear",
        lambda: destructive_calls.append("clear-links"),
    )
    monkeypatch.setattr(
        graph_projection.dpg, "delete_item",
        lambda *_args, **_kwargs: destructive_calls.append("delete-item"),
    )

    with pytest.raises(RuntimeError, match="missing runtime nodes"):
        graph_projection.project_graph_replaced(GraphReplaced(
            old_node_ids=("old",),
            node_positions=(GraphNodePosition("missing", 1.0, 2.0),),
            connections=(),
        ))

    assert destructive_calls == []


def test_graph_replacement_projects_progress_and_compile_skins(monkeypatch):
    from core.graph_contracts import GraphNodePosition, GraphRenderState, GraphReplaced
    from ui import graph_projection

    calls = []

    class Node:
        def __init__(self, node_id):
            self.node_id = node_id
            self.progress_bar_tag = f"progress::{node_id}"

        def draw_node(self, *, parent=None):
            calls.append(("draw", self.node_id, parent))

        def _draw_default_node(self):
            calls.append(("default", self.node_id))

        def _draw_warning_node(self):
            calls.append(("warning", self.node_id))

        def _draw_highlighted_node(self):
            calls.append(("cycle", self.node_id))

    nodes = {node_id: Node(node_id) for node_id in ("baked", "stale", "failed", "cycle")}
    graph_projection.bind_graph_node_projection(nodes.get)
    monkeypatch.setattr(graph_projection.NODE_EDITOR_LINKS, "clear", lambda: None)
    monkeypatch.setattr(
        graph_projection.dpg, "does_item_exist",
        lambda tag: tag == "node_editor" or str(tag).startswith("progress::"),
    )
    monkeypatch.setattr(graph_projection.dpg, "does_alias_exist", lambda _tag: False)
    monkeypatch.setattr(graph_projection.dpg, "delete_item", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(graph_projection.dpg, "set_item_pos", lambda *_args: None)
    monkeypatch.setattr(
        graph_projection.dpg, "set_value",
        lambda tag, value: calls.append(("progress", tag, value)),
    )

    node_ids = frozenset(nodes)
    graph_projection.project_graph_replaced(GraphReplaced(
        old_node_ids=(),
        node_positions=tuple(
            GraphNodePosition(node_id, 0.0, 0.0) for node_id in nodes),
        connections=(),
        render_state=GraphRenderState(
            node_ids=node_ids,
            baked_node_ids=frozenset({"baked"}),
            stale_node_ids=frozenset({"stale"}),
            failed_node_ids=frozenset({"failed"}),
            cycle_node_ids=frozenset({"cycle"}),
        ),
    ))

    assert ("progress", "progress::baked", 1.0) in calls
    assert ("progress", "progress::stale", 0.0) in calls
    assert ("default", "baked") in calls
    assert ("warning", "stale") in calls
    assert ("warning", "failed") in calls
    assert ("cycle", "cycle") in calls


def test_workspace_layout_only_controls_managed_peer_windows(monkeypatch):
    from core.graph_documents import Position, WindowDocument
    from ui import workspace_layout

    configured = []
    monkeypatch.setattr(
        workspace_layout.dpg,
        "does_item_exist",
        lambda tag: tag in {"win_node_editor", "temporary_modal"},
    )
    monkeypatch.setattr(
        workspace_layout.dpg,
        "configure_item",
        lambda tag, **kwargs: configured.append((tag, kwargs)),
    )

    workspace_layout.restore_workspace_layout({
        "win_node_editor": WindowDocument(
            Position(10.0, 20.0), Position(700.0, 500.0), False),
        "temporary_modal": WindowDocument(visible=False),
    }, None)

    assert configured == [("win_node_editor", {
        "show": False,
        "pos": (10.0, 20.0),
        "width": 700.0,
        "height": 500.0,
    })]


def test_workspace_layout_round_trips_native_dock_data(monkeypatch):
    from pathlib import Path
    from ui import workspace_layout

    loaded = []
    monkeypatch.setattr(
        workspace_layout.dpg,
        "save_init_file",
        lambda path: Path(path).write_text("[Docking][Data]", encoding="utf-8"),
    )
    monkeypatch.setattr(
        workspace_layout.dpg,
        "configure_app",
        lambda **kwargs: loaded.append((
            Path(kwargs["init_file"]).read_text(encoding="utf-8"),
            kwargs["load_init_file"],
        )),
    )

    dock_layout = workspace_layout.capture_dock_layout()
    workspace_layout.restore_workspace_layout({}, dock_layout)

    assert dock_layout == "[Docking][Data]"
    assert loaded == [(dock_layout, True)]


def test_node_position_history_commits_once_on_mouse_release(monkeypatch):
    from ui import graph_projection, node_controls

    positions = {"node_1": (10.0, 20.0)}
    synced = []
    committed = []
    graph_projection._projected_node_ids.clear()
    graph_projection._projected_node_ids.add("node_1")
    graph_projection._committed_node_positions.clear()
    graph_projection._committed_node_positions["node_1"] = (10.0, 20.0)
    monkeypatch.setattr(
        graph_projection.dpg, "does_item_exist", lambda tag: tag == "node_1")
    monkeypatch.setattr(
        graph_projection.dpg, "get_item_pos", lambda tag: positions[tag])
    monkeypatch.setattr(
        graph_projection, "sync_live_graph_workspace", lambda: synced.append(True))
    monkeypatch.setattr(
        node_controls, "commit_workspace_gesture", lambda: committed.append(True))

    positions["node_1"] = (20.0, 30.0)
    positions["node_1"] = (30.0, 40.0)
    positions["node_1"] = (40.0, 50.0)
    assert committed == []

    graph_projection.commit_moved_node_positions()
    graph_projection.commit_moved_node_positions()

    assert synced == [True]
    assert committed == [True]


def test_graph_replacement_preserves_draw_exception_without_popping_stack(monkeypatch):
    import pytest

    from core.graph_contracts import GraphNodePosition, GraphReplaced
    from ui import graph_projection

    original_error = SystemError("file dialog creation failed")

    class FailingNode:
        def draw_node(self, *, parent=None):
            assert parent == "node_editor"
            raise original_error

    graph_projection.bind_graph_node_projection(
        lambda node_id: FailingNode() if node_id == "node_1" else None)
    monkeypatch.setattr(graph_projection.NODE_EDITOR_LINKS, "clear", lambda: None)
    monkeypatch.setattr(
        graph_projection.dpg, "does_item_exist",
        lambda tag: tag == "node_editor",
    )
    monkeypatch.setattr(graph_projection.dpg, "delete_item", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        graph_projection.dpg,
        "pop_container_stack",
        lambda: pytest.fail("projection must not pop DearPyGui's global container stack"),
    )

    event = GraphReplaced(
        old_node_ids=(),
        node_positions=(GraphNodePosition("node_1", 10.0, 20.0),),
        connections=(),
    )

    with pytest.raises(SystemError) as raised:
        graph_projection.project_graph_replaced(event)

    assert raised.value is original_error