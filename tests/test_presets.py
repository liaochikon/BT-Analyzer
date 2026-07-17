"""Preset save/load round-trips: the "save a song" contract.

A preset saved "with parameters" is this app's song document -- it must restore the graph
(structure + links), every node's slider params, non-scalar state (file paths, mixer channel
state), the open panels' geometry, and the transport practice state (speed / trims / toggles /
A/B loop). A "template" preset (params off) must carry structure + panel layout but NONE of that
state. These tests lock both shapes through the real graph_presets + transport-globals code, run
headless via the graph_editor fixture (bare DPG context, no viewport).
"""

import numpy as np
import pytest
def test_transport_bar_binds_preset_globals_hook_from_owner_module():
    from core.graph_presets import register_globals_hooks
    from ui import transport_bar

    assert transport_bar.register_globals_hooks is register_globals_hooks



from core.transport import TRANSPORT
from core.playback_engine import ENGINE
from core.base_node import DataType, DataPacket
import ui.transport_bar as TB


def test_shipped_presets_decode_and_materialize(graph_editor, node_registry):
    import json
    from pathlib import Path

    from core.graph_documents import (
        PROJECT_DOCUMENT_SCHEMA_VERSION,
        ProjectDocumentCodec,
        materialize_graph_document,
        migrate_legacy_preset,
    )
    from core.pipeline import known_node_types

    preset_paths = sorted((Path(__file__).parents[1] / "presets").glob("*.json"))
    assert preset_paths

    for path in preset_paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema_version") == PROJECT_DOCUMENT_SCHEMA_VERSION and "graph" in data:
            decoded = ProjectDocumentCodec.from_dict(data)
        else:
            decoded = migrate_legacy_preset(data, graph_id=f"test.{path.stem}")
        assert decoded.ok, f"{path.name}: {decoded.diagnostics}"

        materialized = materialize_graph_document(
            decoded.document.graph, known_node_types())
        assert materialized.ok, f"{path.name}: {materialized.diagnostics}"


def _drain():
    # Apply queued transport commands the way the producer loop would (no running thread in tests).
    for cmd in TRANSPORT.drain_commands():
        ENGINE._apply_cmd(cmd)


# ===== graph structure + params round-trip =====

def test_graph_roundtrip_params(graph_editor, node_registry):
    from core import pipeline
    GP = graph_editor
    GP.apply_graph({"nodes": [{"class": "GainNode", "pos": [10, 20], "params": {"gain_db": -3.0}}],
                    "links": []})
    snap = GP.serialize_graph()
    assert snap["nodes"][0]["class"] == "GainNode"
    assert snap["nodes"][0]["params"]["gain_db"] == -3.0

    GP.clear_all_nodes()
    assert len(pipeline.GRAPH_STATE.nodes) == 0
    GP.apply_graph(snap)
    n = next(iter(pipeline.GRAPH_STATE.nodes.values()))
    assert n.gain_db == -3.0


def test_custom_name_roundtrip_and_fingerprint_independence(graph_editor):
    from core import pipeline
    GP = graph_editor
    GP.apply_graph({"nodes": [{
        "class": "GainNode", "custom_name": "Vocal Gain",
        "pos": [10, 20], "params": {"gain_db": -3.0},
    }], "links": []})
    node = next(iter(pipeline.GRAPH_STATE.nodes.values()))
    fingerprint = node.compute_fingerprint(["upstream"])

    assert node.title == "Vocal Gain"
    assert GP.serialize_graph()["nodes"][0]["custom_name"] == "Vocal Gain"

    node.set_custom_name("Lead Vocal")
    assert node.compute_fingerprint(["upstream"]) == fingerprint

    snap = GP.serialize_graph()
    GP.clear_all_nodes()
    GP.apply_graph(snap)
    assert next(iter(pipeline.GRAPH_STATE.nodes.values())).custom_name == "Lead Vocal"


