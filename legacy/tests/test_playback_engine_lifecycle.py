import threading

import pytest

from core.playback_engine import PlaybackEngine


def _bounded_producer(engine, started=None):
    if started is not None:
        started.set()
    engine._stop.wait(timeout=1.0)


def test_start_failure_stops_producer_and_clears_stream(monkeypatch):
    engine = PlaybackEngine()
    started = threading.Event()
    monkeypatch.setattr(
        engine, "_producer_loop", lambda: _bounded_producer(engine, started))
    monkeypatch.setattr(engine, "_negotiate_rate", lambda: 44_100.0)

    def fail_output_stream(**_kwargs):
        raise RuntimeError("device unavailable")

    monkeypatch.setattr(
        "core.playback_engine.sd.OutputStream", fail_output_stream)

    with pytest.raises(RuntimeError, match="device unavailable"):
        engine.start()

    assert started.wait(timeout=1.0)
    assert engine._producer is None
    assert engine._stream is None


def test_device_switch_failure_reopens_previous_device(monkeypatch):
    engine = PlaybackEngine()
    monkeypatch.setattr(engine, "_producer_loop", lambda: _bounded_producer(engine))
    monkeypatch.setattr(engine, "_negotiate_rate", lambda: 44_100.0)

    class OutputStream:
        def __init__(self, *, device, **_kwargs):
            if device == "unavailable":
                raise RuntimeError("device unavailable")
            self.active = False

        def start(self):
            self.active = True

        def stop(self):
            self.active = False

        def close(self):
            self.active = False

    monkeypatch.setattr("core.playback_engine.sd.OutputStream", OutputStream)
    engine._device = "previous"
    engine.start()

    try:
        assert not engine.set_device("unavailable")
        assert engine.current_device == "previous"
        assert engine.is_running
    finally:
        engine.stop()