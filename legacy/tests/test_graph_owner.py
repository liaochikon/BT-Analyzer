import ast
import subprocess
import sys
import threading
from pathlib import Path

from application.graph_application import GraphApplication, GraphCommandPublisher
from application.graph_owner import GraphOwner
from application.compile_events import CompileCompletionPublisher
from core.bus import Bus, BusMessageResult, Message
from core.graph_contracts import (
    ApplyPresetCandidate,
    ConnectionAdded,
    ConnectionRemoved,
    CompileKind,
    CompileCompleted,
    CommitGraphCandidate,
    CompileStatus,
    EditPhase,
    DeleteNode,
    GraphEndpoint,
    GraphNodePosition,
    GraphReplaced,
    GraphTopic,
    MutationRejected,
    NodeRenamed,
    NodeStateAction,
    NodeStateChanged,
    RenameNode,
    UndoGraph,
    ParameterChanged,
    PresetSaveCompleted,
    PresetCaptureRequested,
    PresetDocumentCaptured,
    PresetSaveFailed,
    PracticeSettingsChanged,
)
from core.persistence_contracts import ApplyResult, ApplyStatus
from core.graph_documents import GraphDocument, PracticeDocument, ProjectDocument
from core.graph_state import GraphConnection


def _graph(
    *,
    project_node_rename=lambda *_args: None,
    project_node_parameter=lambda *_args: None,
    apply_preset_candidate=lambda *_args: ApplyResult(ApplyStatus.APPLIED),
    capture_preset_document=lambda *_args: ProjectDocument(
        1, "project_1", GraphDocument(1, "graph_1", (), ())),
    preset_save_completed=lambda *_args: None,
    preset_save_failed=lambda *_args: None,
    complete_preset_apply=lambda *_args: None,
    submit_preset_document=lambda *_args: None,
    publish_snapshot=lambda: None,
):
    return GraphApplication(
        apply_preset_candidate, capture_preset_document,
        preset_save_completed, preset_save_failed,
        project_node_rename=project_node_rename,
        project_node_parameter=project_node_parameter,
        complete_preset_apply=complete_preset_apply,
        submit_preset_document=submit_preset_document,
    )


def _owner(bus, mailbox, rename_node=lambda *_args: None,
           publish_snapshot=lambda: None, **overrides):
    commit_document = overrides.pop("commit_document", publish_snapshot)
    document_revision = overrides.pop("document_revision", lambda: 0)
    commit_compile_metadata = overrides.pop(
        "commit_compile_metadata", lambda *_args: None)
    replace_graph_candidate = overrides.pop(
        "replace_graph_candidate", lambda *_args: None)
    undo_graph = overrides.pop("undo_graph", lambda: None)
    redo_graph = overrides.pop("redo_graph", lambda: None)
    take_history_event = overrides.pop("take_history_event", lambda: None)
    reset_history = overrides.pop("reset_history", lambda: None)
    capture_graph_document = overrides.pop(
        "capture_graph_document",
        lambda: GraphDocument(1, "graph_1", (), ()),
    )
    capture_graph_render_state = overrides.pop(
        "capture_graph_render_state", lambda: None)
    finalize_graph_replacement = overrides.pop(
        "finalize_graph_replacement", lambda: None)
    ports = {
        "set_parameter": lambda *_args: None,
        "mutate_node_state": lambda *_args: None,
        "connect_pins": lambda *_args: None,
        "disconnect_connection": lambda *_args: None,
        "spawn_node": lambda *_args: None,
        "delete_node": lambda *_args: None,
        "commit_topology_changed": lambda *_args: None,
        "commit_node_spawned": lambda *_args: None,
        "commit_node_deleted": lambda *_args: None,
        "commit_node_renamed": lambda *_args: None,
        "commit_node_parameter_changed": lambda *_args: None,
        "commit_node_state_changed": lambda *_args: None,
        "commit_workspace_gesture": lambda _workspace: None,
        "request_compile": lambda *_args: None,
        "request_preset_load": lambda *_args: None,
        "reject_preset_load": lambda *_args: None,
        "request_preset_save": lambda *_args: None,
    }
    ports.update(overrides)
    return GraphOwner(
        bus, mailbox, rename_node, publish_snapshot,
        commit_document, document_revision, commit_compile_metadata,
        replace_graph_candidate, undo_graph, redo_graph, take_history_event, reset_history,
        capture_graph_document,
        capture_graph_render_state,
        finalize_graph_replacement,
        **ports)


