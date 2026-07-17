"""Pedal Workspace projection and BaseNode inspector contract."""

import pytest


@pytest.fixture
def bound_rename_commands():
    from core.pipeline import runtime_node_view
    from ui import node_controls

    class Commands:
        @staticmethod
        def rename_node(node_tag, name):
            node = runtime_node_view(node_tag)
            return None if node is None else node.set_custom_name(name)

        @staticmethod
        def set_parameter(node_tag, name, value, *, edit_phase=None):
            node = runtime_node_view(node_tag)
            return None if node is None else node._set_declared_param(name, value)

    node_controls.bind_graph_commands(Commands())
    try:
        yield
    finally:
        node_controls.bind_graph_commands(None)


def test_gain_is_projected_into_workspace(graph_editor):
    import dearpygui.dearpygui as dpg
    from ui import pedal_workspace as PW

    if not dpg.does_item_exist(PW.WINDOW_TAG):
        PW.ui_create_pedal_workspace()
    graph_editor.apply_graph({
        "nodes": [{"class": "GainNode", "pos": [0, 0], "params": {}}],
        "links": [],
    })
    PW.update_pedal_workspace(force=True)

    node = PW.inspector_nodes()[0]
    assert type(node).__name__ == "GainNode"
    assert dpg.does_item_exist(f"pedal_tab::{node.node_tag}")
    assert dpg.does_item_exist(f"pedal_fixed_layout::{node.node_tag}")
    assert dpg.does_item_exist(node.gain_db_panel_slider_tag)
    assert dpg.does_item_exist(node.bypass_panel_slider_tag)
    name_tag = f"custom_name_input::{node.node_tag}"
    centered_tag = f"pedal_fixed_layout::{node.node_tag}"
    assert dpg.get_item_parent(name_tag) != centered_tag
    assert dpg.get_item_parent(centered_tag) == f"pedal_panel_body::{node.node_tag}"
    content_tag = f"pedal_content::{node.node_tag}"
    child_types = {
        dpg.get_item_type(item).rsplit("::", 1)[-1]
        for item in dpg.get_item_children(content_tag, 1) or []
    }
    assert "mvSeparator" not in child_types

    node._set_declared_param("bypass", True)
    assert dpg.get_value(node.bypass_slider_tag) is True
    assert dpg.get_value(node.bypass_panel_slider_tag) is True


def test_custom_name_updates_graph_and_workspace_titles(
    graph_editor, bound_rename_commands):
    import dearpygui.dearpygui as dpg
    from ui import pedal_workspace as PW

    if not dpg.does_item_exist(PW.WINDOW_TAG):
        PW.ui_create_pedal_workspace()
    graph_editor.apply_graph({
        "nodes": [{"class": "GainNode", "pos": [0, 0], "params": {}}],
        "links": [],
    })
    PW.update_pedal_workspace(force=True)
    node = PW.inspector_nodes()[0]
    input_tag = f"custom_name_input::{node.node_tag}"

    callback = dpg.get_item_callback(input_tag)
    callback(input_tag, "  Vocal Gain  ", None)

    assert node.custom_name == "Vocal Gain"
    assert node.title == "Vocal Gain"
    assert dpg.get_item_label(node.node_tag) == "Vocal Gain"
    assert dpg.get_item_label(f"pedal_tab::{node.node_tag}") == "Vocal Gain"


def test_custom_name_commits_when_input_loses_focus_after_edit(
    graph_editor, bound_rename_commands):
    import dearpygui.dearpygui as dpg
    from ui import pedal_workspace as PW

    if not dpg.does_item_exist(PW.WINDOW_TAG):
        PW.ui_create_pedal_workspace()
    graph_editor.apply_graph({
        "nodes": [{"class": "GainNode", "pos": [0, 0], "params": {}}],
        "links": [],
    })
    PW.update_pedal_workspace(force=True)
    node = PW.inspector_nodes()[0]
    input_tag = f"custom_name_input::{node.node_tag}"
    handler_tag = f"custom_name_commit_handler::{node.node_tag}"

    dpg.set_value(input_tag, "Rhythm Guitar")
    callback = dpg.get_item_callback(dpg.get_item_children(handler_tag, 1)[0])
    callback(handler_tag, None, None)

    assert node.custom_name == "Rhythm Guitar"
    assert dpg.get_item_label(node.node_tag) == "Rhythm Guitar"
    assert dpg.get_item_label(f"pedal_tab::{node.node_tag}") == "Rhythm Guitar"


