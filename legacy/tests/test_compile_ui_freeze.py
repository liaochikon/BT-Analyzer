from types import SimpleNamespace

from application.compile_events import CompileBusyChanged, CompileTopic


def test_compile_busy_freezes_and_restores_graph_interactions(monkeypatch):
    from ui import ui

    items = {
        "win_node_editor", "win_pedal_workspace", "panel_a", "file_dialog_node_a",
        "node_a", "slider_a", "disabled_button", "panel_input",
        "menu_presets_topbar", "mi_global_analyze_topbar",
        "mi_show_node_editor", "mi_show_pedal_workspace",
    }
    children = {
        "win_node_editor": {1: ["node_a", "disabled_button"]},
        "node_a": {1: ["slider_a"]},
        "win_pedal_workspace": {1: []},
        "panel_a": {1: ["panel_input"]},
    }
    item_types = {
        "node_a": "mvAppItemType::mvNode",
        "slider_a": "mvAppItemType::mvSliderFloat",
        "disabled_button": "mvAppItemType::mvButton",
        "panel_input": "mvAppItemType::mvInputText",
        "menu_presets_topbar": "mvAppItemType::mvMenu",
        "mi_global_analyze_topbar": "mvAppItemType::mvMenuItem",
        "mi_show_node_editor": "mvAppItemType::mvMenuItem",
        "mi_show_pedal_workspace": "mvAppItemType::mvMenuItem",
    }
    configuration = {
        tag: {"enabled": tag != "disabled_button"} for tag in items
    }
    configuration["node_a"]["draggable"] = True
    shown = {"file_dialog_node_a": True}
    themes = []

    def configure_item(tag, **options):
        configuration[tag].update(options)
        if "show" in options:
            shown[tag] = options["show"]

    monkeypatch.setattr(ui, "set_node_interactions_enabled", lambda enabled: None)
    monkeypatch.setattr(
        ui.dpg,
        "get_aliases",
        lambda: ["panel_a", "file_dialog_node_a"],
    )
    monkeypatch.setattr(ui.dpg, "does_item_exist", lambda tag: tag in items)
    monkeypatch.setattr(ui.dpg, "get_item_children", lambda tag: children.get(tag, {}))
    monkeypatch.setattr(ui.dpg, "get_item_type", lambda tag: item_types.get(tag, "mvAppItemType::mvWindowAppItem"))
    monkeypatch.setattr(ui.dpg, "get_item_configuration", lambda tag: dict(configuration[tag]))
    monkeypatch.setattr(ui.dpg, "configure_item", configure_item)
    monkeypatch.setattr(ui.dpg, "is_item_shown", lambda tag: shown.get(tag, False))
    monkeypatch.setattr(ui.dpg, "bind_item_theme", lambda tag, theme: themes.append((tag, theme)))
    ui._DISABLED_GRAPH_ITEMS.clear()
    ui._FROZEN_NODE_DRAGGABLE.clear()
    ui._HIDDEN_GRAPH_DIALOGS.clear()
    ui._EDITOR_DIM_THEME = "dim_theme"

    ui.handle_node_editor_message(SimpleNamespace(
        topic=CompileTopic.BUSY_CHANGED,
        payload=CompileBusyChanged(busy=True),
    ))

    assert configuration["node_a"]["draggable"] is False
    assert configuration["slider_a"]["enabled"] is False
    assert configuration["panel_input"]["enabled"] is False
    assert configuration["menu_presets_topbar"]["enabled"] is False
    assert configuration["mi_global_analyze_topbar"]["enabled"] is False
    assert configuration["mi_show_node_editor"]["enabled"] is False
    assert configuration["mi_show_pedal_workspace"]["enabled"] is False
    assert configuration["disabled_button"]["enabled"] is False
    assert shown["file_dialog_node_a"] is False
    assert themes[-1] == ("win_node_editor", "dim_theme")

    ui.handle_node_editor_message(SimpleNamespace(
        topic=CompileTopic.BUSY_CHANGED,
        payload=CompileBusyChanged(busy=False),
    ))

    assert configuration["node_a"]["draggable"] is True
    assert configuration["slider_a"]["enabled"] is True
    assert configuration["panel_input"]["enabled"] is True
    assert configuration["menu_presets_topbar"]["enabled"] is True
    assert configuration["mi_global_analyze_topbar"]["enabled"] is True
    assert configuration["disabled_button"]["enabled"] is False
    assert shown["file_dialog_node_a"] is True
    assert themes[-1] == ("win_node_editor", 0)


def test_compile_busy_blocks_node_shortcuts_but_not_transport(monkeypatch):
    from ui import control

    graph_calls = []
    transport_calls = []
    graph_commands = SimpleNamespace(
        undo=lambda: graph_calls.append("undo"),
        redo=lambda: graph_calls.append("redo"),
    )
    monkeypatch.setattr(control.TRANSPORT, "stop", lambda: transport_calls.append("stop"))
    control.init_shortcuts(graph_commands)
    control.set_node_interactions_enabled(False)

    control.keymap._ACTIONS_BY_ID["undo"].handler()
    control.keymap._ACTIONS_BY_ID["redo"].handler()
    control.keymap._ACTIONS_BY_ID["stop"].handler()

    assert graph_calls == []
    assert transport_calls == ["stop"]
    control.set_node_interactions_enabled(True)