def test_rename_mutates_on_owner_thread_then_projects_on_gui_handler():
    bus = Bus()
    service_mailbox = bus.register(GraphEndpoint.SERVICE)
    gui_mailbox = bus.register(GraphEndpoint.GUI)
    renamed = threading.Event()
    owner_thread_ids = []
    snapshots = []
    commits = []
    projections = []

    def rename_node(node_tag, name):
        owner_thread_ids.append(threading.get_ident())
        renamed.set()
        return name.strip()

    owner = _owner(
        bus,
        service_mailbox,
        rename_node,
        lambda: snapshots.append(True),
        commit_node_renamed=lambda *args: commits.append(
            (threading.get_ident(), *args)),
    )
    owner.start()
    publisher = GraphCommandPublisher(bus)
    try:
        assert publisher.rename_node("node_1", " Lead ") is BusMessageResult.ENQUEUED
        message = gui_mailbox.get(timeout=1.0)

        assert renamed.is_set()
        assert owner_thread_ids == [owner._thread.ident]
        assert owner_thread_ids[0] != threading.get_ident()
        assert commits == [(owner._thread.ident, "node_1", "Lead")]
        assert snapshots == [True]
        assert projections == []
        assert message.payload == NodeRenamed("node_1", "Lead")

        _graph(project_node_rename=lambda *args: projections.append(args)).handle_message(message)

        assert projections == [("node_1", "Lead")]
    finally:
        owner.close()


def test_delete_commands_mutate_on_owner_in_fifo_order():
    bus = Bus()
    service_mailbox = bus.register(GraphEndpoint.SERVICE)
    bus.register(GraphEndpoint.GUI)
    deleted = []
    owner = _owner(
        bus, service_mailbox,
        delete_node=lambda node_tag: deleted.append(node_tag),
    )
    owner.start()
    publisher = GraphCommandPublisher(bus)
    try:
        assert publisher.delete_node("node_1") is BusMessageResult.ENQUEUED
        assert publisher.delete_node("node_2") is BusMessageResult.ENQUEUED

        owner.close()
        assert deleted == ["node_1", "node_2"]
    finally:
        owner.close()


def test_parameter_state_changes_on_owner_then_projects_normalized_value():
    bus = Bus()
    service_mailbox = bus.register(GraphEndpoint.SERVICE)
    gui_mailbox = bus.register(GraphEndpoint.GUI)
    state_calls = []
    snapshots = []
    projections = []

    def set_parameter(node_tag, name, value):
        state_calls.append((threading.get_ident(), node_tag, name, value))
        return float(value)

    owner = _owner(
        bus,
        service_mailbox,
        lambda *_args: None,
        lambda: snapshots.append(True),
        set_parameter=set_parameter,
    )
    owner.start()
    try:
        publisher = GraphCommandPublisher(bus)
        assert publisher.set_parameter("node_1", "level", 1) is BusMessageResult.ENQUEUED
        message = gui_mailbox.get(timeout=1.0)

        assert state_calls == [(owner._thread.ident, "node_1", "level", 1)]
        assert snapshots == [True]
        assert message.payload == ParameterChanged("node_1", "level", 1.0)
        assert projections == []

        graph = _graph(project_node_parameter=lambda *args: projections.append(args))
        graph.handle_message(message)

        assert projections == [("node_1", "level", 1.0)]
    finally:
        owner.close()


def test_gesture_commands_commit_history_only_on_release():
    bus = Bus()
    service_mailbox = bus.register(GraphEndpoint.SERVICE)
    gui_mailbox = bus.register(GraphEndpoint.GUI)
    parameter_history = []
    state_history = []
    state_calls = []
    workspace_history = []
    workspace_committed = threading.Event()
    owner = _owner(
        bus,
        service_mailbox,
        set_parameter=lambda _node, _name, value: float(value),
        mutate_node_state=lambda node, field, value, index, action: (
            state_calls.append((threading.get_ident(), node, field, value, index, action)),
            float(value),
        )[1],
        commit_node_parameter_changed=lambda *args: parameter_history.append(args),
        commit_node_state_changed=lambda *args: state_history.append(args),
        commit_workspace_gesture=lambda _workspace: (
            workspace_history.append(threading.get_ident()),
            workspace_committed.set(),
        ),
    )
    owner.start()
    publisher = GraphCommandPublisher(bus)
    try:
        publisher.set_parameter(
            "node_1", "level", 1.0, edit_phase=EditPhase.UPDATE)
        publisher.set_parameter(
            "node_1", "level", 2.0, edit_phase=EditPhase.UPDATE)
        publisher.set_parameter(
            "node_1", "level", 3.0, edit_phase=EditPhase.COMMIT)
        for _ in range(3):
            assert isinstance(gui_mailbox.get(timeout=1.0).payload, ParameterChanged)

        publisher.set_node_state(
            "node_1", "volume", -3.0, index=0,
            action=NodeStateAction.SET, edit_phase=EditPhase.UPDATE)
        publisher.set_node_state(
            "node_1", "volume", -6.0, index=0,
            action=NodeStateAction.SET, edit_phase=EditPhase.UPDATE)
        publisher.set_node_state(
            "node_1", "volume", -9.0, index=0,
            action=NodeStateAction.SET, edit_phase=EditPhase.COMMIT)
        for _ in range(3):
            assert isinstance(gui_mailbox.get(timeout=1.0).payload, NodeStateChanged)

        from core.graph_documents import WorkspaceDocument
        publisher.commit_workspace_gesture(WorkspaceDocument())
        assert workspace_committed.wait(timeout=1.0)

        assert parameter_history == [("node_1", "level", 3.0)]
        assert state_history == [("node_1", "volume", -9.0, 0)]
        assert all(call[0] == owner._thread.ident for call in state_calls)
        assert workspace_history == [owner._thread.ident]
    finally:
        owner.close()


