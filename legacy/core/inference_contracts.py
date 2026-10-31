"""Shared immutable failure contracts for optional AI inference backends."""

from dataclasses import dataclass
from enum import Enum, auto


class InferenceErrorCode(Enum):
    DEPENDENCY_UNAVAILABLE = auto()
    MODEL_LOAD_FAILED = auto()
    EXECUTION_FAILED = auto()
    OUTPUT_INVALID = auto()


@dataclass(frozen=True, slots=True)
class InferenceError:
    code: InferenceErrorCode
    message: str
    detail: str = ""