def test_sink_can_be_named_through_standard_workspace_metadata(
    graph_editor, bound_rename_commands):
    import dearpygui.dearpygui as dpg
    from ui import pedal_workspace as PW

    if not dpg.does_item_exist(PW.WINDOW_TAG):
        PW.ui_create_pedal_workspace()
    graph_editor.apply_graph({
        "nodes": [{"class": "AudioOutputNode", "pos": [0, 0], "params": {}}],
        "links": [],
    })
    PW.update_pedal_workspace(force=True)
    node = PW.inspector_nodes()[0]
    input_tag = f"custom_name_input::{node.node_tag}"

    assert dpg.does_item_exist(input_tag)
    dpg.get_item_callback(input_tag)(input_tag, "Practice Output", None)
    assert node.title == "Practice Output"
    assert dpg.get_item_label(node.node_tag) == "Practice Output"


def test_standard_inspector_does_not_require_custom_draw_hook():
    from core.base_node import BaseNode, NodeConfig, NodeUIMode

    class StandardInspectorNode(BaseNode):
        config = NodeConfig(ui=NodeUIMode.INSPECTOR)

        def publish_outputs(self):
            return True

    assert StandardInspectorNode._draw_inspector is BaseNode._draw_inspector


def test_parameter_dsp_nodes_use_inspector_not_independent_panels(node_registry):
    from core.base_node import NodeUIMode

    for display_name in ("Gain", "EQ", "Compressor", "Limiter", "Noise Gate"):
        node_class = node_registry[display_name]
        assert node_class.config.ui is NodeUIMode.INSPECTOR


def test_visual_workspace_nodes_offer_inspector_and_independent_panel(node_registry):
    from core.base_node import NodeUIMode

    for display_name in ("Mixer", "Guitar Fretboard", "MIDI Piano Roll", "Spectrum", "Oscilloscope"):
        node_class = node_registry[display_name]
        assert node_class.config.ui is NodeUIMode.INSPECTOR_PANEL


def test_panel_node_workspace_button_opens_independent_panel(graph_editor):
    import dearpygui.dearpygui as dpg
    from ui import pedal_workspace as PW

    if not dpg.does_item_exist(PW.WINDOW_TAG):
        PW.ui_create_pedal_workspace()
    graph_editor.apply_graph({
        "nodes": [{"class": "MidiPianoRollNode", "pos": [0, 0], "params": {}}],
        "links": [],
    })
    PW.update_pedal_workspace(force=True)

    node = PW.inspector_nodes()[0]
    node.set_custom_name("Practice Piano Roll")
    button_tag = f"inspector_open_panel::{node.node_tag}"
    assert dpg.does_item_exist(f"pedal_tab::{node.node_tag}")
    assert dpg.does_item_exist(button_tag)
    assert dpg.does_item_exist(node.plot_tag)

    callback = dpg.get_item_callback(button_tag)
    callback(button_tag, None, None)
    assert dpg.does_item_exist(node.panel_window_tag)
    assert dpg.get_item_label(node.panel_window_tag) == "Practice Piano Roll"
    assert dpg.does_item_exist(button_tag)
    assert not (dpg.get_item_children(f"pedal_panel_body::{node.node_tag}", 1) or [])


@pytest.mark.parametrize(
    ("display_name", "panel_control", "panel_content"),
    (
        (
            "Guitar Fretboard",
            lambda node: f"num_frets_panel_control_{node.node_tag}",
            lambda node: node.drawlist_tag,
        ),
        (
            "Chord Chart",
            lambda node: f"window_seconds_panel_control_{node.node_tag}",
            lambda node: node.drawlist_tag,
        ),
    ),
)
def test_visual_panel_reopens_with_workspace_inspector_alive(
        graph_editor, display_name, panel_control, panel_content):
    import dearpygui.dearpygui as dpg
    from ui import pedal_workspace as PW

    if not dpg.does_item_exist(PW.WINDOW_TAG):
        PW.ui_create_pedal_workspace()
    graph_editor.clear_all_nodes()
    graph_editor.apply_graph({
        "nodes": [{"class": display_name.replace(" ", "") + "Node", "pos": [0, 0], "params": {}}],
        "links": [],
    })
    PW.update_pedal_workspace(force=True)
    node = PW.inspector_nodes()[0]
    assert dpg.does_item_exist(panel_control(node))
    assert dpg.does_item_exist(panel_content(node))
    for _ in range(2):
        node.open_panel()
        assert dpg.does_item_exist(panel_control(node))
        assert dpg.does_item_exist(panel_content(node))
        node._panel_controller.recall()
        assert not dpg.does_item_exist(node.panel_window_tag)
        assert dpg.does_item_exist(panel_control(node))
        assert dpg.does_item_exist(panel_content(node))


