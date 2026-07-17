"""Scrolling input-vs-output level meter, shared by the dynamics nodes.

A time view comparing the input vs output level (dBFS), with one or two optional reference lines
(threshold / ceiling / floor). The producer thread only stashes the latest per-block peak as plain
floats (``_push_levels``); the GUI thread samples them once per frame (``_update_level_history``)
to build the scrolling history, so there is no cross-thread buffer mutation.

Used only by Compressor / Limiter / Noise Gate. It lived in ``BaseNode`` but every other node
inherited ~90 lines of meter machinery it never touched, so it now lives here as a mixin: a
dynamics node inherits ``(LevelHistoryMixin, BaseNode)`` and calls ``_init_level_history`` in its
constructor. Relies on ``self.node_tag`` (provided by BaseNode), so keep BaseNode second in the MRO.
"""

import numpy as np
import dearpygui.dearpygui as dpg


class LevelHistoryMixin:
    def _init_level_history(self, prefix: str, db_floor: float = -60.0, db_ceil: float = 6.0, points: int = 240):
        self._lh_prefix = prefix
        self._lh_db_floor = db_floor
        self._lh_db_ceil = db_ceil
        self._lh_points = points
        self._lvl_in_db = db_floor
        self._lvl_out_db = db_floor
        self._lh_in_hist = [db_floor] * points
        self._lh_out_hist = [db_floor] * points
        self._lh_xs = list(range(points))
        self.lh_plot_tag = f"{prefix}_lh_plot_{self.node_tag}"
        self.lh_x_tag = f"{prefix}_lh_x_{self.node_tag}"
        self.lh_y_tag = f"{prefix}_lh_y_{self.node_tag}"
        self.lh_in_tag = f"{prefix}_lh_in_{self.node_tag}"
        self.lh_out_tag = f"{prefix}_lh_out_{self.node_tag}"
        self.lh_ref_tag = f"{prefix}_lh_ref_{self.node_tag}"
        self.lh_ref2_tag = f"{prefix}_lh_ref2_{self.node_tag}"

    @staticmethod
    def _block_peak_db(block, eps: float = 1e-9):
        if block is None or len(block) == 0:
            return None
        peak = float(np.max(np.abs(block)))
        return 20.0 * np.log10(peak + eps)

    def _push_levels(self, in_block, out_block):
        in_db = self._block_peak_db(in_block)
        out_db = self._block_peak_db(out_block)
        if in_db is not None:
            self._lvl_in_db = in_db
        if out_db is not None:
            self._lvl_out_db = out_db

    def _draw_level_history(self, height: int = 150,
                            in_color=(150, 150, 150, 255), out_color=(200, 140, 90, 255),
                            ref_color=(120, 120, 120, 255), show_ref: bool = True, ref_label: str = "Ref",
                            show_ref2: bool = False, ref2_color=(110, 90, 150, 255), ref2_label: str = "Floor"):
        with dpg.plot(
                height=height, width=-1, tag=self.lh_plot_tag,
                no_menus=True, no_box_select=True, no_mouse_pos=True):
            # dpg.add_plot_legend()
            dpg.add_plot_axis(
                dpg.mvXAxis, no_tick_labels=True, no_gridlines=True, tag=self.lh_x_tag)
            dpg.set_axis_limits(self.lh_x_tag, 0, self._lh_points)
            dpg.add_plot_axis(dpg.mvYAxis, label="Level (dBFS)", tag=self.lh_y_tag)
            dpg.set_axis_limits(self.lh_y_tag, self._lh_db_floor, self._lh_db_ceil)

            if show_ref:
                dpg.add_line_series(
                    [0, self._lh_points], [0.0, 0.0], label=ref_label,
                    parent=self.lh_y_tag, tag=self.lh_ref_tag)
            if show_ref2:
                dpg.add_line_series(
                    [0, self._lh_points], [self._lh_db_floor, self._lh_db_floor], label=ref2_label,
                    parent=self.lh_y_tag, tag=self.lh_ref2_tag)
            dpg.add_line_series([], [], label="In", parent=self.lh_y_tag, tag=self.lh_in_tag)
            dpg.add_line_series([], [], label="Out", parent=self.lh_y_tag, tag=self.lh_out_tag)

            if show_ref:
                with dpg.theme() as ref_theme:
                    with dpg.theme_component(dpg.mvLineSeries):
                        dpg.add_theme_color(dpg.mvPlotCol_Line, ref_color, category=dpg.mvThemeCat_Plots)
                dpg.bind_item_theme(self.lh_ref_tag, self._track_ui_resource(ref_theme))
            if show_ref2:
                with dpg.theme() as ref2_theme:
                    with dpg.theme_component(dpg.mvLineSeries):
                        dpg.add_theme_color(dpg.mvPlotCol_Line, ref2_color, category=dpg.mvThemeCat_Plots)
                dpg.bind_item_theme(self.lh_ref2_tag, self._track_ui_resource(ref2_theme))
            with dpg.theme() as in_theme:
                with dpg.theme_component(dpg.mvLineSeries):
                    dpg.add_theme_color(dpg.mvPlotCol_Line, in_color, category=dpg.mvThemeCat_Plots)
            dpg.bind_item_theme(self.lh_in_tag, self._track_ui_resource(in_theme))
            with dpg.theme() as out_theme:
                with dpg.theme_component(dpg.mvLineSeries):
                    dpg.add_theme_color(dpg.mvPlotCol_Line, out_color, category=dpg.mvThemeCat_Plots)
                    dpg.add_theme_style(dpg.mvPlotStyleVar_LineWeight, 1.5, category=dpg.mvThemeCat_Plots)
            dpg.bind_item_theme(self.lh_out_tag, self._track_ui_resource(out_theme))

    def _update_level_history(self, ref_db=None, ref2_db=None):
        if not dpg.does_item_exist(self.lh_in_tag):
            return
        lo, hi = self._lh_db_floor, self._lh_db_ceil
        self._lh_in_hist.append(min(hi, max(lo, self._lvl_in_db)))
        self._lh_in_hist.pop(0)
        self._lh_out_hist.append(min(hi, max(lo, self._lvl_out_db)))
        self._lh_out_hist.pop(0)
        dpg.set_value(self.lh_in_tag, [self._lh_xs, list(self._lh_in_hist)])
        dpg.set_value(self.lh_out_tag, [self._lh_xs, list(self._lh_out_hist)])
        if ref_db is not None and dpg.does_item_exist(self.lh_ref_tag):
            dpg.set_value(self.lh_ref_tag, [[0, self._lh_points], [ref_db, ref_db]])
        if ref2_db is not None and dpg.does_item_exist(self.lh_ref2_tag):
            dpg.set_value(self.lh_ref2_tag, [[0, self._lh_points], [ref2_db, ref2_db]])
