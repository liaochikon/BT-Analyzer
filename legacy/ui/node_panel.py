"""Lifecycle owner for independent DearPyGui node panels."""

import dearpygui.dearpygui as dpg


class NodePanelController:
    """Create, focus, synchronize, and serialize one node's panel window."""

    def __init__(self, node):
        self._node = node

    def open(self, pos=None, size=None):
        node = self._node
        width = int(size[0]) if size else node.panel_width
        height = int(size[1]) if size else node.panel_height

        if dpg.does_item_exist(node.panel_window_tag):
            dpg.show_item(node.panel_window_tag)
            if pos:
                dpg.set_item_pos(node.panel_window_tag, [int(pos[0]), int(pos[1])])
            if size:
                dpg.configure_item(node.panel_window_tag, width=width, height=height)
            dpg.focus_item(node.panel_window_tag)
            node._sync_panel_widgets()
            return

        from ui.pedal_workspace import prepare_panel_detach
        prepare_panel_detach(node.node_tag)

        window_args = {
            "label": node.title,
            "tag": node.panel_window_tag,
            "width": width,
            "height": height,
            "no_collapse": True,
            "on_close": self._close_from_ui,
        }
        if pos:
            window_args["pos"] = [int(pos[0]), int(pos[1])]
        with dpg.window(**window_args):
            node._draw_panel()
        node._sync_panel_widgets()

    def _close_from_ui(self, *_args):
        self.recall()

    def recall(self):
        node = self._node
        if dpg.does_item_exist(node.panel_window_tag):
            dpg.delete_item(node.panel_window_tag)
        from ui.pedal_workspace import restore_embedded_panel
        restore_embedded_panel(node.node_tag)

    def serialize(self, *, enabled: bool):
        node = self._node
        if not enabled or not dpg.does_item_exist(node.panel_window_tag):
            return None
        try:
            pos = dpg.get_item_pos(node.panel_window_tag)
            size = dpg.get_item_rect_size(node.panel_window_tag)
        except Exception:
            return {
                "pos": [0.0, 0.0],
                "size": [float(node.panel_width), float(node.panel_height)],
            }
        width = float(size[0]) if size and size[0] > 1 else float(node.panel_width)
        height = float(size[1]) if size and size[1] > 1 else float(node.panel_height)
        return {"pos": [float(pos[0]), float(pos[1])], "size": [width, height]}
