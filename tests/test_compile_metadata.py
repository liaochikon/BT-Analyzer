"""Compile metadata ownership and projection contracts."""

import threading


def test_render_state_keeps_evicted_valid_artifact_progress(
        graph_editor, node_registry):
    from core.pipeline import (
        GRAPH_STATE,
        capture_graph_render_state,
        spawn_node_state,
    )

    spawned = spawn_node_state("Audio Input")
    node = GRAPH_STATE.nodes[spawned.node_tag]
    node.baked_signature = "disk-backed-artifact"
    node.cached_outputs = {}
    node.is_baked = False

    assert spawned.node_tag in capture_graph_render_state().baked_node_ids

    GRAPH_STATE.stale_nodes.add(spawned.node_tag)

    assert spawned.node_tag not in capture_graph_render_state().baked_node_ids


def test_compile_worker_metadata_is_detached_until_owner_commit():
    from core import pipeline

    state = pipeline.GRAPH_STATE
    original = (
        list(state.execution_order),
        set(state.stale_nodes),
        set(state.failed_nodes),
        set(state.cycle_nodes),
        dict(state.heavy_descendants_cache),
    )
    try:
        state.execution_order[:] = ["live_node"]
        state.stale_nodes.clear()
        state.stale_nodes.add("stale_node")
        state.failed_nodes.clear()
        state.cycle_nodes.clear()
        state.heavy_descendants_cache.clear()

        with pipeline._compile_session():
            pipeline._metadata().execution_order[:] = ["compiled_node"]
            pipeline._clear_stale("stale_node")
            pipeline._mark_failed("failed_node")

            assert state.execution_order == ["live_node"]
            assert state.stale_nodes == {"stale_node"}
            assert state.failed_nodes == set()

        result = pipeline.COMPILE_COORDINATOR.take_result()

        assert result.execution_order == ("compiled_node",)
        assert result.stale_node_ids == frozenset()
        assert result.failed_node_ids == frozenset({"failed_node"})
        assert result.success is False
    finally:
        state.execution_order[:] = original[0]
        state.stale_nodes.clear()
        state.stale_nodes.update(original[1])
        state.failed_nodes.clear()
        state.failed_nodes.update(original[2])
        state.cycle_nodes.clear()
        state.cycle_nodes.update(original[3])
        state.heavy_descendants_cache.clear()
        state.heavy_descendants_cache.update(original[4])


def test_owner_commits_compile_metadata_on_owner_thread():
    from application.graph_owner import GraphOwner
    from core.bus import Bus, Message
    from core.graph_contracts import (
        CompileCompleted,
        CompileMetadataChanged,
        CompileStatus,
        GraphEndpoint,
        GraphTopic,
        RequestCompile,
        CompileKind,
    )
    from core.graph_documents import GraphDocument

    bus = Bus()
    service_mailbox = bus.register(GraphEndpoint.SERVICE)
    gui_mailbox = bus.register(GraphEndpoint.GUI)
    committed = []
    snapshots = []
    owner = GraphOwner(
        bus=bus,
        mailbox=service_mailbox,
        rename_node=lambda *_args: None,
        publish_snapshot=lambda: snapshots.append(True),
        commit_document=lambda: None,
        document_revision=lambda: 4,
        commit_compile_metadata=lambda command: committed.append(
            (threading.get_ident(), command)),
        replace_graph_candidate=lambda *_args: None,
        undo_graph=lambda: None,
        redo_graph=lambda: None,
        take_history_event=lambda: None,
        reset_history=lambda: None,
        capture_graph_document=lambda: GraphDocument(1, "graph_1", (), ()),
        capture_graph_render_state=lambda: None,
        finalize_graph_replacement=lambda: None,
        set_parameter=lambda *_args: None,
        mutate_node_state=lambda *_args: None,
        connect_pins=lambda *_args: None,
        disconnect_connection=lambda *_args: None,
        spawn_node=lambda *_args: None,
        delete_node=lambda *_args: None,
        commit_topology_changed=lambda *_args: None,
        commit_node_spawned=lambda *_args: None,
        commit_node_deleted=lambda *_args: None,
        commit_node_renamed=lambda *_args: None,
        commit_node_parameter_changed=lambda *_args: None,
        commit_node_state_changed=lambda *_args: None,
        commit_workspace_gesture=lambda _workspace: None,
        request_compile=lambda *_args: True,
        request_preset_load=lambda *_args: None,
        reject_preset_load=lambda *_args: None,
        request_preset_save=lambda *_args: None,
    )
    owner.start()
    try:
        bus.publish(Message(
            source="test",
            destination=GraphEndpoint.SERVICE,
            topic=GraphTopic.COMMAND,
            payload=RequestCompile(CompileKind.ANALYZE),
        ))
        completion = CompileCompleted(
            success=True,
            job_id="compile_1",
            document_revision=4,
            execution_order=("node_1",),
            node_ids=frozenset({"node_1", "node_2"}),
            stale_node_ids=frozenset({"node_1"}),
            failed_node_ids=frozenset(),
            cycle_node_ids=frozenset(),
        )
        bus.publish(Message(
            source="test",
            destination=GraphEndpoint.SERVICE,
            topic=GraphTopic.COMMAND,
            payload=completion,
        ))

        event = gui_mailbox.get(timeout=1.0).payload

        assert event == CompileMetadataChanged(
            node_ids=frozenset({"node_1", "node_2"}),
            stale_node_ids=frozenset({"node_1"}),
            failed_node_ids=frozenset(),
            cycle_node_ids=frozenset(),
        )
        assert committed == [(owner._thread.ident, completion)]
        assert snapshots == [True]
        assert owner.compile_status is CompileStatus.VALID
    finally:
        owner.close()


def test_compile_metadata_projection_restores_resolved_node_theme():
    from core.graph_contracts import CompileMetadataChanged
    from ui.graph_projection import bind_graph_node_projection, project_compile_metadata

    calls = []

    class Node:
        def __init__(self, node_id):
            self.node_id = node_id

        def _draw_highlighted_node(self):
            calls.append((self.node_id, "cycle"))

        def _draw_warning_node(self):
            calls.append((self.node_id, "warning"))

        def _draw_default_node(self):
            calls.append((self.node_id, "default"))

    nodes = {node_id: Node(node_id) for node_id in ("cycle", "stale", "resolved")}
    bind_graph_node_projection(nodes.get)

    project_compile_metadata(CompileMetadataChanged(
        node_ids=frozenset(nodes),
        stale_node_ids=frozenset({"stale"}),
        failed_node_ids=frozenset(),
        cycle_node_ids=frozenset({"cycle"}),
    ))

    assert set(calls) == {
        ("cycle", "cycle"),
        ("stale", "warning"),
        ("resolved", "default"),
    }
