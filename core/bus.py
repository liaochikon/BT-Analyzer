from dataclasses import dataclass, field
from enum import Enum, auto
import queue
import threading
import time
import uuid


def new_message_id() -> str:
    return str(uuid.uuid4())


@dataclass(frozen=True, slots=True)
class Message:
    source: str
    destination: str
    topic: str
    payload: object = None
    id: str = field(default_factory=new_message_id)
    created_at_ns: int = field(default_factory=time.time_ns)


class BusMessageResult(Enum):
    ENQUEUED = auto()
    NO_DESTINATION = auto()
    MAILBOX_FULL = auto()

    @property
    def ok(self) -> bool:
        return self is BusMessageResult.ENQUEUED

    def describe(self, destination: str) -> str:
        return {
            BusMessageResult.ENQUEUED: "enqueued",
            BusMessageResult.NO_DESTINATION: f"no such recipient: {destination!r}",
            BusMessageResult.MAILBOX_FULL: f"mailbox full: {destination!r}",
        }[self]


class Mailbox:
    def __init__(self, name: str, maxsize: int) -> None:
        if maxsize <= 0:
            raise ValueError("maxsize must be positive")
        self.name = name
        self._queue: queue.Queue[Message] = queue.Queue(maxsize=maxsize)

    def put(self, msg: Message) -> None:
        self._queue.put_nowait(msg)

    def put_wait(self, msg: Message, timeout: float) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._queue.put(msg, timeout=timeout)

    def get(self, timeout: float) -> Message:
        """Block for up to `timeout` seconds waiting for a message.

        A timeout is mandatory: blocking forever would let a reader hang
        permanently once its mailbox is unregistered (nothing would ever wake
        it). Raises queue.Empty if no message arrives in time; use get_nowait()
        for a non-blocking poll.
        """
        if timeout <= 0:
            raise ValueError("timeout must be positive; use get_nowait() to poll")
        return self._queue.get(timeout=timeout)

    def get_nowait(self) -> Message:
        return self._queue.get_nowait()

    def __len__(self) -> int:
        return self._queue.qsize()


class Bus:
    def __init__(self) -> None:
        self._mailboxes: dict[str, Mailbox] = {}
        self._lock = threading.RLock()

    def register(self, name: str, maxsize: int = 256) -> Mailbox:
        with self._lock:
            if name in self._mailboxes:
                raise ValueError(f"mailbox already registered: {name!r}")
            box = Mailbox(name, maxsize=maxsize)
            self._mailboxes[name] = box
            return box

    def unregister(self, name: str) -> None:
        with self._lock:
            self._mailboxes.pop(name, None)

    def publish(self, msg: Message) -> BusMessageResult:
        """Enqueue a message without waiting for the recipient to process it."""
        with self._lock:
            box = self._mailboxes.get(msg.destination)
            if box is None:
                return BusMessageResult.NO_DESTINATION
            try:
                box.put(msg)
            except queue.Full:
                return BusMessageResult.MAILBOX_FULL
            return BusMessageResult.ENQUEUED

    def publish_wait(self, msg: Message, *, timeout: float) -> BusMessageResult:
        """Enqueue with bounded backpressure for messages that must not be casually dropped."""
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        with self._lock:
            box = self._mailboxes.get(msg.destination)
            if box is None:
                return BusMessageResult.NO_DESTINATION
            try:
                box.put_wait(msg, timeout)
            except queue.Full:
                return BusMessageResult.MAILBOX_FULL
            return BusMessageResult.ENQUEUED