def test_graph_roundtrip_links(graph_editor):
    from core import pipeline
    GP = graph_editor
    # Gain -> EQ (out_audio -> in_audio). Links reference node INDEX + pin suffix, so they survive
    # tag renumbering on reload.
    GP.apply_graph({
        "nodes": [
            {"class": "GainNode", "pos": [0, 0], "params": {}},
            {"class": "EqNode", "pos": [200, 0], "params": {}},
        ],
        "links": [[0, "_out_audio::AUDIO", 1, "_in_audio::AUDIO"]],
    })
    snap = GP.serialize_graph()
    assert len(snap["links"]) == 1
    GP.clear_all_nodes()
    GP.apply_graph(snap)
    assert len(pipeline.GRAPH_STATE.connections_by_id) == 1     # the wire came back


def test_graph_roundtrip_extra_file_path(graph_editor):
    from core import pipeline
    GP = graph_editor
    GP.apply_graph({"nodes": [{"class": "AudioInputNode", "pos": [0, 0], "params": {},
                               "extra": {"file_path": "C:/songs/tenki.mp3"}}], "links": []})
    n = next(iter(pipeline.GRAPH_STATE.nodes.values()))
    assert n.file_path == "C:/songs/tenki.mp3"
    # and it survives a re-serialize
    assert GP.serialize_graph()["nodes"][0]["extra"]["file_path"] == "C:/songs/tenki.mp3"


def test_graph_roundtrip_mixer_extra(graph_editor):
    from core import pipeline
    GP = graph_editor
    GP.apply_graph({"nodes": [{"class": "Mixer8Node", "pos": [0, 0], "params": {},
                               "extra": {"vols": [0.5] + [1.0] * 7,
                                         "mutes": [False] * 8, "solos": [False] * 8}}], "links": []})
    m = next(iter(pipeline.GRAPH_STATE.nodes.values()))
    assert m.vols[0] == pytest.approx(0.5)


def test_template_preset_keeps_mixer_labels_only(graph_editor):
    from core import pipeline

    graph_editor.apply_graph({
        "nodes": [{
            "class": "Mixer8Node",
            "pos": [0, 0],
            "params": {},
            "extra": {
                "vols": [0.5] + [1.0] * 7,
                "mutes": [True] + [False] * 7,
                "solos": [False] * 8,
                "labels": ["Vocals", "Guitar"] + [f"Ch {i}" for i in range(3, 9)],
            },
        }],
        "links": [],
    })

    template = graph_editor._strip_params(graph_editor.serialize_graph())

    assert template["nodes"][0]["extra"] == {
        "labels": list(pipeline.GRAPH_STATE.nodes.values())[0].labels,
    }
    assert "vols" not in template["nodes"][0]["extra"]
    assert "mutes" not in template["nodes"][0]["extra"]
    assert "solos" not in template["nodes"][0]["extra"]


# ===== template (params-off) strip =====

def test_strip_params_keeps_structure_drops_state(graph_editor):
    GP = graph_editor
    snap = {"nodes": [{"class": "GainNode", "pos": [5, 6], "params": {"gain_db": -3.0},
                       "custom_name": "Vocal Gain", "extra": {"x": 1}, "baked": "sig",
                       "panel": {"pos": [1, 2], "size": [3, 4]}}],
            "links": [[0, "_a", 0, "_b"]]}
    slim = GP._strip_params(snap)
    node = slim["nodes"][0]
    assert node["class"] == "GainNode"           # structure kept
    assert node["pos"] == [5, 6]                  # position kept
    assert node["custom_name"] == "Vocal Gain"   # preset purpose kept
    assert node["panel"] == {"pos": [1, 2], "size": [3, 4]}  # panel layout kept
    assert node["params"] == {}                  # params dropped
    assert "extra" not in node and "baked" not in node       # state dropped
    assert slim["links"] == snap["links"]        # links kept

    snap["nodes"][0]["pos"][0] = 999
    snap["links"][0][1] = "changed"
    assert node["pos"] == [5, 6]
    assert slim["links"][0][1] == "_a"


