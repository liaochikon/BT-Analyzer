import json

import dearpygui.dearpygui as dpg
import numpy as np
import pytest

from tests.runtime_sim import (
    Analyze,
    Connect,
    ConnectionAbsent,
    ConnectionExists,
    DeleteNode,
    Disconnect,
    GraphHasNodes,
    LoadPreset,
    MoveNode,
    NodeParameter,
    NodePosition,
    Redo,
    RenameNode,
    RuntimeHarness,
    SavePreset,
    Scenario,
    SeededScenarioGenerator,
    SetParameter,
    SetCustomState,
    SpawnNode,
    Undo,
)
from core.graph_contracts import MutationRejected, PresetCaptureRequested
from core.pipeline import runtime_node_view
from core.playback_engine import ENGINE


def test_semantic_user_can_build_edit_delete_undo_and_redo_graph(
        graph_editor, node_registry):
    with RuntimeHarness() as runtime:
        runtime.perform(
            SpawnNode("GainNode", as_name="source"),
            SpawnNode("EqNode", as_name="effect"),
            Connect("source.out_audio", "effect.in_audio"),
            SetParameter("source", "gain_db", -6.0),
            RenameNode("effect", "Lead EQ"),
        )
        runtime.expect(
            GraphHasNodes(2),
            NodeParameter("source", "gain_db", -6.0),
            ConnectionExists("source.out_audio", "effect.in_audio"),
        )

        runtime.perform(DeleteNode("effect"))
        runtime.expect(GraphHasNodes(1))

        runtime.perform(Undo())
        runtime.expect(GraphHasNodes(2))

        runtime.perform(Redo())
        runtime.expect(GraphHasNodes(1))


def test_audio_file_selection_is_an_undo_checkpoint(graph_editor, node_registry):
    with RuntimeHarness() as runtime:
        runtime.perform(
            SpawnNode("AudioInputNode", as_name="audio"),
            SpawnNode("GainNode", as_name="gain"),
            SetCustomState("audio", "file_path", "C:/songs/selected.wav"),
            SetParameter("gain", "gain_db", -6.0),
        )

        runtime.perform(Undo())
        audio_input = runtime_node_view(runtime.resolve_node("audio"))
        assert audio_input.file_path == "C:/songs/selected.wav"
        assert dpg.get_value(audio_input.file_text_tag) == "selected.wav"

        runtime.perform(Undo())
        assert runtime_node_view(runtime.resolve_node("audio")).file_path == ""


def test_semantic_user_can_save_mutate_and_reload_project_preset(
        graph_editor, node_registry):
    with RuntimeHarness() as runtime:
        runtime.perform(
            SpawnNode("GainNode", as_name="gain"),
            SetParameter("gain", "gain_db", -9.0),
            MoveNode("gain", 320.0, 180.0),
            SavePreset("practice", include_params=True),
            MoveNode("gain", 20.0, 40.0),
            SetParameter("gain", "gain_db", -3.0),
        )
        runtime.expect(NodeParameter("gain", "gain_db", -3.0))

        runtime.perform(LoadPreset("practice"))

        runtime.expect(
            GraphHasNodes(1),
            NodeParameter("gain", "gain_db", -9.0),
            NodePosition("gain", 320.0, 180.0),
        )


def test_expected_rejection_is_visible_in_runtime_trace(runtime_harness):
    runtime_harness.perform_rejected(Undo(), reason="HISTORY_UNAVAILABLE")

    assert any(
        entry.kind == "RESULT" and entry.payload.reason == "HISTORY_UNAVAILABLE"
        for entry in runtime_harness.trace.entries
    )


def test_compile_gate_rejects_user_mutation_until_completion(
        graph_editor, node_registry):
    with RuntimeHarness() as runtime:
        runtime.perform(
            SpawnNode("GainNode", as_name="gain"),
            Analyze(),
        )
        assert runtime.owner.compile_status.name == "COMPILING"

        runtime.perform_rejected(
            SetParameter("gain", "gain_db", -12.0),
            reason="COMPILE_BUSY",
        )
        runtime.complete_compile(success=True)

        runtime.perform(SetParameter("gain", "gain_db", -12.0))
        runtime.expect(NodeParameter("gain", "gain_db", -12.0))


