from dataclasses import asdict, dataclass, is_dataclass
import json

from tests.runtime_sim.actions import (
    Analyze,
    Connect,
    DeleteNode,
    Disconnect,
    LoadPreset,
    MoveNode,
    Redo,
    RenameNode,
    SavePreset,
    SetParameter,
    SpawnNode,
    Undo,
)


_ACTION_TYPES = {
    action_type.__name__: action_type
    for action_type in (
        Analyze,
        Connect,
        DeleteNode,
        Disconnect,
        LoadPreset,
        MoveNode,
        Redo,
        RenameNode,
        SavePreset,
        SetParameter,
        SpawnNode,
        Undo,
    )
}


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    actions: tuple[object, ...]

    def run(self, runtime):
        return runtime.perform(*self.actions)

    def to_dict(self) -> dict:
        records = []
        for action in self.actions:
            if not is_dataclass(action) or type(action).__name__ not in _ACTION_TYPES:
                raise TypeError(f"unsupported scenario action {type(action).__name__}")
            records.append({
                "type": type(action).__name__,
                "arguments": asdict(action),
            })
        return {"name": self.name, "actions": records}

    def dumps(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_dict(cls, data: dict) -> "Scenario":
        if not isinstance(data, dict) or not isinstance(data.get("name"), str):
            raise ValueError("scenario must contain a string name")
        records = data.get("actions")
        if not isinstance(records, list):
            raise ValueError("scenario actions must be a list")
        actions = []
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                raise ValueError(f"action {index} must be an object")
            action_type = _ACTION_TYPES.get(record.get("type"))
            arguments = record.get("arguments", {})
            if action_type is None or not isinstance(arguments, dict):
                raise ValueError(f"action {index} has an invalid type or arguments")
            try:
                actions.append(action_type(**arguments))
            except TypeError as exc:
                raise ValueError(f"action {index} arguments are invalid: {exc}") from exc
        return cls(data["name"], tuple(actions))

    @classmethod
    def loads(cls, payload: str) -> "Scenario":
        try:
            data = json.loads(payload)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"scenario JSON is invalid: {exc}") from exc
        return cls.from_dict(data)