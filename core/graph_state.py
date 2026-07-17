"""Mutable runtime state owned by one node graph."""

from dataclasses import dataclass, field
import threading
from typing import Any


@dataclass(frozen=True, slots=True)
class GraphConnection:
    connection_id: str
    output_pin: str
    input_pin: str


@dataclass(frozen=True, slots=True)
class GraphNodeSnapshot:
    node_id: str
    node_type: str
    title: str


@dataclass(frozen=True, slots=True)
class GraphIssueSnapshot:
    category: str
    node_id: str
    name: str


@dataclass(frozen=True, slots=True)
class GraphSnapshot:
    document_revision: int
    state_version: int
    nodes: tuple[GraphNodeSnapshot, ...]
    connection_count: int
    issues: tuple[GraphIssueSnapshot, ...]

    @property
    def revision(self) -> int:
        return self.document_revision


@dataclass(slots=True)
class GraphState:
    """Own graph data without owning topology or execution policy."""

    connections_by_id: dict[str, GraphConnection] = field(default_factory=dict)
    connection_id_by_input: dict[str, str] = field(default_factory=dict)
    nodes: dict[str, Any] = field(default_factory=dict)
    pin_owners: dict[str, str] = field(default_factory=dict)
    execution_order: list[str] = field(default_factory=list)
    stale_nodes: set[str] = field(default_factory=set)
    failed_nodes: set[str] = field(default_factory=set)
    cycle_nodes: set[str] = field(default_factory=set)
    heavy_descendants_cache: dict[str, frozenset[str]] = field(default_factory=dict)
    node_counter: int = 0
    connection_counter: int = 0
    _document_revision: int = field(default=0, init=False, repr=False)
    _state_version: int = field(default=-1, init=False, repr=False)
    _snapshot: GraphSnapshot = field(init=False, repr=False)
    _snapshot_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self):
        self.publish_snapshot()

    def next_node_tag(self, node_class_name: str) -> str:
        self.node_counter += 1
        return f"node_{node_class_name}_{self.node_counter}"

    def add_connection(
        self,
        output_pin: str,
        input_pin: str,
        *,
        connection_id: str | None = None,
    ) -> GraphConnection:
        if input_pin in self.connection_id_by_input:
            raise ValueError(f"input already connected: {input_pin!r}")
        if connection_id is None:
            self.connection_counter += 1
            connection_id = f"connection_{self.connection_counter}"
        elif connection_id in self.connections_by_id:
            raise ValueError(f"connection ID already exists: {connection_id!r}")
        else:
            prefix = "connection_"
            if connection_id.startswith(prefix):
                suffix = connection_id[len(prefix):]
                if suffix.isdigit():
                    self.connection_counter = max(self.connection_counter, int(suffix))

        connection = GraphConnection(connection_id, output_pin, input_pin)
        self.connections_by_id[connection_id] = connection
        self.connection_id_by_input[input_pin] = connection_id
        return connection

    def remove_connection(self, connection_id: str) -> GraphConnection | None:
        connection = self.connections_by_id.pop(connection_id, None)
        if connection is None:
            return None
        self.connection_id_by_input.pop(connection.input_pin, None)
        return connection

    def remove_connection_between(
        self, output_pin: str, input_pin: str
    ) -> GraphConnection | None:
        connection = self.connection_for_input(input_pin)
        if connection is None or connection.output_pin != output_pin:
            return None
        return self.remove_connection(connection.connection_id)

    def connection_for_input(self, input_pin: str) -> GraphConnection | None:
        connection_id = self.connection_id_by_input.get(input_pin)
        if connection_id is None:
            return None
        return self.connections_by_id.get(connection_id)

    def source_for_input(self, input_pin: str) -> str | None:
        connection = self.connection_for_input(input_pin)
        return connection.output_pin if connection is not None else None

    def iter_connections(self) -> tuple[GraphConnection, ...]:
        return tuple(self.connections_by_id.values())

    def clear_connections(self) -> None:
        self.connections_by_id.clear()
        self.connection_id_by_input.clear()

    def replace_runtime(self, candidate) -> None:
        nodes = dict(candidate.nodes)
        pin_owners = {}
        for node_id, node in nodes.items():
            for pin in (*node.input_spec, *node.output_spec):
                if pin in pin_owners:
                    raise ValueError(f"duplicate pin ID: {pin!r}")
                pin_owners[pin] = node_id

        connections_by_id = {}
        connection_id_by_input = {}
        connection_counter = 0
        for connection in candidate.connections:
            if connection.connection_id in connections_by_id:
                raise ValueError(
                    f"duplicate connection ID: {connection.connection_id!r}")
            if connection.output_pin not in pin_owners or connection.input_pin not in pin_owners:
                raise ValueError(
                    f"connection references an unknown pin: {connection.connection_id!r}")
            if connection.input_pin in connection_id_by_input:
                raise ValueError(f"input already connected: {connection.input_pin!r}")
            connections_by_id[connection.connection_id] = connection
            connection_id_by_input[connection.input_pin] = connection.connection_id
            connection_counter = max(
                connection_counter,
                self._monotonic_suffix(connection.connection_id, "connection_"),
            )

        node_counter = max(
            (self._monotonic_suffix(node_id.rsplit("_", 1)[-1], "") for node_id in nodes),
            default=0,
        )
        self.nodes = nodes
        self.pin_owners = pin_owners
        self.connections_by_id = connections_by_id
        self.connection_id_by_input = connection_id_by_input
        self.execution_order = list(candidate.execution_order)
        self.stale_nodes = set()
        self.failed_nodes = set()
        self.cycle_nodes = set()
        self.heavy_descendants_cache = {}
        self.node_counter = node_counter
        self.connection_counter = connection_counter

    @staticmethod
    def _monotonic_suffix(value: str, prefix: str) -> int:
        if not isinstance(value, str) or not value.startswith(prefix):
            return 0
        suffix = value[len(prefix):]
        return int(suffix) if suffix.isdigit() else 0

    def commit_document(self) -> GraphSnapshot:
        with self._snapshot_lock:
            self._document_revision += 1
            return self._build_snapshot()

    def publish_snapshot(self) -> GraphSnapshot:
        with self._snapshot_lock:
            return self._build_snapshot()

    def _build_snapshot(self) -> GraphSnapshot:
        nodes = tuple(sorted(
            (
                GraphNodeSnapshot(
                    node_id=node_id,
                    node_type=type(node).__name__,
                    title=str(getattr(node, "title", node_id)),
                )
                for node_id, node in tuple(self.nodes.items())
            ),
            key=lambda item: item.node_id,
        ))
        names = {node.node_id: node.title for node in nodes}
        issues = []
        seen = set()
        for category, tags in (
            ("wiring", tuple(self.cycle_nodes)),
            ("failed", tuple(self.failed_nodes)),
            ("stale", tuple(self.stale_nodes)),
        ):
            for node_id in sorted(tags):
                if node_id in seen:
                    continue
                seen.add(node_id)
                issues.append(GraphIssueSnapshot(
                    category=category,
                    node_id=node_id,
                    name=names.get(node_id, node_id),
                ))
        self._state_version += 1
        self._snapshot = GraphSnapshot(
            document_revision=self._document_revision,
            state_version=self._state_version,
            nodes=nodes,
            connection_count=len(self.connections_by_id),
            issues=tuple(issues),
        )
        return self._snapshot

    def snapshot(self) -> GraphSnapshot:
        with self._snapshot_lock:
            return self._snapshot