def test_invalid_custom_state_is_rejected_and_owner_keeps_running():
    bus = Bus()
    service_mailbox = bus.register(GraphEndpoint.SERVICE)
    gui_mailbox = bus.register(GraphEndpoint.GUI)

    def mutate_node_state(_node, _field, value, _index, _action):
        if value == "invalid":
            raise ValueError("invalid custom state")
        return float(value)

    owner = _owner(
        bus,
        service_mailbox,
        mutate_node_state=mutate_node_state,
    )
    owner.start()
    publisher = GraphCommandPublisher(bus)
    try:
        publisher.set_node_state("node_1", "volume", "invalid", index=0)
        rejection = gui_mailbox.get(timeout=1.0).payload
        assert isinstance(rejection, MutationRejected)
        assert rejection.reason == "INVALID_NODE_STATE"

        publisher.set_node_state("node_1", "volume", -6.0, index=0)
        changed = gui_mailbox.get(timeout=1.0).payload
        assert changed == NodeStateChanged("node_1", "volume", -6.0, 0)
        assert owner._thread.is_alive()
    finally:
        owner.close()


def test_topology_commands_mutate_and_project_in_owner_fifo_order():
    bus = Bus()
    service_mailbox = bus.register(GraphEndpoint.SERVICE)
    gui_mailbox = bus.register(GraphEndpoint.GUI)
    connections = {}
    thread_ids = []
    commit_calls = []
    snapshots = []
    next_connection_id = 0

    def connect_pins(output_pin, input_pin):
        nonlocal next_connection_id
        thread_ids.append(threading.get_ident())
        if input_pin in connections:
            return None
        next_connection_id += 1
        connection = GraphConnection(
            f"connection_{next_connection_id}", output_pin, input_pin)
        connections[connection.connection_id] = connection
        return connection

    def disconnect_connection(connection_id):
        thread_ids.append(threading.get_ident())
        return connections.pop(connection_id, None)

    owner = _owner(
        bus,
        service_mailbox,
        lambda *_args: None,
        lambda: snapshots.append(True),
        connect_pins=connect_pins,
        disconnect_connection=disconnect_connection,
        commit_topology_changed=lambda *args: commit_calls.append(
            (threading.get_ident(), *args)),
    )
    owner.start()
    publisher = GraphCommandPublisher(bus)
    try:
        publisher.connect_callback("editor", ("out_1", "in_1"))
        publisher.disconnect_connection("connection_1")
        publisher.connect_callback("editor", ("out_2", "in_1"))

        events = [gui_mailbox.get(timeout=1.0).payload for _ in range(3)]

        assert events == [
            ConnectionAdded("connection_1", "editor", "out_1", "in_1"),
            ConnectionRemoved("connection_1", "out_1", "in_1"),
            ConnectionAdded("connection_2", "editor", "out_2", "in_1"),
        ]
        assert connections == {
            "connection_2": GraphConnection("connection_2", "out_2", "in_1"),
        }
        assert thread_ids == [owner._thread.ident] * 3
        assert commit_calls == [
            (owner._thread.ident, "in_1", "new connection"),
            (owner._thread.ident, "in_1", "connection removal"),
            (owner._thread.ident, "in_1", "new connection"),
        ]
        assert snapshots == [True, True, True]
    finally:
        owner.close()


