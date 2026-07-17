from dataclasses import dataclass
from enum import Enum, auto
import math
from core.graph_documents import (
    CandidateRuntimeGraph,
    GraphDocument,
    PracticeDocument,
    ProjectDocument,
    WorkspaceDocument,
)

from core.graph_state import GraphConnection


class GraphEndpoint:
    SERVICE = "domain.graph"
    GUI = "ui.graph"


class GraphTopic:
    COMMAND = "graph.command"
    PROJECTION = "graph.projection"


@dataclass(frozen=True, slots=True)
class GraphNodePosition:
    node_id: str
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class GraphPanelGeometry:
    node_id: str
    position: tuple[float, float] | None = None
    size: tuple[float, float] | None = None


@dataclass(frozen=True, slots=True)
class GraphWindowState:
    tag: str
    position: tuple[float, float] | None = None
    size: tuple[float, float] | None = None
    visible: bool = True


class CompileStatus(Enum):
    DIRTY = auto()
    COMPILING = auto()
    VALID = auto()
    FAILED = auto()


class EditPhase(Enum):
    UPDATE = auto()
    COMMIT = auto()


class NodeStateAction(Enum):
    SET = auto()
    TOGGLE = auto()
    RESET = auto()


@dataclass(frozen=True, slots=True)
class MutationRejected:
    command: str
    reason: str
    request_id: str | None = None
    expected_revision: int | None = None
    current_revision: int | None = None
    diagnostics: tuple[object, ...] = ()


@dataclass(frozen=True, slots=True)
class CommitGraphCandidate:
    candidate: CandidateRuntimeGraph
    workspace: WorkspaceDocument | None = None
    practice: PracticeDocument | None = None
    project_id: str | None = None
    expected_revision: int | None = None
    request_id: str | None = None
    reset_history: bool = False


@dataclass(frozen=True, slots=True)
class UndoGraph:
    project_id: str | None = None
    expected_revision: int | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class RedoGraph:
    project_id: str | None = None
    expected_revision: int | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class GraphRenderState:
    node_ids: frozenset[str]
    baked_node_ids: frozenset[str]
    stale_node_ids: frozenset[str]
    failed_node_ids: frozenset[str]
    cycle_node_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class GraphReplaced:
    old_node_ids: tuple[str, ...]
    node_positions: tuple[GraphNodePosition, ...]
    connections: tuple[GraphConnection, ...]
    panel_geometries: tuple[GraphPanelGeometry, ...] = ()
    window_states: tuple[GraphWindowState, ...] = ()
    dock_layout: str | None = None
    old_ui_resource_tags: tuple[object, ...] = ()
    render_state: GraphRenderState | None = None


@dataclass(frozen=True, slots=True)
class PracticeSettingsChanged:
    practice: PracticeDocument


@dataclass(frozen=True, slots=True)
class ProjectLoadRejected:
    request_id: str
    diagnostics: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class ProjectSaved:
    request_id: str
    project_id: str
    revision: int
    name: str
    as_default: bool = False
    node_count: int = 0


@dataclass(frozen=True, slots=True)
class ProjectSaveFailed:
    request_id: str
    project_id: str
    revision: int
    name: str
    message: str


@dataclass(frozen=True, slots=True)
class ConnectPins:
    editor_tag: str
    output_pin: str
    input_pin: str
    project_id: str | None = None
    expected_revision: int | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class DisconnectLink:
    connection_id: str
    project_id: str | None = None
    expected_revision: int | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class ConnectionAdded:
    connection_id: str
    editor_tag: str
    output_pin: str
    input_pin: str


@dataclass(frozen=True, slots=True)
class ConnectionRemoved:
    connection_id: str
    output_pin: str
    input_pin: str


@dataclass(frozen=True, slots=True)
class DeleteNode:
    node_tag: str
    project_id: str | None = None
    expected_revision: int | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class SpawnNode:
    node_type: str
    project_id: str | None = None
    expected_revision: int | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class NodeSpawned:
    node_tag: str
    position: tuple[float, float]


