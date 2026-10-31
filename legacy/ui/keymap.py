"""Configurable global keyboard shortcuts.

Actions are decoupled from the keys that trigger them so any binding can be remapped from the
Settings window and persisted. The pieces:

  * ACTIONS      - the fixed catalogue of bindable actions (id, default key+mods, scope, label).
  * register()   - a handler is attached to an action id by ui.control at startup.
  * dispatch()   - the single key-press entry point; resolves the pressed key + modifiers to an
                   action and runs its handler, honouring the text-input focus guard and scope.
  * get/set/reset_binding() - read/update the per-user binding overrides (settings-backed).

Binding format stored in user settings under the ``keymap`` key:
    {"<action_id>": {"key": "<KEY_NAME>", "mods": ["Ctrl", "Shift"]}}
An absent action falls back to its default binding. Key names are the stable, locale-independent
strings from KEY_NAMES below (NOT the raw DPG integer codes, which are not stable across builds).

Modifier matching is EXACT: Ctrl+Z and Ctrl+Shift+Z are different bindings, so a plain binding
never fires while an extra modifier is held. This is what lets undo/redo coexist.
"""

import logging

import dearpygui.dearpygui as dpg

from core import settings

logger = logging.getLogger(__name__)


# ----- Key name <-> DPG keycode table --------------------------------------------------------
# Only the keys we actually offer as bindings. Names are stable strings we own (used in settings
# + shown in the UI); the DPG integer codes are resolved lazily so this module imports before a
# DPG context exists.
_KEY_NAME_TO_ATTR = {
    "Space": "mvKey_Spacebar",
    "Left": "mvKey_Left",
    "Right": "mvKey_Right",
    "Up": "mvKey_Up",
    "Down": "mvKey_Down",
    "Delete": "mvKey_Delete",
    "Return": "mvKey_Return",
    "Escape": "mvKey_Escape",
    "Home": "mvKey_Home",
    "End": "mvKey_End",
    "Z": "mvKey_Z",
    "Y": "mvKey_Y",
    "X": "mvKey_X",
    "C": "mvKey_C",
    "V": "mvKey_V",
    "S": "mvKey_S",
    "B": "mvKey_B",
}

# Modifier name -> the PHYSICAL left/right key attributes. is_key_down() only reports real
# keys; the mvKey_Mod* alias codes are for handler dispatch, not state queries, so querying
# them returns garbage (which broke Ctrl/Shift combos and made Space flaky). A modifier is
# "held" when either its left OR right key is down.
_MOD_NAME_TO_ATTRS = {
    "Ctrl": ("mvKey_LControl", "mvKey_RControl"),
    "Shift": ("mvKey_LShift", "mvKey_RShift"),
    "Alt": ("mvKey_LAlt", "mvKey_RAlt"),
}
# Canonical order so a modifier set always renders/compares consistently ("Ctrl+Shift", never
# "Shift+Ctrl").
_MOD_ORDER = ("Ctrl", "Shift", "Alt")

# Resolved caches (name -> int / tuple[int]), filled once a DPG context exists.
_key_code_cache: dict = {}
_mod_code_cache: dict = {}


def _keycode(name: str):
    if name not in _key_code_cache:
        attr = _KEY_NAME_TO_ATTR.get(name)
        _key_code_cache[name] = getattr(dpg, attr) if attr else None
    return _key_code_cache[name]


def _modcodes(name: str) -> tuple:
    """The (left, right) physical keycodes for a modifier name, filtering any not present."""
    if name not in _mod_code_cache:
        attrs = _MOD_NAME_TO_ATTRS.get(name, ())
        _mod_code_cache[name] = tuple(getattr(dpg, a) for a in attrs if hasattr(dpg, a))
    return _mod_code_cache[name]


# ----- Action catalogue ----------------------------------------------------------------------
# scope: "global"  -> fires anywhere (playback / navigation)
#        "editor"  -> only meaningful when the node editor is the active window (edit ops)
class Action:
    def __init__(self, action_id, default_key, default_mods, scope, label_key):
        self.action_id = action_id
        self.default_key = default_key
        self.default_mods = tuple(default_mods)
        self.scope = scope
        self.label_key = label_key            # i18n key for the Settings row label
        self.handler = None                   # attached via register()