def test_project_template_combines_owner_graph_with_gui_workspace(
        graph_editor, monkeypatch):
    from core.graph_documents import Position, WindowDocument
    from core.pipeline import export_current_graph_document
    from ui import workspace_layout

    GP = graph_editor
    GP.apply_graph({
        "nodes": [{
            "class": "GainNode",
            "pos": [15, 25],
            "params": {"gain_db": -3.0},
            "custom_name": "Vocal Gain",
        }],
        "links": [],
    })
    graph = export_current_graph_document()
    monkeypatch.setattr(
        GP, "serialize_graph",
        lambda: (_ for _ in ()).throw(AssertionError("legacy capture used")),
    )
    monkeypatch.setattr(workspace_layout, "capture_windows", lambda: {
        "win_node_editor": WindowDocument(
            Position(20.0, 30.0), Position(900.0, 700.0), False),
    })
    monkeypatch.setattr(
        workspace_layout, "capture_dock_layout", lambda: "[Docking][Data]\nDockSpace ID=1")

    project = GP.build_preset_project_document(False, graph, "project_test")

    assert project.project_id == "project_test"
    assert project.graph.nodes[0].parameters == {}
    assert project.graph.nodes[0].extra == {}
    assert project.graph.nodes[0].display_name == "Vocal Gain"
    assert project.workspace.node_positions[graph.nodes[0].node_id].x == 15.0
    assert project.workspace.windows["win_node_editor"].visible is False
    assert project.workspace.dock_layout == "[Docking][Data]\nDockSpace ID=1"
    assert project.practice is None


def test_project_template_keeps_mixer_channel_names(graph_editor):
    from core.pipeline import export_current_graph_document

    graph_editor.apply_graph({
        "nodes": [{
            "class": "Mixer8Node",
            "pos": [0, 0],
            "params": {},
            "extra": {
                "vols": [0.5] + [1.0] * 7,
                "mutes": [True] + [False] * 7,
                "solos": [False] * 8,
                "labels": ["Vocals", "Guitar"] + [f"Ch {i}" for i in range(3, 9)],
            },
        }],
        "links": [],
    })

    project = graph_editor.build_preset_project_document(
        False, export_current_graph_document(), "mixer_template")

    assert dict(project.graph.nodes[0].extra) == {
        "labels": ("Vocals", "Guitar", "Ch 3", "Ch 4", "Ch 5", "Ch 6", "Ch 7", "Ch 8"),
    }


# ===== transport globals bundle (the practice state) =====

def test_globals_roundtrip_full(graph_editor):
    TRANSPORT.set_length(44100 * 8)
    _drain()
    TRANSPORT.set_loop_range(0.25, 0.6)
    TRANSPORT.set_loop_enabled(True)
    TB._last_rate = 0.7
    TRANSPORT.request_rate(0.7)
    TB._push_master(-6.0)
    TB._push_metro_vol(-12.0)
    _drain()

    g = TB.serialize_globals()
    assert g["speed"] == 0.7 and g["master_db"] == -6.0 and g["metro_db"] == -12.0
    assert g["loop_a"] == pytest.approx(0.25) and g["loop_b"] == pytest.approx(0.6)
    assert g["loop_enabled"] is True

    # Wipe, then restore from the bundle.
    TRANSPORT.clear_loop()
    _drain()
    TB._push_master(0.0)
    TB._push_metro_vol(0.0)
    TB.apply_globals(g)
    _drain()

    assert TRANSPORT.rate == pytest.approx(0.7)
    assert TRANSPORT.loop_a == pytest.approx(0.25) and TRANSPORT.loop_b == pytest.approx(0.6)
    assert TRANSPORT.loop_enabled is True
    assert ENGINE._master_gain == pytest.approx(10.0 ** (-6.0 / 20.0), abs=1e-4)
    assert ENGINE._metro_gain == pytest.approx(10.0 ** (-12.0 / 20.0), abs=1e-4)


