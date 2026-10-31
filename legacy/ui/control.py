"""Global keyboard actions.

The action *handlers* live here (what each shortcut does); the *bindings* (which key triggers
what, and their remapping/persistence) live in ui.keymap. At startup init_shortcuts() attaches
these handlers to their action ids, and the single DPG key-press handler delegates to
keymap.dispatch(), which resolves the pressed key + modifiers to the right action.
"""

import logging
import dearpygui.dearpygui as dpg
from core import *
from core import settings
from ui import keymap


logger = logging.getLogger(__name__)
_node_interactions_enabled = True


def set_node_interactions_enabled(enabled: bool) -> None:
    global _node_interactions_enabled
    _node_interactions_enabled = bool(enabled)


# ----- Relative seek --------------------------------------------------------------------------
# Step sizes (seconds) are user-configurable via settings (seek_step_1..4), defaulting to
# 5/10/15/20s. Seeking works on the OUTPUT timeline, so "back 5s" is 5s of what you hear
# regardless of playback rate (request_seek takes an output-sample index).
DEFAULT_SEEK_STEPS = {1: 5.0, 2: 10.0, 3: 15.0, 4: 20.0}   # public: the settings UI reads it too


def seek_step_seconds(index: int) -> float:
    return float(settings.get(f"seek_step_{index}", DEFAULT_SEEK_STEPS.get(index, 5.0)))


def _relative_seek(delta_seconds: float) -> None:
    # No-op when nothing is loaded (request_seek clamps to 0 anyway, so this is just tidy).
    snapshot = TRANSPORT.snapshot()
    if snapshot.length <= 0:
        return
    delta = int(round(delta_seconds * snapshot.sample_rate))
    TRANSPORT.request_seek(snapshot.current_index + delta)


# ----- Node-editor edit actions ---------------------------------------------------------------

def _delete_selection(graph_commands=None) -> None:
    if not _node_interactions_enabled:
        return
    selected_links = dpg.get_selected_links("node_editor")
    if selected_links:
        logger.debug("Delete: removing %d selected link(s).", len(selected_links))
        for link_id in selected_links:
            if graph_commands is None:
                continue
            from ui.graph_projection import NODE_EDITOR_LINKS
            connection_id = NODE_EDITOR_LINKS.connection_id_for_item(link_id)
            if connection_id is not None:
                graph_commands.disconnect_connection(connection_id)

    selected_nodes = dpg.get_selected_nodes("node_editor")
    if selected_nodes:
        logger.debug("Delete: removing %d selected node(s).", len(selected_nodes))
        for node_id in selected_nodes:
            node_tag = dpg.get_item_alias(node_id)
            if graph_commands is not None:
                graph_commands.delete_node(node_tag)


def _undo(graph_commands=None) -> None:
    if _node_interactions_enabled and graph_commands is not None:
        graph_commands.undo()


def _redo(graph_commands=None) -> None:
    if _node_interactions_enabled and graph_commands is not None:
        graph_commands.redo()


def _play_pause() -> None:
    # Route through the transport bar's shared entry point so the Space shortcut honours the
    # count-in toggle exactly like the play button. Lazy import avoids a ui.ui <-> ui.control
    # import cycle at module load.
    from ui.transport_bar import request_play_pause
    request_play_pause()


# ----- Registration ---------------------------------------------------------------------------

def init_shortcuts(graph_commands=None) -> None:
    """Attach every action handler to its id in the keymap. Call once at startup."""
    keymap.register("play_pause", _play_pause)
    keymap.register("stop", lambda: TRANSPORT.stop())
    for i in (1, 2, 3, 4):
        keymap.register(f"seek_back_{i}", lambda i=i: _relative_seek(-seek_step_seconds(i)))
        keymap.register(f"seek_fwd_{i}", lambda i=i: _relative_seek(+seek_step_seconds(i)))
    keymap.register("delete_selection", lambda: _delete_selection(graph_commands))
    keymap.register("undo", lambda: _undo(graph_commands))
    keymap.register("redo", lambda: _redo(graph_commands))


def global_key_press_handler(sender, app_data):
    # app_data is the pressed key code. All resolution (bindings, modifiers, focus guard,
    # scope) happens in keymap.dispatch so this stays a thin delegate.
    keymap.dispatch(app_data)


def global_key_release_handler(sender, app_data):
    keymap.release(app_data)
