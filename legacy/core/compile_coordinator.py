"""Single-flight lifecycle coordinator for background compile work."""

import contextlib
import logging
import threading


logger = logging.getLogger(__name__)


class CompileCancelled(RuntimeError):
    pass


class CompileCoordinator:
    """Own compile busy state, observer notification, and worker dispatch."""

    def __init__(self):
        self._lock = threading.Lock()
        self._busy = False
        self._lifecycle_callback = None
        self._result = None
        self._cancel_requested = threading.Event()
        self._worker_done = threading.Event()
        self._worker_done.set()

    @property
    def is_busy(self) -> bool:
        with self._lock:
            return self._busy

    def configure_lifecycle(self, callback) -> None:
        with self._lock:
            self._lifecycle_callback = callback

    def set_result(self, result) -> None:
        with self._lock:
            self._result = result

    def take_result(self):
        with self._lock:
            result = self._result
            self._result = None
            return result

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_requested.is_set()

    def request_cancel(self) -> None:
        with self._lock:
            if not self._busy:
                return
            self._cancel_requested.set()

    def raise_if_cancelled(self) -> None:
        if self.cancel_requested:
            raise CompileCancelled("compile cancelled")

    def wait_for_worker(self, timeout: float | None = None) -> bool:
        return self._worker_done.wait(timeout=timeout)

    def _notify(self, busy: bool):
        with self._lock:
            callback = self._lifecycle_callback
        if callback is None:
            return
        try:
            callback(busy)
        except Exception:
            logger.exception("compile lifecycle callback failed")

    def dispatch(self, worker, *args) -> bool:
        with self._lock:
            if self._busy:
                return False
            self._busy = True
            self._result = None
            self._cancel_requested.clear()
            self._worker_done.clear()

        def run_worker():
            try:
                worker(*args)
            finally:
                self._worker_done.set()

        self._notify(True)
        try:
            threading.Thread(target=run_worker, daemon=True).start()
        except Exception:
            with self._lock:
                self._busy = False
            self._worker_done.set()
            self._notify(False)
            raise
        return True

    @contextlib.contextmanager
    def session(self):
        try:
            yield
        finally:
            with self._lock:
                self._busy = False
            self._notify(False)
