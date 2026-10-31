"""In-app internationalization (i18n) with live language switching.

The active locale is a flat ``key -> string`` dict loaded from ``locales/<CODE>.json``.
UI code fetches strings with :func:`tr` and registers items whose text should follow the language
at build time: items whose *label* carries the text (buttons, menus, windows, combos) via
:func:`tr_reg`, and items whose displayed *value* carries it (``add_text``) via
:func:`tr_reg_value`. :func:`set_locale` then refreshes every registered item in place -- label
items via ``configure_item(label=...)``, value items via ``set_value(...)`` -- with no window
rebuild (so node graph / transport / log state is preserved), and persists the choice so it
survives a restart.
"""

import logging

import dearpygui.dearpygui as dpg

from core import settings
from ui.locale import LocaleCode, load_locale

logger = logging.getLogger(__name__)

# Human-readable names shown in the language picker. Each language is named in its own tongue
# (the convention for language menus), so these are intentionally NOT translated.
LOCALE_LABELS = {
    LocaleCode.EN_US: "English",
    LocaleCode.ZH_TW: "繁體中文",
}

_current_code: LocaleCode | None = None
_current: dict = {}
# tag -> locale key, for items whose ``label`` should track the active locale.
_registry: dict = {}
# tag -> locale key, for items whose displayed *value* (e.g. add_text) should track the locale.
_value_registry: dict = {}


def init_locale(default_code: LocaleCode) -> LocaleCode:
    """Load the starting locale (a remembered choice in user settings wins over ``default_code``).

    Call once before building the UI (after :func:`core.settings.load`).
    """
    global _current_code, _current
    name = settings.get("locale")
    _current_code = LocaleCode[name] if name in LocaleCode.__members__ else default_code
    _current = load_locale(_current_code) or {}
    return _current_code


def current_code() -> LocaleCode | None:
    return _current_code


def tr(key: str) -> str:
    """Return the active-locale string for ``key`` (falls back to the key itself)."""
    return _current.get(key, key)


def register(tag, key: str):
    """Track a DPG item so its ``label`` is refreshed on every language switch."""
    _registry[tag] = key
    return tag


def tr_reg(tag, key: str) -> str:
    """Register ``tag`` for live relabelling and return its current string in one call.

    Usage: ``dpg.add_menu_item(tag="mi_exit", label=i18n.tr_reg("mi_exit", "top_bar_file_exit"))``.
    """
    register(tag, key)
    return tr(key)


def tr_reg_value(tag, key: str) -> str:
    """Like :func:`tr_reg` but for items whose displayed *value* (not label) holds the text,
    e.g. ``dpg.add_text``. The value is refreshed via ``set_value`` on every language switch.
    """
    _value_registry[tag] = key
    return tr(key)


def set_locale(code: LocaleCode) -> None:
    """Switch language in place: reload strings, relabel every registered item, then persist."""
    global _current_code, _current
    if code == _current_code:
        return
    new = load_locale(code)
    if new is None:
        return  # load_locale already logged the reason; keep the current language.
    _current_code = code
    _current = new
    for tag, key in list(_registry.items()):
        if dpg.does_item_exist(tag):
            dpg.configure_item(tag, label=_current.get(key, key))
    for tag, key in list(_value_registry.items()):
        if dpg.does_item_exist(tag):
            dpg.set_value(tag, _current.get(key, key))
    settings.set("locale", code.name)
    logger.info("Language switched to %s", code.name)
