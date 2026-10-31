"""Declarative node parameter and DearPyGui control metadata."""

from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable, TypeAlias


class StalePolicy(Enum):
    NONE = auto()
    DOWNSTREAM = auto()
    STRUCTURAL = auto()


@dataclass(frozen=True, slots=True)
class SliderSpec:
    label: str
    minimum: float
    maximum: float
    format: str = "%.2f"
    width: int = 120


@dataclass(frozen=True, slots=True)
class CheckboxSpec:
    label: str


@dataclass(frozen=True, slots=True)
class ComboSpec:
    label: str
    items: tuple[str, ...]
    width: int = 120

    def __post_init__(self):
        if not self.items:
            raise ValueError("ComboSpec.items must not be empty")


@dataclass(frozen=True, slots=True)
class InputFloatSpec:
    label: str
    step: float = 0.0
    step_fast: float = 0.0
    format: str = "%.2f"
    width: int = 120


@dataclass(frozen=True, slots=True)
class InputIntSpec:
    label: str
    step: int = 1
    step_fast: int = 10
    width: int = 120


ControlSpec: TypeAlias = SliderSpec | CheckboxSpec | ComboSpec | InputFloatSpec | InputIntSpec


ParameterValue: TypeAlias = bool | int | float | str


@dataclass(frozen=True, slots=True)
class ParameterSpec:
    """Backend parameter policy with no UI widget dependency."""

    name: str
    default: ParameterValue
    stale: StalePolicy = StalePolicy.DOWNSTREAM
    serialize: bool = True
    signature: bool = True
    signature_precision: int | None = None
    coerce: Callable[[object], ParameterValue] | None = None
    on_change: str | None = None

    def __post_init__(self):
        if not self.name or not self.name.isidentifier():
            raise ValueError("ParameterSpec.name must be a valid identifier")
        if self.signature_precision is not None and self.signature_precision < 0:
            raise ValueError("ParameterSpec.signature_precision must be non-negative")

    def normalize(self, value):
        if self.coerce is not None:
            return self.coerce(value)
        if isinstance(self.default, bool):
            return bool(value)
        if isinstance(self.default, int):
            return int(value)
        if isinstance(self.default, float):
            return float(value)
        if isinstance(self.default, str):
            return str(value)
        return value


@dataclass(frozen=True, slots=True)
class ParameterControlSpec:
    """Bind one backend parameter name to a UI control description."""

    parameter: str
    control: ControlSpec

    def __post_init__(self):
        if not self.parameter or not self.parameter.isidentifier():
            raise ValueError("ParameterControlSpec.parameter must be a valid identifier")