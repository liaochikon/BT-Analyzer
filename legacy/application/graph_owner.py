import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import replace

from core.bus import Bus, BusMessageResult, Mailbox, Message
from core.graph_documents import CandidateRuntimeGraph, GraphDocument, WorkspaceDocument
from core.graph_contracts import (
    ConnectPins,
    CompileCompleted,
    CompileMetadataChanged,
    CompileStatus,
    CommitGraphCandidate,
    CommitWorkspaceGesture,
    ConnectionAdded,
    ConnectionRemoved,
    DisconnectLink,
    EditPhase,
    GraphEndpoint,
    GraphReplaced,
    GraphTopic,
    NodeRenamed,
    NodeStateChanged,
    NodeDeleted,
    NodeSpawned,
    ParameterChanged,
    ApplyPresetCandidate,
    LoadPreset,
    MutationRejected,
    PresetApplyCompleted,
    PresetApplyRequested,
    PresetCaptureRequested,
    PresetDocumentCaptured,
    PresetLoadRejected,
    PresetSaveCompleted,
    PresetSaveFailed,
    PracticeSettingsChanged,
    ProjectLoadRejected,
    ProjectSaved,
    ProjectSaveFailed,
    SavePreset,
    RenameNode,
    RequestCompile,
    RedoGraph,
    SetParameter,
    SetNodeState,
    DeleteNode,
    SpawnNode,
    UndoGraph,
)


logger = logging.getLogger(__name__)


