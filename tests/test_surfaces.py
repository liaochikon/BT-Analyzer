import pytest
import numpy as np


def test_center_offset_respects_padding():
    from ui.surfaces import _center_offset

    assert _center_offset(500, 100, 16) == 200
    assert _center_offset(80, 100, 16) == 16


def test_surface_primitives_build_composable_containers(dpg_context):
    import dearpygui.dearpygui as dpg
    from ui.surfaces import CenterAxes, canvas_surface, centered_surface

    with dpg.window(tag="surface_test_window"):
        with canvas_surface(tag="surface_test_canvas"):
            with centered_surface(tag="surface_test_center", axes=CenterAxes.BOTH) as content_tag:
                dpg.add_text("Empty", tag="surface_test_content")

    assert dpg.does_item_exist("surface_test_canvas")
    assert dpg.does_item_exist("surface_test_center")
    assert dpg.get_item_parent("surface_test_center") == "surface_test_canvas"
    assert dpg.get_item_parent("surface_test_content") == content_tag

    dpg.delete_item("surface_test_window")


def test_centered_surface_registration_is_removed_with_parent(dpg_context):
    import dearpygui.dearpygui as dpg
    from ui import surfaces

    with dpg.window(tag="surface_cleanup_window"):
        with surfaces.centered_surface(tag="surface_cleanup_center"):
            dpg.add_text("Empty")

    assert "surface_cleanup_center" in surfaces._centered_surfaces
    dpg.delete_item("surface_cleanup_window")
    surfaces.update_surfaces()
    assert "surface_cleanup_center" not in surfaces._centered_surfaces


def test_centered_surface_rejects_invalid_axes(dpg_context):
    import dearpygui.dearpygui as dpg
    from ui.surfaces import centered_surface

    with dpg.window(tag="invalid_surface_test_window"):
        with pytest.raises(TypeError, match="CenterAxes"):
            with centered_surface(axes="both"):
                pass

    dpg.delete_item("invalid_surface_test_window")


def test_midi_piano_roll_switches_between_centered_empty_state_and_canvas(graph_editor):
    import dearpygui.dearpygui as dpg
    from core import DataPacket, DataType, NOTE_DTYPE
    from core import pipeline

    graph_editor.apply_graph({
        "nodes": [{"class": "MidiPianoRollNode", "pos": [0, 0], "params": {}}],
        "links": [],
    })
    node = next(iter(pipeline.GRAPH_STATE.nodes.values()))
    node.open_panel()
    assert dpg.get_item_configuration(node.plot_tag)["show"] is False
    assert dpg.get_item_configuration(node.empty_surface_tag)["show"] is True

    notes = np.zeros(1, dtype=NOTE_DTYPE)
    notes[0] = (60, 0.0, 1.0, 100)
    node.input_values[node.in_midi] = DataPacket(notes, DataType.MIDI, "notes-1")
    node.on_gui_frame()
    assert dpg.get_item_configuration(node.plot_tag)["show"] is True
    assert dpg.get_item_configuration(node.empty_surface_tag)["show"] is False

    node.input_values.pop(node.in_midi)
    node.on_gui_frame()
    assert dpg.get_item_configuration(node.plot_tag)["show"] is False
    assert dpg.get_item_configuration(node.empty_surface_tag)["show"] is True


def test_oscilloscope_switches_from_empty_state_to_canvas(make_node):
    import dearpygui.dearpygui as dpg

    node = make_node("Oscilloscope")
    node.open_panel()
    node.on_gui_frame()
    assert dpg.get_item_configuration(node.plot_tag)["show"] is False
    assert dpg.get_item_configuration(node.empty_surface_tag)["show"] is True

    node._processor.capture(np.zeros((node.WINDOW, 2), dtype=np.float32))
    node.on_gui_frame()
    assert dpg.get_item_configuration(node.plot_tag)["show"] is True
    assert dpg.get_item_configuration(node.empty_surface_tag)["show"] is False

    dpg.delete_item(node.panel_window_tag)


def test_spectrum_switches_from_empty_state_to_canvas(make_node, monkeypatch):
    import dearpygui.dearpygui as dpg
    from nodes._shared.spectrum import SpectrumSnapshot

    node = make_node("Spectrum")
    node.open_panel()
    monkeypatch.setattr(node._spectrum, "snapshot", lambda **kwargs: None)
    node.on_gui_frame()
    assert dpg.get_item_configuration(node.plot_tag)["show"] is False
    assert dpg.get_item_configuration(node.empty_surface_tag)["show"] is True

    snapshot = SpectrumSnapshot((100.0, 1000.0), (-20.0, -30.0))
    monkeypatch.setattr(node._spectrum, "snapshot", lambda **kwargs: snapshot)
    node.on_gui_frame()
    assert dpg.get_item_configuration(node.plot_tag)["show"] is True
    assert dpg.get_item_configuration(node.empty_surface_tag)["show"] is False

    dpg.delete_item(node.panel_window_tag)


def test_chord_chart_switches_between_empty_state_and_canvas(make_node):
    import dearpygui.dearpygui as dpg
    from core import DataPacket, DataType, NOTE_DTYPE

    node = make_node("Chord Chart")
    node.open_panel()
    assert dpg.does_item_exist(f"chordchart_controls_surface_{node.node_tag}")
    assert dpg.get_item_parent(node.drawlist_tag) == node.canvas_tag
    assert dpg.get_item_configuration(node.drawlist_tag)["show"] is False
    assert dpg.get_item_configuration(node.empty_surface_tag)["show"] is True

    notes = np.zeros(3, dtype=NOTE_DTYPE)
    notes["pitch"] = (60, 64, 67)
    notes["start"] = 0.0
    notes["end"] = 1.0
    notes["velocity"] = 100
    node.input_values[node.in_midi] = DataPacket(notes, DataType.MIDI, "chord-1")
    node.on_gui_frame()
    assert dpg.get_item_configuration(node.drawlist_tag)["show"] is True
    assert dpg.get_item_configuration(node.empty_surface_tag)["show"] is False

    node.input_values.pop(node.in_midi)
    node.on_gui_frame()
    assert dpg.get_item_configuration(node.drawlist_tag)["show"] is False
    assert dpg.get_item_configuration(node.empty_surface_tag)["show"] is True

    dpg.delete_item(node.panel_window_tag)


def test_guitar_fretboard_drawlist_uses_canvas_surface(make_node):
    import dearpygui.dearpygui as dpg

    node = make_node("Guitar Fretboard")
    node.open_panel()
    assert dpg.does_item_exist(f"fretboard_controls_surface_{node.node_tag}")
    assert dpg.does_item_exist(node.canvas_tag)
    assert dpg.get_item_parent(node.drawlist_tag) == node.canvas_tag
    dpg.delete_item(node.panel_window_tag)


def test_mixer_uses_centered_surface_without_custom_resize_handler(make_node):
    import dearpygui.dearpygui as dpg

    node = make_node("Mixer")
    node.open_panel()
    surface_tag = f"mixer_centered_surface_{node.node_tag}"
    assert dpg.does_item_exist(surface_tag)
    assert dpg.get_item_parent(f"mixer_content_{node.node_tag}") != node.panel_window_tag
    assert not dpg.does_item_exist(f"mixer_panel_handlers_{node.node_tag}")
    dpg.delete_item(node.panel_window_tag)