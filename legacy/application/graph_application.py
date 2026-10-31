from collections.abc import Callable
import logging

from core.bus import Bus, BusMessageResult, Message
from core.graph_documents import GraphDocument, ProjectDocument, WorkspaceDocument
from core.graph_contracts import (
    ConnectPins,
    CompileKind,
    CompileMetadataChanged,
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
    NodeStateAction,
    NodeDeleted,
    NodeSpawned,
    ParameterChanged,
    PresetApplyRequested,
    PresetCaptureRequested,
    PresetDocumentCaptured,
    PresetSaveCompleted,
    PresetSaveFailed,
    PracticeSettingsChanged,
    ProjectSaved,
    ProjectSaveFailed,
    PresetApplyCompleted,
    RequestCompile,
    RedoGraph,
    RenameNode,
    SavePreset,
    SetParameter,
    SetNodeState,
    SpawnNode,
    UndoGraph,
    DeleteNode,
    LoadPreset,
    MutationRejected,
)


logger = logging.getLogger(__name__)


class GraphApplication:
    """Project immutable graph events on the GUI thread that drains its mailbox."""

    def __init__(
        self,
        apply_preset_candidate: Callable[[str, dict, tuple[str, ...]], object],
        capture_preset_document: Callable[
            [bool, GraphDocument, str], ProjectDocument | None],
        preset_save_completed: Callable[[str, bool, int], object],
        preset_save_failed: Callable[[str, str], object],
        project_node_rename: Callable[[str, str], None] = lambda *_args: None,
        project_node_parameter: Callable[[str, str, object], None] = lambda *_args: None,
        project_node_state: Callable[[str, str, object, int | None], None] = lambda *_args: None,
        project_connection_added: Callable[[ConnectionAdded], None] = lambda *_args: None,
        project_connection_removed: Callable[[ConnectionRemoved], None] = lambda *_args: None,
        project_node_spawned: Callable[[NodeSpawned], None] = lambda *_args: None,
        project_node_deleted: Callable[[NodeDeleted], None] = lambda *_args: None,
        project_mutation_rejected: Callable[[MutationRejected], None] = lambda *_args: None,
        project_compile_metadata: Callable[[CompileMetadataChanged], None] = lambda *_args: None,
        project_graph_replaced: Callable[[GraphReplaced], None] = lambda *_args: None,
        project_saved: Callable[[ProjectSaved], None] = lambda *_args: None,
        project_save_failed: Callable[[ProjectSaveFailed], None] = lambda *_args: None,
        complete_preset_apply: Callable[[str, object], None] = lambda *_args: None,
        submit_preset_document: Callable[
            [str, ProjectDocument | None, bool, int, str], None] = lambda *_args: None,
        project_practice_settings: Callable[[PracticeSettingsChanged], None] = lambda *_args: None,
        projection_changed: Callable[[], None] = lambda: None,
        projection_completed: Callable[[], None] = lambda: None,
    ) -> None:
        self._apply_preset_candidate = apply_preset_candidate
        self._capture_preset_document = capture_preset_document
        self._preset_save_completed = preset_save_completed
        self._preset_save_failed = preset_save_failed
        self._project_node_rename = project_node_rename
        self._project_node_parameter = project_node_parameter
        self._project_node_state = project_node_state
        self._project_connection_added = project_connection_added
        self._project_connection_removed = project_connection_removed
        self._project_node_spawned = project_node_spawned
        self._project_node_deleted = project_node_deleted
        self._project_mutation_rejected = project_mutation_rejected
        self._project_compile_metadata = project_compile_metadata
        self._project_graph_replaced = project_graph_replaced
        self._project_saved = project_saved
        self._project_save_failed = project_save_failed
        self._complete_preset_apply = complete_preset_apply
        self._submit_preset_document = submit_preset_document
        self._project_practice_settings = project_practice_settings
        self._projection_changed = projection_changed
        self._projection_completed = projection_completed

    def handle_message(self, message: Message) -> None:
        if message.topic == GraphTopic.PROJECTION:
            event = message.payload
            if isinstance(event, NodeRenamed):
                self._project_node_rename(event.node_tag, event.name)
                self._projection_changed()
                return
            if isinstance(event, ParameterChanged):
                self._project_node_parameter(event.node_tag, event.name, event.value)
                self._projection_changed()
                return
            if isinstance(event, NodeStateChanged):
                self._project_node_state(
                    event.node_tag, event.field, event.value, event.index)
                self._projection_changed()
                return
            if isinstance(event, ConnectionAdded):
                self._project_connection_added(event)
                self._projection_changed()
                return
            if isinstance(event, ConnectionRemoved):
                self._project_connection_removed(event)
                self._projection_changed()
                return
            if isinstance(event, NodeSpawned):
                self._project_node_spawned(event)
                self._projection_changed()
                return
            if isinstance(event, NodeDeleted):
                self._project_node_deleted(event)
                self._projection_changed()
                return
            if isinstance(event, MutationRejected):
                self._project_mutation_rejected(event)
                return
            if isinstance(event, CompileMetadataChanged):
                self._project_compile_metadata(event)
                return
            if isinstance(event, GraphReplaced):
                try:
                    self._project_graph_replaced(event)
                    self._projection_changed()
                finally:
                    self._projection_completed()
                return
            if isinstance(event, PracticeSettingsChanged):
                self._project_practice_settings(event)
                return
            if isinstance(event, ProjectSaved):
                self._project_saved(event)
                return
            if isinstance(event, ProjectSaveFailed):
                self._project_save_failed(event)
                return
            if isinstance(event, PresetApplyRequested):
                result = self._apply_preset_candidate(
                    event.name, event.data, event.warnings)
                self._complete_preset_apply(event.name, result)
                self._projection_changed()
                return
            if isinstance(event, PresetCaptureRequested):
                document = self._capture_preset_document(
                    event.include_params, event.graph, event.project_id)
                self._submit_preset_document(
                    event.name,
                    document,
                    event.as_default,
                    event.captured_revision,
                    event.request_id,
                )
                return
            if isinstance(event, PresetSaveCompleted):
                self._preset_save_completed(
                    event.name, event.as_default, event.node_count)
                return
            if isinstance(event, PresetSaveFailed):
                self._preset_save_failed(event.name, event.message)
                return
            raise TypeError(f"unsupported graph projection: {type(event).__name__}")
        raise ValueError(f"unsupported graph topic: {message.topic!r}")


