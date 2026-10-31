import copy
import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor

from core.bus import Bus, BusMessageResult, Message
from core.graph_contracts import (
    ApplyPresetCandidate,
    CommitGraphCandidate,
    GraphEndpoint,
    GraphTopic,
    PresetLoadRejected,
    PresetSaveCompleted,
    PresetSaveFailed,
    ProjectLoadRejected,
    ProjectSaved,
    ProjectSaveFailed,
)
from core.graph_documents import (
    ProjectDocument,
    ProjectDocumentCodec,
    ProjectMetadata,
    PROJECT_DOCUMENT_SCHEMA_VERSION,
    materialize_graph_document,
    migrate_legacy_preset,
)
from core.graph_validation import validate_preset
from core.preset_store import PresetStore, PresetStoreError


logger = logging.getLogger(__name__)


class PersistenceWorker:
    """Read and validate detached preset documents away from the GUI thread."""

    _PUBLISH_TIMEOUT = 0.25

    def __init__(self, bus: Bus, store: PresetStore, *, max_pending: int = 8) -> None:
        if max_pending <= 0:
            raise ValueError("max_pending must be positive")
        self._bus = bus
        self._store = store
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="preset-io")
        self._pending_slots = threading.BoundedSemaphore(max_pending)
        self._lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._accepting = True
        self._closed = False

    def request_load(self, name: str, known_classes) -> Future | None:
        classes = frozenset(str(item) for item in known_classes)
        return self._submit(self._load, str(name), classes)

    def request_save(self, name: str, data: dict, as_default: bool) -> Future | None:
        return self._submit(
            self._save,
            str(name),
            copy.deepcopy(data),
            bool(as_default),
        )

    def request_load_project(
        self,
        name: str,
        node_types,
        *,
        project_id: str,
        expected_revision: int,
        request_id: str,
    ) -> Future | None:
        return self._submit(
            self._load_project,
            str(name),
            dict(node_types),
            str(project_id),
            int(expected_revision),
            str(request_id),
        )

    def request_save_project(
        self,
        name: str,
        document: ProjectDocument,
        *,
        revision: int,
        request_id: str,
        as_default: bool = False,
    ) -> Future | None:
        if not isinstance(document, ProjectDocument):
            raise TypeError("document must be a ProjectDocument")
        return self._submit(
            self._save_project,
            str(name),
            document,
            int(revision),
            str(request_id),
            bool(as_default),
        )

    def _submit(self, operation, *args) -> Future | None:
        with self._lock:
            if not self._accepting:
                return None
            if not self._pending_slots.acquire(blocking=False):
                return None
            future = self._executor.submit(operation, *args)
            future.add_done_callback(lambda _future: self._pending_slots.release())
            return future

    def _load(self, name: str, known_classes: frozenset[str]) -> BusMessageResult:
        try:
            data = self._store.read(name)
        except PresetStoreError:
            command = PresetLoadRejected(name, ("preset unreadable",))
        else:
            validation = validate_preset(data, known_classes=known_classes)
            if validation.ok:
                command = ApplyPresetCandidate(
                    name=name,
                    data=copy.deepcopy(data),
                    warnings=tuple(validation.warnings),
                )
            else:
                command = PresetLoadRejected(name, tuple(validation.errors))

        return self._publish(command)

    def _save(self, name: str, data: dict, as_default: bool) -> BusMessageResult:
        try:
            self._store.write_atomic(name, data)
        except PresetStoreError:
            command = PresetSaveFailed(name, "atomic write failed")
        else:
            command = PresetSaveCompleted(name, as_default, len(data.get("nodes", [])))
        return self._publish(command)

    def _load_project(
        self,
        name: str,
        node_types: dict,
        project_id: str,
        expected_revision: int,
        request_id: str,
    ) -> BusMessageResult:
        try:
            data = self._store.read(name)
        except PresetStoreError:
            return self._publish(ProjectLoadRejected(
                request_id, ("project unreadable",)))

        if data.get("schema_version") == PROJECT_DOCUMENT_SCHEMA_VERSION and "graph" in data:
            decoded = ProjectDocumentCodec.from_dict(data)
            if not decoded.ok:
                return self._publish(ProjectLoadRejected(
                    request_id, decoded.diagnostics))
            project = decoded.document
        else:
            migrated = migrate_legacy_preset(data, graph_id=f"{project_id}.graph")
            if not migrated.ok:
                return self._publish(ProjectLoadRejected(
                    request_id, migrated.diagnostics))
            project = ProjectDocument(
                schema_version=PROJECT_DOCUMENT_SCHEMA_VERSION,
                project_id=project_id,
                graph=migrated.graph,
                workspace=migrated.workspace,
                practice=migrated.practice,
                metadata=ProjectMetadata({"migrated_from": "legacy_preset"}),
            )

        materialized = materialize_graph_document(project.graph, node_types)
        if not materialized.ok:
            return self._publish(ProjectLoadRejected(
                request_id, materialized.diagnostics))
        return self._publish(CommitGraphCandidate(
            candidate=materialized.candidate,
            workspace=project.workspace,
            practice=project.practice,
            project_id=project.project_id,
            expected_revision=expected_revision,
            request_id=request_id,
            reset_history=True,
        ))

    def _save_project(
        self,
        name: str,
        document: ProjectDocument,
        revision: int,
        request_id: str,
        as_default: bool,
    ) -> BusMessageResult:
        try:
            self._store.write_atomic(name, ProjectDocumentCodec.to_dict(document))
        except PresetStoreError:
            command = ProjectSaveFailed(
                request_id, document.project_id, revision, name, "atomic write failed")
        else:
            command = ProjectSaved(
                request_id, document.project_id, revision, name,
                as_default, len(document.graph.nodes))
        return self._publish(command)

    def _publish(self, command) -> BusMessageResult:
        with self._lock:
            if self._closed:
                return BusMessageResult.NO_DESTINATION
            result = self._bus.publish_wait(Message(
                source="application.persistence.worker",
                destination=GraphEndpoint.SERVICE,
                topic=GraphTopic.COMMAND,
                payload=command,
            ), timeout=self._PUBLISH_TIMEOUT)
        if result is not BusMessageResult.ENQUEUED:
            logger.warning(
                "Persistence result %s was not enqueued: %s",
                type(command).__name__, result.describe(GraphEndpoint.SERVICE),
            )
        return result

    def close(self) -> None:
        with self._close_lock:
            with self._lock:
                if self._closed:
                    return
                self._accepting = False
            self._executor.shutdown(wait=True, cancel_futures=False)
            with self._lock:
                self._closed = True
