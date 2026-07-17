"""Synchronize full graph replacement with the mutable runtime generation."""

import threading


class GraphProjectionBarrier:
    """Allow only one owner-side runtime generation during a full GUI rebuild."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._generation = 0
        self._completed = threading.Event()
        self._completed.set()

    def begin(self) -> int:
        with self._lock:
            self._generation += 1
            generation = self._generation
            self._completed.clear()
            return generation

    def complete(self, generation: int) -> None:
        with self._lock:
            if generation == self._generation:
                self._completed.set()

    def release(self) -> None:
        self._completed.set()

    def wait(self, generation: int, timeout: float | None = None) -> bool:
        with self._lock:
            if generation != self._generation:
                return True
            completed = self._completed
        return completed.wait(timeout=timeout)

    @property
    def pending(self) -> bool:
        return not self._completed.is_set()
