from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.graph_contracts import (
    CompileKind,
    EditPhase,
    ConnectionAdded,
    ConnectionRemoved,
    GraphReplaced,
    MutationRejected,
    NodeDeleted,
    NodeRenamed,
    NodeStateAction,
    NodeStateChanged,
    NodeSpawned,
    ParameterChanged,
    PracticeSettingsChanged,
    PresetSaveFailed,
    ProjectSaveFailed,
    ProjectSaved,
)

if TYPE_CHECKING:
    from tests.runtime_sim.harness import RuntimeHarness


class UserAction:
    expected_events: tuple[type, ...] = ()
    failure_events: tuple[type, ...] = (MutationRejected,)

    def dispatch(self, runtime: "RuntimeHarness") -> None:
        raise NotImplementedError

    def completed(self, runtime: "RuntimeHarness", event: object) -> None:
        pass

    def await_completion(self, runtime: "RuntimeHarness"):
        if not self.expected_events:
            return None
        return runtime.pump_for(self.expected_events)


@dataclass(frozen=True, slots=True)
class SpawnNode(UserAction):
    node_type: str
    as_name: str
    expected_events = (NodeSpawned, MutationRejected)

    def dispatch(self, runtime: "RuntimeHarness") -> None:
        runtime.publisher.spawn_callback(
            None, None, runtime.resolve_node_type(self.node_type))

    def completed(self, runtime: "RuntimeHarness", event: object) -> None:
        if isinstance(event, NodeSpawned):
            runtime.bind_alias(self.as_name, event.node_tag)


@dataclass(frozen=True, slots=True)
class Connect(UserAction):
    output: str
    input: str
    expected_events = (ConnectionAdded, MutationRejected)

    def dispatch(self, runtime: "RuntimeHarness") -> None:
        runtime.publisher.connect_callback(
            "node_editor",
            (runtime.resolve_pin(self.output, output=True),
             runtime.resolve_pin(self.input, output=False)),
        )


@dataclass(frozen=True, slots=True)
class Disconnect(UserAction):
    output: str
    input: str
    expected_events = (ConnectionRemoved, MutationRejected)

    def dispatch(self, runtime: "RuntimeHarness") -> None:
        runtime.publisher.disconnect_connection(
            runtime.resolve_connection(self.output, self.input).connection_id)


@dataclass(frozen=True, slots=True)
class SetParameter(UserAction):
    node: str
    name: str
    value: object
    expected_events = (ParameterChanged, MutationRejected)

    def dispatch(self, runtime: "RuntimeHarness") -> None:
        runtime.publisher.set_parameter(
            runtime.resolve_node(self.node), self.name, self.value)


@dataclass(frozen=True, slots=True)
class SetCustomState(UserAction):
    node: str
    field: str
    value: object = None
    index: int | None = None
    action: NodeStateAction = NodeStateAction.SET
    edit_phase: EditPhase = EditPhase.COMMIT
    expected_events = (NodeStateChanged, MutationRejected)

    def dispatch(self, runtime: "RuntimeHarness") -> None:
        runtime.publisher.set_node_state(
            runtime.resolve_node(self.node),
            self.field,
            self.value,
            index=self.index,
            action=self.action,
            edit_phase=self.edit_phase,
        )


@dataclass(frozen=True, slots=True)
class RenameNode(UserAction):
    node: str
    name: str
    expected_events = (NodeRenamed, MutationRejected)

    def dispatch(self, runtime: "RuntimeHarness") -> None:
        runtime.publisher.rename_node(runtime.resolve_node(self.node), self.name)


@dataclass(frozen=True, slots=True)
class DeleteNode(UserAction):
    node: str
    expected_events = (NodeDeleted, MutationRejected)

    def dispatch(self, runtime: "RuntimeHarness") -> None:
        runtime.publisher.delete_node(runtime.resolve_node(self.node))


@dataclass(frozen=True, slots=True)
class Undo(UserAction):
    expected_events = (GraphReplaced, MutationRejected)

    def dispatch(self, runtime: "RuntimeHarness") -> None:
        runtime.publisher.undo()


@dataclass(frozen=True, slots=True)
class Redo(UserAction):
    expected_events = (GraphReplaced, MutationRejected)

    def dispatch(self, runtime: "RuntimeHarness") -> None:
        runtime.publisher.redo()


@dataclass(frozen=True, slots=True)
class SavePreset(UserAction):
    name: str
    include_params: bool = True
    as_default: bool = False
    expected_events = (
        ProjectSaved, ProjectSaveFailed, PresetSaveFailed, MutationRejected)
    failure_events = (ProjectSaveFailed, PresetSaveFailed, MutationRejected)

    def dispatch(self, runtime: "RuntimeHarness") -> None:
        runtime.publisher.save_preset(
            self.name, self.include_params, self.as_default)


@dataclass(frozen=True, slots=True)
class LoadPreset(UserAction):
    name: str
    expected_events = (GraphReplaced, MutationRejected)

    def dispatch(self, runtime: "RuntimeHarness") -> None:
        runtime.publisher.load_preset(self.name)

    def await_completion(self, runtime: "RuntimeHarness"):
        event = runtime.pump_for(self.expected_events)
        if (isinstance(event, GraphReplaced)
                and runtime.preset_has_practice(self.name)):
            return runtime.pump_for((PracticeSettingsChanged, MutationRejected))
        return event


@dataclass(frozen=True, slots=True)
class Analyze(UserAction):
    def dispatch(self, runtime: "RuntimeHarness") -> None:
        runtime.publisher.request_compile(CompileKind.ANALYZE)

    def await_completion(self, runtime: "RuntimeHarness"):
        return runtime.wait_for_compile_request(CompileKind.ANALYZE)


@dataclass(frozen=True, slots=True)
class MoveNode(UserAction):
    node: str
    x: float
    y: float

    def dispatch(self, runtime: "RuntimeHarness") -> None:
        runtime.move_node(self.node, self.x, self.y)