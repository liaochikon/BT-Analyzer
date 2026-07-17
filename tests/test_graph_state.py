"""GraphState ownership and isolation contracts."""


def test_graph_states_are_isolated():
    from core.graph_state import GraphState

    first = GraphState()
    second = GraphState()
    connection = first.add_connection("output", "input")
    first.stale_nodes.add("node_a")

    assert connection.connection_id == "connection_1"
    assert first.source_for_input("input") == "output"
    assert second.connections_by_id == {}
    assert second.stale_nodes == set()
    assert first.next_node_tag("GainNode") == "node_GainNode_1"
    assert second.next_node_tag("GainNode") == "node_GainNode_1"


def test_connection_ids_are_monotonic_and_not_reused():
    from core.graph_state import GraphState

    state = GraphState()
    first = state.add_connection("output_1", "input_1")
    assert state.remove_connection(first.connection_id) == first

    second = state.add_connection("output_2", "input_2")

    assert first.connection_id == "connection_1"
    assert second.connection_id == "connection_2"


def test_restored_connection_id_advances_allocator():
    from core.graph_state import GraphState

    state = GraphState()
    restored = state.add_connection(
        "output_8", "input_8", connection_id="connection_8")
    generated = state.add_connection("output_9", "input_9")

    assert restored.connection_id == "connection_8"
    assert generated.connection_id == "connection_9"


def test_pipeline_exposes_only_graph_state_owner():
    from core import pipeline

    for name in (
        "NODE_CONNECTIONS", "NODE_CONNECTION_IDS", "NODE_IN_RUNTIME", "PIN_OWNER",
        "NODE_EXECUTION_ORDER", "STALE_NODES", "FAILED_NODES", "CYCLE_NODES",
        "_HEAVY_DESCENDANTS_CACHE",
    ):
        assert not hasattr(pipeline, name)


def test_graph_snapshot_is_immutable_revisioned_and_detached():
    from core.graph_state import GraphState

    class RuntimeNode:
        title = "Lead"

    state = GraphState()
    initial = state.snapshot()
    state.nodes["node_1"] = RuntimeNode()
    state.add_connection("output", "input")
    state.stale_nodes.add("node_1")
    published = state.commit_document()

    assert published.revision == initial.revision + 1
    assert published.state_version == initial.state_version + 1
    assert published.connection_count == 1
    assert published.nodes[0].title == "Lead"
    assert published.issues[0].category == "stale"
    assert published.nodes[0] is not state.nodes["node_1"]
    try:
        published.connection_count = 2
    except (AttributeError, TypeError):
        pass
    else:
        raise AssertionError("GraphSnapshot must be immutable")


def test_snapshot_publication_does_not_change_document_revision():
    from core.graph_state import GraphState

    state = GraphState()
    initial = state.snapshot()
    published = state.publish_snapshot()

    assert published.document_revision == initial.document_revision
    assert published.state_version == initial.state_version + 1


def test_runtime_candidate_replace_is_atomic_and_restores_allocators():
    from types import SimpleNamespace
    from core.graph_state import GraphConnection, GraphState

    class Node:
        def __init__(self, node_id):
            self.node_tag = node_id
            self.input_spec = {f"{node_id}_in::AUDIO": object()}
            self.output_spec = {f"{node_id}_out::AUDIO": object()}

    state = GraphState()
    old_node = Node("node_Old_1")
    state.nodes[old_node.node_tag] = old_node
    invalid = SimpleNamespace(
        nodes={"node_New_8": Node("node_New_8")},
        connections=(GraphConnection("connection_9", "missing", "missing"),),
        execution_order=("node_New_8",),
    )

    try:
        state.replace_runtime(invalid)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid candidate must be rejected")

    assert state.nodes == {"node_Old_1": old_node}

    first = Node("node_New_8")
    second = Node("node_New_9")
    valid = SimpleNamespace(
        nodes={first.node_tag: first, second.node_tag: second},
        connections=(GraphConnection(
            "connection_9",
            f"{first.node_tag}_out::AUDIO",
            f"{second.node_tag}_in::AUDIO",
        ),),
        execution_order=(first.node_tag, second.node_tag),
    )

    state.replace_runtime(valid)

    assert tuple(state.nodes) == (first.node_tag, second.node_tag)
    assert state.execution_order == [first.node_tag, second.node_tag]
    assert state.next_node_tag("GainNode") == "node_GainNode_10"
    assert state.add_connection("new_out", "new_in").connection_id == "connection_10"


def test_graph_snapshot_reports_each_node_highest_priority_issue_once():
    from core.graph_state import GraphState

    state = GraphState()
    state.cycle_nodes.add("node_1")
    state.failed_nodes.add("node_1")
    state.stale_nodes.add("node_1")

    snapshot = state.publish_snapshot()

    assert [(issue.node_id, issue.category) for issue in snapshot.issues] == [
        ("node_1", "wiring"),
    ]