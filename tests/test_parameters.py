"""Declarative node parameter and control-spec contracts."""

import pytest


def test_control_specs_draw_and_declared_params_roundtrip(dpg_context):
    import dearpygui.dearpygui as dpg
    from core.base_node import BaseNode
    from core.parameters import (
        CheckboxSpec, ComboSpec, InputFloatSpec, InputIntSpec,
        ParameterControlSpec, ParameterSpec, SliderSpec,
    )

    class ParameterNode(BaseNode):
        parameter_specs = (
            ParameterSpec("level", 0.5),
            ParameterSpec("enabled", True),
            ParameterSpec("mode", "Peak"),
            ParameterSpec("ratio", 2.0),
            ParameterSpec("voices", 4),
        )
        parameter_control_specs = (
            ParameterControlSpec("level", SliderSpec("Level", 0.0, 1.0)),
            ParameterControlSpec("enabled", CheckboxSpec("Enabled")),
            ParameterControlSpec("mode", ComboSpec("Mode", ("Peak", "RMS"))),
            ParameterControlSpec("ratio", InputFloatSpec("Ratio")),
            ParameterControlSpec("voices", InputIntSpec("Voices")),
        )

        def publish_outputs(self):
            return True

    node = ParameterNode("parameter_test")
    assert node._parameter_ui_adapter is None
    from ui.node_controls import ParameterUIAdapter
    assert isinstance(node._parameter_ui, ParameterUIAdapter)
    with dpg.window(tag="parameter_test_window"):
        for name in node._param_control_names:
            node._draw_declared_param(name)
            node._draw_declared_param(name, secondary=True)

    assert dpg.does_item_exist(node.level_slider_tag)
    assert dpg.does_item_exist(node.enabled_panel_slider_tag)
    assert node.serialize_params() == {
        "level": 0.5,
        "enabled": True,
        "mode": "Peak",
        "ratio": 2.0,
        "voices": 4,
    }

    node.apply_params({"level": 0.75, "enabled": False, "mode": "RMS", "ratio": 3, "voices": 6})
    assert node.level == 0.75
    assert node.enabled is False
    assert node.mode == "RMS"
    assert node.ratio == 3.0
    assert node.voices == 6
    assert dpg.get_value(node.level_slider_tag) == pytest.approx(0.75)
    assert dpg.get_value(node.enabled_panel_slider_tag) is False


def test_base_node_uses_panel_lifecycle_controller(make_node):
    from ui.node_panel import NodePanelController

    node = make_node("MIDI Piano Roll")
    assert node._node_panel_controller is None
    assert isinstance(node._panel_controller, NodePanelController)
    assert node.serialize_panel() is None
    node.open_panel(pos=(15, 25), size=(640, 360))
    panel = node.serialize_panel()
    assert panel is not None
    assert panel["size"] == [640.0, 360.0]


def test_parameter_specs_validate_names_defaults_and_values():
    from core.parameters import (
        CheckboxSpec, ComboSpec, ParameterControlSpec, ParameterSpec,
    )

    with pytest.raises(ValueError, match="valid identifier"):
        ParameterSpec("not valid", False)
    with pytest.raises(ValueError, match="must not be empty"):
        ComboSpec("Mode", ())

    with pytest.raises(ValueError, match="valid identifier"):
        ParameterSpec("not valid", 1.0)
    with pytest.raises(ValueError, match="valid identifier"):
        ParameterControlSpec("not valid", CheckboxSpec("Invalid"))


def test_backend_parameter_can_exist_without_ui_control():
    from core.base_node import BaseNode
    from core.parameters import ParameterSpec

    class HeadlessParameterNode(BaseNode):
        parameter_specs = (ParameterSpec("threshold", 0.5),)

        def publish_outputs(self):
            return True

    node = HeadlessParameterNode("headless_parameter")
    assert node.threshold == 0.5
    assert node.serialize_params() == {"threshold": 0.5}
    node.apply_params({"threshold": 1})
    assert node.threshold == 1.0
    assert node._param_control_names == []


def test_parameter_ui_publishes_without_direct_mutation(monkeypatch):
    from core.base_node import BaseNode
    from core.parameters import ParameterSpec
    from ui import node_controls

    class ParameterNode(BaseNode):
        parameter_specs = (ParameterSpec("level", 0.5),)

        def publish_outputs(self):
            return True

    calls = []
    publisher = type("Publisher", (), {
        "set_parameter": lambda self, *args, **kwargs: calls.append((args, kwargs)),
    })()
    monkeypatch.setattr(node_controls, "_graph_commands", publisher)
    node = ParameterNode("parameter_command_test")

    node._parameter_ui.apply_declared("level", 0.75)

    assert node.level == 0.5
    assert calls == [(
        ("parameter_command_test", "level", 0.75),
        {"edit_phase": node_controls.EditPhase.COMMIT},
    )]


def test_parameter_projection_does_not_overwrite_newer_owner_state():
    from core.base_node import BaseNode
    from core.parameters import ParameterSpec

    class ParameterNode(BaseNode):
        parameter_specs = (ParameterSpec("level", 0.5),)

        def publish_outputs(self):
            return True

    node = ParameterNode("parameter_projection_test")
    node._set_declared_param_state("level", 0.75)

    assert node._project_declared_param("level", 0.5) is False
    assert node.level == 0.75


def test_node_name_and_bake_callbacks_publish_without_direct_mutation(monkeypatch):
    from core.base_node import BaseNode
    from core.graph_contracts import CompileKind
    from ui import node_controls

    class CommandNode(BaseNode):
        def publish_outputs(self):
            return True

    calls = []

    class Publisher:
        def rename_node(self, *args):
            calls.append(("rename", *args))

        def request_compile(self, *args):
            calls.append(("compile", *args))

    monkeypatch.setattr(node_controls, "_graph_commands", Publisher())
    node = CommandNode("node_command_test")

    node._on_custom_name(None, "Lead")
    node._request_single_bake()

    assert node.custom_name == ""
    assert calls == [
        ("rename", "node_command_test", "Lead"),
        ("compile", CompileKind.BAKE_NODE, "node_command_test"),
    ]


def test_parameter_control_is_bound_separately(dpg_context):
    import dearpygui.dearpygui as dpg
    from core.base_node import BaseNode
    from core.parameters import ParameterControlSpec, ParameterSpec, SliderSpec

    class SplitParameterNode(BaseNode):
        parameter_specs = (ParameterSpec("level", 0.5),)
        parameter_control_specs = (
            ParameterControlSpec("level", SliderSpec("Level", 0.0, 1.0)),
        )

        def publish_outputs(self):
            return True

    node = SplitParameterNode("split_parameter")
    with dpg.window(tag="split_parameter_window"):
        node._draw_declared_param("level")
    assert dpg.does_item_exist(node.level_slider_tag)
    assert node.serialize_params() == {"level": 0.5}