def test_compile_request_dispatches_on_owner_and_completion_publishes_snapshot():
    bus = Bus()
    service_mailbox = bus.register(GraphEndpoint.SERVICE)
    gui_mailbox = bus.register(GraphEndpoint.GUI)
    requested = threading.Event()
    completed = threading.Event()
    request_calls = []

    def request_compile(kind, node_tag):
        request_calls.append((threading.get_ident(), kind, node_tag))
        requested.set()
        return True

    owner = _owner(
        bus,
        service_mailbox,
        lambda *_args: None,
        completed.set,
        request_compile=request_compile,
    )
    owner.start()
    publisher = GraphCommandPublisher(bus)
    try:
        assert publisher.request_compile(
            CompileKind.BAKE_NODE, "node_1") is BusMessageResult.ENQUEUED
        assert requested.wait(timeout=1.0)

        assert request_calls == [
            (owner._thread.ident, CompileKind.BAKE_NODE, "node_1"),
        ]
        assert len(gui_mailbox) == 0
        assert not completed.is_set()
        assert owner.compile_status is CompileStatus.COMPILING

        CompileCompletionPublisher(bus)(False)

        assert completed.wait(timeout=1.0)
        assert owner.compile_status is CompileStatus.VALID
        assert len(gui_mailbox) == 0
    finally:
        owner.close()


def test_compile_busy_rejects_mutations_before_they_reach_state_ports():
    bus = Bus()
    service_mailbox = bus.register(GraphEndpoint.SERVICE)
    gui_mailbox = bus.register(GraphEndpoint.GUI)
    requested = threading.Event()
    renamed = []
    owner = _owner(
        bus,
        service_mailbox,
        rename_node=lambda *args: renamed.append(args),
        request_compile=lambda *_args: requested.set() or True,
    )
    owner.start()
    publisher = GraphCommandPublisher(bus)
    try:
        publisher.request_compile(CompileKind.ANALYZE)
        assert requested.wait(timeout=1.0)
        publisher.rename_node("node_1", "Blocked")

        event = gui_mailbox.get(timeout=1.0).payload

        assert event == MutationRejected("RenameNode", "COMPILE_BUSY")
        assert owner.compile_status is CompileStatus.COMPILING
        assert renamed == []
    finally:
        owner.close()


def test_failed_compile_completion_enters_failed_state():
    bus = Bus()
    service_mailbox = bus.register(GraphEndpoint.SERVICE)
    bus.register(GraphEndpoint.GUI)
    requested = threading.Event()
    completed = threading.Event()
    owner = _owner(
        bus,
        service_mailbox,
        publish_snapshot=completed.set,
        request_compile=lambda *_args: requested.set() or True,
    )
    owner.start()
    publisher = GraphCommandPublisher(bus)
    try:
        publisher.request_compile(CompileKind.ANALYZE)
        assert requested.wait(timeout=1.0)
        bus.publish(Message(
            source="domain.compile",
            destination=GraphEndpoint.SERVICE,
            topic=GraphTopic.COMMAND,
            payload=CompileCompleted(success=False),
        ))

        assert completed.wait(timeout=1.0)
        assert owner.compile_status is CompileStatus.FAILED
    finally:
        owner.close()


def test_rejected_compile_dispatch_keeps_graph_dirty_and_editable():
    bus = Bus()
    service_mailbox = bus.register(GraphEndpoint.SERVICE)
    gui_mailbox = bus.register(GraphEndpoint.GUI)
    requested = threading.Event()
    renamed = []
    owner = _owner(
        bus,
        service_mailbox,
        rename_node=lambda *args: renamed.append(args) or args[1],
        request_compile=lambda *_args: requested.set() or False,
    )
    owner.start()
    publisher = GraphCommandPublisher(bus)
    try:
        publisher.request_compile(CompileKind.ANALYZE)
        assert requested.wait(timeout=1.0)
        publisher.rename_node("node_1", "Allowed")

        event = gui_mailbox.get(timeout=1.0).payload

        assert event == NodeRenamed("node_1", "Allowed")
        assert owner.compile_status is CompileStatus.DIRTY
        assert renamed == [("node_1", "Allowed")]
    finally:
        owner.close()


def test_stale_and_duplicate_compile_completions_cannot_unlock_or_republish():
    bus = Bus()
    service_mailbox = bus.register(GraphEndpoint.SERVICE)
    gui_mailbox = bus.register(GraphEndpoint.GUI)
    requested = threading.Event()
    snapshots = []
    owner = _owner(
        bus,
        service_mailbox,
        publish_snapshot=lambda: snapshots.append(True),
        document_revision=lambda: 12,
        request_compile=lambda *_args: requested.set() or True,
    )
    owner.start()
    publisher = GraphCommandPublisher(bus)
    try:
        publisher.request_compile(CompileKind.ANALYZE)
        assert requested.wait(timeout=1.0)
        completion = owner.compile_completion()
        assert completion == CompileCompleted(
            success=True, job_id="compile_1", document_revision=12)

        bus.publish(Message(
            source="domain.compile",
            destination=GraphEndpoint.SERVICE,
            topic=GraphTopic.COMMAND,
            payload=CompileCompleted(
                success=True, job_id="compile_old", document_revision=11),
        ))
        publisher.rename_node("node_1", "Still blocked")

        assert gui_mailbox.get(timeout=1.0).payload == MutationRejected(
            "RenameNode", "COMPILE_BUSY")
        assert owner.compile_status is CompileStatus.COMPILING
        assert snapshots == []

        bus.publish(Message(
            source="domain.compile",
            destination=GraphEndpoint.SERVICE,
            topic=GraphTopic.COMMAND,
            payload=completion,
        ))
        bus.publish(Message(
            source="domain.compile",
            destination=GraphEndpoint.SERVICE,
            topic=GraphTopic.COMMAND,
            payload=completion,
        ))
        owner.close()

        assert owner.compile_status is CompileStatus.VALID
        assert snapshots == [True]
    finally:
        owner.close()