def test_tabs_follow_graph_membership(graph_editor):
    import dearpygui.dearpygui as dpg
    from ui import pedal_workspace as PW

    graph_editor.clear_all_nodes()
    graph_editor.apply_graph({
        "nodes": [
            {"class": "GainNode", "pos": [0, 0], "params": {}},
            {"class": "GainNode", "pos": [200, 0], "params": {}},
        ],
        "links": [],
    })
    PW.update_pedal_workspace(force=True)
    first, second = PW.inspector_nodes()
    assert dpg.get_item_label(f"pedal_tab::{first.node_tag}") == "Gain 1"
    assert dpg.get_item_label(f"pedal_tab::{second.node_tag}") == "Gain 2"

    graph_editor.clear_all_nodes()
    PW.update_pedal_workspace()
    assert PW.inspector_nodes() == []
    assert not dpg.does_item_exist(f"pedal_tab::{first.node_tag}")
    assert not dpg.does_item_exist(f"pedal_tab::{second.node_tag}")


def test_workspace_rebinds_tab_when_replacement_reuses_node_id(graph_editor, monkeypatch):
    import dearpygui.dearpygui as dpg
    from ui import pedal_workspace as PW

    if not dpg.does_item_exist(PW.WINDOW_TAG):
        PW.ui_create_pedal_workspace()

    graph_editor.apply_graph({
        "nodes": [{"class": "GainNode", "pos": [0, 0], "params": {}}],
        "links": [],
    })
    PW.update_pedal_workspace(force=True)
    previous = PW.inspector_nodes()[0]
    node_id = previous.node_tag

    previous._clear_ui_resources()
    replacement = type(previous)(node_id)
    monkeypatch.setattr(PW, "inspector_nodes", lambda: [replacement])
    assert replacement is not previous

    PW.update_pedal_workspace()

    assert PW._rendered_nodes[node_id] is replacement
    assert dpg.does_item_exist(f"custom_name_input::{node_id}")


def test_workspace_rebinds_when_tab_alias_outlives_alias_lookup(
        graph_editor, monkeypatch):
    import dearpygui.dearpygui as dpg
    from ui import pedal_workspace as PW

    if not dpg.does_item_exist(PW.WINDOW_TAG):
        PW.ui_create_pedal_workspace()

    graph_editor.apply_graph({
        "nodes": [{"class": "GainNode", "pos": [0, 0], "params": {}}],
        "links": [],
    })
    PW.update_pedal_workspace(force=True)
    previous = PW.inspector_nodes()[0]
    node_id = previous.node_tag
    tab_tag = f"pedal_tab::{node_id}"
    tab_id = dpg.get_alias_id(tab_tag)
    replacement = type(previous)(node_id)
    monkeypatch.setattr(PW, "inspector_nodes", lambda: [replacement])
    real_does_item_exist = dpg.does_item_exist
    monkeypatch.setattr(
        dpg,
        "does_item_exist",
        lambda item: False if item == tab_tag else real_does_item_exist(item),
    )

    PW.update_pedal_workspace()

    assert PW._rendered_nodes[node_id] is replacement
    assert dpg.get_alias_id(tab_tag) != tab_id
    assert dpg.does_item_exist(f"custom_name_input::{node_id}")


def test_spectrum_reuses_owned_themes_across_panel_transitions(graph_editor):
    import dearpygui.dearpygui as dpg
    from ui import pedal_workspace as PW

    if not dpg.does_item_exist(PW.WINDOW_TAG):
        PW.ui_create_pedal_workspace()
    graph_editor.apply_graph({
        "nodes": [{"class": "SpectrumNode", "pos": [0, 0], "params": {}}],
        "links": [],
    })
    PW.update_pedal_workspace(force=True)
    node = PW.inspector_nodes()[0]
    expected = {node.fill_theme_tag, node.line_theme_tag, node.octave_theme_tag}

    assert expected <= node._ui_resource_tags
    for _ in range(3):
        node.open_panel()
        node._panel_controller.recall()

    assert expected <= node._ui_resource_tags
    assert all(dpg.does_item_exist(tag) for tag in expected)