class GraphOwner:
    """Consume every graph command through explicit owner-side ports."""

    _PUBLISH_TIMEOUT = 0.25

    def __init__(
        self,
        bus: Bus,
        mailbox: Mailbox,
        rename_node: Callable[[str, str], str | None],
        publish_snapshot: Callable[[], object],
        commit_document: Callable[[], object],
        document_revision: Callable[[], int],
        commit_compile_metadata: Callable[[CompileCompleted], object],
        replace_graph_candidate: Callable[
            [CandidateRuntimeGraph, WorkspaceDocument | None], GraphReplaced],
        undo_graph: Callable[[], object],
        redo_graph: Callable[[], object],
        take_history_event: Callable[[], object | None],
        reset_history: Callable[[], object],
        capture_graph_document: Callable[[], GraphDocument],
        capture_graph_render_state: Callable[[], object],
        finalize_graph_replacement: Callable[[], object],
        set_parameter: Callable[[str, str, object], object | None],
        mutate_node_state: Callable[[str, str, object, int | None, object], object | None],
        connect_pins: Callable[[str, str], bool],
        disconnect_connection: Callable[[str], object | None],
        spawn_node: Callable[[str], NodeSpawned | None],
        delete_node: Callable[[str], NodeDeleted | None],
        commit_topology_changed: Callable[[str, str], object],
        commit_node_spawned: Callable[[NodeSpawned], object],
        commit_node_deleted: Callable[[NodeDeleted], object],
        commit_node_renamed: Callable[[str, str], object],
        commit_node_parameter_changed: Callable[[str, str, object], object],
        commit_node_state_changed: Callable[[str, str, object, int | None], object],
        commit_workspace_gesture: Callable[[WorkspaceDocument], object],
        request_compile: Callable[[object, str | None], object],
        request_preset_load: Callable[[str, str, int, str], object],
        reject_preset_load: Callable[[str, tuple[str, ...]], object],
        request_preset_save: Callable[[str, object, int, str, bool], object],
        projection_barrier=None,
    ) -> None:
        self._bus = bus
        self._mailbox = mailbox
        self._rename_node = rename_node
        self._publish_snapshot = publish_snapshot
        self._commit_document = commit_document
        self._document_revision = document_revision
        self._commit_compile_metadata = commit_compile_metadata
        self._replace_graph_candidate = replace_graph_candidate
        self._undo_graph = undo_graph
        self._redo_graph = redo_graph
        self._take_history_event = take_history_event
        self._reset_history = reset_history
        self._capture_graph_document = capture_graph_document
        self._capture_graph_render_state = capture_graph_render_state
        self._finalize_graph_replacement = finalize_graph_replacement
        self._set_parameter = set_parameter
        self._mutate_node_state = mutate_node_state
        self._connect_pins = connect_pins
        self._disconnect_connection = disconnect_connection
        self._spawn_node = spawn_node
        self._delete_node = delete_node
        self._commit_topology_changed = commit_topology_changed
        self._commit_node_spawned = commit_node_spawned
        self._commit_node_deleted = commit_node_deleted
        self._commit_node_renamed = commit_node_renamed
        self._commit_node_parameter_changed = commit_node_parameter_changed
        self._commit_node_state_changed = commit_node_state_changed
        self._commit_workspace_gesture = commit_workspace_gesture
        self._request_compile = request_compile
        self._request_preset_load = request_preset_load
        self._reject_preset_load = reject_preset_load
        self._request_preset_save = request_preset_save
        self._projection_barrier = projection_barrier
        self._stopping = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="graph-owner",
            daemon=True,
        )
        self._started = False
        self._compile_status = CompileStatus.DIRTY
        self._status_lock = threading.Lock()
        self._compile_job_counter = 0
        self._active_compile: tuple[str, int] | None = None

    @property
    def compile_status(self) -> CompileStatus:
        with self._status_lock:
            return self._compile_status

    def _set_compile_status(self, status: CompileStatus) -> None:
        with self._status_lock:
            self._compile_status = status

    def compile_completion(self, result=None, *, success: bool = True) -> CompileCompleted:
        with self._status_lock:
            active = self._active_compile
        if active is None:
            return CompileCompleted(success=success)
        return CompileCompleted(
            success=getattr(result, "success", success),
            job_id=active[0],
            document_revision=active[1],
            execution_order=getattr(result, "execution_order", None),
            node_ids=getattr(result, "node_ids", None),
            stale_node_ids=getattr(result, "stale_node_ids", None),
            failed_node_ids=getattr(result, "failed_node_ids", None),
            cycle_node_ids=getattr(result, "cycle_node_ids", None),
        )

    def start(self) -> None:
        if self._started:
            raise RuntimeError("graph owner already started")
        self._started = True
        self._thread.start()

    def close(self, *, timeout: float = 5.0) -> bool:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if not self._started:
            return True
        self._stopping.set()
        self._thread.join(timeout=timeout)
        stopped = not self._thread.is_alive()
        if not stopped:
            logger.error(
                "Graph owner did not stop within %.2f seconds", timeout)
        return stopped

    def _run(self) -> None:
        while True:
            try:
                message = self._mailbox.get(timeout=0.05)
            except queue.Empty:
                if self._stopping.is_set():
                    return
                continue
            try:
                self._handle_message(message)
            except Exception:
                logger.exception(
                    "Graph owner failed to handle %s", message.topic)

    def _handle_message(self, message: Message) -> None:
        if message.topic != GraphTopic.COMMAND:
            raise ValueError(f"unsupported graph topic: {message.topic!r}")
        command = message.payload
        if self.compile_status is CompileStatus.COMPILING and isinstance(command, (
            RenameNode, SetParameter, SetNodeState, CommitWorkspaceGesture,
            ConnectPins, DisconnectLink, SpawnNode, DeleteNode,
            LoadPreset, ApplyPresetCandidate, PresetLoadRejected, PresetApplyCompleted,
            CommitGraphCandidate,
            ProjectLoadRejected,
            UndoGraph, RedoGraph,
        )):
            self._publish_projection(MutationRejected(
                type(command).__name__, "COMPILE_BUSY",
                request_id=getattr(command, "request_id", None)))
            return
        expected_revision = getattr(command, "expected_revision", None)
        if expected_revision is not None:
            current_revision = self._document_revision()
            if expected_revision != current_revision:
                self._publish_projection(MutationRejected(
                    type(command).__name__,
                    "REVISION_CONFLICT",
                    request_id=getattr(command, "request_id", None),
                    expected_revision=expected_revision,
                    current_revision=current_revision,
                ))
                return
        if isinstance(command, CommitGraphCandidate):
            try:
                event = self._replace_graph_candidate(
                    command.candidate, command.workspace)
            except (TypeError, ValueError) as exc:
                self._publish_projection(MutationRejected(
                    type(command).__name__,
                    "CANDIDATE_REJECTED",
                    request_id=command.request_id,
                    expected_revision=command.expected_revision,
                    current_revision=self._document_revision(),
                ))
                logger.warning("Graph candidate rejected: %s", exc)
                return
            if not isinstance(event, GraphReplaced):
                raise TypeError("replace_graph_candidate must return GraphReplaced")
            self._finalize_graph_replacement()
            event = replace(event, render_state=self._capture_graph_render_state())
            if event.render_state is not None:
                logger.debug(
                    "Graph candidate ready nodes=%d connections=%d baked=%d stale=%d "
                    "failed=%d cycle=%d",
                    len(event.node_positions), len(event.connections),
                    len(event.render_state.baked_node_ids),
                    len(event.render_state.stale_node_ids),
                    len(event.render_state.failed_node_ids),
                    len(event.render_state.cycle_node_ids),
                )
            self._set_compile_status(CompileStatus.DIRTY)
            self._commit_document()
            if command.reset_history:
                self._reset_history()
            self._publish_graph_replacement(event)
            if command.practice is not None:
                self._publish_projection(PracticeSettingsChanged(command.practice))
            return
        if isinstance(command, (UndoGraph, RedoGraph)):
            result = self._undo_graph() if isinstance(command, UndoGraph) else self._redo_graph()
            if not getattr(result, "ok", False):
                self._publish_projection(MutationRejected(
                    type(command).__name__,
                    "HISTORY_UNAVAILABLE",
                    request_id=command.request_id,
                    expected_revision=command.expected_revision,
                    current_revision=self._document_revision(),
                ))
                return
            event = self._take_history_event()
            if not isinstance(event, GraphReplaced):
                raise TypeError("successful history restore must return GraphReplaced")
            self._finalize_graph_replacement()
            event = replace(event, render_state=self._capture_graph_render_state())
            if event.render_state is not None:
                logger.debug(
                    "History restore ready action=%s nodes=%d connections=%d baked=%d "
                    "stale=%d failed=%d cycle=%d",
                    type(command).__name__, len(event.node_positions),
                    len(event.connections), len(event.render_state.baked_node_ids),
                    len(event.render_state.stale_node_ids),
                    len(event.render_state.failed_node_ids),
                    len(event.render_state.cycle_node_ids),
                )
            self._set_compile_status(CompileStatus.DIRTY)
            self._commit_document()
            self._publish_graph_replacement(event)
            return
        if isinstance(command, ProjectLoadRejected):
            self._publish_projection(MutationRejected(
                "LoadProject",
                "PROJECT_INVALID",
                request_id=command.request_id,
                current_revision=self._document_revision(),
                diagnostics=command.diagnostics,
            ))
            return
        if isinstance(command, (ProjectSaved, ProjectSaveFailed)):
            self._publish_projection(command)
            return
        if isinstance(command, RenameNode):
            normalized = self._rename_node(command.node_tag, command.name)
            if normalized is not None:
                self._commit_node_renamed(command.node_tag, normalized)
                self._publish_projection(NodeRenamed(command.node_tag, normalized))
                self._set_compile_status(CompileStatus.DIRTY)
                self._commit_document()
            return
        if isinstance(command, SetParameter):
            value = self._set_parameter(command.node_tag, command.name, command.value)
            if value is not None:
                if command.edit_phase is EditPhase.COMMIT:
                    self._commit_node_parameter_changed(
                        command.node_tag, command.name, value)
                self._publish_projection(ParameterChanged(
                    command.node_tag, command.name, value))
                self._set_compile_status(CompileStatus.DIRTY)
                self._commit_document()
            return
        if isinstance(command, SetNodeState):
            try:
                value = self._mutate_node_state(
                    command.node_tag, command.field, command.value,
                    command.index, command.action)
            except (TypeError, ValueError) as exc:
                logger.warning("Node state mutation rejected: %s", exc)
                value = None
            if value is None:
                self._publish_projection(MutationRejected(
                    type(command).__name__,
                    "INVALID_NODE_STATE",
                    request_id=command.request_id,
                    current_revision=self._document_revision(),
                ))
                return
            if command.edit_phase is EditPhase.COMMIT:
                self._commit_node_state_changed(
                    command.node_tag, command.field, value, command.index)
            self._publish_projection(NodeStateChanged(
                command.node_tag, command.field, value, command.index))
            self._set_compile_status(CompileStatus.DIRTY)
            self._commit_document()
            return
        if isinstance(command, CommitWorkspaceGesture):
            self._commit_workspace_gesture(command.workspace)
            return
        if isinstance(command, ConnectPins):
            connection = self._connect_pins(command.output_pin, command.input_pin)
            if connection is not None:
                self._commit_topology_changed(
                    connection.input_pin, "new connection")
                self._publish_projection(ConnectionAdded(
                    connection.connection_id,
                    command.editor_tag,
                    connection.output_pin,
                    connection.input_pin,
                ))
                self._set_compile_status(CompileStatus.DIRTY)
                self._commit_document()
            return
        if isinstance(command, DisconnectLink):
            connection = self._disconnect_connection(command.connection_id)
            if connection is not None:
                self._commit_topology_changed(
                    connection.input_pin, "connection removal")
                self._publish_projection(ConnectionRemoved(
                    connection.connection_id,
                    connection.output_pin,
                    connection.input_pin,
                ))
                self._set_compile_status(CompileStatus.DIRTY)
                self._commit_document()
            return
        if isinstance(command, SpawnNode):
            event = self._spawn_node(command.node_type)
            if event is not None:
                self._commit_node_spawned(event)
                self._publish_projection(event)
                self._set_compile_status(CompileStatus.DIRTY)
                self._commit_document()
            return
        if isinstance(command, DeleteNode):
            event = self._delete_node(command.node_tag)
            if event is not None:
                self._commit_node_deleted(event)
                self._publish_projection(event)
                self._set_compile_status(CompileStatus.DIRTY)
                self._commit_document()
            return
        if isinstance(command, RequestCompile):
            if self.compile_status is CompileStatus.COMPILING:
                self._publish_projection(MutationRejected(
                    type(command).__name__, "COMPILE_BUSY",
                    request_id=getattr(command, "request_id", None)))
                return
            previous_status = self.compile_status
            self._compile_job_counter += 1
            active = (
                f"compile_{self._compile_job_counter}",
                self._document_revision(),
            )
            with self._status_lock:
                self._active_compile = active
                self._compile_status = CompileStatus.COMPILING
            if not self._request_compile(command.kind, command.node_tag):
                with self._status_lock:
                    self._active_compile = None
                    self._compile_status = previous_status
            return
        if isinstance(command, CompileCompleted):
            if self.compile_status is not CompileStatus.COMPILING:
                return
            with self._status_lock:
                active = self._active_compile
                identified = command.job_id is not None or command.document_revision is not None
                if identified and active != (command.job_id, command.document_revision):
                    return
                has_metadata = command.execution_order is not None
                if has_metadata:
                    self._commit_compile_metadata(command)
                self._active_compile = None
                self._compile_status = (
                    CompileStatus.VALID if command.success else CompileStatus.FAILED)
            if has_metadata:
                self._publish_projection(CompileMetadataChanged(
                    node_ids=command.node_ids or frozenset(),
                    stale_node_ids=command.stale_node_ids or frozenset(),
                    failed_node_ids=command.failed_node_ids or frozenset(),
                    cycle_node_ids=command.cycle_node_ids or frozenset(),
                ))
            self._publish_snapshot()
            return
        if isinstance(command, LoadPreset):
            revision = self._document_revision()
            self._request_preset_load(
                command.name,
                command.project_id or "project_1",
                revision,
                command.request_id or f"load:{command.name}:{revision}",
            )
            return
        if isinstance(command, ApplyPresetCandidate):
            self._publish_projection(PresetApplyRequested(
                command.name, command.data, command.warnings))
            return
        if isinstance(command, PresetLoadRejected):
            self._reject_preset_load(command.name, command.errors)
            return
        if isinstance(command, SavePreset):
            revision = self._document_revision()
            self._publish_projection(PresetCaptureRequested(
                command.name,
                command.include_params,
                command.as_default,
                self._capture_graph_document(),
                "project_1",
                revision,
                f"save:{command.name}:{revision}",
            ))
            return
        if isinstance(command, PresetDocumentCaptured):
            if command.captured_revision != self._document_revision():
                self._publish_projection(PresetSaveFailed(
                    command.name, "graph changed during workspace capture"))
                return
            future = self._request_preset_save(
                command.name,
                command.document,
                command.captured_revision,
                command.request_id,
                command.as_default,
            )
            if future is None:
                self._publish_projection(PresetSaveFailed(
                    command.name, "persistence worker unavailable"))
            return
        if isinstance(command, PresetApplyCompleted):
            if command.status == "APPLIED":
                self._set_compile_status(CompileStatus.DIRTY)
                self._commit_document()
            else:
                self._publish_snapshot()
            return
        if isinstance(command, (PresetSaveCompleted, PresetSaveFailed)):
            self._publish_projection(command)
            return
        raise TypeError(f"unsupported graph command: {type(command).__name__}")

    def _publish_graph_replacement(self, event: GraphReplaced) -> None:
        if self._projection_barrier is None:
            self._publish_projection(event)
            return
        generation = self._projection_barrier.begin()
        result = self._publish_projection(event)
        if result is not BusMessageResult.ENQUEUED:
            self._projection_barrier.complete(generation)
            return
        self._projection_barrier.wait(generation)

    def _publish_projection(self, event) -> BusMessageResult:
        return self._publish(Message(
            source="application.graph.owner",
            destination=GraphEndpoint.GUI,
            topic=GraphTopic.PROJECTION,
            payload=event,
        ))

    def _publish(self, message: Message) -> BusMessageResult:
        result = self._bus.publish_wait(
            message, timeout=self._PUBLISH_TIMEOUT)
        if result is not BusMessageResult.ENQUEUED:
            logger.error(
                "Graph owner could not publish %s: %s",
                message.topic,
                result.describe(message.destination),
            )
        return result