@dataclass(frozen=True, slots=True)
class NodeDeleted:
    node_tag: str
    panel_tag: str
    ui_resource_tags: tuple[str, ...]
    removed_connections: tuple[GraphConnection, ...]
    downstream_stale: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SetParameter:
    node_tag: str
    name: str
    value: object
    project_id: str | None = None
    expected_revision: int | None = None
    request_id: str | None = None
    edit_phase: EditPhase = EditPhase.COMMIT

    def __post_init__(self) -> None:
        if not isinstance(self.edit_phase, EditPhase):
            raise ValueError("edit_phase must be an EditPhase")


@dataclass(frozen=True, slots=True)
class SetNodeState:
    node_tag: str
    field: str
    value: None | bool | int | float | str = None
    index: int | None = None
    action: NodeStateAction = NodeStateAction.SET
    edit_phase: EditPhase = EditPhase.COMMIT
    project_id: str | None = None
    expected_revision: int | None = None
    request_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.node_tag, str) or not self.node_tag:
            raise ValueError("node_tag must be a non-empty string")
        if not isinstance(self.field, str) or not self.field:
            raise ValueError("field must be a non-empty string")
        if self.index is not None and (
                not isinstance(self.index, int) or isinstance(self.index, bool)
                or self.index < 0):
            raise ValueError("index must be a non-negative integer or null")
        if not isinstance(self.action, NodeStateAction):
            raise ValueError("action must be a NodeStateAction")
        if not isinstance(self.edit_phase, EditPhase):
            raise ValueError("edit_phase must be an EditPhase")
        if not isinstance(self.value, (type(None), bool, int, float, str)):
            raise ValueError("value must be a scalar")
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError("value must be finite")


@dataclass(frozen=True, slots=True)
class CommitWorkspaceGesture:
    workspace: WorkspaceDocument
    project_id: str | None = None
    expected_revision: int | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class ParameterChanged:
    node_tag: str
    name: str
    value: object


@dataclass(frozen=True, slots=True)
class NodeStateChanged:
    node_tag: str
    field: str
    value: None | bool | int | float | str
    index: int | None = None


@dataclass(frozen=True, slots=True)
class RenameNode:
    node_tag: str
    name: str
    project_id: str | None = None
    expected_revision: int | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class NodeRenamed:
    node_tag: str
    name: str


@dataclass(frozen=True, slots=True)
class LoadPreset:
    name: str
    project_id: str | None = None
    expected_revision: int | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class ApplyPresetCandidate:
    name: str
    data: dict
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PresetLoadRejected:
    name: str
    errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SavePreset:
    name: str
    include_params: bool
    as_default: bool


@dataclass(frozen=True, slots=True)
class PresetSaveCompleted:
    name: str
    as_default: bool
    node_count: int


@dataclass(frozen=True, slots=True)
class PresetSaveFailed:
    name: str
    message: str


@dataclass(frozen=True, slots=True)
class PresetApplyRequested:
    name: str
    data: dict
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PresetApplyCompleted:
    name: str
    status: str
    message: str = ""


@dataclass(frozen=True, slots=True)
class PresetCaptureRequested:
    name: str
    include_params: bool
    as_default: bool
    graph: GraphDocument
    project_id: str
    captured_revision: int
    request_id: str


@dataclass(frozen=True, slots=True)
class PresetDocumentCaptured:
    name: str
    document: ProjectDocument
    as_default: bool
    captured_revision: int
    request_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.document, ProjectDocument):
            raise TypeError("document must be a ProjectDocument")


class CompileKind(Enum):
    ANALYZE = auto()
    BAKE_NODE = auto()


@dataclass(frozen=True, slots=True)
class RequestCompile:
    kind: CompileKind
    node_tag: str | None = None

    def __post_init__(self):
        if self.kind is not CompileKind.ANALYZE and not self.node_tag:
            raise ValueError(f"{self.kind.name} requires node_tag")


@dataclass(frozen=True, slots=True)
class CompileCompleted:
    success: bool = True
    job_id: str | None = None
    document_revision: int | None = None
    execution_order: tuple[str, ...] | None = None
    node_ids: frozenset[str] | None = None
    stale_node_ids: frozenset[str] | None = None
    failed_node_ids: frozenset[str] | None = None
    cycle_node_ids: frozenset[str] | None = None


@dataclass(frozen=True, slots=True)
class CompileMetadataChanged:
    node_ids: frozenset[str]
    stale_node_ids: frozenset[str]
    failed_node_ids: frozenset[str]
    cycle_node_ids: frozenset[str]