def test_semantic_user_can_disconnect_and_undo_topology(
        graph_editor, node_registry):
    with RuntimeHarness() as runtime:
        runtime.perform(
            SpawnNode("GainNode", as_name="source"),
            SpawnNode("EqNode", as_name="effect"),
            Connect("source.out_audio", "effect.in_audio"),
            Disconnect("source.out_audio", "effect.in_audio"),
        )
        runtime.expect(ConnectionAbsent(
            "source.out_audio", "effect.in_audio"))

        runtime.perform(Undo())

        runtime.expect(ConnectionExists(
            "source.out_audio", "effect.in_audio"))


def test_undo_restores_player_source_and_baked_progress(
        graph_editor, node_registry, monkeypatch):
    source_updates = []
    monkeypatch.setattr(
        ENGINE,
        "set_source",
        lambda render, length=0: source_updates.append((render, length)),
    )

    with RuntimeHarness() as runtime:
        runtime.perform(
            SpawnNode("AudioInputNode", as_name="source"),
            SpawnNode("AudioOutputNode", as_name="player"),
        )
        source = runtime_node_view(runtime.resolve_node("source"))
        source.cached_outputs[source.out_audio] = np.ones(
            (2, 128), dtype=np.float32)
        source.baked_signature = "runtime-undo-source"
        source.is_baked = True

        runtime.perform(
            Connect("source.out_audio", "player.in_audio"),
            Disconnect("source.out_audio", "player.in_audio"),
            Undo(),
        )

        restored_source = runtime_node_view(runtime.resolve_node("source"))
        render, length = source_updates[-1]
        assert callable(render)
        assert length == 128
        assert dpg.get_value(restored_source.progress_bar_tag) == pytest.approx(1.0)

        runtime.perform(Redo())
        assert source_updates[-1] == (None, 0)

        runtime.perform(Undo())
        restored_source = runtime_node_view(runtime.resolve_node("source"))
        render, length = source_updates[-1]
        assert callable(render)
        assert length == 128
        assert dpg.get_value(restored_source.progress_bar_tag) == pytest.approx(1.0)


def test_undo_across_compile_checkpoint_keeps_player_source(
        graph_editor, node_registry, monkeypatch):
    from core.graph_history import record_compile_checkpoint
    from core.pipeline import GRAPH_STATE, capture_history_artifacts

    source_updates = []
    monkeypatch.setattr(
        ENGINE,
        "set_source",
        lambda render, length=0: source_updates.append((render, length)),
    )

    with RuntimeHarness() as runtime:
        runtime.perform(
            SpawnNode("AudioInputNode", as_name="source"),
            SpawnNode("AudioOutputNode", as_name="player"),
            Connect("source.out_audio", "player.in_audio"),
        )
        source = runtime_node_view(runtime.resolve_node("source"))
        source.cached_outputs[source.out_audio] = np.ones(
            (2, 128), dtype=np.float32)
        source.baked_signature = "compile-checkpoint-source"
        source.is_baked = True
        GRAPH_STATE.stale_nodes.add(source.node_tag)
        assert capture_history_artifacts() == ()
        worker_artifacts = capture_history_artifacts(set())
        assert len(worker_artifacts) == 1
        record_compile_checkpoint(worker_artifacts)

        runtime.perform(Undo())

        render, length = source_updates[-1]
        assert callable(render)
        assert length == 128


def test_deferred_project_load_rejects_candidate_after_user_edit(
        graph_editor, node_registry):
    with RuntimeHarness(defer_persistence=True) as runtime:
        runtime.perform(
            SpawnNode("GainNode", as_name="gain"),
            SetParameter("gain", "gain_db", -9.0),
        )

        pending_save = runtime.begin(SavePreset("practice"))
        runtime.pump_for((PresetCaptureRequested, MutationRejected))
        runtime.wait_for_deferred_persistence("save")
        runtime.release_persistence("save")
        runtime.complete(pending_save)

        pending_load = runtime.begin(LoadPreset("practice"))
        runtime.wait_for_deferred_persistence("load")
        runtime.perform(SetParameter("gain", "gain_db", -3.0))
        runtime.release_persistence("load")
        runtime.complete_rejected(
            pending_load, reason="REVISION_CONFLICT")

        runtime.expect(NodeParameter("gain", "gain_db", -3.0))


