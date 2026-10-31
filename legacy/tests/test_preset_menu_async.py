from core.bus import BusMessageResult


def test_async_save_does_not_commit_ui_before_worker_success(monkeypatch):
    from ui import preset_menu

    calls = []
    monkeypatch.setattr(preset_menu, "append_log", lambda *_args, **_kwargs: None)

    class Publisher:
        def save_preset(self, *args):
            calls.append(("request", *args))
            return BusMessageResult.ENQUEUED

    monkeypatch.setattr(preset_menu, "_graph_commands", Publisher())
    monkeypatch.setattr(
        preset_menu.dpg,
        "configure_item",
        lambda *args, **kwargs: calls.append(("configure", args, kwargs)),
    )
    monkeypatch.setattr(
        preset_menu.dpg,
        "set_value",
        lambda *args, **kwargs: calls.append(("value", args, kwargs)),
    )
    monkeypatch.setattr(preset_menu.dpg, "does_item_exist", lambda _tag: True)
    monkeypatch.setattr(
        preset_menu,
        "set_startup_preset_name",
        lambda name: calls.append(("default", name)),
    )
    monkeypatch.setattr(
        preset_menu,
        "rebuild_preset_menus",
        lambda: calls.append(("menus",)),
    )

    preset_menu._do_save("practice", True, True)

    assert ("default", "practice") not in calls
    assert ("menus",) not in calls
    assert ("configure", ("win_preset_save",), {"show": False}) not in calls

    preset_menu.handle_preset_save_completed("practice", True, 2)

    assert ("default", "practice") in calls
    assert ("menus",) in calls
    assert ("configure", ("win_preset_save",), {"show": False}) in calls


def test_async_save_failure_keeps_dialog_open(monkeypatch):
    from ui import preset_menu

    calls = []
    monkeypatch.setattr(preset_menu, "append_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(preset_menu.dpg, "does_item_exist", lambda _tag: True)
    monkeypatch.setattr(
        preset_menu.dpg,
        "configure_item",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    monkeypatch.setattr(preset_menu.dpg, "set_value", lambda *_args, **_kwargs: None)

    preset_menu.handle_preset_save_failed("practice", "disk full")

    assert (("preset_save_confirm",), {"enabled": True}) in calls
    assert (("win_preset_save",), {"show": False}) not in calls


def test_existing_preset_warns_then_overwrites_on_second_confirmation(monkeypatch):
    from ui import preset_menu

    calls = []
    values = {
        "preset_save_input": "practice",
        "preset_save_params": True,
        "preset_save_as_default": False,
    }

    class Publisher:
        def save_preset(self, *args):
            calls.append(("save", *args))
            return BusMessageResult.ENQUEUED

    monkeypatch.setattr(preset_menu, "_pending_overwrite", None)
    monkeypatch.setattr(preset_menu, "_graph_commands", Publisher())
    monkeypatch.setattr(preset_menu, "list_presets", lambda: ["practice"])
    monkeypatch.setattr(
        preset_menu, "append_log",
        lambda message, **kwargs: calls.append(("log", message, kwargs)),
    )
    monkeypatch.setattr(preset_menu.i18n, "tr", lambda key: {
        "preset_overwrite_message": "Preset {name} exists. Overwrite?",
        "preset_overwrite_confirm": "Overwrite",
        "preset_save_as_confirm": "Save",
    }[key])
    monkeypatch.setattr(preset_menu.dpg, "get_value", values.get)
    monkeypatch.setattr(preset_menu.dpg, "does_item_exist", lambda _tag: True)
    monkeypatch.setattr(
        preset_menu.dpg, "set_value",
        lambda *args: calls.append(("value", *args)),
    )
    monkeypatch.setattr(
        preset_menu.dpg, "configure_item",
        lambda *args, **kwargs: calls.append(("configure", *args, kwargs)),
    )

    preset_menu._on_preset_save_confirm()

    assert not any(call[0] == "save" for call in calls)
    assert ("value", "preset_save_overwrite_warning",
            "Preset practice exists. Overwrite?") in calls
    assert (
        "configure", "preset_save_confirm", {"label": "Overwrite"},
    ) in calls

    preset_menu._on_preset_save_confirm()

    assert ("save", "practice", True, False) in calls
    assert any(
        call[0] == "log" and "Overwriting existing preset" in call[1]
        for call in calls
    )


def test_changed_overwrite_options_require_confirmation_again(monkeypatch):
    from ui import preset_menu

    requested = []
    values = {
        "preset_save_input": "practice",
        "preset_save_params": False,
        "preset_save_as_default": False,
    }
    monkeypatch.setattr(preset_menu, "_pending_overwrite", None)
    monkeypatch.setattr(preset_menu, "list_presets", lambda: ["practice"])
    monkeypatch.setattr(
        preset_menu, "_request_overwrite_confirmation",
        lambda request: (
            setattr(preset_menu, "_pending_overwrite", request),
            requested.append(request),
        ),
    )
    monkeypatch.setattr(preset_menu.dpg, "get_value", values.get)

    preset_menu._on_preset_save_confirm()
    values["preset_save_params"] = True
    preset_menu._on_preset_save_confirm()

    assert requested == [
        ("practice", False, False),
        ("practice", True, False),
    ]


def test_duplicate_warning_uses_existing_save_modal(dpg_context, monkeypatch):
    from ui import preset_menu

    dpg = dpg_context
    for tag in ("win_preset_save", "win_preset_overwrite"):
        if dpg.does_item_exist(tag):
            dpg.delete_item(tag)
    monkeypatch.setattr(preset_menu, "_pending_overwrite", None)
    monkeypatch.setattr(preset_menu, "_graph_commands", None)
    monkeypatch.setattr(preset_menu, "get_startup_preset_name", lambda: "practice")
    monkeypatch.setattr(preset_menu, "list_presets", lambda: ["practice"])
    monkeypatch.setattr(preset_menu, "append_log", lambda *_args, **_kwargs: None)
    translations = {
        "preset_overwrite_message": "Preset '{name}' exists. Overwrite it?",
        "preset_overwrite_confirm": "Overwrite",
        "preset_save_as_confirm": "Save",
    }
    monkeypatch.setattr(
        preset_menu.i18n, "tr",
        lambda key: translations.get(key, key),
    )

    preset_menu._open_preset_save()
    dpg.set_value("preset_save_input", "practice")
    preset_menu._on_preset_save_confirm()

    assert dpg.does_item_exist("win_preset_save")
    assert not dpg.does_item_exist("win_preset_overwrite")
    assert dpg.get_item_configuration("preset_save_overwrite_warning")["show"] is True
    assert "practice" in dpg.get_value("preset_save_overwrite_warning")
    assert dpg.get_item_label("preset_save_confirm") == preset_menu.i18n.tr(
        "preset_overwrite_confirm")

    dpg.delete_item("win_preset_save")