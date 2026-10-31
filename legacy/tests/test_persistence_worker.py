import json
import os
import threading

from application.context import ApplicationContext
from application.persistence_worker import PersistenceWorker
from core.bus import BusMessageResult, Message
from core.graph_contracts import (
    ApplyPresetCandidate,
    CommitGraphCandidate,
    GraphEndpoint,
    PresetLoadRejected,
    PresetSaveCompleted,
    PresetSaveFailed,
    ProjectLoadRejected,
    ProjectSaved,
)
from core.graph_documents import (
    GraphDocument,
    NodeDocument,
    PanelDocument,
    Position,
    PracticeDocument,
    ProjectDocument,
    ProjectDocumentCodec,
    WorkspaceDocument,
)
from core.preset_store import PresetStore, PresetStoreError


def _store(tmp_path):
    return PresetStore(str(tmp_path), os_module=os, json_module=json)


def test_worker_publishes_detached_valid_candidate(tmp_path):
    context = ApplicationContext()
    messages = []
    context.register_gui_endpoint(GraphEndpoint.SERVICE, messages.append)
    store = _store(tmp_path)
    source = {"nodes": [{"class": "GainNode", "params": {}}], "links": []}
    store.write_atomic("practice", source)
    worker = PersistenceWorker(context.bus, store)

    future = worker.request_load("practice", {"GainNode"})
    assert future is not None
    future.result()
    source["nodes"][0]["class"] = "Changed"

    assert messages == []
    assert context.drain_gui() == 1
    command = messages[0].payload
    assert isinstance(command, ApplyPresetCandidate)
    assert command.data["nodes"][0]["class"] == "GainNode"
    worker.close()


def test_worker_rejects_invalid_candidate_without_runtime_access(tmp_path):
    context = ApplicationContext()
    messages = []
    context.register_gui_endpoint(GraphEndpoint.SERVICE, messages.append)
    store = _store(tmp_path)
    store.write_atomic("broken", {"nodes": "invalid"})
    worker = PersistenceWorker(context.bus, store)

    future = worker.request_load("broken", set())
    assert future is not None
    future.result()
    context.drain_gui()

    assert isinstance(messages[0].payload, PresetLoadRejected)
    worker.close()


def test_worker_close_is_idempotent_and_drops_late_requests(tmp_path):
    context = ApplicationContext()
    context.register_gui_endpoint(GraphEndpoint.SERVICE, lambda _message: None)
    worker = PersistenceWorker(context.bus, _store(tmp_path))

    worker.close()
    worker.close()

    assert worker.request_load("practice", set()) is None


def test_worker_logs_when_service_mailbox_is_full(tmp_path, caplog):
    context = ApplicationContext()
    context.register_endpoint(GraphEndpoint.SERVICE, maxsize=1)
    context.bus.publish(Message(
        source="test",
        destination=GraphEndpoint.SERVICE,
        topic="fill",
    ))
    worker = PersistenceWorker(context.bus, _store(tmp_path))

    future = worker.request_save("practice", {"nodes": [], "links": []}, False)
    assert future is not None
    assert future.result() is BusMessageResult.MAILBOX_FULL

    assert "Persistence result PresetSaveCompleted was not enqueued" in caplog.text
    worker.close()


def test_worker_writes_detached_document_and_reports_completion(tmp_path):
    context = ApplicationContext()
    messages = []
    context.register_gui_endpoint(GraphEndpoint.SERVICE, messages.append)
    store = _store(tmp_path)
    worker = PersistenceWorker(context.bus, store)
    source = {"nodes": [{"class": "GainNode"}], "links": []}

    future = worker.request_save("practice", source, True)
    assert future is not None
    source["nodes"][0]["class"] = "Changed"
    future.result()
    context.drain_gui()

    assert store.read("practice")["nodes"][0]["class"] == "GainNode"
    assert messages[0].payload == PresetSaveCompleted("practice", True, 1)
    worker.close()


def test_worker_reports_atomic_save_failure(tmp_path, monkeypatch):
    context = ApplicationContext()
    messages = []
    context.register_gui_endpoint(GraphEndpoint.SERVICE, messages.append)
    store = _store(tmp_path)
    monkeypatch.setattr(
        store,
        "write_atomic",
        lambda *_args: (_ for _ in ()).throw(PresetStoreError("disk full")),
    )
    worker = PersistenceWorker(context.bus, store)

    future = worker.request_save("practice", {"nodes": [], "links": []}, False)
    assert future is not None
    future.result()
    context.drain_gui()

    assert isinstance(messages[0].payload, PresetSaveFailed)
    worker.close()


def test_worker_rejects_request_when_pending_capacity_is_full():
    class BlockingStore:
        def __init__(self):
            self.started = threading.Event()
            self.release = threading.Event()

        def read(self, _name):
            self.started.set()
            assert self.release.wait(timeout=2)
            return {"nodes": [], "links": []}

    context = ApplicationContext()
    context.register_gui_endpoint(GraphEndpoint.SERVICE, lambda _message: None)
    store = BlockingStore()
    worker = PersistenceWorker(context.bus, store, max_pending=1)

    first = worker.request_load("first", set())
    assert first is not None
    assert store.started.wait(timeout=2)
    assert worker.request_load("second", set()) is None

    store.release.set()
    first.result()
    worker.close()