def test_fretboard_fingering_radio_tracks_domain_mode(
    graph_editor, bound_rename_commands):
    import dearpygui.dearpygui as dpg
    from ui import pedal_workspace as PW

    if not dpg.does_item_exist(PW.WINDOW_TAG):
        PW.ui_create_pedal_workspace()
    graph_editor.apply_graph({
        "nodes": [{"class": "GuitarFretboardNode", "pos": [0, 0], "params": {}}],
        "links": [],
    })
    PW.update_pedal_workspace(force=True)
    node = PW.inspector_nodes()[0]
    tag = node.fingering_mode_panel_slider_tag
    callback = dpg.get_item_callback(tag)

    callback(tag, node.MODE_SUGGEST)
    assert node.fingering_mode == 1.0
    assert dpg.get_value(tag) == node.MODE_SUGGEST
    assert node._last_pos == -1.0

    callback(tag, node.MODE_ALL)
    assert node.fingering_mode == 0.0
    assert dpg.get_value(tag) == node.MODE_ALL

    node.open_panel()
    assert dpg.get_value(tag) == node.MODE_ALL
    node._panel_controller.recall()


def test_fretboard_pitch_filter_controls_use_sliders(
        graph_editor, bound_rename_commands):
    import dearpygui.dearpygui as dpg
    from ui import pedal_workspace as PW

    if not dpg.does_item_exist(PW.WINDOW_TAG):
        PW.ui_create_pedal_workspace()
    graph_editor.apply_graph({
        "nodes": [{"class": "GuitarFretboardNode", "pos": [0, 0], "params": {}}],
        "links": [],
    })
    PW.update_pedal_workspace(force=True)
    node = PW.inspector_nodes()[0]
    min_tag = f"pitch_min_panel_control_{node.node_tag}"
    max_tag = f"pitch_max_panel_control_{node.node_tag}"

    assert dpg.get_item_type(min_tag) == "mvAppItemType::mvSliderFloat"
    assert dpg.get_item_type(max_tag) == "mvAppItemType::mvSliderFloat"
    assert dpg.get_value(min_tag) == 40.0
    assert dpg.get_value(max_tag) == 88.0
    assert dpg.get_item_configuration(min_tag)["min_value"] == 40.0
    assert dpg.get_item_configuration(min_tag)["max_value"] == 88.0
    assert dpg.get_item_configuration(max_tag)["min_value"] == 40.0
    assert dpg.get_item_configuration(max_tag)["max_value"] == 88.0

    dpg.get_item_callback(min_tag)(min_tag, 20.0)
    dpg.get_item_callback(max_tag)(max_tag, 96.0)

    assert node.pitch_min == 40.0
    assert node.pitch_max == 88.0

def test_workspace_reopens_from_latest_snapshot_without_mutating_owner(graph_editor):
    import dearpygui.dearpygui as dpg
    from core.pipeline import graph_snapshot, runtime_node_view
    from ui import pedal_workspace as PW

    graph_editor.apply_graph({
        "nodes": [{"class": "GainNode", "pos": [0, 0], "params": {}}],
        "links": [],
    })
    PW.update_pedal_workspace(force=True)
    node = PW.inspector_nodes()[0]
    before = graph_snapshot()

    PW.bind_graph_projection(None, None)
    assert not dpg.does_item_exist(f"pedal_tab::{node.node_tag}")

    PW.bind_graph_projection(graph_snapshot, runtime_node_view)

    after = graph_snapshot()
    assert after is before
    assert dpg.does_item_exist(f"pedal_tab::{node.node_tag}")


def test_incremental_sync_preserves_selection_and_item_count(graph_editor):
    import dearpygui.dearpygui as dpg
    from ui import pedal_workspace as PW

    graph_editor.apply_graph({
        "nodes": [{"class": "EqNode", "pos": [0, 0], "params": {}}],
        "links": [],
    })
    PW.update_pedal_workspace()
    first = PW.inspector_nodes()[0]
    first_tab = f"pedal_tab::{first.node_tag}"
    dpg.set_value(PW.TAB_BAR_TAG, first_tab)
    item_count = len(dpg.get_all_items())

    for _ in range(5):
        PW.update_pedal_workspace(force=True)

    assert len(dpg.get_all_items()) == item_count
    assert dpg.get_value(PW.TAB_BAR_TAG) == first_tab


def test_failed_inspector_can_retry_without_partial_resources(graph_editor, monkeypatch):
    import dearpygui.dearpygui as dpg
    from ui import pedal_workspace as PW

    graph_editor.apply_graph({
        "nodes": [{"class": "GainNode", "pos": [0, 0], "params": {}}],
        "links": [],
    })
    node = PW.inspector_nodes()[0]
    original_draw = node._draw_inspector
    monkeypatch.setattr(node, "_draw_inspector", lambda: (_ for _ in ()).throw(RuntimeError("broken")))
    PW.update_pedal_workspace()
    assert not dpg.does_item_exist(node.gain_db_panel_slider_tag)

    monkeypatch.setattr(node, "_draw_inspector", original_draw)
    PW._retry_inspector(node.node_tag)
    assert dpg.does_item_exist(node.gain_db_panel_slider_tag)


