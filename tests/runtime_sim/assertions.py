from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GraphHasNodes:
    count: int

    def check(self, runtime) -> None:
        actual = len(runtime.state.nodes)
        assert actual == self.count, f"expected {self.count} nodes, found {actual}"


@dataclass(frozen=True, slots=True)
class NodeParameter:
    node: str
    name: str
    value: object

    def check(self, runtime) -> None:
        node = runtime.node(self.node)
        actual = getattr(node, self.name)
        assert actual == self.value, (
            f"expected {self.node}.{self.name}={self.value!r}, found {actual!r}")


@dataclass(frozen=True, slots=True)
class ConnectionExists:
    output: str
    input: str

    def check(self, runtime) -> None:
        output_pin = runtime.resolve_pin(self.output, output=True)
        input_pin = runtime.resolve_pin(self.input, output=False)
        assert any(
            connection.output_pin == output_pin and connection.input_pin == input_pin
            for connection in runtime.state.iter_connections()
        ), f"connection {self.output} -> {self.input} does not exist"


@dataclass(frozen=True, slots=True)
class ConnectionAbsent:
    output: str
    input: str

    def check(self, runtime) -> None:
        output_pin = runtime.resolve_pin(self.output, output=True)
        input_pin = runtime.resolve_pin(self.input, output=False)
        assert not any(
            connection.output_pin == output_pin and connection.input_pin == input_pin
            for connection in runtime.state.iter_connections()
        ), f"connection {self.output} -> {self.input} still exists"


@dataclass(frozen=True, slots=True)
class NodePosition:
    node: str
    x: float
    y: float

    def check(self, runtime) -> None:
        actual = runtime.node_position(self.node)
        expected = (self.x, self.y)
        assert actual == expected, (
            f"expected {self.node} at {expected}, found {actual}")