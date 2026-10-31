from dataclasses import dataclass
from enum import StrEnum


class TransportEndpoint(StrEnum):
    SERVICE = "domain.transport"


class TransportTopic(StrEnum):
    COMMAND = "transport.command"


@dataclass(frozen=True, slots=True)
class Play:
    pass


@dataclass(frozen=True, slots=True)
class Pause:
    pass


@dataclass(frozen=True, slots=True)
class Stop:
    pass


@dataclass(frozen=True, slots=True)
class TogglePlayback:
    pass


@dataclass(frozen=True, slots=True)
class Seek:
    sample_index: int


@dataclass(frozen=True, slots=True)
class SetRate:
    rate: float


@dataclass(frozen=True, slots=True)
class SetLoopPoint:
    point: str
    fraction: float

    def __post_init__(self) -> None:
        if self.point not in {"A", "B"}:
            raise ValueError("point must be 'A' or 'B'")


@dataclass(frozen=True, slots=True)
class SetLoopRange:
    start: float
    end: float


@dataclass(frozen=True, slots=True)
class ClearLoop:
    pass


@dataclass(frozen=True, slots=True)
class SetLoopEnabled:
    enabled: bool


@dataclass(frozen=True, slots=True)
class SetSnapEnabled:
    enabled: bool


@dataclass(frozen=True, slots=True)
class SetBeatTrim:
    bpm: float
    offset: float


@dataclass(frozen=True, slots=True)
class SetMasterGain:
    gain: float


@dataclass(frozen=True, slots=True)
class SetMetronomeGain:
    gain: float


@dataclass(frozen=True, slots=True)
class ConfigureMetronome:
    enabled: bool
    bpm: float
    offset: float


@dataclass(frozen=True, slots=True)
class Resync:
    pass


@dataclass(frozen=True, slots=True)
class CancelCountIn:
    pass


@dataclass(frozen=True, slots=True)
class SetLength:
    sample_count: int


@dataclass(frozen=True, slots=True)
class SetSource:
    render: object
    sample_count: int


@dataclass(frozen=True, slots=True)
class StartCountIn:
    audio: object


TransportControlCommand = (
    Play
    | Pause
    | Stop
    | TogglePlayback
    | Seek
    | SetRate
    | SetLoopPoint
    | SetLoopRange
    | ClearLoop
    | SetLoopEnabled
    | SetSnapEnabled
    | SetBeatTrim
    | SetMasterGain
    | SetMetronomeGain
    | ConfigureMetronome
    | Resync
    | CancelCountIn
    | SetLength
    | SetSource
    | StartCountIn
)