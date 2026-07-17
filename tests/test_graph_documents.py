"""GraphDocument v1 codec, export, and detached materialization contracts."""

import pytest


def _gain_document(**overrides):
    from core.graph_documents import (
        ConnectionDocument,
        GraphDocument,
        NodeDocument,
    )

    values = {
        "schema_version": 1,
        "graph_id": "graph_1",
        "nodes": (
            NodeDocument(
                node_id="node_GainNode_1",
                node_type="GainNode",
                display_name="Lead",
                parameters={"gain_db": 3.0, "bypass": False},
                extra={},
            ),
            NodeDocument(
                node_id="node_GainNode_2",
                node_type="GainNode",
                parameters={"gain_db": 0.0, "bypass": False},
                extra={},
            ),
        ),
        "connections": (
            ConnectionDocument(
                connection_id="connection_7",
                output_node_id="node_GainNode_1",
                output_port="out_audio",
                input_node_id="node_GainNode_2",
                input_port="in_audio",
            ),
        ),
    }
    values.update(overrides)
    return GraphDocument(**values)


def test_graph_document_json_round_trip_is_equal_and_deeply_immutable():
    from core.graph_documents import GraphDocumentCodec

    original_parameters = {"gain_db": 3.0, "bands": [1, 2]}
    document = _gain_document(nodes=(
        _gain_document().nodes[0].__class__(
            node_id="node_GainNode_1",
            node_type="GainNode",
            parameters=original_parameters,
        ),
    ), connections=())
    original_parameters["bands"].append(3)

    decoded = GraphDocumentCodec.loads(GraphDocumentCodec.dumps(document))

    assert decoded.ok
    assert decoded.document == document
    assert decoded.document.nodes[0].parameters["bands"] == (1, 2)
    with pytest.raises(TypeError):
        decoded.document.nodes[0].parameters["gain_db"] = 4.0


def test_graph_document_codec_reports_schema_and_json_errors():
    from core.graph_documents import GraphDocumentCodec

    invalid_json = GraphDocumentCodec.loads("{")
    unsupported = GraphDocumentCodec.from_dict({
        "schema_version": 9,
        "graph_id": "graph_1",
        "nodes": [],
        "connections": [],
    })

    assert invalid_json.diagnostics[0].code == "invalid_json"
    assert unsupported.diagnostics[0].code == "unsupported_schema_version"
    assert invalid_json.document is None
    assert unsupported.document is None


def test_graph_state_exports_stable_node_and_port_roles_without_gui():
    from core.graph_documents import export_graph_document
    from core.graph_state import GraphState
    from nodes.gain_node.node import GainNode

    state = GraphState()
    first = GainNode("node_GainNode_1")
    second = GainNode("node_GainNode_2")
    state.nodes[first.node_tag] = first
    state.nodes[second.node_tag] = second
    for node in (first, second):
        for pin in (*node.input_spec, *node.output_spec):
            state.pin_owners[pin] = node.node_tag
    connection = state.add_connection(first.out_audio, second.in_audio)

    document = export_graph_document(state, "graph_1")

    assert document.nodes[0].node_id == first.node_tag
    assert document.connections[0].connection_id == connection.connection_id
    assert document.connections[0].output_port == "out_audio"
    assert document.connections[0].input_port == "in_audio"


def test_materialize_graph_document_builds_detached_runtime_without_dpg_context(monkeypatch):
    import dearpygui.dearpygui as dpg
    from core.graph_documents import materialize_graph_document
    from nodes.gain_node.node import GainNode

    monkeypatch.setattr(
        dpg, "create_context",
        lambda: pytest.fail("headless materialization must not create a DPG context"),
    )

    result = materialize_graph_document(_gain_document(), {"GainNode": GainNode})

    assert result.ok
    assert result.candidate.graph_id == "graph_1"
    assert result.candidate.nodes["node_GainNode_1"].gain_db == 3.0
    assert result.candidate.nodes["node_GainNode_1"].custom_name == "Lead"
    assert result.candidate.connections[0].connection_id == "connection_7"


@pytest.mark.parametrize(
    ("document", "code"),
    [
        (_gain_document(nodes=(_gain_document().nodes[0].__class__(
            node_id="node_1", node_type="MissingNode"),), connections=()),
         "unknown_node_type"),
        (_gain_document(nodes=(_gain_document().nodes[0].__class__(
            node_id="node_GainNode_1", node_type="GainNode",
            parameters={"missing": 1}),), connections=()),
         "unknown_parameter"),
        (_gain_document(connections=(_gain_document().connections[0].__class__(
            connection_id="connection_7",
            output_node_id="node_GainNode_1", output_port="missing",
            input_node_id="node_GainNode_2", input_port="in_audio"),)),
         "unknown_output_port"),
    ],
)
def test_materialize_graph_document_rejects_invalid_domain_references(document, code):
    from core.graph_documents import materialize_graph_document
    from nodes.gain_node.node import GainNode

    result = materialize_graph_document(document, {"GainNode": GainNode})

    assert result.candidate is None
    assert code in {diagnostic.code for diagnostic in result.diagnostics}


