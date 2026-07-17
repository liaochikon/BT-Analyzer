import queue
import threading

import pytest

from core.bus import Bus, BusMessageResult, Message


def make_message(destination: str, payload: object = None) -> Message:
    return Message(
        source="test.sender",
        destination=destination,
        topic="test.message",
        payload=payload,
    )


def test_publish_enqueues_messages_in_fifo_order() -> None:
    bus = Bus()
    mailbox = bus.register("test.receiver", maxsize=2)
    first = make_message("test.receiver", payload=1)
    second = make_message("test.receiver", payload=2)

    assert bus.publish(first) is BusMessageResult.ENQUEUED
    assert bus.publish(second) is BusMessageResult.ENQUEUED
    assert mailbox.get_nowait() is first
    assert mailbox.get_nowait() is second


def test_publish_reports_missing_and_full_mailboxes_without_bounce() -> None:
    bus = Bus()
    mailbox = bus.register("test.receiver", maxsize=1)
    accepted = make_message("test.receiver")

    assert bus.publish(accepted) is BusMessageResult.ENQUEUED
    assert bus.publish(make_message("test.receiver")) is BusMessageResult.MAILBOX_FULL
    assert bus.publish(make_message("test.missing")) is BusMessageResult.NO_DESTINATION
    assert len(mailbox) == 1
    assert mailbox.get_nowait() is accepted


def test_register_rejects_duplicate_or_unbounded_mailbox() -> None:
    bus = Bus()
    original = bus.register("test.receiver", maxsize=1)

    with pytest.raises(ValueError, match="already registered"):
        bus.register("test.receiver", maxsize=2)
    with pytest.raises(ValueError, match="maxsize must be positive"):
        bus.register("test.invalid", maxsize=0)

    assert bus.publish(make_message("test.receiver")) is BusMessageResult.ENQUEUED
    assert original.get_nowait().destination == "test.receiver"


def test_unregister_stops_future_enqueues() -> None:
    bus = Bus()
    mailbox = bus.register("test.receiver", maxsize=1)

    bus.unregister("test.receiver")

    assert bus.publish(make_message("test.receiver")) is BusMessageResult.NO_DESTINATION
    with pytest.raises(queue.Empty):
        mailbox.get_nowait()


def test_mailbox_requires_positive_timeout() -> None:
    mailbox = Bus().register("test.receiver")

    with pytest.raises(ValueError, match="timeout must be positive"):
        mailbox.get(timeout=0)


def test_publish_wait_applies_bounded_backpressure() -> None:
    bus = Bus()
    mailbox = bus.register("test.receiver", maxsize=1)
    first = make_message("test.receiver", payload=1)
    second = make_message("test.receiver", payload=2)
    received = []

    assert bus.publish(first) is BusMessageResult.ENQUEUED

    def consume() -> None:
        received.append(mailbox.get(timeout=0.5))

    consumer = threading.Thread(target=consume)
    consumer.start()
    assert bus.publish_wait(second, timeout=0.5) is BusMessageResult.ENQUEUED
    consumer.join(timeout=1.0)

    assert received == [first]
    assert mailbox.get_nowait() is second


def test_publish_wait_times_out_when_mailbox_stays_full() -> None:
    bus = Bus()
    bus.register("test.receiver", maxsize=1)
    assert bus.publish(make_message("test.receiver")) is BusMessageResult.ENQUEUED
    assert bus.publish_wait(
        make_message("test.receiver"), timeout=0.01,
    ) is BusMessageResult.MAILBOX_FULL


def test_concurrent_producers_do_not_lose_enqueued_messages() -> None:
    bus = Bus()
    mailbox = bus.register("test.receiver", maxsize=400)
    messages = [make_message("test.receiver", payload=index) for index in range(400)]
    results: list[BusMessageResult] = []

    def publish_batch(batch: list[Message]) -> None:
        results.extend(bus.publish(message) for message in batch)

    threads = [
        threading.Thread(target=publish_batch, args=(messages[start:start + 100],))
        for start in range(0, 400, 100)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    received_ids = {mailbox.get_nowait().id for _ in range(400)}
    assert results == [BusMessageResult.ENQUEUED] * 400
    assert received_ids == {message.id for message in messages}


def test_publish_and_unregister_have_a_linearizable_result() -> None:
    for _ in range(50):
        bus = Bus()
        mailbox = bus.register("test.receiver", maxsize=1)
        message = make_message("test.receiver")
        start = threading.Barrier(3)
        results: list[BusMessageResult] = []

        def publish() -> None:
            start.wait()
            results.append(bus.publish(message))

        def unregister() -> None:
            start.wait()
            bus.unregister("test.receiver")

        publish_thread = threading.Thread(target=publish)
        unregister_thread = threading.Thread(target=unregister)
        publish_thread.start()
        unregister_thread.start()
        start.wait()
        publish_thread.join()
        unregister_thread.join()

        if results == [BusMessageResult.ENQUEUED]:
            assert mailbox.get_nowait() is message
        else:
            assert results == [BusMessageResult.NO_DESTINATION]
            with pytest.raises(queue.Empty):
                mailbox.get_nowait()