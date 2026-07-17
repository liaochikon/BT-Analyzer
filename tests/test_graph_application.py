import threading

import pytest

from application.context import ApplicationContext
from application.graph_application import GraphApplication, GraphCommandPublisher
from core.bus import BusMessageResult, Message
from core.graph_contracts import (
    CompileKind,
    ConnectPins,
    ConnectionAdded,
    ConnectionRemoved,
    DeleteNode,
    GraphEndpoint,
    GraphTopic,
    NodeDeleted,
    NodeRenamed,
    NodeStateChanged,
    NodeSpawned,
    ParameterChanged,
    PresetApplyRequested,
    PresetCaptureRequested,
    PresetSaveCompleted,
    PresetSaveFailed,
    PracticeSettingsChanged,
)
from core.persistence_contracts import ApplyResult, ApplyStatus
from core.graph_documents import GraphDocument, PracticeDocument, ProjectDocument


def _project_document():
    return ProjectDocument(1, "project_1", GraphDocument(1, "graph_1", (), ()))


def _graph(calls, **overrides):
    ports = {
        "apply_preset_candidate": lambda *args: calls.append(("apply", *args)) or ApplyResult(
            ApplyStatus.APPLIED),
        "capture_preset_document": lambda include, graph, project_id: (
            calls.append(("capture", include, graph, project_id)) or _project_document()),
        "preset_save_completed": lambda *args: calls.append(("save-complete", *args)),
        "preset_save_failed": lambda *args: calls.append(("save-failed", *args)),
        "project_node_rename": lambda *args: calls.append(("rename", *args)),
        "project_node_parameter": lambda *args: calls.append(("parameter", *args)),
        "project_node_state": lambda *args: calls.append(("state", *args)),
        "project_connection_added": lambda *args: calls.append(("connect", *args)),
        "project_connection_removed": lambda *args: calls.append(("disconnect", *args)),
        "project_node_spawned": lambda event: calls.append(("spawn", event)),
        "project_node_deleted": lambda event: calls.append(("delete", event)),
        "project_practice_settings": lambda event: calls.append(("practice", event)),
        "complete_preset_apply": lambda *args: calls.append(("apply-complete", *args)),
        "submit_preset_document": lambda *args: calls.append(("submit", *args)),
        "projection_changed": lambda: calls.append(("changed",)),
    }
    ports.update(overrides)
    return GraphApplication(**ports)


def _projection(payload):
    return Message(
        source="application.graph.owner",
        destination=GraphEndpoint.GUI,
        topic=GraphTopic.PROJECTION,
        payload=payload,
    )


def test_graph_application_rejects_command_topic_and_unknown_projection():
    graph = _graph([])

    with pytest.raises(ValueError, match="unsupported graph topic"):
        graph.handle_message(Message(
            source="test",
            destination=GraphEndpoint.GUI,
            topic=GraphTopic.COMMAND,
            payload=DeleteNode("node_1"),
        ))
    with pytest.raises(TypeError, match="unsupported graph projection"):
        graph.handle_message(_projection(object()))


def test_node_and_parameter_events_project_then_refresh_snapshot_views():
    calls = []
    graph = _graph(calls)
    spawned = NodeSpawned("node_1", (10, 20))
    deleted = NodeDeleted("node_1", "panel_1", (), (), ())

    for event in (
        NodeRenamed("node_1", "Lead"),
        ParameterChanged("node_1", "gain_db", 3.0),
        NodeStateChanged("node_1", "mute", True, 2),
        spawned,
        deleted,
    ):
        graph.handle_message(_projection(event))

    assert calls == [
        ("rename", "node_1", "Lead"), ("changed",),
        ("parameter", "node_1", "gain_db", 3.0), ("changed",),
        ("state", "node_1", "mute", True, 2), ("changed",),
        ("spawn", spawned), ("changed",),
        ("delete", deleted), ("changed",),
    ]


