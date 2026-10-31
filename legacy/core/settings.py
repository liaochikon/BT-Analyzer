"""Unified application settings: shipped defaults layered under per-user overrides.

Two layers, one accessor:

* ``DEFAULTS`` — the static, version-controlled seed values. Edit these in code; they ship with
  the app and are the fallback for every key.
* ``user_settings.json`` — per-user overrides written live by the in-app Settings window. This
  file is git-ignored and may not exist on a fresh checkout.

``get(key)`` returns the user override if present, else the default; ``set(key, value)`` writes to
the user file only (defaults are never mutated).

Kept dependency-free (only json/os/logging) so any layer — core, UI, nodes — can read settings
without creating an import cycle. ``locale`` is stored as a plain string name (e.g. ``"ZH_TW"``)
rather than a ``LocaleCode`` enum to keep this module free of UI dependencies.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

# Static, shipped defaults (single source of truth for fallback values).
DEFAULTS = {
    "locale": "ZH_TW",
    "default_layout_file": "default_layout.ini",
    "font_file": "fonts/sarasa-gothic-tc-nerd-merged.ttf",
    "font_size": 18,
    "viewport_title": "BT Analyzer",
    "viewport_width": 1000,
    "viewport_height": 700,
    "start_maximized": False,   # maximise the window on launch (the width/height above still seed
                                # the pre-maximise size, so unchecking restores that size next run)
    "mem_cache_mb": 512,
}

# Per-user override file, anchored to the project root (one level above this core/ package) so it
# does not depend on the launch directory.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FILE = os.path.join(_PROJECT_ROOT, "user_settings.json")

_user: dict = {}


def load() -> dict:
    """Read the user override file into memory. Call once at startup before reading any value."""
    global _user
    try:
        with open(_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        _user = loaded if isinstance(loaded, dict) else {}
    except FileNotFoundError:
        _user = {}
    except (OSError, json.JSONDecodeError):
        logger.warning("Failed to load user settings from %s", _FILE, exc_info=True)
        _user = {}
    return _user


def _save() -> None:
    try:
        with open(_FILE, "w", encoding="utf-8") as f:
            json.dump(_user, f, ensure_ascii=False, indent=2)
    except OSError:
        logger.warning("Failed to persist user settings to %s", _FILE, exc_info=True)


def get(key: str, default=None):
    """User override -> shipped default -> caller-supplied ``default``."""
    if key in _user:
        return _user[key]
    return DEFAULTS.get(key, default)


def set(key: str, value) -> None:
    """Update one user setting and persist immediately (defaults are never touched)."""
    _user[key] = value
    _save()
