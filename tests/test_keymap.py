from ui import keymap


def test_text_input_guard_ignores_stale_or_inactive_focus(monkeypatch):
    monkeypatch.setattr(keymap.dpg, "get_focused_item", lambda: "mixer_label")
    monkeypatch.setattr(keymap.dpg, "get_item_type", lambda _item: "mvAppItemType::mvInputText")

    monkeypatch.setattr(keymap.dpg, "does_item_exist", lambda _item: False)
    assert keymap._text_input_focused() is False

    monkeypatch.setattr(keymap.dpg, "does_item_exist", lambda _item: True)
    monkeypatch.setattr(keymap.dpg, "is_item_active", lambda _item: False)
    assert keymap._text_input_focused() is False

    monkeypatch.setattr(keymap.dpg, "is_item_active", lambda _item: True)
    assert keymap._text_input_focused() is True


def test_undo_shortcut_fires_once_until_key_release(monkeypatch):
    calls = []
    key = keymap._keycode("Z")
    ctrl_codes = set(keymap._modcodes("Ctrl"))
    monkeypatch.setattr(keymap, "_text_input_focused", lambda: False)
    monkeypatch.setattr(
        keymap.dpg, "is_key_down", lambda code: code in ctrl_codes)
    keymap.register("undo", lambda: calls.append("undo"))
    keymap.release(key)

    assert keymap.dispatch(key)
    assert keymap.dispatch(key)
    assert keymap.dispatch(key)
    assert calls == ["undo"]

    keymap.release(key)
    assert keymap.dispatch(key)
    assert calls == ["undo", "undo"]
    keymap.release(key)


def test_seek_shortcut_remains_repeatable(monkeypatch):
    calls = []
    key = keymap._keycode("Right")
    monkeypatch.setattr(keymap, "_text_input_focused", lambda: False)
    monkeypatch.setattr(keymap.dpg, "is_key_down", lambda _code: False)
    keymap.register("seek_fwd_1", lambda: calls.append("seek"))

    assert keymap.dispatch(key)
    assert keymap.dispatch(key)
    assert calls == ["seek", "seek"]