def test_globals_no_loop_clears_leftover(graph_editor):
    # A bundle without loop keys must clear a loop left over from a previous graph.
    TRANSPORT.set_length(44100 * 8)
    TRANSPORT.set_loop_range(0.1, 0.2)
    TRANSPORT.set_loop_enabled(True)
    _drain()
    TB.apply_globals({"speed": 1.0})   # no loop_a/loop_b
    _drain()
    assert TRANSPORT.loop_a is None and TRANSPORT.loop_enabled is False


def test_globals_omits_loop_when_unset(graph_editor):
    # With no A/B set, serialize must not emit loop_a/loop_b (nothing to restore).
    TRANSPORT.clear_loop()
    _drain()
    g = TB.serialize_globals()
    assert "loop_a" not in g and "loop_b" not in g


# ===== transactional load: malformed presets must not destroy the current graph =====

def _write_preset(dirpath, name, data):
    import json
    import os
    path = os.path.join(dirpath, f"{name}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return path


def test_malformed_preset_leaves_graph_unchanged(graph_editor, tmp_path, monkeypatch):
    from core import pipeline
    GP = graph_editor
    monkeypatch.setattr(GP, "PRESETS_DIR", str(tmp_path))
    # Start with a real Gain node open.
    GP.apply_graph({"nodes": [{"class": "GainNode", "pos": [0, 0], "params": {"gain_db": -3.0}}],
                    "links": []})
    before = next(iter(pipeline.GRAPH_STATE.nodes.values()))

    # A structurally broken preset (nodes not a list) must be rejected up front.
    _write_preset(tmp_path, "bad", {"nodes": "not a list"})
    GP.load_preset("bad")

    # The Gain node is still exactly the one we had; the editor was never cleared.
    assert len(pipeline.GRAPH_STATE.nodes) == 1
    assert next(iter(pipeline.GRAPH_STATE.nodes.values())) is before


def test_bad_link_shape_preset_rejected(graph_editor, tmp_path, monkeypatch):
    from core import pipeline
    GP = graph_editor
    monkeypatch.setattr(GP, "PRESETS_DIR", str(tmp_path))
    GP.apply_graph({"nodes": [{"class": "GainNode", "pos": [0, 0], "params": {}}], "links": []})
    _write_preset(tmp_path, "badlink",
                  {"nodes": [{"class": "GainNode", "pos": [0, 0]}], "links": [[0, "_a", 0]]})
    GP.load_preset("badlink")
    # Rejected before clearing: original graph intact.
    assert len(pipeline.GRAPH_STATE.nodes) == 1


@pytest.mark.parametrize("custom_name", [123, "x" * 65])
def test_invalid_custom_name_preset_rejected(graph_editor, tmp_path, monkeypatch, custom_name):
    from core import pipeline
    GP = graph_editor
    monkeypatch.setattr(GP, "PRESETS_DIR", str(tmp_path))
    GP.apply_graph({"nodes": [{"class": "GainNode", "pos": [0, 0], "params": {}}], "links": []})
    before = next(iter(pipeline.GRAPH_STATE.nodes.values()))
    _write_preset(tmp_path, "badname", {
        "nodes": [{"class": "GainNode", "custom_name": custom_name, "pos": [0, 0]}],
        "links": [],
    })

    GP.load_preset("badname")

    assert next(iter(pipeline.GRAPH_STATE.nodes.values())) is before


def test_type_mismatch_link_skipped_but_nodes_load(graph_editor, tmp_path, monkeypatch):
    from core import pipeline
    GP = graph_editor
    monkeypatch.setattr(GP, "PRESETS_DIR", str(tmp_path))
    # A hand-edited link wiring an AUDIO output into a MIDI input: structurally valid preset, but
    # the connection validator drops the wire while still loading both nodes.
    _write_preset(tmp_path, "mismatch", {
        "nodes": [
            {"class": "GainNode", "pos": [0, 0], "params": {}},
            {"class": "MidiPianoRollNode", "pos": [200, 0], "params": {}},
        ],
        "links": [[0, "_out_audio::AUDIO", 1, "_in_midi::MIDI"]],
    })
    GP.load_preset("mismatch")
    assert len(pipeline.GRAPH_STATE.nodes) == 2      # both nodes created
    assert len(pipeline.GRAPH_STATE.connections_by_id) == 0     # the incompatible wire was skipped


def test_rollback_restores_previous_graph_on_apply_failure(graph_editor, tmp_path, monkeypatch):
    from core import pipeline
    GP = graph_editor
    monkeypatch.setattr(GP, "PRESETS_DIR", str(tmp_path))
    # Pre-load graph uses EQ; the failing preset uses Gain, so rolling back to EQ can't re-trip
    # the injected Gain failure.
    GP.apply_graph({"nodes": [{"class": "EqNode", "pos": [0, 0], "params": {}}], "links": []})

    gain_cls = GP._node_class_lookup()["GainNode"]

    def _boom(self):
        raise RuntimeError("draw exploded")

    monkeypatch.setattr(gain_cls, "draw_node", _boom)

    _write_preset(tmp_path, "explode",
                  {"nodes": [{"class": "GainNode", "pos": [0, 0], "params": {}}], "links": []})
    result = GP.load_preset("explode")

    # Rollback rebuilt the EQ graph exactly.
    assert len(pipeline.GRAPH_STATE.nodes) == 1
    assert type(next(iter(pipeline.GRAPH_STATE.nodes.values()))).__name__ == "EqNode"
    assert result.status.name == "ROLLED_BACK"


def test_globals_apply_failure_restores_graph_and_previous_globals(
        graph_editor, tmp_path, monkeypatch):
    from core import pipeline
    from core.persistence_contracts import ApplyStatus

    GP = graph_editor
    monkeypatch.setattr(GP, "PRESETS_DIR", str(tmp_path))
    GP.apply_graph({"nodes": [{"class": "EqNode", "pos": [0, 0], "params": {}}],
                    "links": []})
    current_globals = {"speed": 0.75}
    calls = 0

    def apply_globals(values):
        nonlocal calls
        calls += 1
        current_globals.clear()
        current_globals.update(values)
        if calls == 1:
            raise RuntimeError("candidate globals failed")

    monkeypatch.setattr(GP, "_globals_getter", lambda: dict(current_globals))
    monkeypatch.setattr(GP, "_globals_applier", apply_globals)
    _write_preset(tmp_path, "candidate", {
        "nodes": [{"class": "GainNode", "pos": [0, 0], "params": {}}],
        "links": [],
        "globals": {"speed": 1.5},
    })

    result = GP.load_preset("candidate")

    assert result.status is ApplyStatus.ROLLED_BACK
    assert type(next(iter(pipeline.GRAPH_STATE.nodes.values()))).__name__ == "EqNode"
    assert current_globals == {"speed": 0.75}


def test_globals_rollback_failure_returns_degraded(graph_editor, tmp_path, monkeypatch):
    from core.persistence_contracts import ApplyStatus

    GP = graph_editor
    monkeypatch.setattr(GP, "PRESETS_DIR", str(tmp_path))
    GP.apply_graph({"nodes": [{"class": "EqNode", "pos": [0, 0], "params": {}}],
                    "links": []})
    monkeypatch.setattr(GP, "_globals_getter", lambda: {"speed": 0.75})
    monkeypatch.setattr(
        GP,
        "_globals_applier",
        lambda _values: (_ for _ in ()).throw(RuntimeError("globals unavailable")),
    )
    _write_preset(tmp_path, "candidate", {
        "nodes": [{"class": "GainNode", "pos": [0, 0], "params": {}}],
        "links": [],
        "globals": {"speed": 1.5},
    })

    result = GP.load_preset("candidate")

    assert result.status is ApplyStatus.DEGRADED


def test_apply_result_is_immutable():
    from core.persistence_contracts import ApplyResult, ApplyStatus

    result = ApplyResult(ApplyStatus.APPLIED)
    with pytest.raises((AttributeError, TypeError)):
        result.status = ApplyStatus.REJECTED


def test_container_stack_balanced_after_apply_failure(graph_editor, monkeypatch):
    from core import pipeline
    from core.graph_presets import PresetApplyError
    GP = graph_editor
    gain_cls = GP._node_class_lookup()["GainNode"]
    monkeypatch.setattr(gain_cls, "draw_node",
                        lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(PresetApplyError):
        GP.apply_graph({"nodes": [{"class": "GainNode", "pos": [0, 0], "params": {}}], "links": []})
    # A balanced push/pop means the editor still accepts new nodes afterwards.
    GP.clear_all_nodes()
    monkeypatch.undo()
    GP.apply_graph({"nodes": [{"class": "GainNode", "pos": [0, 0], "params": {}}], "links": []})
    assert len(pipeline.GRAPH_STATE.nodes) == 1


# ===== atomic save: an interrupted write must not corrupt an existing preset =====

def _tmp_files(dirpath):
    import os
    return [f for f in os.listdir(dirpath) if f.endswith(".tmp")]


def test_save_preset_success_returns_true_no_tmp(graph_editor, tmp_path, monkeypatch):
    import os
    GP = graph_editor
    monkeypatch.setattr(GP, "PRESETS_DIR", str(tmp_path))
    GP.apply_graph({"nodes": [{"class": "GainNode", "pos": [0, 0], "params": {}}], "links": []})
    assert GP.save_preset("mysong") is True
    assert os.path.exists(os.path.join(str(tmp_path), "mysong.json"))
    assert _tmp_files(str(tmp_path)) == []      # temp consumed by os.replace


def test_save_preset_replace_failure_preserves_old_file(graph_editor, tmp_path, monkeypatch):
    import os
    GP = graph_editor
    monkeypatch.setattr(GP, "PRESETS_DIR", str(tmp_path))
    GP.apply_graph({"nodes": [{"class": "GainNode", "pos": [0, 0], "params": {}}], "links": []})
    assert GP.save_preset("mysong") is True
    target = os.path.join(str(tmp_path), "mysong.json")
    with open(target, encoding="utf-8") as fh:
        original = fh.read()

    # Change the graph, then force the atomic swap to fail: the on-disk preset must be untouched.
    GP.clear_all_nodes()
    GP.apply_graph({"nodes": [{"class": "EqNode", "pos": [0, 0], "params": {}}], "links": []})
    monkeypatch.setattr(GP.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    assert GP.save_preset("mysong") is False

    with open(target, encoding="utf-8") as fh:
        assert fh.read() == original            # old preset intact
    assert _tmp_files(str(tmp_path)) == []       # temp cleaned up on the failure path


def test_save_preset_dump_failure_returns_false(graph_editor, tmp_path, monkeypatch):
    GP = graph_editor
    monkeypatch.setattr(GP, "PRESETS_DIR", str(tmp_path))
    GP.apply_graph({"nodes": [{"class": "GainNode", "pos": [0, 0], "params": {}}], "links": []})
    monkeypatch.setattr(GP.json, "dump", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    assert GP.save_preset("mysong") is False
    assert _tmp_files(str(tmp_path)) == []


def test_save_preset_refuses_during_compile(graph_editor, tmp_path, monkeypatch):
    import os
    import core.pipeline as PIPE
    GP = graph_editor
    monkeypatch.setattr(GP, "PRESETS_DIR", str(tmp_path))
    monkeypatch.setattr(PIPE, "is_compile_busy", lambda: True)
    assert GP.save_preset("busy") is False
    assert not os.path.exists(os.path.join(str(tmp_path), "busy.json"))


def test_save_preset_serializer_typeerror_returns_false(graph_editor, tmp_path, monkeypatch):
    import os
    GP = graph_editor
    monkeypatch.setattr(GP, "PRESETS_DIR", str(tmp_path))
    GP.apply_graph({"nodes": [{"class": "GainNode", "pos": [0, 0], "params": {}}], "links": []})
    assert GP.save_preset("song") is True
    target = os.path.join(str(tmp_path), "song.json")
    with open(target, encoding="utf-8") as fh:
        original = fh.read()

    # A plugin serializer emitting a non-JSON value makes json.dump raise TypeError. That must
    # become a clean save failure (old file intact, temp cleaned), not an escaping exception.
    monkeypatch.setattr(GP.json, "dump",
                        lambda *a, **k: (_ for _ in ()).throw(TypeError("not serializable")))
    assert GP.save_preset("song") is False
    with open(target, encoding="utf-8") as fh:
        assert fh.read() == original
    assert _tmp_files(str(tmp_path)) == []


def test_save_preset_directory_failure_returns_false(graph_editor, tmp_path, monkeypatch):
    GP = graph_editor
    monkeypatch.setattr(GP, "PRESETS_DIR", str(tmp_path / "presets"))
    monkeypatch.setattr(GP.os, "makedirs", lambda *a, **k: (_ for _ in ()).throw(PermissionError("denied")))

    assert GP.save_preset("song") is False


def test_load_preset_snapshot_failure_preserves_graph(graph_editor, tmp_path, monkeypatch):
    from core import pipeline
    GP = graph_editor
    monkeypatch.setattr(GP, "PRESETS_DIR", str(tmp_path))
    GP.apply_graph({"nodes": [{"class": "GainNode", "pos": [0, 0], "params": {}}], "links": []})
    original_tags = set(pipeline.GRAPH_STATE.nodes)
    _write_preset(tmp_path, "replacement", {"nodes": [], "links": []})
    monkeypatch.setattr(GP, "serialize_graph", lambda: (_ for _ in ()).throw(RuntimeError("bad plugin")))

    GP.load_preset("replacement")

    assert set(pipeline.GRAPH_STATE.nodes) == original_tags


# ===== partial-draw cleanup: a node that fails mid-draw must leave no orphan DPG item =====

def test_orphan_node_item_cleaned_when_draw_fails_after_creating_item(graph_editor, tmp_path, monkeypatch):
    import dearpygui.dearpygui as dpg
    from core import pipeline
    GP = graph_editor
    monkeypatch.setattr(GP, "PRESETS_DIR", str(tmp_path))
    # Pre-load graph uses EQ so the rollback can't re-trip the injected Gain failure.
    GP.apply_graph({"nodes": [{"class": "EqNode", "pos": [0, 0], "params": {}}], "links": []})

    gain_cls = GP._node_class_lookup()["GainNode"]
    captured = {}

    def _boom(self, extra):
        # Runs AFTER draw_node() has already created the dpg.node item, so this exercises the
        # "item exists but node never registered" orphan path (not the immediate-throw path).
        captured["tag"] = self.node_tag
        raise RuntimeError("apply_extra exploded")

    monkeypatch.setattr(gain_cls, "apply_extra", _boom)

    _write_preset(tmp_path, "explode",
                  {"nodes": [{"class": "GainNode", "pos": [0, 0], "params": {}, "extra": {"x": 1}}],
                   "links": []})
    GP.load_preset("explode")

    assert captured.get("tag") is not None
    assert not dpg.does_item_exist(captured["tag"])   # the orphan node item was deleted
    # Rollback rebuilt the EQ graph exactly.
    assert len(pipeline.GRAPH_STATE.nodes) == 1
    assert type(next(iter(pipeline.GRAPH_STATE.nodes.values()))).__name__ == "EqNode"



