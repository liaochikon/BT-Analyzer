"""Node-local status-line label, shared by the output/export sink nodes.

The three sinks (Audio Output, Audio Export, MIDI Export) use processing mode ``NONE``, so they never
enter ``GraphState.failed_nodes`` and can't show the yellow warning frame that bake nodes use.
Their only status affordance is a small text widget on the node body. Each node used to hand-roll an
identical ``_set_status(text, color)`` and pick raw RGB literals at the call site, which drifted:
local failures ("No input" / "Empty" / "Write failed") were painted SYSTEM RED -- the colour the
project reserves for whole-graph failures (a wiring cycle). See docs/consistency_cleanup.md §1C/§1E.

This mixin gives them ONE ``_set_status`` plus a named colour vocabulary that follows the project's
blast-radius rule:
  * IDLE  (grey)   -- resting / nothing wired yet. "Not connected" is a normal mid-editing state,
                      NOT an error, so it is deliberately un-alarming (was red).
  * BUSY  (amber)  -- transient progress ("Exporting...", "Paused").
  * OK    (green)  -- success ("Playing", "Ready", "Saved").
  * WARN  (yellow) -- a LOCAL failure ("Write failed", render produced nothing). Matches the
                      yellow the pipeline paints on a failed bake node; never system red.

``_set_status`` defers its DPG writes through ``run_on_gui`` unconditionally: export() runs on the
worker thread (must marshal), while Audio Output calls it from ``on_gui_frame`` (already on the GUI
thread, where run_on_gui runs inline) -- so one code path is correct for both.

Usage: a sink inherits ``(StatusLabelMixin, BaseNode)`` and sets ``self.status_text_tag`` before
drawing the label. Relies on ``self.status_text_tag``; carries no other state.
"""

import dearpygui.dearpygui as dpg

import palette
from app_logging.dispatch import run_on_gui

# Status colours by blast radius, as views of the shared semantic palette (see palette.py). These
# are node-local HUD readouts (RGB, no alpha), distinct from the pipeline's node-frame themes.
# Copy (list(...)) rather than alias the palette lists: a status colour handed to dpg is never
# mutated today, but a copy means a future in-place tweak here can't corrupt the shared palette.
STATUS_IDLE = list(palette.MUTED)   # grey  -- resting / not connected (not an error)
STATUS_BUSY = list(palette.BUSY)    # amber -- in progress
STATUS_OK = list(palette.OK)        # green -- success
STATUS_WARN = list(palette.WARN)    # yellow -- local failure (mirrors node_warning; never system red)


class StatusLabelMixin:
    def _set_status(self, text, color):
        # Defer the DPG writes to the GUI thread. run_on_gui runs inline when already on it, so this
        # is correct whether called from the export worker or from on_gui_frame.
        def _do():
            if dpg.does_item_exist(self.status_text_tag):
                dpg.set_value(self.status_text_tag, text)
                dpg.configure_item(self.status_text_tag, color=color)
        run_on_gui(_do)
