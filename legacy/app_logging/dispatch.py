"""Thread-safe UI dispatch: marshal DearPyGui mutations onto the GUI thread.

DearPyGui's item tree is read/written every frame by the render loop (the GUI thread).
Worker threads (bake / compile / export) must therefore never touch DPG directly, or they
race the renderer. Instead they enqueue a zero-arg callable here; the main loop drains and
runs them once per frame, just before rendering, so every DPG call happens on one thread.

This mirrors the engine's existing "command queue + single owner" model (see
``core.transport``): worker threads only *post* work, a single owner *applies* it.

Calls made from the GUI thread itself run immediately, so interactive updates never lag a
frame; only cross-thread calls are deferred.
"""

import logging
import queue
import threading

logger = logging.getLogger(__name__)

# Identity of the GUI (main-loop) thread, captured by bind_gui_thread(). Until it is set,
# every caller is treated as "already on the GUI thread" (true during startup/UI build).
_GUI_THREAD_ID = None

# Cross-thread UI work waiting to run on the next frame. SimpleQueue is itself thread-safe.
_QUEUE: "queue.SimpleQueue" = queue.SimpleQueue()


def bind_gui_thread() -> None:
    """Record the current thread as the GUI thread. Call once, on the main loop thread."""
    global _GUI_THREAD_ID
    _GUI_THREAD_ID = threading.get_ident()


def on_gui_thread() -> bool:
    return _GUI_THREAD_ID is None or threading.get_ident() == _GUI_THREAD_ID


def run_on_gui(fn) -> None:
    """Run ``fn()`` now if on the GUI thread, else defer it to the next frame.

    ``fn`` must be a zero-arg callable that performs only DPG mutations. Capture any values
    it needs by closure so the deferred run sees a consistent snapshot.
    """
    if on_gui_thread():
        fn()
    else:
        _QUEUE.put(fn)


def drain_ui() -> None:
    """Run all queued UI callables. Call once per frame on the GUI thread before rendering."""
    while True:
        try:
            fn = _QUEUE.get_nowait()
        except queue.Empty:
            break
        try:
            fn()
        except Exception:
            # A single bad UI update must not stall the drain or kill the frame loop.
            logger.exception("Deferred UI callable raised; skipping.")
