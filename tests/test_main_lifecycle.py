import pytest

import main


class _Closable:
    def __init__(self, name, calls):
        self.name = name
        self.calls = calls

    def close(self):
        self.calls.append(self.name)


class _Application(_Closable):
    def drain_gui_all(self):
        self.calls.append("gui-drain")


def test_startup_failure_closes_every_created_runtime_owner(monkeypatch):
    calls = []
    monkeypatch.setattr(main.ENGINE, "stop", lambda: calls.append("engine"))
    monkeypatch.setattr(main, "bind_graph_commands", lambda value: calls.append(("graph", value)))
    monkeypatch.setattr(
        main, "bind_preset_graph_commands", lambda value: calls.append(("preset", value)))
    monkeypatch.setattr(
        main.TRANSPORT, "bind_control_bus", lambda value: calls.append(("transport", value)))
    monkeypatch.setattr(
        main.COMPILE_COORDINATOR,
        "configure_lifecycle",
        lambda value: calls.append(("compile", value)),
    )
    monkeypatch.setattr(main.disk_cache, "clear", lambda: calls.append("cache"))

    def fail_startup(runtime):
        runtime.application = _Application("application", calls)
        runtime.persistence_worker = _Closable("persistence", calls)
        runtime.graph_owner = _Closable("owner", calls)
        raise RuntimeError("startup failed")

    monkeypatch.setattr(main, "_run_application", fail_startup)

    with pytest.raises(RuntimeError, match="startup failed"):
        main.main()

    assert calls.count("persistence") == 1
    assert calls.count("owner") == 1
    assert calls.count("application") == 1
    assert calls.index("persistence") < calls.index("owner") < calls.index("application")
    assert "gui-drain" not in calls


def test_runtime_close_is_idempotent(monkeypatch):
    calls = []
    monkeypatch.setattr(main.ENGINE, "stop", lambda: calls.append("engine"))
    monkeypatch.setattr(main, "bind_graph_commands", lambda _value: None)
    monkeypatch.setattr(main, "bind_preset_graph_commands", lambda _value: None)
    monkeypatch.setattr(main.TRANSPORT, "bind_control_bus", lambda _value: None)
    monkeypatch.setattr(main.COMPILE_COORDINATOR, "configure_lifecycle", lambda _value: None)
    monkeypatch.setattr(main.disk_cache, "clear", lambda: None)
    runtime = main._ApplicationRuntime()
    runtime.application = _Application("application", calls)

    runtime.close()
    runtime.close()

    assert calls == ["engine", "application"]