def test_close_waits_for_accepted_save_and_publishes_completion(tmp_path):
    class BlockingStore:
        def __init__(self, store):
            self._store = store
            self.started = threading.Event()
            self.release = threading.Event()

        def write_atomic(self, name, data):
            self.started.set()
            assert self.release.wait(timeout=2)
            self._store.write_atomic(name, data)

    context = ApplicationContext()
    messages = []
    context.register_gui_endpoint(GraphEndpoint.SERVICE, messages.append)
    backing_store = _store(tmp_path)
    store = BlockingStore(backing_store)
    worker = PersistenceWorker(context.bus, store)
    future = worker.request_save(
        "practice", {"nodes": [{"class": "GainNode"}], "links": []}, True)
    assert future is not None
    assert store.started.wait(timeout=1)

    closed = threading.Event()
    closer = threading.Thread(target=lambda: (worker.close(), closed.set()))
    closer.start()
    assert worker.request_save("late", {"nodes": [], "links": []}, False) is None
    assert not closed.wait(timeout=0.01)

    store.release.set()
    assert closed.wait(timeout=1)
    closer.join(timeout=1)
    assert context.drain_gui() == 1
    assert messages[0].payload == PresetSaveCompleted("practice", True, 1)
    assert backing_store.read("practice")["nodes"][0]["class"] == "GainNode"


def test_worker_migrates_and_materializes_legacy_project_candidate(tmp_path):
    from nodes.gain_node.node import GainNode

    context = ApplicationContext()
    messages = []
    context.register_gui_endpoint(GraphEndpoint.SERVICE, messages.append)
    store = _store(tmp_path)
    store.write_atomic("legacy", {
        "nodes": [{"class": "GainNode", "params": {"gain_db": 2.0}}],
        "links": [],
    })
    worker = PersistenceWorker(context.bus, store)

    future = worker.request_load_project(
        "legacy",
        {"GainNode": GainNode},
        project_id="project_1",
        expected_revision=3,
        request_id="load_1",
    )
    assert future is not None
    future.result()
    context.drain_gui()

    command = messages[0].payload
    assert isinstance(command, CommitGraphCandidate)
    assert command.project_id == "project_1"
    assert command.expected_revision == 3
    assert command.candidate.nodes["node_GainNode_1"].gain_db == 2.0
    worker.close()


def test_worker_preserves_project_workspace_and_practice(tmp_path):
    from nodes.gain_node.node import GainNode

    context = ApplicationContext()
    messages = []
    context.register_gui_endpoint(GraphEndpoint.SERVICE, messages.append)
    store = _store(tmp_path)
    project = ProjectDocument(
        schema_version=1,
        project_id="project_1",
        graph=GraphDocument(1, "graph_1", (
            NodeDocument("node_1", "GainNode"),), ()),
        workspace=WorkspaceDocument(
            node_positions={"node_1": Position(10.0, 20.0)},
            node_panels={"node_1": PanelDocument(
                Position(100.0, 200.0), Position(400.0, 300.0))},
        ),
        practice=PracticeDocument({"speed": 1.5}),
    )
    store.write_atomic("project", ProjectDocumentCodec.to_dict(project))
    worker = PersistenceWorker(context.bus, store)

    future = worker.request_load_project(
        "project", {"GainNode": GainNode}, project_id="project_1",
        expected_revision=2, request_id="load_project")
    assert future is not None
    future.result()
    context.drain_gui()

    command = messages[0].payload
    assert command.workspace == project.workspace
    assert command.practice == project.practice
    worker.close()


def test_worker_rejects_project_with_unknown_node_type(tmp_path):
    context = ApplicationContext()
    messages = []
    context.register_gui_endpoint(GraphEndpoint.SERVICE, messages.append)
    store = _store(tmp_path)
    project = ProjectDocument(
        schema_version=1,
        project_id="project_1",
        graph=GraphDocument(
            schema_version=1,
            graph_id="graph_1",
            nodes=(NodeDocument("node_1", "MissingNode"),),
            connections=(),
        ),
    )
    store.write_atomic("invalid", ProjectDocumentCodec.to_dict(project))
    worker = PersistenceWorker(context.bus, store)

    future = worker.request_load_project(
        "invalid", {}, project_id="project_1",
        expected_revision=0, request_id="load_invalid")
    assert future is not None
    future.result()
    context.drain_gui()

    command = messages[0].payload
    assert isinstance(command, ProjectLoadRejected)
    assert command.request_id == "load_invalid"
    assert command.diagnostics[0].code == "unknown_node_type"
    worker.close()


def test_worker_saves_immutable_project_with_request_revision(tmp_path):
    context = ApplicationContext()
    messages = []
    context.register_gui_endpoint(GraphEndpoint.SERVICE, messages.append)
    store = _store(tmp_path)
    worker = PersistenceWorker(context.bus, store)
    project = ProjectDocument(
        schema_version=1,
        project_id="project_1",
        graph=GraphDocument(1, "graph_1", (), ()),
    )

    future = worker.request_save_project(
        "project", project, revision=7, request_id="save_1")
    assert future is not None
    future.result()
    context.drain_gui()

    assert ProjectDocumentCodec.from_dict(store.read("project")).document == project
    assert messages[0].payload == ProjectSaved(
        "save_1", "project_1", 7, "project")
    worker.close()