def test_declared_slider_commits_history_once_when_released(graph_editor):
    import dearpygui.dearpygui as dpg
    from ui import node_controls
    from ui import pedal_workspace as PW

    class Commands:
        def __init__(self):
            self.calls = []

        def set_parameter(self, node_tag, name, value, *, edit_phase):
            self.calls.append((node_tag, name, value, edit_phase))

    if not dpg.does_item_exist(PW.WINDOW_TAG):
        PW.ui_create_pedal_workspace()
    commands = Commands()
    node_controls.bind_graph_commands(commands)
    try:
        graph_editor.apply_graph({
            "nodes": [{"class": "GainNode", "pos": [0, 0], "params": {}}],
            "links": [],
        })
        PW.update_pedal_workspace(force=True)
        node = PW.inspector_nodes()[0]
        tag = node.gain_db_panel_slider_tag
        callback = dpg.get_item_callback(tag)
        callback(tag, -1.0)
        callback(tag, -2.0)
        callback(tag, -3.0)

        handler = dpg.get_item_info(tag)["handlers"]
        release_handler = dpg.get_item_children(handler, slot=1)[0]
        dpg.set_value(tag, -3.0)
        dpg.get_item_callback(release_handler)()

        assert [call[3] for call in commands.calls] == [
            node_controls.EditPhase.UPDATE,
            node_controls.EditPhase.UPDATE,
            node_controls.EditPhase.UPDATE,
            node_controls.EditPhase.COMMIT,
        ]
        assert commands.calls[-1][2] == -3.0
    finally:
        node_controls.bind_graph_commands(None)


def test_mixer_fader_commits_graph_history_once_when_released(graph_editor):
    import dearpygui.dearpygui as dpg
    from ui import node_controls
    from ui import pedal_workspace as PW

    class Commands:
        def __init__(self):
            self.calls = []

        def set_node_state(
                self, node_tag, field, value=None, *, index=None,
                action=None, edit_phase=None):
            self.calls.append(
                (node_tag, field, value, index, action, edit_phase))

    if not dpg.does_item_exist(PW.WINDOW_TAG):
        PW.ui_create_pedal_workspace()
    commands = Commands()
    node_controls.bind_graph_commands(commands)
    try:
        graph_editor.apply_graph({
            "nodes": [{"class": "Mixer8Node", "pos": [0, 0], "params": {}}],
            "links": [],
        })
        PW.update_pedal_workspace(force=True)
        node = PW.inspector_nodes()[0]
        tag = f"mixer_vol_{node.node_tag}_0"
        callback = dpg.get_item_callback(tag)
        callback(tag, -3.0, 0)
        callback(tag, -6.0, 0)
        callback(tag, -9.0, 0)

        assert [call[5] for call in commands.calls] == [
            node_controls.EditPhase.UPDATE,
            node_controls.EditPhase.UPDATE,
            node_controls.EditPhase.UPDATE,
        ]
        handler = dpg.get_item_info(tag)["handlers"]
        release_handler = dpg.get_item_children(handler, slot=1)[0]
        dpg.set_value(tag, -9.0)
        dpg.get_item_callback(release_handler)()

        assert len(commands.calls) == 4
        assert commands.calls[-1] == (
            node.node_tag,
            "volume",
            -9.0,
            0,
            node_controls.NodeStateAction.SET,
            node_controls.EditPhase.COMMIT,
        )

        node._on_mute("", None, 1)
        node._on_solo("", None, 2)
        node._on_reset("", None, 3)
        node._on_label("", " Vocals ", 4)
        assert [(call[1], call[3], call[4], call[5]) for call in commands.calls[-4:]] == [
            ("mute", 1, node_controls.NodeStateAction.TOGGLE,
             node_controls.EditPhase.COMMIT),
            ("solo", 2, node_controls.NodeStateAction.TOGGLE,
             node_controls.EditPhase.COMMIT),
            ("volume", 3, node_controls.NodeStateAction.RESET,
             node_controls.EditPhase.COMMIT),
            ("label", 4, node_controls.NodeStateAction.SET,
             node_controls.EditPhase.COMMIT),
        ]
    finally:
        node_controls.bind_graph_commands(None)