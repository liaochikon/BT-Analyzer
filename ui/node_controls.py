"""DearPyGui adapter for node parameter controls."""

import dearpygui.dearpygui as dpg

from core.graph_contracts import CompileKind, EditPhase, NodeStateAction
from core.parameters import (
    CheckboxSpec,
    ComboSpec,
    InputFloatSpec,
    InputIntSpec,
    SliderSpec,
)


_graph_commands = None


def bind_graph_commands(graph_commands) -> None:
    global _graph_commands
    _graph_commands = graph_commands


def _require_graph_commands():
    if _graph_commands is None:
        raise RuntimeError("graph command publisher is not bound")
    return _graph_commands


def request_node_compile(kind: CompileKind, node_tag: str):
    return _require_graph_commands().request_compile(kind, node_tag)


def request_node_rename(node, name: str):
    return _require_graph_commands().rename_node(node.node_tag, name)


def commit_workspace_gesture():
    from core.pipeline import capture_history_workspace
    workspace = capture_history_workspace()
    return _require_graph_commands().commit_workspace_gesture(workspace)


class ParameterUIAdapter:
    """Render and synchronize controls for one node runtime."""

    def __init__(self, node):
        self._node = node

    def apply_declared(
            self, name: str, value, *, edit_phase=EditPhase.COMMIT):
        return _require_graph_commands().set_parameter(
            self._node.node_tag,
            name,
            value,
            edit_phase=edit_phase,
        )

    def apply_extra(
            self, field: str, value=None, *, index=None,
            action=NodeStateAction.SET, edit_phase=EditPhase.COMMIT):
        return _require_graph_commands().set_node_state(
            self._node.node_tag,
            field,
            value,
            index=index,
            action=action,
            edit_phase=edit_phase,
        )

    def _bind_slider_gesture(self, tag, name):
        handler = self._node._track_ui_resource(dpg.add_item_handler_registry())
        dpg.add_item_deactivated_after_edit_handler(
            parent=handler,
            callback=lambda: self.apply_declared(
                name, dpg.get_value(tag), edit_phase=EditPhase.COMMIT),
        )
        dpg.bind_item_handler_registry(tag, handler)

    def draw_declared(self, name: str, *, secondary=False, width=None, scope=None):
        node = self._node
        spec = node._param_specs[name]
        control = node._param_controls[name]
        if scope is None:
            tag = node._param_widget_tags[name][1 if secondary else 0]
        else:
            tag = f"{name}_{scope}_control_{node.node_tag}"
            node._param_projection_tags[name].add(tag)
        value = getattr(node, name)
        callback = lambda sender, app_data, user_data=None: self.apply_declared(
            name,
            app_data,
            edit_phase=(
                EditPhase.UPDATE if isinstance(control, SliderSpec)
                else EditPhase.COMMIT),
        )

        def reset(sender=None, app_data=None, user_data=None):
            self.apply_declared(name, spec.default)

        with dpg.group(horizontal=True):
            dpg.add_button(label="R", width=22, callback=reset)
            if isinstance(control, SliderSpec):
                dpg.add_slider_float(
                    label=control.label, tag=tag, default_value=value,
                    min_value=control.minimum, max_value=control.maximum,
                    width=width or control.width, format=control.format, callback=callback)
                self._bind_slider_gesture(tag, name)
            elif isinstance(control, CheckboxSpec):
                dpg.add_checkbox(
                    label=control.label, tag=tag, default_value=value, callback=callback)
            elif isinstance(control, ComboSpec):
                dpg.add_combo(
                    control.items, label=control.label, tag=tag, default_value=value,
                    width=width or control.width, callback=callback)
            elif isinstance(control, InputFloatSpec):
                dpg.add_input_float(
                    label=control.label, tag=tag, default_value=value,
                    step=control.step, step_fast=control.step_fast,
                    width=width or control.width, format=control.format, callback=callback)
            elif isinstance(control, InputIntSpec):
                dpg.add_input_int(
                    label=control.label, tag=tag, default_value=value,
                    step=control.step, step_fast=control.step_fast,
                    width=width or control.width, callback=callback)
            else:
                raise TypeError(f"Unsupported control spec: {type(control).__name__}")

    def draw_slider(self, label: str, tag: str, default_value: float,
                    min_value: float, max_value: float, callback, reset_callback,
                    slider_width=120, format="%.2f", reset_value=None):
        reset_target = default_value if reset_value is None else reset_value

        def reset(sender=None, app_data=None, user_data=None):
            dpg.set_value(tag, reset_target)
            reset_callback(sender, app_data)

        with dpg.group(horizontal=True):
            dpg.add_button(label="R", width=22, callback=reset)
            dpg.add_slider_float(
                label=label, tag=tag,
                default_value=default_value,
                min_value=min_value, max_value=max_value,
                width=slider_width, format=format, callback=callback)

    def sync(self):
        node = self._node
        for name, tags in node._param_projection_tags.items():
            value = getattr(node, name)
            for tag in tags:
                if dpg.does_item_exist(tag):
                    dpg.set_value(tag, value)
        for key in list(vars(node).keys()):
            if not key.endswith("_default"):
                continue
            name = key[: -len("_default")]
            if name in node._param_specs or not hasattr(node, name):
                continue
            value = getattr(node, name)
            if not isinstance(value, (int, float, str, bool)):
                continue
            for tag_attr in (f"{name}_slider_tag", f"{name}_panel_slider_tag"):
                tag = getattr(node, tag_attr, None)
                if tag and dpg.does_item_exist(tag):
                    dpg.set_value(tag, value)
