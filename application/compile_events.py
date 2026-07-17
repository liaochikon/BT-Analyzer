import logging
from dataclasses import dataclass
from enum import StrEnum

from core.bus import Bus, BusMessageResult, Message
from core.graph_contracts import CompileCompleted, GraphEndpoint, GraphTopic


logger = logging.getLogger(__name__)


class CompileEndpoint(StrEnum):
    SERVICE = "domain.compile"
    NODE_EDITOR = "ui.node_editor"
    ANALYZER_STATUS = "ui.analyzer_status"


class CompileTopic(StrEnum):
    BUSY_CHANGED = "compile.busy.changed"


@dataclass(frozen=True, slots=True)
class CompileBusyChanged:
    busy: bool


class CompileEventPublisher:
    """Translate coordinator notifications into explicit one-to-one UI messages."""

    _DESTINATIONS = (
        CompileEndpoint.NODE_EDITOR,
        CompileEndpoint.ANALYZER_STATUS,
    )
    _PUBLISH_TIMEOUT = 0.25

    def __init__(self, bus: Bus) -> None:
        self._bus = bus

    def __call__(self, busy: bool) -> None:
        payload = CompileBusyChanged(busy=busy)
        for destination in self._DESTINATIONS:
            result = self._bus.publish_wait(Message(
                source=CompileEndpoint.SERVICE,
                destination=destination,
                topic=CompileTopic.BUSY_CHANGED,
                payload=payload,
            ), timeout=self._PUBLISH_TIMEOUT)
            if result is not BusMessageResult.ENQUEUED:
                logger.warning(
                    "Compile event was not enqueued for %s: %s",
                    destination,
                    result.describe(destination),
                )


class CompileCompletionPublisher:
    """Return worker completion to the graph owner for revision publication."""

    def __init__(self, bus: Bus, completion_factory=None, result_provider=None) -> None:
        self._bus = bus
        self._completion_factory = completion_factory
        self._result_provider = result_provider or (lambda: None)

    def __call__(self, busy: bool) -> None:
        if busy:
            return
        payload = (
            CompileCompleted()
            if self._completion_factory is None
            else self._completion_factory(self._result_provider())
        )
        result = self._bus.publish_wait(Message(
            source=CompileEndpoint.SERVICE,
            destination=GraphEndpoint.SERVICE,
            topic=GraphTopic.COMMAND,
            payload=payload,
        ), timeout=0.25)
        if result is not BusMessageResult.ENQUEUED:
            logger.warning(
                "Compile completion was not enqueued: %s",
                result.describe(GraphEndpoint.SERVICE),
            )


class CompileLifecyclePublisher:
    """Publish one coordinator lifecycle transition to all explicit destinations."""

    def __init__(self, bus: Bus, completion_factory=None, result_provider=None) -> None:
        self._busy = CompileEventPublisher(bus)
        self._completion = CompileCompletionPublisher(
            bus, completion_factory, result_provider)

    def __call__(self, busy: bool) -> None:
        self._busy(busy)
        self._completion(busy)