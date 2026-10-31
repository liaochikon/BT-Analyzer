"""Analyzer Status window -- a musician-facing peer window (same level as the log / transport).

The engine's health (which heavy nodes are stale, failed, or trapped in a wiring loop) is normally
only visible as coloured node frames INSIDE the node editor. A player working from the transport +
fretboard + chord panels never sees it, and would just wonder why a change didn't take. This window
translates that engine state into one plain-language line + a big Bake button, so the player can
re-process without opening the node editor, and can tell at a glance whether anything is wrong.

Colour = blast radius (the project-wide rule): RED = a wiring loop (the whole graph can't run);
YELLOW = a local problem (a node failed to process, or its result is out of date). Detail lines are
capped so a broken graph doesn't flood the window; the top light + count always reflect the total.
"""

import dearpygui.dearpygui as dpg

from application.compile_events import CompileBusyChanged, CompileTopic
from core import pipeline
from ui import i18n, icons

# Status-light glyphs (Nerd Font, from ui.icons) -- one per state.
_ICON_OK = icons.CHECK_CIRCLE   # everything up to date
_ICON_YELLOW = icons.WARNING    # local problem(s): failed / stale
_ICON_RED = icons.ERROR         # system problem: a wiring loop
_ICON_BUSY = icons.REFRESH      # a bake is running

# How many problem nodes to list before collapsing the rest into "...and N more".
_MAX_DETAIL_ROWS = 6

# Colours for the top light / detail rows (match the node-frame warning/error hues loosely).
_COL_OK = (90, 200, 120, 255)
_COL_YELLOW = (230, 200, 90, 255)
_COL_RED = (225, 90, 90, 255)
_COL_BUSY = (100, 170, 235, 255)   # "processing" blue -- a bake is running

# Per-category detail colour + the i18n key for its plain-language phrase.
_CATEGORY = {
    pipeline.ISSUE_WIRING: (_COL_RED, "analyzer_status_reason_wiring"),
    pipeline.ISSUE_FAILED: (_COL_YELLOW, "analyzer_status_reason_failed"),
    pipeline.ISSUE_STALE: (_COL_YELLOW, "analyzer_status_reason_stale"),
}

# Last-rendered signature so the per-frame update only rebuilds the detail rows when something
# actually changed (the same change-gating the transport readouts use -- cheap, no flicker). The
# compile-busy flag is part of the signature so entering/leaving "Processing..." repaints too.
_last_key = None
_compile_busy = False


def build_analyzer_status(on_advanced=None, on_analyze=None):
    if on_analyze is None:
        raise ValueError("on_analyze command callback is required")
    """Build the Analyzer Status readout into the CURRENT container (the node editor's Simple view).

    Previously a standalone peer window; now it is the node editor's musician-facing "Simple" face.
    The editor normally changes face responsively; ``on_advanced`` remains an optional embedding
    hook. All inner tags are preserved so ``update_analyzer_status()`` drives the same widgets.
    """
    # Top row: a status light + one-line summary.
    with dpg.group(horizontal=True):
        dpg.add_text(_ICON_OK, tag="analyzer_light", color=_COL_OK)
        dpg.add_text(i18n.tr("analyzer_status_up_to_date"), tag="analyzer_summary")

    dpg.add_spacer(height=2)
    dpg.add_separator()
    dpg.add_spacer(height=2)

    # Actions: one big Bake (re-process everything, no node graph needed). An embedding surface
    # may optionally provide an explicit advanced-view action.
    with dpg.group(horizontal=True):
        dpg.add_spacer(width=10)
        with dpg.group():
            dpg.add_button(tag="analyzer_bake_btn",
                            label=i18n.tr_reg("analyzer_bake_btn", "analyzer_status_bake_btn"),
                            width=200, height=40, callback=on_analyze)
            if on_advanced is not None:
                dpg.add_button(
                    tag="analyzer_open_editor_btn",
                    label=i18n.tr_reg(
                        "analyzer_open_editor_btn", "analyzer_status_open_editor_btn"),
                    width=200, height=25, callback=lambda: on_advanced())

    dpg.add_spacer(height=2)
    dpg.add_separator()
    dpg.add_spacer(height=6)

    # Detail rows are (re)built into this group each time the issue set changes.
    dpg.add_group(tag="analyzer_detail")


def _issue_key(issues):
    # A cheap, order-stable signature of the current issues so we only redraw on change.
    return tuple((issue.category, issue.node_id, issue.name) for issue in issues)


def handle_analyzer_status_message(message) -> None:
    global _compile_busy
    if message.topic != CompileTopic.BUSY_CHANGED:
        raise TypeError(f"unsupported analyzer status topic: {message.topic}")
    event = message.payload
    if not isinstance(event, CompileBusyChanged):
        raise TypeError("compile.busy.changed requires CompileBusyChanged payload")
    _compile_busy = event.busy
    update_analyzer_status()


def update_analyzer_status():
    """Project the latest immutable graph snapshot and compile lifecycle state."""
    global _last_key
    if not dpg.does_item_exist("analyzer_light"):
        return

    busy = _compile_busy
    issues = pipeline.graph_snapshot().issues
    # Busy is part of the signature so entering/leaving "Processing..." always repaints, even when
    # the issue set is momentarily unchanged.
    key = (busy, _issue_key(issues))
    if key == _last_key:
        return
    _last_key = key

    # While a bake runs, override the readout: blue "Processing..." light + disabled Bake button
    # (so the press has visible feedback and can't be double-fired). Detail rows are cleared -- the
    # issue set is mid-recompute and about to change anyway.
    if busy:
        dpg.set_value("analyzer_light", _ICON_BUSY)
        dpg.configure_item("analyzer_light", color=_COL_BUSY)
        dpg.set_value("analyzer_summary", i18n.tr("analyzer_status_processing"))
        dpg.configure_item("analyzer_bake_btn", enabled=False)
        dpg.delete_item("analyzer_detail", children_only=True)
        return

    # Idle: Bake is usable again; show the health readout.
    dpg.configure_item("analyzer_bake_btn", enabled=True)
    has_red = any(issue.category == pipeline.ISSUE_WIRING for issue in issues)
    n = len(issues)

    # Top light + summary.
    if n == 0:
        dpg.set_value("analyzer_light", _ICON_OK)
        dpg.configure_item("analyzer_light", color=_COL_OK)
        dpg.set_value("analyzer_summary", i18n.tr("analyzer_status_up_to_date"))
    else:
        dpg.set_value("analyzer_light", _ICON_RED if has_red else _ICON_YELLOW)
        dpg.configure_item("analyzer_light", color=_COL_RED if has_red else _COL_YELLOW)
        summary = i18n.tr("analyzer_status_needs_attention").replace("{n}", str(n))
        dpg.set_value("analyzer_summary", summary)

    # Rebuild the detail rows (red first -- get_analyzer_issues already sorts that way).
    dpg.delete_item("analyzer_detail", children_only=True)
    for issue in issues[:_MAX_DETAIL_ROWS]:
        col, reason_key = _CATEGORY[issue.category]
        text = f"{issue.name} - {i18n.tr(reason_key)}"
        dpg.add_text(text, color=col, parent="analyzer_detail")
    hidden = n - _MAX_DETAIL_ROWS
    if hidden > 0:
        more = i18n.tr("analyzer_status_and_more").replace("{n}", str(hidden))
        dpg.add_text(more, color=(150, 150, 160, 255), parent="analyzer_detail")
