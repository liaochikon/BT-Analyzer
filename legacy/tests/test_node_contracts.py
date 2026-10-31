"""Cross-node contracts -- invariants that span MULTIPLE nodes, so they don't live in any single
node's test_node.py. Per-node behaviour lives next to each node in nodes/<node>/test_node.py.
"""

import numpy as np
import pytest


def test_processing_mode_contract_requires_valid_enum_and_hook():
    from dataclasses import FrozenInstanceError
    from core.base_node import BaseNode, NodeConfig, NodeProcessingMode

    with pytest.raises(TypeError, match="config must be a NodeConfig"):
        class InvalidConfigNode(BaseNode):
            config = "streaming"

            def publish_outputs(self):
                return True

    with pytest.raises(TypeError, match="config.processing=BAKED"):
        class BrokenBakedNode(BaseNode):
            config = NodeConfig(processing=NodeProcessingMode.BAKED)

            def publish_outputs(self):
                return True

    with pytest.raises(TypeError, match="config.processing=STREAMING"):
        class BrokenStreamingNode(BaseNode):
            config = NodeConfig(processing=NodeProcessingMode.STREAMING)

            def publish_outputs(self):
                return True

    config = NodeConfig(processing=NodeProcessingMode.STREAMING)
    with pytest.raises(FrozenInstanceError):
        config.realtime = True
    with pytest.raises(TypeError, match="NodeConfig.processing"):
        NodeConfig(processing="streaming")
    with pytest.raises(TypeError, match="NodeConfig.realtime"):
        NodeConfig(realtime=1)


def test_realtime_config_requires_frame_hook():
    from core.base_node import BaseNode, NodeConfig

    with pytest.raises(TypeError, match="config.realtime=True"):
        class BrokenRealtimeNode(BaseNode):
            config = NodeConfig(realtime=True)

            def publish_outputs(self):
                return True


def test_streamable_effects_output_finite_and_shaped(make_node):
    # Every streamable effect must return same-shape, finite audio for a normal block.
    block = (np.random.default_rng(1).standard_normal((512, 2)) * 0.3).astype(np.float32)
    for name in ("Gain", "EQ", "Compressor", "Limiter", "Noise Gate"):
        out = make_node(name)._process_block(block.copy())
        assert out.shape == block.shape, name
        assert np.isfinite(out).all(), name


def test_export_nodes_serialize_file_path(make_node):
    from core.base_node import NodeProcessingMode

    for name in ("Audio Export", "MIDI Export"):
        n = make_node(name)
        assert n.config.processing is NodeProcessingMode.BAKED
        n.file_path = "C:/out/f"
        assert n.serialize_extra().get("file_path") == "C:/out/f"


@pytest.mark.parametrize("node_name", ("Audio Export", "MIDI Export"))
def test_export_bake_updates_completion_state(make_node, node_name):
    node = make_node(node_name)
    progress = []
    calls = []
    node.export = lambda: calls.append("export") or True
    node._report_progress = progress.append

    assert node.bake() is True
    assert node.is_baked is True
    assert calls == ["export"]
    assert progress == [1.0]


@pytest.mark.parametrize(("node_name", "selected", "expected"), (
    ("Audio Export", "C:/out/mix", "C:/out/mix.wav"),
    ("MIDI Export", "C:/out/notes", "C:/out/notes.mid"),
))
def test_export_file_selection_only_commits_path(
        make_node, monkeypatch, node_name, selected, expected):
    node = make_node(node_name)
    calls = []
    node._parameter_ui_adapter = type("Adapter", (), {
        "apply_extra": lambda _self, *args: calls.append(("path", args)),
    })()
    node._file_selected_callback(None, {"file_path_name": selected})

    assert calls == [("path", ("file_path", expected))]


@pytest.mark.parametrize(("node_name", "module_name"), (
    ("Audio Export", "nodes.audio_export_node.node"),
    ("MIDI Export", "nodes.midi_export_node.node"),
))
def test_export_path_change_marks_heavy_stale(
        make_node, monkeypatch, node_name, module_name):
    import importlib
    from core.graph_contracts import NodeStateAction

    node = make_node(node_name)
    stale = []
    module = importlib.import_module(module_name)
    monkeypatch.setattr(
        module, "mark_structural_heavy_stale", stale.append)

    result = node.mutate_extra_state(
        "file_path", "C:/out/configured", action=NodeStateAction.SET)

    assert result == "C:/out/configured"
    assert stale == [node.node_tag]


@pytest.mark.parametrize(("node_name", "filename"), (
    ("Audio Export", "mix.wav"),
    ("MIDI Export", "notes.mid"),
))
def test_export_inspector_projects_selected_path(
        make_node, dpg_context, node_name, filename):
    from core.base_node import NodeUIMode

    node = make_node(node_name)
    assert node.config.ui is NodeUIMode.INSPECTOR

    parent_tag = f"export_inspector::{node_name}"
    with dpg_context.window(tag=parent_tag):
        node._draw_inspector()

    node.file_path = f"C:/out/{filename}"
    node.project_extra_state("file_path", node.file_path)

    assert dpg_context.get_value(node.inspector_file_text_tag) == filename
    dpg_context.delete_item(parent_tag)


def test_every_non_sink_node_declares_a_ui(node_registry):
    from core.base_node import NodeUIMode

    missing = []
    for node_class in set(node_registry.values()):
        node = node_class(f"ui_contract::{node_class.__name__}")
        if node.output_spec and node.config.ui is NodeUIMode.NONE:
            missing.append(node.display_name)

    assert missing == []


def test_node_library_categories_are_task_oriented(node_registry):
    from ui.ui import NODE_CATEGORY_ORDER

    expected = {
        "Beat Grid Output": "Input / Output",
        "MIDI to Audio": "Input / Output",
        "Oscilloscope": "Display",
        "Spectrum": "Display",
        "MIDI Piano Roll": "Display",
        "Guitar Fretboard": "Display",
        "Chord Chart": "Display",
        "Basic Pitch": "Analysis",
        "Audio Velocity Map": "Analysis",
        "Demucs v6": "Effects",
        "BS-RoFormer": "Effects",
    }

    assert {
        name: node_registry[name].category for name in expected
    } == expected
    assert "Beat Grid" not in node_registry
    assert "MIDI" not in NODE_CATEGORY_ORDER
    assert "AI" not in NODE_CATEGORY_ORDER
    assert "Display" in NODE_CATEGORY_ORDER


def test_every_inspector_builds_widgets(node_registry, dpg_context):
    from core.base_node import NodeUIMode

    parent_tag = "all_inspectors_contract"
    with dpg_context.window(tag=parent_tag):
        for node_class in set(node_registry.values()):
            if node_class.config.ui is NodeUIMode.NONE:
                continue
            node = node_class(f"inspector_contract::{node_class.__name__}")
            with dpg_context.group(tag=f"inspector_contract_group::{node_class.__name__}"):
                node._draw_inspector()

    dpg_context.delete_item(parent_tag)
