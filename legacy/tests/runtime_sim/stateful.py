from copy import deepcopy
from dataclasses import dataclass, field
import random

from tests.runtime_sim.actions import (
    Connect,
    DeleteNode,
    Disconnect,
    Redo,
    RenameNode,
    SetParameter,
    SpawnNode,
    Undo,
)
from tests.runtime_sim.scenario import Scenario


@dataclass
class ModelNode:
    node_type: str
    name: str = ""
    parameters: dict[str, object] = field(default_factory=dict)


@dataclass
class ModelState:
    nodes: dict[str, ModelNode] = field(default_factory=dict)
    connections: set[tuple[str, str]] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class GeneratedStep:
    action: object
    expected: ModelState


@dataclass(frozen=True, slots=True)
class GeneratedPlan:
    seed: int
    steps: tuple[GeneratedStep, ...]

    @property
    def scenario(self) -> Scenario:
        return Scenario(
            f"seeded-{self.seed}",
            tuple(step.action for step in self.steps),
        )

    def run(self, runtime) -> None:
        for index, step in enumerate(self.steps):
            try:
                runtime.perform(step.action)
                assert_runtime_matches_model(runtime, step.expected)
            except Exception as exc:
                raise AssertionError(
                    f"seeded scenario failed at step {index} with seed {self.seed}\n"
                    f"Scenario JSON: {self.scenario.dumps()}\n"
                    f"Trace:\n{runtime.trace.format_tail(30)}"
                ) from exc


class SeededScenarioGenerator:
    def __init__(self, seed: int) -> None:
        self.seed = int(seed)
        self._random = random.Random(self.seed)
        self._state = ModelState()
        self._history = [deepcopy(self._state)]
        self._cursor = 0
        self._next_alias = 1
        self._rename_counter = 1

    def generate(self, steps: int = 30) -> GeneratedPlan:
        if steps <= 0:
            raise ValueError("steps must be positive")
        generated = []
        for _ in range(steps):
            action, records_history = self._next_action()
            if records_history:
                del self._history[self._cursor + 1:]
                self._history.append(deepcopy(self._state))
                self._cursor = len(self._history) - 1
            generated.append(GeneratedStep(action, deepcopy(self._state)))
        return GeneratedPlan(self.seed, tuple(generated))

    def _next_action(self):
        choices = [self._spawn]
        if self._state.nodes:
            choices.extend((self._rename,))
        if self._gain_aliases():
            choices.append(self._set_parameter)
        if self._connectable_pairs():
            choices.append(self._connect)
        if self._state.connections:
            choices.append(self._disconnect)
        if len(self._state.nodes) > 1:
            choices.append(self._delete)
        if self._cursor > 0:
            choices.append(self._undo)
        if self._cursor < len(self._history) - 1:
            choices.append(self._redo)
        return self._random.choice(choices)()

    def _spawn(self):
        alias = f"node_{self._next_alias}"
        self._next_alias += 1
        node_type = self._random.choice(("GainNode", "EqNode"))
        self._state.nodes[alias] = ModelNode(node_type)
        return SpawnNode(node_type, as_name=alias), True

    def _rename(self):
        alias = self._random.choice(sorted(self._state.nodes))
        name = f"Generated {self._rename_counter}"
        self._rename_counter += 1
        self._state.nodes[alias].name = name
        return RenameNode(alias, name), True

    def _set_parameter(self):
        alias = self._random.choice(self._gain_aliases())
        value = self._random.choice((-12.0, -9.0, -6.0, -3.0, 0.0, 3.0))
        self._state.nodes[alias].parameters["gain_db"] = value
        return SetParameter(alias, "gain_db", value), True

    def _connect(self):
        source, target = self._random.choice(self._connectable_pairs())
        connection = (f"{source}.out_audio", f"{target}.in_audio")
        self._state.connections.add(connection)
        return Connect(*connection), True

    def _disconnect(self):
        connection = self._random.choice(sorted(self._state.connections))
        self._state.connections.remove(connection)
        return Disconnect(*connection), True

    def _delete(self):
        alias = self._random.choice(sorted(self._state.nodes))
        del self._state.nodes[alias]
        self._state.connections = {
            connection for connection in self._state.connections
            if not connection[0].startswith(f"{alias}.")
            and not connection[1].startswith(f"{alias}.")
        }
        return DeleteNode(alias), True

    def _undo(self):
        self._cursor -= 1
        self._state = deepcopy(self._history[self._cursor])
        return Undo(), False

    def _redo(self):
        self._cursor += 1
        self._state = deepcopy(self._history[self._cursor])
        return Redo(), False

    def _gain_aliases(self) -> list[str]:
        return sorted(
            alias for alias, node in self._state.nodes.items()
            if node.node_type == "GainNode")

    def _connectable_pairs(self) -> list[tuple[str, str]]:
        aliases = sorted(self._state.nodes, key=self._alias_number)
        occupied_inputs = {item[1].split(".", 1)[0] for item in self._state.connections}
        return [
            (source, target)
            for source in aliases
            for target in aliases
            if self._alias_number(source) < self._alias_number(target)
            and target not in occupied_inputs
        ]

    @staticmethod
    def _alias_number(alias: str) -> int:
        return int(alias.rsplit("_", 1)[1])


def assert_runtime_matches_model(runtime, model: ModelState) -> None:
    active_ids = {
        alias: runtime.resolve_node(alias)
        for alias in model.nodes
    }
    assert set(runtime.state.nodes) == set(active_ids.values())
    for alias, expected in model.nodes.items():
        node = runtime.state.nodes[active_ids[alias]]
        assert type(node).__name__ == expected.node_type
        assert node.custom_name == expected.name
        for name, value in expected.parameters.items():
            assert getattr(node, name) == value
    actual_connections = {
        (
            _pin_alias(runtime, connection.output_pin),
            _pin_alias(runtime, connection.input_pin),
        )
        for connection in runtime.state.iter_connections()
    }
    assert actual_connections == model.connections


def _pin_alias(runtime, pin: str) -> str:
    node_id = runtime.state.pin_owners[pin]
    alias = next(
        alias for alias, bound_id in runtime.aliases.items()
        if bound_id == node_id)
    role = pin[len(node_id) + 1:].rsplit("::", 1)[0]
    return f"{alias}.{role}"