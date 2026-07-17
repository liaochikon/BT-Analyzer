import logging
import queue
from collections.abc import Callable

from core.bus import Bus, Mailbox, Message


logger = logging.getLogger(__name__)


class ApplicationContext:
    """Own application messaging resources and GUI-thread mailbox drains."""

    def __init__(self, bus: Bus | None = None) -> None:
        self.bus = bus or Bus()
        self._gui_endpoints: dict[str, tuple[Mailbox, Callable[[Message], None]]] = {}
        self._registered_endpoints: set[str] = set()
        self._closed = False

    def register_endpoint(self, name: str, *, maxsize: int = 256) -> Mailbox:
        if self._closed:
            raise RuntimeError("application context is closed")
        mailbox = self.bus.register(name, maxsize=maxsize)
        self._registered_endpoints.add(name)
        return mailbox

    def register_gui_endpoint(
        self,
        name: str,
        handler: Callable[[Message], None],
        *,
        maxsize: int = 64,
    ) -> None:
        if self._closed:
            raise RuntimeError("application context is closed")
        mailbox = self.register_endpoint(name, maxsize=maxsize)
        self._gui_endpoints[name] = (mailbox, handler)

    def drain_gui(self, *, limit_per_endpoint: int = 32) -> int:
        if limit_per_endpoint <= 0:
            raise ValueError("limit_per_endpoint must be positive")
        processed = 0
        for name, (mailbox, handler) in tuple(self._gui_endpoints.items()):
            for _ in range(limit_per_endpoint):
                try:
                    message = mailbox.get_nowait()
                except queue.Empty:
                    break
                try:
                    handler(message)
                except Exception:
                    logger.exception("GUI endpoint %r failed to handle %s", name, message.topic)
                processed += 1
        return processed

    def drain_gui_all(self) -> int:
        """Drain every currently queued GUI message after producers have stopped."""
        processed = 0
        while True:
            batch = self.drain_gui(limit_per_endpoint=256)
            processed += batch
            if batch == 0:
                return processed

    def close(self) -> None:
        if self._closed:
            return
        for name in tuple(self._registered_endpoints):
            self.bus.unregister(name)
        self._gui_endpoints.clear()
        self._registered_endpoints.clear()
        self._closed = True