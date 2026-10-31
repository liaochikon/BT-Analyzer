import threading

from application.compile_events import (
    CompileCompletionPublisher,
    CompileBusyChanged,
    CompileEndpoint,
    CompileEventPublisher,
    CompileTopic,
)
from core.graph_contracts import CompileCompleted, GraphEndpoint
from application.context import ApplicationContext
from core.compile_coordinator import CompileCoordinator


def test_compile_events_execute_only_when_gui_mailboxes_are_drained() -> None:
    context = ApplicationContext()
    editor_events = []
    analyzer_events = []
    context.register_gui_endpoint(CompileEndpoint.NODE_EDITOR, editor_events.append)
    context.register_gui_endpoint(CompileEndpoint.ANALYZER_STATUS, analyzer_events.append)
    publish = CompileEventPublisher(context.bus)

    publish(True)

    assert editor_events == []
    assert analyzer_events == []
    assert context.drain_gui() == 2
    for event in (editor_events[0], analyzer_events[0]):
        assert event.topic == CompileTopic.BUSY_CHANGED
        assert event.payload == CompileBusyChanged(busy=True)


def test_gui_drain_is_bounded_and_isolates_handler_failures() -> None:
    context = ApplicationContext()
    handled = []

    def fail(_message) -> None:
        raise RuntimeError("bad projection")

    context.register_gui_endpoint(CompileEndpoint.NODE_EDITOR, fail, maxsize=2)
    context.register_gui_endpoint(CompileEndpoint.ANALYZER_STATUS, handled.append, maxsize=2)
    publish = CompileEventPublisher(context.bus)
    publish(True)
    publish(False)

    assert context.drain_gui(limit_per_endpoint=1) == 2
    assert len(handled) == 1
    assert context.drain_gui(limit_per_endpoint=1) == 2
    assert [message.payload.busy for message in handled] == [True, False]


def test_close_unregisters_endpoints_and_is_idempotent() -> None:
    context = ApplicationContext()
    context.register_gui_endpoint(CompileEndpoint.NODE_EDITOR, lambda _message: None)

    context.close()
    context.close()

    publish = CompileEventPublisher(context.bus)
    publish(True)
    assert context.drain_gui() == 0


def test_close_unregisters_non_gui_endpoints() -> None:
    from core.bus import BusMessageResult, Message

    context = ApplicationContext()
    context.register_endpoint("domain.test", maxsize=1)
    context.close()

    result = context.bus.publish(Message(
        source="test.sender",
        destination="domain.test",
        topic="test.command",
    ))
    assert result is BusMessageResult.NO_DESTINATION


def test_mailbox_saturation_recovers_after_bounded_drain() -> None:
    from core.bus import BusMessageResult, Message

    context = ApplicationContext()
    handled = []
    context.register_gui_endpoint("ui.stress", handled.append, maxsize=2)

    def publish(index):
        return context.bus.publish(Message(
            source="test.sender", destination="ui.stress", topic="stress", payload=index))

    assert [publish(index) for index in range(3)] == [
        BusMessageResult.ENQUEUED,
        BusMessageResult.ENQUEUED,
        BusMessageResult.MAILBOX_FULL,
    ]
    assert context.drain_gui(limit_per_endpoint=1) == 1
    assert publish(3) is BusMessageResult.ENQUEUED
    assert context.drain_gui() == 2
    assert [message.payload for message in handled] == [0, 1, 3]
    context.close()


def test_repeated_context_startup_shutdown_releases_endpoints() -> None:
    from core.bus import Bus, BusMessageResult, Message

    bus = Bus()
    for _ in range(20):
        context = ApplicationContext(bus)
        context.register_gui_endpoint("ui.restart", lambda _message: None)
        context.close()
        assert bus.publish(Message(
            source="test.sender", destination="ui.restart", topic="restart",
        )) is BusMessageResult.NO_DESTINATION


def test_drain_gui_all_consumes_more_than_one_frame_budget() -> None:
    from core.bus import Message

    context = ApplicationContext()
    handled = []
    context.register_gui_endpoint("ui.shutdown", handled.append, maxsize=80)
    for index in range(70):
        assert context.bus.publish(Message(
            source="test.sender",
            destination="ui.shutdown",
            topic="shutdown",
            payload=index,
        )).ok

    assert context.drain_gui_all() == 70
    assert [message.payload for message in handled] == list(range(70))
    assert context.drain_gui_all() == 0
    context.close()


def test_compile_coordinator_lifecycle_reaches_gui_through_mailboxes() -> None:
    context = ApplicationContext()
    coordinator = CompileCoordinator()
    editor_states = []
    analyzer_states = []
    context.register_gui_endpoint(
        CompileEndpoint.NODE_EDITOR,
        lambda message: editor_states.append(message.payload.busy),
    )
    context.register_gui_endpoint(
        CompileEndpoint.ANALYZER_STATUS,
        lambda message: analyzer_states.append(message.payload.busy),
    )
    coordinator.configure_lifecycle(CompileEventPublisher(context.bus))
    finished = threading.Event()

    def worker() -> None:
        with coordinator.session():
            pass
        finished.set()

    assert coordinator.dispatch(worker)
    assert finished.wait(timeout=1.0)
    assert editor_states == []
    assert analyzer_states == []

    assert context.drain_gui() == 4
    assert editor_states == [True, False]
    assert analyzer_states == [True, False]

    coordinator.configure_lifecycle(None)
    context.close()


def test_compile_completion_returns_to_graph_service_only_when_idle() -> None:
    context = ApplicationContext()
    mailbox = context.register_endpoint(GraphEndpoint.SERVICE)
    publish = CompileCompletionPublisher(context.bus)

    publish(True)
    assert len(mailbox) == 0

    publish(False)

    assert mailbox.get_nowait().payload == CompileCompleted()
    context.close()