def test_revision_conflict_rejects_without_mutation_and_latest_retry_commits_once():
    bus = Bus()
    service_mailbox = bus.register(GraphEndpoint.SERVICE)
    gui_mailbox = bus.register(GraphEndpoint.GUI)
    revision = [4]
    renamed = []

    def commit_document():
        revision[0] += 1

    owner = _owner(
        bus,
        service_mailbox,
        rename_node=lambda *args: renamed.append(args) or args[1],
        commit_document=commit_document,
        document_revision=lambda: revision[0],
    )
    owner.start()
    publisher = GraphCommandPublisher(bus)
    try:
        publisher.execute(RenameNode(
            "node_1", "Stale", expected_revision=3, request_id="request_stale"))

        rejected = gui_mailbox.get(timeout=1.0).payload

        assert rejected == MutationRejected(
            "RenameNode",
            "REVISION_CONFLICT",
            request_id="request_stale",
            expected_revision=3,
            current_revision=4,
        )
        assert renamed == []
        assert revision == [4]

        publisher.execute(RenameNode(
            "node_1", "Current", expected_revision=4, request_id="request_current"))

        assert gui_mailbox.get(timeout=1.0).payload == NodeRenamed("node_1", "Current")
        assert renamed == [("node_1", "Current")]
        assert revision == [5]
    finally:
        owner.close()


def test_candidate_replace_commits_once_and_rejection_preserves_revision():
    bus = Bus()
    service_mailbox = bus.register(GraphEndpoint.SERVICE)
    gui_mailbox = bus.register(GraphEndpoint.GUI)
    revision = [6]
    event = GraphReplaced(
        old_node_ids=("node_old",),
        node_positions=(GraphNodePosition("node_new", 10.0, 20.0),),
        connections=(),
    )
    replacements = []
    history_resets = []

    def replace(candidate, workspace):
        replacements.append((threading.get_ident(), candidate, workspace))
        if candidate == "invalid":
            raise ValueError("invalid candidate")
        return event

    owner = _owner(
        bus,
        service_mailbox,
        commit_document=lambda: revision.__setitem__(0, revision[0] + 1),
        document_revision=lambda: revision[0],
        replace_graph_candidate=replace,
        reset_history=lambda: history_resets.append(
            (threading.get_ident(), revision[0])),
    )
    owner.start()
    publisher = GraphCommandPublisher(bus)
    try:
        publisher.execute(CommitGraphCandidate(
            "invalid", expected_revision=6, request_id="bad", reset_history=True))
        rejected = gui_mailbox.get(timeout=1.0).payload

        assert rejected.reason == "CANDIDATE_REJECTED"
        assert revision == [6]
        assert history_resets == []

        publisher.execute(CommitGraphCandidate(
            "valid", workspace="layout", expected_revision=6, request_id="good"))

        assert gui_mailbox.get(timeout=1.0).payload == event
        assert replacements[-1] == (owner._thread.ident, "valid", "layout")
        assert revision == [7]
        assert history_resets == []
        assert owner.compile_status is CompileStatus.DIRTY

        publisher.execute(CommitGraphCandidate(
            "loaded",
            practice=PracticeDocument({"speed": 1.5}),
            expected_revision=7,
            request_id="load",
            reset_history=True,
        ))

        assert gui_mailbox.get(timeout=1.0).payload == event
        assert gui_mailbox.get(timeout=1.0).payload == PracticeSettingsChanged(
            PracticeDocument({"speed": 1.5}))
        assert revision == [8]
        assert history_resets == [(owner._thread.ident, 8)]
    finally:
        owner.close()