# The catalogue. Relative-seek step sizes live in settings (seek_step_1..4); the actions just
# reference which step index they use, so changing the seconds never touches this table.
ACTIONS = [
    Action("play_pause",     "Space", [],               "global", "key_play_pause"),
    Action("stop",           "Space", ["Shift"],         "global", "key_stop"),
    Action("seek_back_1",    "Left",  [],                "global", "key_seek_back_1"),
    Action("seek_fwd_1",     "Right", [],                "global", "key_seek_fwd_1"),
    Action("seek_back_2",    "Left",  ["Shift"],         "global", "key_seek_back_2"),
    Action("seek_fwd_2",     "Right", ["Shift"],         "global", "key_seek_fwd_2"),
    Action("seek_back_3",    "Left",  ["Ctrl"],          "global", "key_seek_back_3"),
    Action("seek_fwd_3",     "Right", ["Ctrl"],          "global", "key_seek_fwd_3"),
    Action("seek_back_4",    "Left",  ["Ctrl", "Shift"], "global", "key_seek_back_4"),
    Action("seek_fwd_4",     "Right", ["Ctrl", "Shift"], "global", "key_seek_fwd_4"),
    Action("undo",           "Z",     ["Ctrl"],          "editor", "key_undo"),
    Action("redo",           "Y",     ["Ctrl"],          "editor", "key_redo"),
    Action("delete_selection", "Delete", [],             "editor", "key_delete_selection"),
]

_ACTIONS_BY_ID = {a.action_id: a for a in ACTIONS}


def register(action_id: str, handler) -> None:
    """Attach the callable that performs ``action_id``. Called by ui.control at startup."""
    action = _ACTIONS_BY_ID.get(action_id)
    if action is None:
        logger.warning("Tried to register unknown action '%s'", action_id)
        return
    action.handler = handler


# ----- Binding storage (settings-backed) -----------------------------------------------------

def _all_overrides() -> dict:
    ov = settings.get("keymap", {})
    return ov if isinstance(ov, dict) else {}


def get_binding(action_id: str) -> tuple:
    """Return the effective ``(key_name, (mods...))`` for an action (override or default)."""
    action = _ACTIONS_BY_ID[action_id]
    ov = _all_overrides().get(action_id)
    if isinstance(ov, dict) and ov.get("key") in _KEY_NAME_TO_ATTR:
        mods = tuple(m for m in ov.get("mods", []) if m in _MOD_NAME_TO_ATTRS)
        return ov["key"], _sorted_mods(mods)
    return action.default_key, _sorted_mods(action.default_mods)


def set_binding(action_id: str, key_name: str, mods) -> bool:
    """Persist a new binding for ``action_id``. Returns False on an unknown key/action."""
    if action_id not in _ACTIONS_BY_ID or key_name not in _KEY_NAME_TO_ATTR:
        return False
    mods = _sorted_mods(tuple(m for m in mods if m in _MOD_NAME_TO_ATTRS))
    overrides = dict(_all_overrides())
    overrides[action_id] = {"key": key_name, "mods": list(mods)}
    settings.set("keymap", overrides)
    return True


def reset_binding(action_id: str) -> None:
    """Drop the override for one action (revert to its default)."""
    overrides = dict(_all_overrides())
    if overrides.pop(action_id, None) is not None:
        settings.set("keymap", overrides)


def reset_all() -> None:
    """Drop every override (revert all actions to defaults)."""
    settings.set("keymap", {})


def _sorted_mods(mods) -> tuple:
    s = set(mods)
    return tuple(m for m in _MOD_ORDER if m in s)


def binding_label(action_id: str) -> str:
    """Human-readable binding, e.g. "Ctrl+Shift+Left" or "Space", for the Settings UI."""
    key_name, mods = get_binding(action_id)
    return "+".join((*mods, key_name)) if mods else key_name


def find_conflict(action_id: str, key_name: str, mods) -> str:
    """Return the id of another action already bound to this key+mods combo, or ""."""
    target = (key_name, _sorted_mods(tuple(mods)))
    for a in ACTIONS:
        if a.action_id == action_id:
            continue
        if get_binding(a.action_id) == target:
            return a.action_id
    return ""


# ----- Rebinding capture ---------------------------------------------------------------------
# While capturing, the next non-modifier key press is recorded as the binding for the target
# action instead of firing any action. Escape cancels. The Settings UI drives this.
_capture_target = None
_capture_done = None
_latched_keys: set[int] = set()
_REPEATABLE_ACTIONS = {
    "seek_back_1", "seek_fwd_1", "seek_back_2", "seek_fwd_2",
    "seek_back_3", "seek_fwd_3", "seek_back_4", "seek_fwd_4",
}