class GraphCommandPublisher:
    _PUBLISH_TIMEOUT = 0.25

    def __init__(self, bus: Bus) -> None:
        self._bus = bus

    def connect_callback(self, sender, app_data) -> BusMessageResult:
        output_pin, input_pin = app_data
        return self._publish(ConnectPins(
            str(sender), str(output_pin), str(input_pin)))

    def disconnect_connection(self, connection_id: str) -> BusMessageResult:
        return self._publish(DisconnectLink(str(connection_id)))

    def delete_node(self, node_tag: str) -> BusMessageResult:
        return self._publish(DeleteNode(str(node_tag)))

    def spawn_callback(self, _sender, _app_data, node_type) -> BusMessageResult:
        return self._publish(SpawnNode(str(node_type)))

    def set_parameter(
            self, node_tag: str, name: str, value,
            *, edit_phase: EditPhase = EditPhase.COMMIT) -> BusMessageResult:
        return self._publish(SetParameter(
            str(node_tag), str(name), value, edit_phase=edit_phase))

    def set_node_state(
            self, node_tag: str, field: str, value=None, *, index=None,
            action: NodeStateAction = NodeStateAction.SET,
            edit_phase: EditPhase = EditPhase.COMMIT) -> BusMessageResult:
        return self._publish(SetNodeState(
            str(node_tag), str(field), value, index, action, edit_phase))

    def commit_workspace_gesture(self, workspace: WorkspaceDocument) -> BusMessageResult:
        return self._publish(CommitWorkspaceGesture(workspace))


    def rename_node(self, node_tag: str, name: str) -> BusMessageResult:
        return self._publish(RenameNode(str(node_tag), str(name)))

    def analyze_callback(self, *_args) -> BusMessageResult:
        return self._publish(RequestCompile(CompileKind.ANALYZE))

    def request_compile(self, kind: CompileKind, node_tag: str | None = None) -> BusMessageResult:
        return self._publish(RequestCompile(kind, node_tag))

    def load_preset(self, name: str) -> BusMessageResult:
        return self._publish(LoadPreset(str(name)))

    def save_preset(
            self, name: str, include_params: bool, as_default: bool) -> BusMessageResult:
        return self._publish(SavePreset(
            str(name), bool(include_params), bool(as_default)))

    def complete_preset_apply(self, name: str, result) -> BusMessageResult:
        status = getattr(getattr(result, "status", None), "name", "UNKNOWN")
        return self._publish(PresetApplyCompleted(
            str(name), status, str(getattr(result, "message", ""))))

    def submit_preset_document(
            self, name: str, document: ProjectDocument | None, as_default: bool,
            captured_revision: int, request_id: str) -> BusMessageResult:
        if document is None:
            return self._publish(PresetSaveFailed(str(name), "workspace capture failed"))
        return self._publish(PresetDocumentCaptured(
            str(name), document, bool(as_default),
            int(captured_revision), str(request_id)))

    def execute(self, command) -> BusMessageResult:
        return self._publish(command)

    def commit_candidate(
        self,
        candidate,
        workspace=None,
        *,
        project_id: str | None = None,
        expected_revision: int | None = None,
        request_id: str | None = None,
    ) -> BusMessageResult:
        return self._publish(CommitGraphCandidate(
            candidate=candidate,
            workspace=workspace,
            project_id=project_id,
            expected_revision=expected_revision,
            request_id=request_id,
        ))

    def undo(self, *, expected_revision: int | None = None) -> BusMessageResult:
        return self._publish(UndoGraph(expected_revision=expected_revision))

    def redo(self, *, expected_revision: int | None = None) -> BusMessageResult:
        return self._publish(RedoGraph(expected_revision=expected_revision))

    def _publish(self, command) -> BusMessageResult:
        result = self._bus.publish_wait(Message(
            source="ui.node_editor",
            destination=GraphEndpoint.SERVICE,
            topic=GraphTopic.COMMAND,
            payload=command,
        ), timeout=self._PUBLISH_TIMEOUT)
        if result is not BusMessageResult.ENQUEUED:
            logger.warning(
                "Graph command %s was not enqueued: %s",
                type(command).__name__, result.describe(GraphEndpoint.SERVICE),
            )
        return result