def test_undo_restores_on_owner_and_commits_revision_once():
    bus = Bus()
    service_mailbox = bus.register(GraphEndpoint.SERVICE)
    gui_mailbox = bus.register(GraphEndpoint.GUI)
    revision = [8]
    event = GraphReplaced((), (GraphNodePosition("node_1", 0.0, 0.0),), ())
    undo_calls = []
    restore_order = []
    owner = _owner(
        bus,
        service_mailbox,
        commit_document=lambda: revision.__setitem__(0, revision[0] + 1),
        document_revision=lambda: revision[0],
        undo_graph=lambda: undo_calls.append(threading.get_ident()) or ApplyResult(
            ApplyStatus.APPLIED),
        take_history_event=lambda: event,
        finalize_graph_replacement=lambda: restore_order.append("finalize"),
        capture_graph_render_state=lambda: restore_order.append("capture") or None,
    )
    owner.start()
    publisher = GraphCommandPublisher(bus)
    try:
        publisher.execute(UndoGraph(expected_revision=8, request_id="undo_1"))

        assert gui_mailbox.get(timeout=1.0).payload == event
        assert undo_calls == [owner._thread.ident]
        assert restore_order == ["finalize", "capture"]
        assert revision == [9]
        assert owner.compile_status is CompileStatus.DIRTY
    finally:
        owner.close()


def test_graph_replacement_blocks_following_mutation_until_gui_projection():
    from application.graph_projection_barrier import GraphProjectionBarrier

    bus = Bus()
    service_mailbox = bus.register(GraphEndpoint.SERVICE)
    gui_mailbox = bus.register(GraphEndpoint.GUI)
    barrier = GraphProjectionBarrier()
    parameter_called = threading.Event()
    event = GraphReplaced(
        (), (GraphNodePosition("node_1", 0.0, 0.0),), ())
    owner = _owner(
        bus,
        service_mailbox,
        undo_graph=lambda: ApplyResult(ApplyStatus.APPLIED),
        take_history_event=lambda: event,
        set_parameter=lambda *_args: parameter_called.set() or 2.0,
        projection_barrier=barrier,
    )
    owner.start()
    publisher = GraphCommandPublisher(bus)
    try:
        publisher.execute(UndoGraph())
        publisher.set_parameter("node_1", "level", 2.0)

        assert isinstance(gui_mailbox.get(timeout=1.0).payload, GraphReplaced)
        assert barrier.pending
        assert not parameter_called.wait(timeout=0.05)

        barrier.release()

        assert parameter_called.wait(timeout=1.0)
        assert isinstance(gui_mailbox.get(timeout=1.0).payload, ParameterChanged)
    finally:
        barrier.release()
        owner.close()


def test_unavailable_history_rejects_without_revision_change():
    bus = Bus()
    service_mailbox = bus.register(GraphEndpoint.SERVICE)
    gui_mailbox = bus.register(GraphEndpoint.GUI)
    revision = [2]
    owner = _owner(
        bus,
        service_mailbox,
        commit_document=lambda: revision.__setitem__(0, revision[0] + 1),
        document_revision=lambda: revision[0],
        undo_graph=lambda: ApplyResult(ApplyStatus.REJECTED, "nothing to undo"),
    )
    owner.start()
    try:
        GraphCommandPublisher(bus).undo(expected_revision=2)

        rejected = gui_mailbox.get(timeout=1.0).payload
        assert rejected.reason == "HISTORY_UNAVAILABLE"
        assert revision == [2]
    finally:
        owner.close()