def begin_capture(action_id: str, on_done=None) -> None:
    """Enter capture mode for ``action_id``. ``on_done(bound_or_None)`` fires when it resolves."""
    global _capture_target, _capture_done
    if action_id in _ACTIONS_BY_ID:
        _capture_target = action_id
        _capture_done = on_done


def is_capturing() -> bool:
    return _capture_target is not None


def cancel_capture() -> None:
    global _capture_target, _capture_done
    _capture_target, _capture_done = None, None


def _keyname_for_code(code: int):
    for name in _KEY_NAME_TO_ATTR:
        if _keycode(name) == code:
            return name
    return None


def _handle_capture(pressed_key: int) -> None:
    global _capture_target, _capture_done
    target, done = _capture_target, _capture_done
    # Escape cancels without changing anything.
    if pressed_key == _keycode("Escape"):
        cancel_capture()
        if done:
            done(None)
        return
    # Ignore bare modifier-key presses; wait for a real key while mods are held.
    _all_mod_codes = {c for m in _MOD_ORDER for c in _modcodes(m)}
    if pressed_key in _all_mod_codes:
        return
    key_name = _keyname_for_code(pressed_key)
    if key_name is None:
        return  # unsupported key; keep waiting
    mods = _current_mods()
    set_binding(target, key_name, mods)
    cancel_capture()
    if done:
        done((key_name, _sorted_mods(tuple(mods))))


# ----- Focus guard + dispatch ----------------------------------------------------------------

def _text_input_focused() -> bool:
    """True when a text/number input is actively editing, so shortcuts do not steal keystrokes.

    Without this, typing a preset name and pressing Space would toggle playback, and Delete /
    letters would trigger edit actions instead of editing the field. DearPyGui may retain a
    deleted or inactive input as its focused item after the user clicks the node canvas, so focus
    and type alone are insufficient: only an active editor owns keyboard input.
    """
    try:
        item = dpg.get_focused_item()
    except Exception:
        return False
    if not item:
        return False
    try:
        if not dpg.does_item_exist(item):
            return False
        t = dpg.get_item_type(item)
    except Exception:
        return False
    # DPG item type strings look like "mvAppItemType::mvInputText".
    is_input = any(name in t for name in ("mvInputText", "mvInputInt", "mvInputFloat"))
    if not is_input:
        return False
    try:
        return bool(dpg.is_item_active(item))
    except Exception:
        return False


def _current_mods() -> frozenset:
    """The set of modifier names currently held down (either physical side counts)."""
    held = set()
    for name in _MOD_ORDER:
        for code in _modcodes(name):
            try:
                if dpg.is_key_down(code):
                    held.add(name)
                    break
            except Exception:
                pass
    return frozenset(held)


def dispatch(pressed_key: int) -> bool:
    """Resolve a key-press event to an action and run it. Returns True if an action fired.

    Called from the single global key-press handler. Exact-modifier matching means Ctrl+Z and
    Ctrl+Shift+Z never collide. Text-input focus suppresses all shortcuts.
    """
    # Rebinding capture takes precedence over everything (and over the focus guard, so a click
    # into the capture button that leaves focus elsewhere still records the next key).
    if _capture_target is not None:
        _handle_capture(pressed_key)
        return True

    if _text_input_focused():
        return False

    held_mods = _current_mods()
    for action in ACTIONS:
        if action.handler is None:
            continue
        key_name, mods = get_binding(action.action_id)
        code = _keycode(key_name)
        if code is None or code != pressed_key:
            continue
        if frozenset(mods) != held_mods:  # exact modifier match
            continue
        if action.action_id not in _REPEATABLE_ACTIONS:
            if pressed_key in _latched_keys:
                return True
            _latched_keys.add(pressed_key)
        try:
            action.handler()
        except Exception:
            logger.exception("Shortcut action '%s' crashed", action.action_id)
        return True
    return False


def release(released_key: int) -> None:
    """Allow the next physical press of a non-repeatable shortcut key to fire."""
    _latched_keys.discard(released_key)


# Names exposed for the Settings UI to build the rebinding rows / capture picker.
KEY_NAMES = tuple(_KEY_NAME_TO_ATTR.keys())
MOD_NAMES = _MOD_ORDER
