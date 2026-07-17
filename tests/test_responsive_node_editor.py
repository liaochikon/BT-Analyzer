def test_node_editor_enters_and_exits_advanced_with_hysteresis(monkeypatch):
    from ui import ui

    size = [700, 450]
    states = []
    monkeypatch.setattr(ui.dpg, "does_item_exist", lambda tag: tag == "win_node_editor")
    monkeypatch.setattr(ui.dpg, "get_item_rect_size", lambda _tag: tuple(size))
    monkeypatch.setattr(
        ui,
        "set_node_editor_view",
        lambda advanced: (
            states.append(advanced),
            setattr(ui, "_node_editor_advanced", advanced),
        ),
    )

    ui._node_editor_advanced = False
    ui.update_node_editor_view()
    assert states == []

    size[:] = [720, 480]
    ui.update_node_editor_view()
    assert states == [True]

    size[:] = [690, 450]
    ui.update_node_editor_view()
    assert states == [True]

    size[:] = [659, 450]
    ui.update_node_editor_view()
    assert states == [True, False]


def test_node_editor_view_switch_does_not_resize_window(monkeypatch):
    from ui import ui

    configured = []
    monkeypatch.setattr(ui.dpg, "does_item_exist", lambda _tag: True)
    monkeypatch.setattr(
        ui.dpg,
        "configure_item",
        lambda tag, **kwargs: configured.append((tag, kwargs)),
    )

    ui.set_node_editor_view(True)

    assert all("width" not in options and "height" not in options
               for _tag, options in configured)
    assert ("node_advanced_body", {"show": True}) in configured
    assert ("node_simple_body", {"show": False}) in configured


def test_node_editor_has_no_manual_simple_or_advanced_switch():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "ui" / "ui.py").read_text(encoding="utf-8")

    assert "mi_simple_view" not in source
    assert "analyzer_open_editor_btn" not in source