def test_persistence_round_trip_keeps_io_on_owner_and_transactions_on_gui():
    bus = Bus()
    service_mailbox = bus.register(GraphEndpoint.SERVICE)
    gui_mailbox = bus.register(GraphEndpoint.GUI)
    load_requested = threading.Event()
    save_requested = threading.Event()
    snapshot_published = threading.Event()
    owner_calls = []
    gui_calls = []
    captured_document = ProjectDocument(
        1, "project_1", GraphDocument(1, "graph_1", (), ()))

    def request_load(name, project_id, revision, request_id):
        owner_calls.append((
            "load", threading.get_ident(), name,
            project_id, revision, request_id))
        load_requested.set()
        return object()

    def request_save(name, document, revision, request_id, as_default):
        owner_calls.append((
            "save", threading.get_ident(), name, document,
            revision, request_id, as_default))
        save_requested.set()
        return object()

    owner = _owner(
        bus,
        service_mailbox,
        lambda *_args: None,
        snapshot_published.set,
        request_preset_load=request_load,
        request_preset_save=request_save,
    )
    owner.start()
    publisher = GraphCommandPublisher(bus)
    graph = _graph(
        apply_preset_candidate=lambda *args: (
            gui_calls.append(("apply", *args))
            or ApplyResult(ApplyStatus.APPLIED)
        ),
        capture_preset_document=lambda include, graph, project_id: (
            gui_calls.append(("capture", include, graph, project_id))
            or captured_document
        ),
        preset_save_completed=lambda *args: gui_calls.append(("saved", *args)),
        complete_preset_apply=publisher.complete_preset_apply,
        submit_preset_document=publisher.submit_preset_document,
    )
    try:
        assert publisher.load_preset("practice") is BusMessageResult.ENQUEUED
        assert load_requested.wait(timeout=1.0)
        assert owner_calls[0] == (
            "load", owner._thread.ident, "practice",
            "project_1", 0, "load:practice:0")
        assert len(gui_mailbox) == 0

        candidate = {"nodes": [], "links": []}
        assert bus.publish(Message(
            source="application.persistence.worker",
            destination=GraphEndpoint.SERVICE,
            topic=GraphTopic.COMMAND,
            payload=ApplyPresetCandidate("practice", candidate),
        )) is BusMessageResult.ENQUEUED
        graph.handle_message(gui_mailbox.get(timeout=1.0))

        assert snapshot_published.wait(timeout=1.0)
        assert gui_calls == [("apply", "practice", candidate, ())]

        assert publisher.save_preset("practice", True, True) is BusMessageResult.ENQUEUED
        graph.handle_message(gui_mailbox.get(timeout=1.0))

        assert save_requested.wait(timeout=1.0)
        assert owner_calls[1] == (
            "save",
            owner._thread.ident,
            "practice",
            captured_document,
            0,
            "save:practice:0",
            True,
        )
        assert gui_calls[-1] == (
            "capture", True, GraphDocument(1, "graph_1", (), ()), "project_1")

        assert bus.publish(Message(
            source="application.persistence.worker",
            destination=GraphEndpoint.SERVICE,
            topic=GraphTopic.COMMAND,
            payload=PresetSaveCompleted("practice", True, 1),
        )) is BusMessageResult.ENQUEUED
        graph.handle_message(gui_mailbox.get(timeout=1.0))

        assert gui_calls[-1] == ("saved", "practice", True, 1)
    finally:
        owner.close()


def test_preset_save_rejects_workspace_captured_from_stale_revision():
    bus = Bus()
    service_mailbox = bus.register(GraphEndpoint.SERVICE)
    gui_mailbox = bus.register(GraphEndpoint.GUI)
    revision = [3]
    graph = GraphDocument(1, "graph_1", (), ())
    project = ProjectDocument(1, "project_1", graph)
    save_requests = []
    capture_threads = []
    owner = _owner(
        bus,
        service_mailbox,
        document_revision=lambda: revision[0],
        capture_graph_document=lambda: (
            capture_threads.append(threading.get_ident()) or graph),
        request_preset_save=lambda *args: save_requests.append(args),
    )
    owner.start()
    publisher = GraphCommandPublisher(bus)
    try:
        publisher.save_preset("practice", True, False)
        capture = gui_mailbox.get(timeout=1.0).payload

        assert isinstance(capture, PresetCaptureRequested)
        assert capture.graph is graph
        assert capture.captured_revision == 3
        assert capture_threads == [owner._thread.ident]

        revision[0] = 4
        publisher.execute(PresetDocumentCaptured(
            "practice", project, False, 3, capture.request_id))

        failed = gui_mailbox.get(timeout=1.0).payload
        assert failed == PresetSaveFailed(
            "practice", "graph changed during workspace capture")
        assert save_requests == []
    finally:
        owner.close()


def test_close_drains_commands_accepted_before_shutdown():
    bus = Bus()
    service_mailbox = bus.register(GraphEndpoint.SERVICE)
    gui_mailbox = bus.register(GraphEndpoint.GUI)
    deleted = []
    owner = _owner(
        bus, service_mailbox,
        delete_node=lambda node_tag: deleted.append(node_tag),
    )
    owner.start()
    publisher = GraphCommandPublisher(bus)

    assert publisher.delete_node("node_1") is BusMessageResult.ENQUEUED
    owner.close()

    assert deleted == ["node_1"]


def test_owner_survives_handler_exception_and_close_joins_thread():
    bus = Bus()
    service_mailbox = bus.register(GraphEndpoint.SERVICE)
    bus.register(GraphEndpoint.GUI)
    handled = threading.Event()

    def delete_node(node_tag):
        if node_tag == "bad":
            raise RuntimeError("expected test failure")
        handled.set()
        return None

    owner = _owner(
        bus,
        service_mailbox,
        lambda *_args: None,
        lambda: None,
        delete_node=delete_node,
    )
    owner.start()
    publisher = GraphCommandPublisher(bus)

    publisher.delete_node("bad")
    publisher.delete_node("good")

    assert handled.wait(timeout=1.0)
    owner.close()
    owner.close()
    assert not owner._thread.is_alive()


