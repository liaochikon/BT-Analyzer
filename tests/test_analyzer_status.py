"""Analyzer Status projection tests.

GraphState publishes immutable issue snapshots; graph and compile events trigger the GUI renderer.
Tests mutate owner state only to construct snapshots, then verify classification and projection.
"""

import pytest

from application.compile_events import CompileBusyChanged, CompileEndpoint, CompileTopic
from core.bus import Message
from core import pipeline


@pytest.fixture
def clean_pipeline(graph_editor):
    # graph_editor gives a live headless graph + clears nodes around each test; also wipe the
    # status sets so tests don't leak state into each other.
    pipeline.GRAPH_STATE.stale_nodes.clear()
    pipeline.GRAPH_STATE.failed_nodes.clear()
    pipeline.GRAPH_STATE.cycle_nodes.clear()
    yield
    pipeline.GRAPH_STATE.stale_nodes.clear()
    pipeline.GRAPH_STATE.failed_nodes.clear()
    pipeline.GRAPH_STATE.cycle_nodes.clear()


def _make_two_nodes(graph_editor):
    # Two real nodes so tags resolve to display names; return their tags.
    graph_editor.apply_graph({"nodes": [
        {"class": "GainNode", "pos": [0, 0], "params": {}},
        {"class": "EqNode", "pos": [200, 0], "params": {}},
    ], "links": []})
    from core import pipeline
    return list(pipeline.GRAPH_STATE.nodes.keys())


def test_no_issues_when_all_clean(clean_pipeline, graph_editor):
    _make_two_nodes(graph_editor)
    assert pipeline.get_analyzer_issues() == []


def test_stale_and_failed_are_yellow_with_names(clean_pipeline, graph_editor):
    from core import pipeline
    a, b = _make_two_nodes(graph_editor)
    pipeline.GRAPH_STATE.stale_nodes.add(a)
    pipeline.GRAPH_STATE.failed_nodes.add(b)
    issues = pipeline.get_analyzer_issues()
    cats = {it["node_tag"]: it["category"] for it in issues}
    assert cats[a] == pipeline.ISSUE_STALE
    assert cats[b] == pipeline.ISSUE_FAILED
    # Names come from the node display_name, not the raw tag.
    names = {it["name"] for it in issues}
    assert names == {pipeline.GRAPH_STATE.nodes[a].display_name, pipeline.GRAPH_STATE.nodes[b].display_name}


def test_issue_name_prefers_custom_node_title(clean_pipeline, graph_editor):
    from core import pipeline
    node_tag, _ = _make_two_nodes(graph_editor)
    pipeline.GRAPH_STATE.nodes[node_tag].set_custom_name("Vocal Gain")
    pipeline.GRAPH_STATE.stale_nodes.add(node_tag)

    assert pipeline.get_analyzer_issues()[0]["name"] == "Vocal Gain"


def test_cycle_is_red_and_sorts_first(clean_pipeline, graph_editor):
    a, b = _make_two_nodes(graph_editor)
    pipeline.GRAPH_STATE.stale_nodes.add(a)       # yellow
    pipeline.GRAPH_STATE.cycle_nodes.add(b)       # red
    issues = pipeline.get_analyzer_issues()
    assert issues[0]["category"] == pipeline.ISSUE_WIRING   # red first
    assert issues[0]["node_tag"] == b


def test_cycle_node_reported_only_as_wiring(clean_pipeline, graph_editor):
    a, _b = _make_two_nodes(graph_editor)
    # A node can be both in a cycle AND stale/failed; the loop is the root cause, so it's reported
    # once, as wiring only (fixing the loop is the prerequisite; stale/failed are moot until then).
    pipeline.GRAPH_STATE.cycle_nodes.add(a)
    pipeline.GRAPH_STATE.stale_nodes.add(a)
    pipeline.GRAPH_STATE.failed_nodes.add(a)
    issues = [it for it in pipeline.get_analyzer_issues() if it["node_tag"] == a]
    assert len(issues) == 1 and issues[0]["category"] == pipeline.ISSUE_WIRING


def test_analyzer_projection_reads_cached_graph_snapshot(
        clean_pipeline, graph_editor, monkeypatch):
    from ui import analyzer_status

    node_tag, _ = _make_two_nodes(graph_editor)
    pipeline.GRAPH_STATE.stale_nodes.add(node_tag)
    pipeline.GRAPH_STATE.publish_snapshot()
    monkeypatch.setattr(
        pipeline,
        "get_analyzer_issues",
        lambda: (_ for _ in ()).throw(AssertionError("legacy issue builder called")),
    )
    monkeypatch.setattr(analyzer_status, "_last_key", None)
    monkeypatch.setattr(analyzer_status.dpg, "does_item_exist", lambda _tag: True)
    monkeypatch.setattr(analyzer_status.dpg, "set_value", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(analyzer_status.dpg, "configure_item", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(analyzer_status.dpg, "delete_item", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(analyzer_status.dpg, "add_text", lambda *_args, **_kwargs: None)

    analyzer_status.update_analyzer_status()


def test_compile_event_drives_analyzer_projection(monkeypatch):
    from ui import analyzer_status

    updates = []
    monkeypatch.setattr(analyzer_status, "_compile_busy", False)
    monkeypatch.setattr(analyzer_status, "update_analyzer_status", lambda: updates.append(True))

    analyzer_status.handle_analyzer_status_message(Message(
        source=CompileEndpoint.SERVICE,
        destination=CompileEndpoint.ANALYZER_STATUS,
        topic=CompileTopic.BUSY_CHANGED,
        payload=CompileBusyChanged(busy=True),
    ))

    assert analyzer_status._compile_busy is True
    assert updates == [True]
