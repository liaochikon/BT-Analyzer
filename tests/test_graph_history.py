from core.persistence_contracts import ApplyStatus


def _graph(node_class):
    return {
        "nodes": [{"class": node_class, "pos": [0, 0], "params": {}}],
        "links": [],
    }


def test_undo_commits_cursor_only_after_success(graph_editor):
    from core import graph_history as history
    from core import pipeline

    graph_editor.apply_graph(_graph("EqNode"))
    history.reset_history()
    graph_editor.clear_all_nodes()
    graph_editor.apply_graph(_graph("GainNode"))
    history.record_history()

    result = history.undo()

    assert result.status is ApplyStatus.APPLIED
    assert history._HISTORY_CURSOR == 0
    assert type(next(iter(pipeline.GRAPH_STATE.nodes.values()))).__name__ == "EqNode"


def test_failed_undo_restores_graph_and_preserves_cursor(graph_editor, monkeypatch):
    from core import graph_history as history
    from core.graph_presets import PresetApplyError
    from core import pipeline

    graph_editor.apply_graph(_graph("EqNode"))
    history.reset_history()
    graph_editor.clear_all_nodes()
    graph_editor.apply_graph(_graph("GainNode"))
    history.record_history()
    previous_cursor = history._HISTORY_CURSOR
    real_apply = graph_editor.apply_graph
    calls = 0

    def fail_candidate_then_restore(data):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PresetApplyError("candidate restore failed")
        return real_apply(data)

    monkeypatch.setattr(history, "_apply_graph", fail_candidate_then_restore)

    result = history.undo()

    assert result.status is ApplyStatus.ROLLED_BACK
    assert history._HISTORY_CURSOR == previous_cursor
    assert type(next(iter(pipeline.GRAPH_STATE.nodes.values()))).__name__ == "GainNode"


def test_failed_redo_preserves_cursor(graph_editor, monkeypatch):
    from core import graph_history as history
    from core.graph_presets import PresetApplyError

    graph_editor.apply_graph(_graph("EqNode"))
    history.reset_history()
    graph_editor.clear_all_nodes()
    graph_editor.apply_graph(_graph("GainNode"))
    history.record_history()
    history.undo()
    previous_cursor = history._HISTORY_CURSOR
    real_apply = graph_editor.apply_graph
    calls = 0

    def fail_candidate_then_restore(data):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PresetApplyError("candidate restore failed")
        return real_apply(data)

    monkeypatch.setattr(history, "_apply_graph", fail_candidate_then_restore)

    result = history.redo()

    assert result.status is ApplyStatus.ROLLED_BACK
    assert history._HISTORY_CURSOR == previous_cursor


def test_document_history_restores_candidate_and_moves_cursor_only_on_success():
    from types import SimpleNamespace
    from core import graph_history as history

    current = ["revision_1"]
    replacements = []

    def materialize(document):
        if document == "invalid":
            return SimpleNamespace(ok=False, candidate=None)
        return SimpleNamespace(ok=True, candidate=f"candidate:{document}")

    def replace(candidate, workspace):
        replacements.append((candidate, workspace))
        current[0] = candidate.removeprefix("candidate:")
        return f"event:{candidate}"

    history.configure_document_history(lambda: current[0], materialize, replace)
    try:
        history.reset_history()
        current[0] = "revision_2"
        history.record_history()

        result = history.undo()

        assert result.ok
        assert current == ["revision_1"]
        assert replacements == [("candidate:revision_1", None)]
        assert history.take_restore_event() == "event:candidate:revision_1"
        assert history.can_redo()

        result = history.redo()

        assert result.ok
        assert current == ["revision_2"]
        assert history.take_restore_event() == "event:candidate:revision_2"
    finally:
        history.clear_document_history()


def test_document_history_invalid_candidate_preserves_cursor():
    from types import SimpleNamespace
    from core import graph_history as history

    current = ["valid"]
    history.configure_document_history(
        lambda: current[0],
        lambda document: SimpleNamespace(ok=document != "invalid", candidate=document),
        lambda *_args: None,
    )
    try:
        history.reset_history()
        current[0] = "invalid"
        history.record_history()
        history.undo()
        assert history.can_redo()

        result = history.redo()

        assert not result.ok
        assert history.can_redo()
        assert history.take_restore_event() is None
    finally:
        history.clear_document_history()


def test_heavy_compile_checkpoint_is_restored_before_the_next_edit(monkeypatch):
    from types import SimpleNamespace
    from core import graph_history as history

    current = ["revision_1"]
    artifacts = [()]
    restored = []
    monkeypatch.setattr(history, "append_log", lambda *_args, **_kwargs: None)

    def replace(candidate, _workspace):
        current[0] = candidate
        restored.append((candidate, artifacts[0]))
        return SimpleNamespace(ok=True)

    history.configure_document_history(
        lambda: current[0],
        lambda document: SimpleNamespace(ok=True, candidate=document),
        replace,
        capture_artifacts=lambda: artifacts[0],
        restore_artifacts=lambda refs: artifacts.__setitem__(0, refs),
    )
    try:
        history.reset_history()
        artifacts[0] = ("compiled",)
        history.record_compile_checkpoint()

        result = history.undo()

        assert result.ok
        assert current == ["revision_1"]
        assert artifacts == [("compiled",)]

        assert history.redo().ok
        current[0] = "revision_2"
        history.record_history()

        result = history.undo()

        assert result.ok
        assert current == ["revision_1"]
        assert artifacts == [("compiled",)]
        assert history._HISTORY_CURSOR == 1
        assert len(history._HISTORY) == 3
    finally:
        history.clear_document_history()