def test_serialized_scenario_can_be_replayed_with_machine_readable_trace(
        runtime_harness):
    source = Scenario("agent-authored-gain", (
        SpawnNode("GainNode", as_name="gain"),
        SetParameter("gain", "gain_db", -4.5),
        MoveNode("gain", 240.0, 120.0),
    ))

    replayed = Scenario.loads(source.dumps())
    replayed.run(runtime_harness)

    runtime_harness.expect(
        GraphHasNodes(1),
        NodeParameter("gain", "gain_db", -4.5),
        NodePosition("gain", 240.0, 120.0),
    )
    trace_records = json.loads(runtime_harness.trace.dumps())
    assert trace_records[-1]["kind"] == "ASSERT"
    assert trace_records[0]["thread_name"] == "MainThread"


def test_parameter_edit_is_reversible_through_document_history(runtime_harness):
    runtime_harness.perform(
        SpawnNode("GainNode", as_name="gain"),
        MoveNode("gain", 315.0, 175.0),
        SetParameter("gain", "gain_db", -9.0),
        SetParameter("gain", "gain_db", -3.0),
        Undo(),
    )

    runtime_harness.expect(
        NodeParameter("gain", "gain_db", -9.0),
        NodePosition("gain", 315.0, 175.0),
    )


def test_node_drag_release_is_one_reversible_history_step(runtime_harness):
    runtime_harness.perform(
        SpawnNode("GainNode", as_name="gain"),
        MoveNode("gain", 120.0, 80.0),
        MoveNode("gain", 340.0, 210.0),
        Undo(),
    )
    runtime_harness.expect(NodePosition("gain", 120.0, 80.0))

    runtime_harness.perform(Redo())
    runtime_harness.expect(NodePosition("gain", 340.0, 210.0))


def test_custom_node_state_is_owner_owned_and_reversible(runtime_harness):
    from core.graph_contracts import EditPhase, NodeStateAction
    from core.pipeline import runtime_node_view

    runtime_harness.perform(
        SpawnNode("Mixer8Node", as_name="mixer"),
        SetCustomState("mixer", "volume", -6.0, index=0),
        SetCustomState(
            "mixer", "volume", -7.0, index=0,
            edit_phase=EditPhase.UPDATE),
        SetCustomState(
            "mixer", "volume", -8.0, index=0,
            edit_phase=EditPhase.UPDATE),
        SetCustomState("mixer", "volume", -9.0, index=0),
        Undo(),
    )
    mixer = runtime_node_view(runtime_harness.resolve_node("mixer"))
    assert mixer._lin_to_db(mixer.vols[0]) == pytest.approx(-6.0)

    runtime_harness.perform(Redo())
    mixer = runtime_node_view(runtime_harness.resolve_node("mixer"))
    assert mixer._lin_to_db(mixer.vols[0]) == pytest.approx(-9.0)

    runtime_harness.perform(
        SetCustomState(
            "mixer", "mute", index=0, action=NodeStateAction.TOGGLE),
        Undo(),
    )
    mixer = runtime_node_view(runtime_harness.resolve_node("mixer"))
    assert mixer.mutes[0] is False


