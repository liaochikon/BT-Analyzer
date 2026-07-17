import logging

from core import settings


def test_missing_user_settings_is_a_normal_empty_override(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(settings, "_FILE", str(tmp_path / "missing.json"))

    with caplog.at_level(logging.WARNING, logger=settings.__name__):
        assert settings.load() == {}

    assert not caplog.records


def test_malformed_user_settings_logs_warning(tmp_path, monkeypatch, caplog):
    settings_file = tmp_path / "user_settings.json"
    settings_file.write_text("{invalid", encoding="utf-8")
    monkeypatch.setattr(settings, "_FILE", str(settings_file))

    with caplog.at_level(logging.WARNING, logger=settings.__name__):
        assert settings.load() == {}

    assert "Failed to load user settings" in caplog.text