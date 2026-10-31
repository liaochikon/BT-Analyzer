import json
from pathlib import Path


def test_window_menu_does_not_offer_default_layout_save():
    source = (Path(__file__).parents[1] / "ui" / "ui.py").read_text(encoding="utf-8")

    assert "mi_save_layout" not in source
    assert "save_init_file" not in source


def test_workspace_and_player_user_facing_names():
    locales = Path(__file__).parents[1] / "locales"
    english = json.loads((locales / "en_US.json").read_text(encoding="utf-8"))
    traditional_chinese = json.loads(
        (locales / "zh_TW.json").read_text(encoding="utf-8"))

    assert english["pedal_workspace_title"] == "Workspace"
    assert english["top_bar_window_show_pedal_workspace"] == "Show Workspace"
    assert english["transport_window_title"] == "Player"
    assert english["top_bar_window_show_transport"] == "Show Player"
    assert traditional_chinese["pedal_workspace_title"] == "工作區"
    assert traditional_chinese["transport_window_title"] == "播放器"

    for locale in (english, traditional_chinese):
        assert "top_bar_window_save_default_layout" not in locale