def test_guitar_preset_undo_redo_preserves_positions_and_midi_chain(
        runtime_harness):
    import json
    from pathlib import Path

    import numpy as np

    from core.base_node import NOTE_DTYPE
    from core.graph_history import refresh_current_artifacts
    from core.pipeline import (
        export_current_graph_document,
        light_node_compile,
        runtime_node_view,
    )
    from ui.graph_projection import NODE_EDITOR_LINKS

    preset_path = Path(__file__).parents[1] / "presets" / "guitar.json"
    preset = json.loads(preset_path.read_text(encoding="utf-8"))
    expected_positions = {
        node_id: tuple(position)
        for node_id, position in preset["workspace"]["node_positions"].items()
    }
    expected_connections = {
        connection["connection_id"]: (
            connection["output_node_id"],
            connection["output_port"],
            connection["input_node_id"],
            connection["input_port"],
        )
        for connection in preset["graph"]["connections"]
    }
    runtime_harness.store.write_atomic("guitar", preset)

    runtime_harness.perform(LoadPreset("guitar"))

    notes = np.zeros(2, dtype=NOTE_DTYPE)
    notes["pitch"] = [64, 67]
    notes["start"] = [0.0, 0.5]
    notes["end"] = [0.4, 0.9]
    notes["velocity"] = [100, 96]
    basic_pitch = runtime_node_view("node_BasicPitchNode_11")
    basic_pitch.cached_outputs[basic_pitch.out_midi] = notes
    basic_pitch.baked_signature = "guitar-basic-pitch-baked"
    basic_pitch.is_baked = True
    assert light_node_compile()
    refresh_current_artifacts()

    def assert_workspace_and_connections(*, projected=False):
        actual_positions = {
            node_id: runtime_harness.node_position(node_id)
            for node_id in expected_positions
        }
        assert actual_positions == expected_positions
        graph = export_current_graph_document()
        actual_connections = {
            connection.connection_id: (
                connection.output_node_id,
                connection.output_port,
                connection.input_node_id,
                connection.input_port,
            )
            for connection in graph.connections
        }
        assert actual_connections == expected_connections
        assert set(NODE_EDITOR_LINKS._items_by_connection) == set(expected_connections)
        assert actual_connections["connection_15"] == (
            "node_DemucsNode_2", "out_guitar",
            "node_BasicPitchNode_11", "in_audio",
        )
        assert actual_connections["connection_16"] == (
            "node_BasicPitchNode_11", "out_midi",
            "node_GuitarFretboardNode_10", "in_midi",
        )
        restored_basic_pitch = runtime_node_view("node_BasicPitchNode_11")
        restored_fretboard = runtime_node_view("node_GuitarFretboardNode_10")
        assert restored_basic_pitch.is_baked is True
        assert restored_basic_pitch.baked_signature == "guitar-basic-pitch-baked"
        if projected:
            assert dpg.get_value(
                restored_basic_pitch.progress_bar_tag) == pytest.approx(1.0)
        assert np.array_equal(
            restored_basic_pitch.cached_outputs[restored_basic_pitch.out_midi], notes)
        assert np.array_equal(
            restored_fretboard.input_values[restored_fretboard.in_midi].data, notes)

    assert_workspace_and_connections()
    for mode in (1.0, 0.0, 1.0, 0.0, 1.0):
        runtime_harness.perform(SetParameter(
            "node_GuitarFretboardNode_10", "fingering_mode", mode))
        runtime_harness.perform(Undo())
        assert_workspace_and_connections(projected=True)
        assert dpg.does_alias_exist("file_dialog_node_AudioInputNode_1")
        runtime_harness.perform(Redo())
        assert_workspace_and_connections(projected=True)
        assert dpg.does_alias_exist("file_dialog_node_AudioInputNode_1")


def test_seeded_model_scenarios_match_runtime_state(graph_editor, node_registry):
    for seed in (7, 19, 41):
        with RuntimeHarness() as runtime:
            SeededScenarioGenerator(seed).generate(steps=30).run(runtime)


@pytest.mark.parametrize("node_type,dialog_prefix", (
    ("AudioInputNode", "file_dialog_"),
    ("AudioExportNode", "export_dialog_"),
    ("MidiExportNode", "midi_export_dialog_"),
))
def test_file_dialog_resources_are_replaced_with_their_node(
        graph_editor, node_registry, node_type, dialog_prefix):
    with RuntimeHarness() as runtime:
        runtime.perform(SpawnNode(node_type, as_name="resource_owner"))
        node_id = runtime.resolve_node("resource_owner")
        dialog_tag = f"{dialog_prefix}{node_id}"
        assert dpg.does_item_exist(dialog_tag)

        runtime.perform(Undo())
        assert not dpg.does_item_exist(dialog_tag)

        runtime.perform(Redo())
        assert dpg.does_item_exist(dialog_tag)