def test_connection_events_project_endpoints_and_refresh_snapshot_views():
    calls = []
    graph = _graph(calls)
    added = ConnectionAdded("connection_1", "editor", "out", "in")
    removed = ConnectionRemoved("connection_1", "out", "in")

    graph.handle_message(_projection(added))
    graph.handle_message(_projection(removed))

    assert calls == [
        ("connect", added), ("changed",),
        ("disconnect", removed), ("changed",),
    ]


def test_practice_settings_use_dedicated_gui_projection_port():
    calls = []
    graph = _graph(calls)
    event = PracticeSettingsChanged(PracticeDocument({"speed": 1.5}))

    graph.handle_message(_projection(event))

    assert calls == [("practice", event)]


def test_preset_projection_round_trip_uses_explicit_gui_ports():
    calls = []
    graph = _graph(calls)
    data = {"nodes": [], "links": []}
    document = _project_document()
    graph_document = document.graph

    graph.handle_message(_projection(PresetApplyRequested("practice", data, ("warning",))))
    graph.handle_message(_projection(PresetCaptureRequested(
        "practice", True, True, graph_document, "project_1", 4, "save_1")))
    graph.handle_message(_projection(PresetSaveCompleted("practice", True, 0)))
    graph.handle_message(_projection(PresetSaveFailed("other", "disk full")))

    assert calls == [
        ("apply", "practice", data, ("warning",)),
        ("apply-complete", "practice", ApplyResult(ApplyStatus.APPLIED)),
        ("changed",),
        ("capture", True, graph_document, "project_1"),
        ("submit", "practice", document, True, 4, "save_1"),
        ("save-complete", "practice", True, 0),
        ("save-failed", "other", "disk full"),
    ]


def test_command_publisher_sends_immutable_commands_to_service_fifo():
    context = ApplicationContext()
    mailbox = context.register_endpoint(GraphEndpoint.SERVICE, maxsize=3)
    publisher = GraphCommandPublisher(context.bus)

    assert publisher.connect_callback("editor", ("out", "in")) is BusMessageResult.ENQUEUED
    assert publisher.delete_node("node_1") is BusMessageResult.ENQUEUED
    assert publisher.request_compile(CompileKind.ANALYZE) is BusMessageResult.ENQUEUED

    messages = [mailbox.get_nowait() for _ in range(3)]
    assert [message.destination for message in messages] == [GraphEndpoint.SERVICE] * 3
    assert messages[0].payload == ConnectPins("editor", "out", "in")
    assert messages[1].payload == DeleteNode("node_1")
    with pytest.raises((AttributeError, TypeError)):
        messages[0].payload.input_pin = "other"


def test_graph_publisher_waits_for_capacity_before_timing_out():
    context = ApplicationContext()
    mailbox = context.register_endpoint(GraphEndpoint.SERVICE, maxsize=1)
    publisher = GraphCommandPublisher(context.bus)
    assert publisher.delete_node("node_1") is BusMessageResult.ENQUEUED
    consumed = threading.Event()

    def consume():
        mailbox.get(timeout=0.5)
        consumed.set()

    consumer = threading.Thread(target=consume)
    consumer.start()
    assert publisher.delete_node("node_2") is BusMessageResult.ENQUEUED
    consumer.join(timeout=1)
    assert consumed.is_set()
    assert mailbox.get_nowait().payload == DeleteNode("node_2")


def test_graph_publisher_reports_missing_destination_after_shutdown():
    context = ApplicationContext()
    context.register_endpoint(GraphEndpoint.SERVICE)
    publisher = GraphCommandPublisher(context.bus)
    context.close()

    assert publisher.delete_node("node_1") is BusMessageResult.NO_DESTINATION


def test_graph_publisher_logs_when_service_mailbox_is_full(caplog):
    context = ApplicationContext()
    context.register_endpoint(GraphEndpoint.SERVICE, maxsize=1)
    publisher = GraphCommandPublisher(context.bus)
    assert publisher.delete_node("node_1") is BusMessageResult.ENQUEUED

    assert publisher.delete_node("node_2") is BusMessageResult.MAILBOX_FULL

    assert "Graph command DeleteNode was not enqueued" in caplog.text
