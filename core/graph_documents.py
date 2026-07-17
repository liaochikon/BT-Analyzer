"""Versioned, immutable graph documents and detached runtime materialization."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from core.graph_state import GraphConnection, GraphState


GRAPH_DOCUMENT_SCHEMA_VERSION = 1
PROJECT_DOCUMENT_SCHEMA_VERSION = 1
JsonValue = None | bool | int | float | str | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]


def _freeze_json(value: Any, path: str) -> JsonValue:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must not contain a non-finite number")
        return value
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item, f"{path}[]") for item in value)
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError(f"{path} object keys must be strings")
        return MappingProxyType({
            key: _freeze_json(item, f"{path}.{key}")
            for key, item in value.items()
        })
    raise ValueError(f"{path} contains unsupported value {type(value).__name__}")


def _thaw_json(value: JsonValue) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class NodeDocument:
    node_id: str
    node_type: str
    node_version: int = 1
    display_name: str | None = None
    parameters: Mapping[str, JsonValue] = field(default_factory=dict)
    extra: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.node_id, str) or not self.node_id:
            raise ValueError("node_id must be a non-empty string")
        if not isinstance(self.node_type, str) or not self.node_type:
            raise ValueError("node_type must be a non-empty string")
        if not isinstance(self.node_version, int) or isinstance(self.node_version, bool):
            raise ValueError("node_version must be an integer")
        if self.display_name is not None and not isinstance(self.display_name, str):
            raise ValueError("display_name must be a string or null")
        object.__setattr__(self, "parameters", _freeze_json(self.parameters, "parameters"))
        object.__setattr__(self, "extra", _freeze_json(self.extra, "extra"))


@dataclass(frozen=True, slots=True)
class ConnectionDocument:
    connection_id: str
    output_node_id: str
    output_port: str
    input_node_id: str
    input_port: str

    def __post_init__(self) -> None:
        for name in (
            "connection_id", "output_node_id", "output_port", "input_node_id", "input_port",
        ):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ValueError(f"{name} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class ArtifactBinding:
    port_id: str
    artifact_id: str


@dataclass(frozen=True, slots=True)
class OutputBinding:
    port_id: str
    name: str


@dataclass(frozen=True, slots=True)
class Position:
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class PanelDocument:
    position: Position | None = None
    size: Position | None = None


@dataclass(frozen=True, slots=True)
class WindowDocument:
    position: Position | None = None
    size: Position | None = None
    visible: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.visible, bool):
            raise ValueError("visible must be a boolean")


@dataclass(frozen=True, slots=True)
class WorkspaceDocument:
    node_positions: Mapping[str, Position] = field(default_factory=dict)
    node_panels: Mapping[str, PanelDocument] = field(default_factory=dict)
    windows: Mapping[str, WindowDocument] = field(default_factory=dict)
    dock_layout: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "node_positions", MappingProxyType(dict(self.node_positions)))
        object.__setattr__(self, "node_panels", MappingProxyType(dict(self.node_panels)))
        object.__setattr__(self, "windows", MappingProxyType(dict(self.windows)))
        if self.dock_layout is not None and not isinstance(self.dock_layout, str):
            raise ValueError("dock_layout must be a string or null")


@dataclass(frozen=True, slots=True)
class PracticeDocument:
    settings: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "settings", _freeze_json(self.settings, "practice.settings"))


@dataclass(frozen=True, slots=True)
class ProjectMetadata:
    properties: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "properties", _freeze_json(self.properties, "metadata.properties"))


@dataclass(frozen=True, slots=True)
class ProjectDocument:
    schema_version: int
    project_id: str
    graph: GraphDocument
    workspace: WorkspaceDocument | None = None
    practice: PracticeDocument | None = None
    metadata: ProjectMetadata = field(default_factory=ProjectMetadata)

    def __post_init__(self) -> None:
        if self.schema_version != PROJECT_DOCUMENT_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {PROJECT_DOCUMENT_SCHEMA_VERSION}")
        if not isinstance(self.project_id, str) or not self.project_id:
            raise ValueError("project_id must be a non-empty string")


@dataclass(frozen=True, slots=True)
class LegacyMigrationResult:
    graph: GraphDocument | None
    workspace: WorkspaceDocument | None = None
    practice: PracticeDocument | None = None
    diagnostics: tuple[DocumentDiagnostic, ...] = ()

    @property
    def ok(self) -> bool:
        return self.graph is not None and not self.diagnostics


@dataclass(frozen=True, slots=True)
class GraphDocument:
    schema_version: int
    graph_id: str
    nodes: tuple[NodeDocument, ...]
    connections: tuple[ConnectionDocument, ...]
    inputs: tuple[ArtifactBinding, ...] = ()
    outputs: tuple[OutputBinding, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != GRAPH_DOCUMENT_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {GRAPH_DOCUMENT_SCHEMA_VERSION}")
        if not isinstance(self.graph_id, str) or not self.graph_id:
            raise ValueError("graph_id must be a non-empty string")


@dataclass(frozen=True, slots=True)
class DocumentDiagnostic:
    code: str
    path: str
    message: str


@dataclass(frozen=True, slots=True)
class DocumentDecodeResult:
    document: GraphDocument | None
    diagnostics: tuple[DocumentDiagnostic, ...] = ()

    @property
    def ok(self) -> bool:
        return self.document is not None and not self.diagnostics


@dataclass(frozen=True, slots=True)
class ProjectDecodeResult:
    document: ProjectDocument | None
    diagnostics: tuple[DocumentDiagnostic, ...] = ()

    @property
    def ok(self) -> bool:
        return self.document is not None and not self.diagnostics


@dataclass(frozen=True, slots=True)
class CandidateRuntimeGraph:
    graph_id: str
    nodes: Mapping[str, Any]
    connections: tuple[GraphConnection, ...]
    execution_order: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "nodes", MappingProxyType(dict(self.nodes)))


@dataclass(frozen=True, slots=True)
class MaterializeResult:
    candidate: CandidateRuntimeGraph | None
    diagnostics: tuple[DocumentDiagnostic, ...] = ()

    @property
    def ok(self) -> bool:
        return self.candidate is not None and not self.diagnostics


class GraphDocumentCodec:
    @staticmethod
    def to_dict(document: GraphDocument) -> dict:
        return {
            "schema_version": document.schema_version,
            "graph_id": document.graph_id,
            "nodes": [
                {
                    "node_id": node.node_id,
                    "node_type": node.node_type,
                    "node_version": node.node_version,
                    "display_name": node.display_name,
                    "parameters": _thaw_json(node.parameters),
                    "extra": _thaw_json(node.extra),
                }
                for node in document.nodes
            ],
            "connections": [
                {
                    "connection_id": connection.connection_id,
                    "output_node_id": connection.output_node_id,
                    "output_port": connection.output_port,
                    "input_node_id": connection.input_node_id,
                    "input_port": connection.input_port,
                }
                for connection in document.connections
            ],
            "inputs": [
                {"port_id": binding.port_id, "artifact_id": binding.artifact_id}
                for binding in document.inputs
            ],
            "outputs": [
                {"port_id": binding.port_id, "name": binding.name}
                for binding in document.outputs
            ],
        }

    @classmethod
    def dumps(cls, document: GraphDocument) -> str:
        return json.dumps(cls.to_dict(document), ensure_ascii=False, sort_keys=True)

    @classmethod
    def loads(cls, payload: str) -> DocumentDecodeResult:
        try:
            data = json.loads(payload)
        except (TypeError, json.JSONDecodeError) as exc:
            return DocumentDecodeResult(None, (
                DocumentDiagnostic("invalid_json", "$", str(exc)),
            ))
        return cls.from_dict(data)

    @staticmethod
    def from_dict(data: Any) -> DocumentDecodeResult:
        diagnostics: list[DocumentDiagnostic] = []
        if not isinstance(data, dict):
            return DocumentDecodeResult(None, (
                DocumentDiagnostic("invalid_document", "$", "Document must be an object."),
            ))
        version = data.get("schema_version")
        if version != GRAPH_DOCUMENT_SCHEMA_VERSION:
            return DocumentDecodeResult(None, (
                DocumentDiagnostic(
                    "unsupported_schema_version", "$.schema_version",
                    f"Expected schema version {GRAPH_DOCUMENT_SCHEMA_VERSION}, got {version!r}.",
                ),
            ))
        graph_id = data.get("graph_id")
        if not isinstance(graph_id, str) or not graph_id:
            diagnostics.append(DocumentDiagnostic(
                "invalid_graph_id", "$.graph_id", "graph_id must be a non-empty string."))

        nodes = _decode_records(data.get("nodes"), "nodes", NodeDocument, diagnostics)
        connections = _decode_records(
            data.get("connections"), "connections", ConnectionDocument, diagnostics)
        inputs = _decode_records(data.get("inputs", []), "inputs", ArtifactBinding, diagnostics)
        outputs = _decode_records(data.get("outputs", []), "outputs", OutputBinding, diagnostics)
        if diagnostics:
            return DocumentDecodeResult(None, tuple(diagnostics))
        return DocumentDecodeResult(GraphDocument(
            schema_version=version,
            graph_id=graph_id,
            nodes=tuple(nodes),
            connections=tuple(connections),
            inputs=tuple(inputs),
            outputs=tuple(outputs),
        ))


class ProjectDocumentCodec:
    @staticmethod
    def to_dict(document: ProjectDocument) -> dict:
        workspace = None
        if document.workspace is not None:
            workspace = {
                "node_positions": {
                    node_id: [position.x, position.y]
                    for node_id, position in document.workspace.node_positions.items()
                },
                "node_panels": {
                    node_id: {
                        "position": _position_to_list(panel.position),
                        "size": _position_to_list(panel.size),
                    }
                    for node_id, panel in document.workspace.node_panels.items()
                },
                "windows": {
                    tag: {
                        "position": _position_to_list(window.position),
                        "size": _position_to_list(window.size),
                        "visible": window.visible,
                    }
                    for tag, window in document.workspace.windows.items()
                },
                "dock_layout": document.workspace.dock_layout,
            }
        practice = None
        if document.practice is not None:
            practice = {"settings": _thaw_json(document.practice.settings)}
        return {
            "schema_version": document.schema_version,
            "project_id": document.project_id,
            "graph": GraphDocumentCodec.to_dict(document.graph),
            "workspace": workspace,
            "practice": practice,
            "metadata": {
                "properties": _thaw_json(document.metadata.properties),
            },
        }

    @classmethod
    def dumps(cls, document: ProjectDocument) -> str:
        return json.dumps(cls.to_dict(document), ensure_ascii=False, sort_keys=True)

    @classmethod
    def loads(cls, payload: str) -> ProjectDecodeResult:
        try:
            data = json.loads(payload)
        except (TypeError, json.JSONDecodeError) as exc:
            return ProjectDecodeResult(None, (
                DocumentDiagnostic("invalid_json", "$", str(exc)),
            ))
        return cls.from_dict(data)

    @staticmethod
    def from_dict(data: Any) -> ProjectDecodeResult:
        if not isinstance(data, dict):
            return ProjectDecodeResult(None, (
                DocumentDiagnostic("invalid_project", "$", "Project must be an object."),
            ))
        version = data.get("schema_version")
        if version != PROJECT_DOCUMENT_SCHEMA_VERSION:
            return ProjectDecodeResult(None, (
                DocumentDiagnostic(
                    "unsupported_project_schema", "$.schema_version",
                    f"Expected project schema {PROJECT_DOCUMENT_SCHEMA_VERSION}, got {version!r}.",
                ),
            ))
        project_id = data.get("project_id")
        if not isinstance(project_id, str) or not project_id:
            return ProjectDecodeResult(None, (
                DocumentDiagnostic(
                    "invalid_project_id", "$.project_id",
                    "project_id must be a non-empty string.",
                ),
            ))
        graph_result = GraphDocumentCodec.from_dict(data.get("graph"))
        if not graph_result.ok:
            return ProjectDecodeResult(None, tuple(
                DocumentDiagnostic(item.code, f"$.graph{item.path[1:]}", item.message)
                for item in graph_result.diagnostics
            ))
        diagnostics = []
        workspace = _decode_workspace(data.get("workspace"), diagnostics)
        practice = _decode_practice(data.get("practice"), diagnostics)
        metadata_data = data.get("metadata", {})
        if not isinstance(metadata_data, dict):
            diagnostics.append(DocumentDiagnostic(
                "invalid_metadata", "$.metadata", "metadata must be an object."))
            metadata = ProjectMetadata()
        else:
            try:
                metadata = ProjectMetadata(metadata_data.get("properties", {}))
            except ValueError as exc:
                diagnostics.append(DocumentDiagnostic(
                    "invalid_metadata", "$.metadata.properties", str(exc)))
                metadata = ProjectMetadata()
        if diagnostics:
            return ProjectDecodeResult(None, tuple(diagnostics))
        return ProjectDecodeResult(ProjectDocument(
            schema_version=version,
            project_id=project_id,
            graph=graph_result.document,
            workspace=workspace,
            practice=practice,
            metadata=metadata,
        ))


def _position_to_list(position: Position | None):
    return None if position is None else [position.x, position.y]


def _decode_position(value, path, diagnostics) -> Position | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 2 or not all(
        isinstance(item, (int, float)) and not isinstance(item, bool)
        and math.isfinite(item) for item in value
    ):
        diagnostics.append(DocumentDiagnostic(
            "invalid_position", path, "Position must contain two finite numbers."))
        return None
    return Position(float(value[0]), float(value[1]))


def _decode_workspace(value, diagnostics) -> WorkspaceDocument | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        diagnostics.append(DocumentDiagnostic(
            "invalid_workspace", "$.workspace", "workspace must be an object or null."))
        return None
    positions_data = value.get("node_positions", {})
    panels_data = value.get("node_panels", {})
    windows_data = value.get("windows", {})
    dock_layout = value.get("dock_layout")
    if not all(isinstance(item, dict) for item in (
            positions_data, panels_data, windows_data)):
        diagnostics.append(DocumentDiagnostic(
            "invalid_workspace", "$.workspace",
            "node_positions, node_panels, and windows must be objects."))
        return None
    if dock_layout is not None and not isinstance(dock_layout, str):
        diagnostics.append(DocumentDiagnostic(
            "invalid_dock_layout", "$.workspace.dock_layout",
            "dock_layout must be a string or null."))
    positions = {
        node_id: position
        for node_id, raw in positions_data.items()
        if isinstance(node_id, str)
        and (position := _decode_position(
            raw, f"$.workspace.node_positions.{node_id}", diagnostics)) is not None
    }
    panels = {}
    for node_id, raw in panels_data.items():
        if not isinstance(node_id, str) or not isinstance(raw, dict):
            diagnostics.append(DocumentDiagnostic(
                "invalid_panel", f"$.workspace.node_panels.{node_id}",
                "Panel must be an object."))
            continue
        panels[node_id] = PanelDocument(
            _decode_position(
                raw.get("position"),
                f"$.workspace.node_panels.{node_id}.position", diagnostics),
            _decode_position(
                raw.get("size"),
                f"$.workspace.node_panels.{node_id}.size", diagnostics),
        )
    windows = {}
    for tag, raw in windows_data.items():
        path = f"$.workspace.windows.{tag}"
        if not isinstance(tag, str) or not tag or not isinstance(raw, dict):
            diagnostics.append(DocumentDiagnostic(
                "invalid_window", path, "Window must be a named object."))
            continue
        visible = raw.get("visible", True)
        if not isinstance(visible, bool):
            diagnostics.append(DocumentDiagnostic(
                "invalid_window", f"{path}.visible", "visible must be a boolean."))
            continue
        windows[tag] = WindowDocument(
            _decode_position(raw.get("position"), f"{path}.position", diagnostics),
            _decode_position(raw.get("size"), f"{path}.size", diagnostics),
            visible,
        )
    return WorkspaceDocument(positions, panels, windows, dock_layout)


def _decode_practice(value, diagnostics) -> PracticeDocument | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        diagnostics.append(DocumentDiagnostic(
            "invalid_practice", "$.practice", "practice must be an object or null."))
        return None
    try:
        return PracticeDocument(value.get("settings", {}))
    except ValueError as exc:
        diagnostics.append(DocumentDiagnostic(
            "invalid_practice", "$.practice.settings", str(exc)))
        return None


def _decode_records(data, name, record_type, diagnostics):
    if not isinstance(data, list):
        diagnostics.append(DocumentDiagnostic(
            "invalid_collection", f"$.{name}", f"{name} must be an array."))
        return []
    records = []
    for index, item in enumerate(data):
        path = f"$.{name}[{index}]"
        if not isinstance(item, dict):
            diagnostics.append(DocumentDiagnostic(
                "invalid_record", path, f"{name} item must be an object."))
            continue
        try:
            records.append(record_type(**item))
        except (TypeError, ValueError) as exc:
            diagnostics.append(DocumentDiagnostic("invalid_record", path, str(exc)))
    return records


def _port_role(node_id: str, pin_id: str) -> str:
    prefix = f"{node_id}_"
    if not pin_id.startswith(prefix) or "::" not in pin_id:
        raise ValueError(f"pin {pin_id!r} does not belong to node {node_id!r}")
    return pin_id[len(prefix):].rsplit("::", 1)[0]


def export_graph_document(state: GraphState, graph_id: str) -> GraphDocument:
    nodes = tuple(
        NodeDocument(
            node_id=node_id,
            node_type=type(node).__name__,
            node_version=getattr(type(node), "node_version", 1),
            display_name=node.custom_name or None,
            parameters=node.serialize_params(),
            extra=node.serialize_extra(),
        )
        for node_id, node in state.nodes.items()
    )
    connections = tuple(
        ConnectionDocument(
            connection_id=connection.connection_id,
            output_node_id=state.pin_owners[connection.output_pin],
            output_port=_port_role(
                state.pin_owners[connection.output_pin], connection.output_pin),
            input_node_id=state.pin_owners[connection.input_pin],
            input_port=_port_role(
                state.pin_owners[connection.input_pin], connection.input_pin),
        )
        for connection in state.iter_connections()
    )
    return GraphDocument(
        schema_version=GRAPH_DOCUMENT_SCHEMA_VERSION,
        graph_id=graph_id,
        nodes=nodes,
        connections=connections,
    )


def materialize_graph_document(
    document: GraphDocument,
    node_types: Mapping[str, type],
) -> MaterializeResult:
    diagnostics: list[DocumentDiagnostic] = []
    nodes = {}
    for index, node_document in enumerate(document.nodes):
        path = f"$.nodes[{index}]"
        if node_document.node_id in nodes:
            diagnostics.append(DocumentDiagnostic(
                "duplicate_node_id", f"{path}.node_id",
                f"Node ID {node_document.node_id!r} is duplicated."))
            continue
        node_class = node_types.get(node_document.node_type)
        if node_class is None:
            diagnostics.append(DocumentDiagnostic(
                "unknown_node_type", f"{path}.node_type",
                f"Unknown node type {node_document.node_type!r}."))
            continue
        expected_version = getattr(node_class, "node_version", 1)
        if node_document.node_version != expected_version:
            diagnostics.append(DocumentDiagnostic(
                "unsupported_node_version", f"{path}.node_version",
                f"Expected node version {expected_version}, got {node_document.node_version}.",
            ))
            continue
        try:
            node = node_class(node_document.node_id)
        except Exception as exc:
            diagnostics.append(DocumentDiagnostic(
                "node_construction_failed", path, str(exc)))
            continue
        unknown_parameters = set(node_document.parameters) - set(node._param_specs)
        for name in sorted(unknown_parameters):
            diagnostics.append(DocumentDiagnostic(
                "unknown_parameter", f"{path}.parameters.{name}",
                f"Unknown parameter {name!r} for {node_document.node_type}."))
        if unknown_parameters:
            continue
        node.apply_params_state(_thaw_json(node_document.parameters))
        node.apply_extra_state(_thaw_json(node_document.extra))
        node.set_custom_name(node_document.display_name or "", sync_ui=False)
        nodes[node_document.node_id] = node

    connection_ids = set()
    connected_inputs = set()
    connections = []
    for index, connection in enumerate(document.connections):
        path = f"$.connections[{index}]"
        if connection.connection_id in connection_ids:
            diagnostics.append(DocumentDiagnostic(
                "duplicate_connection_id", f"{path}.connection_id",
                f"Connection ID {connection.connection_id!r} is duplicated."))
            continue
        connection_ids.add(connection.connection_id)
        output_node = nodes.get(connection.output_node_id)
        input_node = nodes.get(connection.input_node_id)
        if output_node is None or input_node is None:
            diagnostics.append(DocumentDiagnostic(
                "unknown_connection_node", path,
                "Connection references a node that could not be materialized."))
            continue
        output_pin = _pin_for_role(output_node.output_spec, connection.output_node_id, connection.output_port)
        input_pin = _pin_for_role(input_node.input_spec, connection.input_node_id, connection.input_port)
        if output_pin is None:
            diagnostics.append(DocumentDiagnostic(
                "unknown_output_port", f"{path}.output_port",
                f"Unknown output port {connection.output_port!r}."))
            continue
        if input_pin is None:
            diagnostics.append(DocumentDiagnostic(
                "unknown_input_port", f"{path}.input_port",
                f"Unknown input port {connection.input_port!r}."))
            continue
        if input_pin in connected_inputs:
            diagnostics.append(DocumentDiagnostic(
                "duplicate_input", f"{path}.input_port", "Input already has a connection."))
            continue
        if output_node.output_spec[output_pin] != input_node.input_spec[input_pin]:
            diagnostics.append(DocumentDiagnostic(
                "port_type_mismatch", path, "Connection port data types do not match."))
            continue
        connected_inputs.add(input_pin)
        connections.append(GraphConnection(connection.connection_id, output_pin, input_pin))

    if diagnostics:
        return MaterializeResult(None, tuple(diagnostics))
    execution_order = _candidate_execution_order(nodes, connections)
    if execution_order is None:
        return MaterializeResult(None, (DocumentDiagnostic(
            "graph_cycle", "$.connections",
            "Connections contain a cycle; candidate was not materialized.",
        ),))
    return MaterializeResult(CandidateRuntimeGraph(
        graph_id=document.graph_id,
        nodes=nodes,
        connections=tuple(connections),
        execution_order=execution_order,
    ))


def _pin_for_role(spec: Mapping[str, Any], node_id: str, role: str) -> str | None:
    prefix = f"{node_id}_{role}::"
    return next((pin for pin in spec if pin.startswith(prefix)), None)


def _candidate_execution_order(nodes, connections) -> tuple[str, ...] | None:
    owners = {}
    for node_id, node in nodes.items():
        for pin in (*node.input_spec, *node.output_spec):
            owners[pin] = node_id
    adjacency = {node_id: [] for node_id in nodes}
    in_degree = {node_id: 0 for node_id in nodes}
    for connection in connections:
        source = owners[connection.output_pin]
        target = owners[connection.input_pin]
        if source == target:
            return None
        adjacency[source].append(target)
        in_degree[target] += 1
    ready = [node_id for node_id, degree in in_degree.items() if degree == 0]
    order = []
    while ready:
        node_id = ready.pop(0)
        order.append(node_id)
        for target in adjacency[node_id]:
            in_degree[target] -= 1
            if in_degree[target] == 0:
                ready.append(target)
    return tuple(order) if len(order) == len(nodes) else None


def migrate_legacy_preset(data: Any, graph_id: str = "graph_1") -> LegacyMigrationResult:
    diagnostics: list[DocumentDiagnostic] = []
    if not isinstance(data, dict):
        return LegacyMigrationResult(None, diagnostics=(DocumentDiagnostic(
            "invalid_legacy_preset", "$", "Legacy preset must be an object."),))
    legacy_nodes = data.get("nodes", [])
    legacy_links = data.get("links", [])
    if not isinstance(legacy_nodes, list):
        diagnostics.append(DocumentDiagnostic(
            "invalid_collection", "$.nodes", "nodes must be an array."))
        legacy_nodes = []
    if not isinstance(legacy_links, list):
        diagnostics.append(DocumentDiagnostic(
            "invalid_collection", "$.links", "links must be an array."))
        legacy_links = []

    nodes = []
    positions = {}
    panels = {}
    for index, item in enumerate(legacy_nodes):
        path = f"$.nodes[{index}]"
        if not isinstance(item, dict):
            diagnostics.append(DocumentDiagnostic(
                "invalid_record", path, "Node must be an object."))
            nodes.append(None)
            continue
        node_type = item.get("class")
        if not isinstance(node_type, str) or not node_type:
            diagnostics.append(DocumentDiagnostic(
                "invalid_node_type", f"{path}.class", "class must be a non-empty string."))
            nodes.append(None)
            continue
        node_id = f"node_{node_type}_{index + 1}"
        try:
            node = NodeDocument(
                node_id=node_id,
                node_type=node_type,
                display_name=item.get("custom_name") or None,
                parameters=item.get("params", {}),
                extra=item.get("extra", {}),
            )
        except ValueError as exc:
            diagnostics.append(DocumentDiagnostic("invalid_node", path, str(exc)))
            nodes.append(None)
            continue
        nodes.append(node)
        position = _legacy_position(item.get("pos"), f"{path}.pos", diagnostics)
        if position is not None:
            positions[node_id] = position
        panel = item.get("panel")
        if panel is not None:
            if not isinstance(panel, dict):
                diagnostics.append(DocumentDiagnostic(
                    "invalid_panel", f"{path}.panel", "panel must be an object."))
            else:
                panel_position = _legacy_position(
                    panel.get("pos"), f"{path}.panel.pos", diagnostics)
                panel_size = _legacy_position(
                    panel.get("size"), f"{path}.panel.size", diagnostics)
                panels[node_id] = PanelDocument(panel_position, panel_size)

    existing_numbers = [
        _connection_number(link[4])
        for link in legacy_links
        if isinstance(link, (list, tuple)) and len(link) == 5
    ]
    next_connection = max((number for number in existing_numbers if number is not None), default=0)
    used_connection_ids = set()
    connections = []
    for index, link in enumerate(legacy_links):
        path = f"$.links[{index}]"
        if not isinstance(link, (list, tuple)) or len(link) not in (4, 5):
            diagnostics.append(DocumentDiagnostic(
                "invalid_legacy_link", path, "Link must have four or five elements."))
            continue
        output_index, output_suffix, input_index, input_suffix = link[:4]
        if not all(isinstance(value, int) and not isinstance(value, bool)
                   for value in (output_index, input_index)):
            diagnostics.append(DocumentDiagnostic(
                "invalid_node_index", path, "Link node indices must be integers."))
            continue
        if not (0 <= output_index < len(nodes) and 0 <= input_index < len(nodes)):
            diagnostics.append(DocumentDiagnostic(
                "unknown_connection_node", path, "Link node index is out of range."))
            continue
        output_node = nodes[output_index]
        input_node = nodes[input_index]
        if output_node is None or input_node is None:
            diagnostics.append(DocumentDiagnostic(
                "unknown_connection_node", path, "Link references an invalid node."))
            continue
        output_port = _legacy_port_role(output_suffix)
        input_port = _legacy_port_role(input_suffix)
        if output_port is None or input_port is None:
            diagnostics.append(DocumentDiagnostic(
                "invalid_port_role", path, "Link pin suffix is invalid."))
            continue
        if len(link) == 5:
            connection_id = link[4]
            if not isinstance(connection_id, str) or not connection_id:
                diagnostics.append(DocumentDiagnostic(
                    "invalid_connection_id", f"{path}[4]",
                    "Connection ID must be a non-empty string."))
                continue
        else:
            next_connection += 1
            connection_id = f"connection_{next_connection}"
        if connection_id in used_connection_ids:
            diagnostics.append(DocumentDiagnostic(
                "duplicate_connection_id", f"{path}[4]",
                f"Connection ID {connection_id!r} is duplicated."))
            continue
        used_connection_ids.add(connection_id)
        connections.append(ConnectionDocument(
            connection_id=connection_id,
            output_node_id=output_node.node_id,
            output_port=output_port,
            input_node_id=input_node.node_id,
            input_port=input_port,
        ))

    globals_state = data.get("globals")
    practice = None
    if globals_state is not None:
        if not isinstance(globals_state, dict):
            diagnostics.append(DocumentDiagnostic(
                "invalid_practice", "$.globals", "globals must be an object."))
        else:
            try:
                practice = PracticeDocument(globals_state)
            except ValueError as exc:
                diagnostics.append(DocumentDiagnostic("invalid_practice", "$.globals", str(exc)))
    if diagnostics:
        return LegacyMigrationResult(None, diagnostics=tuple(diagnostics))
    return LegacyMigrationResult(
        graph=GraphDocument(
            schema_version=GRAPH_DOCUMENT_SCHEMA_VERSION,
            graph_id=graph_id,
            nodes=tuple(nodes),
            connections=tuple(connections),
        ),
        workspace=WorkspaceDocument(positions, panels),
        practice=practice,
    )


def _legacy_position(value, path, diagnostics) -> Position | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        diagnostics.append(DocumentDiagnostic(
            "invalid_position", path, "Position must contain two finite numbers."))
        return None
    if not all(isinstance(item, (int, float)) and not isinstance(item, bool)
               and math.isfinite(item) for item in value):
        diagnostics.append(DocumentDiagnostic(
            "invalid_position", path, "Position must contain two finite numbers."))
        return None
    return Position(float(value[0]), float(value[1]))


def _legacy_port_role(suffix) -> str | None:
    if not isinstance(suffix, str) or not suffix.startswith("_") or "::" not in suffix:
        return None
    role = suffix[1:].rsplit("::", 1)[0]
    return role or None


def _connection_number(connection_id) -> int | None:
    if not isinstance(connection_id, str) or not connection_id.startswith("connection_"):
        return None
    suffix = connection_id[len("connection_"):]
    return int(suffix) if suffix.isdigit() else None