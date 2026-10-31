"""Single source of the app's SEMANTIC colours (ok / warn / error / neutral, by blast radius).

Colour in this app carries meaning, and that meaning was drifting: "error" existed as three
different reds, "success"/"idle" had no named home at all and leaked into literals in every node,
and the log-level palette (app_logging.logging) duplicated the same concepts a fourth time. See
docs/consistency_cleanup.md §6.2. This module is the one place those colours are declared.

This is a LEAF module -- it imports nothing, so the lowest layer (``app_logging``) can use it
without a cycle, and so can ``core`` / ``nodes`` / ``ui`` above it. Values are RGB lists; consumers
that need RGBA append their own alpha (node frames use 200, the log panel uses 255) via ``rgba()``.

The blast-radius rule (mirrored in the node-frame themes and the sink status HUDs):
  * RED   = system-level: the whole graph can't run (a wiring cycle).
  * YELLOW= local: one node/branch failed or is stale; the rest still runs.
  * "not connected yet" is NOT an error -> neutral grey, never coloured as failure.

Two surfaces sometimes want different shades of the SAME concept -- a saturated FILL behind title
text vs a lighter TEXT colour readable on a dark panel. Those are named separately (``ERROR_FILL``
vs ``ERROR_TEXT``) rather than merged, so the difference stays intentional and no pixel changes.
"""


def rgba(rgb: list, alpha: int) -> list:
    """``[r, g, b]`` + alpha -> ``[r, g, b, a]``. Lets one RGB constant serve both the node-frame
    themes (alpha 200) and the log panel (alpha 255) without duplicating the triple."""
    return [rgb[0], rgb[1], rgb[2], alpha]


# ---- Neutral ---------------------------------------------------------------------------------
NEUTRAL = [230, 230, 230]   # default log-panel text
MUTED = [180, 180, 180]     # idle / resting status (a normal state, not an error)

# ---- Success / info (green) ------------------------------------------------------------------
OK = [100, 255, 100]        # success status on a node HUD ("Playing" / "Ready" / "Saved")
INFO_TEXT = [200, 255, 200] # info log line -- a paler green so a wall of INFO stays readable

# ---- Warning / caution (yellow-amber) = LOCAL blast radius -----------------------------------
WARN = [235, 205, 40]       # local-failure status; also the node_warning title-bar fill
WARN_HI = [245, 220, 80]    # lighter warning tint (node_warning hover / selected)
WARN_EDGE = [235, 210, 60]  # node_warning outline
WARN_TEXT = [255, 200, 0]   # warning log line -- brighter than WARN for the log wall
BUSY = [255, 200, 100]      # transient progress ("Exporting..." / "Paused") -- amber, not a warning

# ---- Error (red) = SYSTEM blast radius --------------------------------------------------------
ERROR_FILL = [255, 40, 40]    # node_error title-bar fill (saturated, behind white title text)
ERROR_TEXT = [255, 100, 100]  # error log line + node_error hover/selected (lighter, on dark)
ERROR_EDGE = [255, 75, 75]    # node_error outline