def test_repeated_owner_startup_shutdown_leaves_no_threads():
    baseline = {thread.ident for thread in threading.enumerate()}

    for _ in range(20):
        bus = Bus()
        mailbox = bus.register(GraphEndpoint.SERVICE)
        owner = _owner(bus, mailbox)
        owner.start()
        owner.close()
        assert not owner._thread.is_alive()

    assert {thread.ident for thread in threading.enumerate()} == baseline


def test_delete_node_accepts_mixed_gui_resource_tag_types(graph_editor, node_registry):
    from core.pipeline import GRAPH_STATE, delete_node_state, spawn_node_state

    spawned = spawn_node_state("Gain")
    node = GRAPH_STATE.nodes[spawned.node_tag]
    node._ui_resource_tags.update({101, "theme::gain"})
    GRAPH_STATE.stale_nodes.add(spawned.node_tag)
    GRAPH_STATE.failed_nodes.add(spawned.node_tag)
    GRAPH_STATE.cycle_nodes.add(spawned.node_tag)

    event = delete_node_state(spawned.node_tag)

    assert set(event.ui_resource_tags) == {101, "theme::gain"}
    assert node._ui_resource_tags == set()
    assert spawned.node_tag not in GRAPH_STATE.stale_nodes
    assert spawned.node_tag not in GRAPH_STATE.failed_nodes
    assert spawned.node_tag not in GRAPH_STATE.cycle_nodes


def test_owner_close_timeout_does_not_hang_caller():
    bus = Bus()
    mailbox = bus.register(GraphEndpoint.SERVICE)
    entered = threading.Event()
    release = threading.Event()

    def blocked_delete(_node_tag):
        entered.set()
        release.wait(timeout=1.0)

    owner = _owner(
        bus, mailbox, lambda *_args: None, lambda: None,
        delete_node=blocked_delete,
    )
    owner.start()
    GraphCommandPublisher(bus).delete_node("node_1")
    assert entered.wait(timeout=0.5)

    assert owner.close(timeout=0.01) is False
    assert owner._thread.is_alive()

    release.set()
    assert owner.close(timeout=0.5) is True


def test_graph_owner_module_does_not_import_dearpygui():
    path = Path(__file__).parents[1] / "application" / "graph_owner.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert not any(name.startswith("dearpygui") for name in imports)


def test_production_graph_state_flow_runs_without_dpg_context():
    script = r'''
import dearpygui.dearpygui as dpg
dpg.create_context = lambda: (_ for _ in ()).throw(AssertionError("DPG context created"))
from core.pipeline import (
    GRAPH_STATE,
    connect_pins_state,
    delete_node_state,
    disconnect_pins_state,
    scan_and_register_nodes,
    set_node_parameter_state,
    spawn_node_state,
)

scan_and_register_nodes()
first = spawn_node_state("Gain")
second = spawn_node_state("Gain")
assert first is not None and second is not None
first_node = GRAPH_STATE.nodes[first.node_tag]
second_node = GRAPH_STATE.nodes[second.node_tag]
output_pin = next(iter(first_node.output_spec))
input_pin = next(iter(second_node.input_spec))
assert set_node_parameter_state(first.node_tag, "gain_db", 3) == 3.0
connection = connect_pins_state(output_pin, input_pin)
assert connection is not None
assert GRAPH_STATE.source_for_input(input_pin) == output_pin
assert disconnect_pins_state(output_pin, input_pin) == connection
assert delete_node_state(first.node_tag) is not None
assert delete_node_state(second.node_tag) is not None
assert first_node._parameter_ui_adapter is None
assert first_node._node_panel_controller is None
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr


def test_production_node_lifecycle_separates_state_from_dpg_projection(
    graph_editor,
    node_registry,
):
    import dearpygui.dearpygui as dpg
    from core.pipeline import (
        GRAPH_STATE,
        delete_node_state,
        spawn_node_state,
    )
    from ui.graph_projection import (
        bind_graph_node_projection,
        project_node_deleted,
        project_node_spawned,
    )
    bind_graph_node_projection(GRAPH_STATE.nodes.get)

    assert "Gain" in node_registry
    spawned = spawn_node_state("Gain")

    assert spawned is not None
    assert spawned.node_tag in GRAPH_STATE.nodes
    assert not dpg.does_item_exist(spawned.node_tag)

    project_node_spawned(spawned)

    assert dpg.does_item_exist(spawned.node_tag)

    deleted = delete_node_state(spawned.node_tag)

    assert deleted is not None
    assert spawned.node_tag not in GRAPH_STATE.nodes
    assert dpg.does_item_exist(spawned.node_tag)

    project_node_deleted(deleted)

    assert not dpg.does_item_exist(spawned.node_tag)