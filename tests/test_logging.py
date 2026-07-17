import logging


def test_setup_logging_writes_utf8_session_text_file(tmp_path):
    from app_logging.logging import setup_logging, shutdown_logging

    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    for handler in original_handlers:
        root.removeHandler(handler)

    try:
        path = setup_logging(logging.INFO, logs_dir=tmp_path)
        logging.getLogger("test.session").info("使用者動作 recorded")
        shutdown_logging()

        assert path.parent == tmp_path
        assert path.suffix == ".txt"
        content = path.read_text(encoding="utf-8")
        assert "Session log started" in content
        assert "使用者動作 recorded" in content
        assert "test.session" in content
        assert "MainThread" in content
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()
        for handler in original_handlers:
            root.addHandler(handler)
        root.setLevel(original_level)


def test_gui_action_log_includes_control_context_without_values(monkeypatch, caplog):
    from app_logging import gui_actions

    monkeypatch.setattr(gui_actions.dpg, "get_focused_item", lambda: "gain_slider")
    monkeypatch.setattr(gui_actions.dpg, "get_active_window", lambda: "win_node_editor")
    monkeypatch.setattr(gui_actions.dpg, "get_mouse_pos", lambda **_kwargs: (120, 80))
    monkeypatch.setattr(
        gui_actions.dpg,
        "get_item_alias",
        lambda item: item,
    )
    monkeypatch.setattr(
        gui_actions.dpg,
        "get_item_label",
        lambda item: "Gain" if item == "gain_slider" else "Node Editor",
    )
    monkeypatch.setattr(gui_actions.dpg, "get_item_type", lambda _item: "mvAppItemType")

    with caplog.at_level(logging.INFO, logger="app_logging.gui_actions"):
        gui_actions.log_mouse_click(None, 0)

    message = caplog.records[-1].getMessage()
    assert "GUI action mouse_click" in message
    assert "gain_slider" in message
    assert "win_node_editor" in message
    assert "mouse=(120, 80)" in message
    assert "value=" not in message


def test_gui_action_handlers_cover_click_double_click_wheel_and_key(monkeypatch):
    from app_logging import gui_actions

    registered = []

    def capture(kind):
        return lambda *, callback: registered.append((kind, callback))

    monkeypatch.setattr(gui_actions.dpg, "add_mouse_click_handler", capture("click"))
    monkeypatch.setattr(
        gui_actions.dpg, "add_mouse_double_click_handler", capture("double_click"))
    monkeypatch.setattr(gui_actions.dpg, "add_mouse_release_handler", capture("release"))
    monkeypatch.setattr(gui_actions.dpg, "add_mouse_wheel_handler", capture("wheel"))
    monkeypatch.setattr(gui_actions.dpg, "add_key_press_handler", capture("key"))

    gui_actions.register_gui_action_handlers()

    assert [kind for kind, _callback in registered] == [
        "click", "double_click", "release", "wheel", "key",
    ]