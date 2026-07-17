"""CompileCoordinator single-flight and lifecycle contracts."""

import threading


def test_compile_coordinator_rejects_overlap_and_publishes_lifecycle():
    from core.compile_coordinator import CompileCoordinator

    coordinator = CompileCoordinator()
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    observed = []
    coordinator.configure_lifecycle(observed.append)

    def worker():
        with coordinator.session():
            started.set()
            release.wait(timeout=1.0)
        finished.set()

    assert coordinator.dispatch(worker)
    assert started.wait(timeout=1.0)
    assert coordinator.is_busy
    assert not coordinator.dispatch(lambda: None)
    release.set()
    assert finished.wait(timeout=1.0)
    assert not coordinator.is_busy
    assert observed == [True, False]


def test_compile_lifecycle_port_can_be_cleared_idempotently():
    from core.compile_coordinator import CompileCoordinator

    coordinator = CompileCoordinator()
    observed = []
    coordinator.configure_lifecycle(observed.append)
    coordinator.configure_lifecycle(None)
    coordinator.configure_lifecycle(None)

    with coordinator.session():
        pass
    assert observed == []


def test_cancel_request_reaches_worker_and_waits_for_full_exit():
    from core.compile_coordinator import CompileCancelled, CompileCoordinator

    coordinator = CompileCoordinator()
    started = threading.Event()
    release = threading.Event()
    cancelled = threading.Event()

    def worker():
        with coordinator.session():
            started.set()
            release.wait(timeout=1.0)
            try:
                coordinator.raise_if_cancelled()
            except CompileCancelled:
                cancelled.set()

    assert coordinator.dispatch(worker)
    assert started.wait(timeout=1.0)
    coordinator.request_cancel()
    release.set()

    assert coordinator.wait_for_worker(timeout=1.0)
    assert cancelled.is_set()
    assert not coordinator.is_busy


def test_cancel_request_is_ignored_without_active_work():
    from core.compile_coordinator import CompileCoordinator

    coordinator = CompileCoordinator()

    coordinator.request_cancel()

    assert not coordinator.cancel_requested