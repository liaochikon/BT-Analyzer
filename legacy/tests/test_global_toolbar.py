import json
from pathlib import Path


def test_preset_menu_uses_product_name_in_all_locales():
    root = Path(__file__).parents[1] / "locales"

    for locale in ("en_US", "zh_TW"):
        messages = json.loads((root / f"{locale}.json").read_text(encoding="utf-8"))
        assert messages["node_editor_preset_btn"] == "Preset"


def test_global_toolbar_analyze_uses_graph_command_callback():
    source = (Path(__file__).parents[1] / "ui" / "ui.py").read_text(encoding="utf-8")

    assert 'tag="mi_global_analyze_topbar"' in source
    assert "callback=graph_commands.analyze_callback" in source