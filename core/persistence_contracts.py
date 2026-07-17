from dataclasses import dataclass
from enum import Enum, auto


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    graph: dict
    globals: dict | None = None


@dataclass(frozen=True, slots=True)
class RestoreToken:
    snapshot: WorkspaceSnapshot


class ApplyStatus(Enum):
    APPLIED = auto()
    REJECTED = auto()
    ROLLED_BACK = auto()
    DEGRADED = auto()


@dataclass(frozen=True, slots=True)
class ApplyResult:
    status: ApplyStatus
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status is ApplyStatus.APPLIED