def test_legacy_preset_migrates_ids_ports_workspace_and_practice():
    from core.graph_documents import Position, migrate_legacy_preset

    result = migrate_legacy_preset({
        "nodes": [
            {
                "class": "GainNode", "pos": [10, 20],
                "params": {"gain_db": 2.0}, "custom_name": "Lead",
                "panel": {"pos": [30, 40], "size": [500, 300]},
            },
            {"class": "GainNode", "pos": [50, 60], "params": {}},
        ],
        "links": [
            [0, "_out_audio::AUDIO", 1, "_in_audio::AUDIO", "connection_7"],
        ],
        "globals": {"speed": 1.5},
    })

    assert result.ok
    assert [node.node_id for node in result.graph.nodes] == [
        "node_GainNode_1", "node_GainNode_2",
    ]
    assert result.graph.connections[0].connection_id == "connection_7"
    assert result.graph.connections[0].output_port == "out_audio"
    assert result.workspace.node_positions["node_GainNode_1"] == Position(10.0, 20.0)
    assert result.workspace.node_panels["node_GainNode_1"].size == Position(500.0, 300.0)
    assert result.practice.settings["speed"] == 1.5


def test_legacy_preset_allocates_missing_ids_after_highest_persisted_id():
    from core.graph_documents import migrate_legacy_preset

    result = migrate_legacy_preset({
        "nodes": [
            {"class": "GainNode"},
            {"class": "GainNode"},
            {"class": "GainNode"},
        ],
        "links": [
            [0, "_out_audio::AUDIO", 1, "_in_audio::AUDIO", "connection_8"],
            [1, "_out_audio::AUDIO", 2, "_in_audio::AUDIO"],
        ],
    })

    assert result.ok
    assert [item.connection_id for item in result.graph.connections] == [
        "connection_8", "connection_9",
    ]


def test_legacy_preset_rejects_malformed_link_without_partial_document():
    from core.graph_documents import migrate_legacy_preset

    result = migrate_legacy_preset({
        "nodes": [{"class": "GainNode"}],
        "links": [[0, "bad", 2, "_in_audio::AUDIO"]],
    })

    assert result.graph is None
    assert result.diagnostics[0].code == "unknown_connection_node"


def test_materialize_rejects_cycle_before_runtime_candidate_exists():
    from core.graph_documents import ConnectionDocument, materialize_graph_document
    from nodes.gain_node.node import GainNode

    document = _gain_document(connections=(
        ConnectionDocument(
            "connection_1", "node_GainNode_1", "out_audio",
            "node_GainNode_2", "in_audio"),
        ConnectionDocument(
            "connection_2", "node_GainNode_2", "out_audio",
            "node_GainNode_1", "in_audio"),
    ))

    result = materialize_graph_document(document, {"GainNode": GainNode})

    assert result.candidate is None
    assert result.diagnostics[0].code == "graph_cycle"


def test_project_document_round_trip_preserves_all_sections():
    from core.graph_documents import (
        PanelDocument,
        Position,
        PracticeDocument,
        ProjectDocument,
        ProjectDocumentCodec,
        ProjectMetadata,
        WindowDocument,
        WorkspaceDocument,
    )

    project = ProjectDocument(
        schema_version=1,
        project_id="project_1",
        graph=_gain_document(),
        workspace=WorkspaceDocument(
            node_positions={"node_GainNode_1": Position(10.0, 20.0)},
            node_panels={
                "node_GainNode_1": PanelDocument(
                    Position(30.0, 40.0), Position(500.0, 300.0)),
            },
            windows={
                "win_node_editor": WindowDocument(
                    Position(50.0, 60.0), Position(800.0, 600.0), False),
            },
            dock_layout="[Window][win_node_editor]\nDockId=0x1234",
        ),
        practice=PracticeDocument({"speed": 1.25, "loop": [1.0, 4.0]}),
        metadata=ProjectMetadata({"title": "Practice"}),
    )

    decoded = ProjectDocumentCodec.loads(ProjectDocumentCodec.dumps(project))

    assert decoded.ok
    assert decoded.document == project
    assert decoded.document.practice.settings["loop"] == (1.0, 4.0)
    with pytest.raises(TypeError):
        decoded.document.metadata.properties["title"] = "Changed"


def test_old_project_workspace_without_desktop_layout_remains_supported():
    from core.graph_documents import ProjectDocument, ProjectDocumentCodec

    payload = ProjectDocumentCodec.to_dict(ProjectDocument(
        schema_version=1,
        project_id="project_1",
        graph=_gain_document(),
    ))
    payload["workspace"] = {"node_positions": {}, "node_panels": {}}

    decoded = ProjectDocumentCodec.from_dict(payload)

    assert decoded.ok
    assert decoded.document.workspace.windows == {}
    assert decoded.document.workspace.dock_layout is None


def test_project_document_reports_nested_graph_schema_path():
    from core.graph_documents import ProjectDocumentCodec

    result = ProjectDocumentCodec.from_dict({
        "schema_version": 1,
        "project_id": "project_1",
        "graph": {"schema_version": 99},
        "workspace": None,
        "practice": None,
    })

    assert result.document is None
    assert result.diagnostics[0].code == "unsupported_schema_version"
    assert result.diagnostics[0].path == "$.graph.schema_version"
