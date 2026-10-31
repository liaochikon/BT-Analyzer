"""Undo / redo over immutable graph documents and workspace geometry.

Document-mode history pairs each owner-captured graph with GUI-synchronized geometry and handles
for valid baked artifacts. Artifact arrays remain in the bounded session registry or disk cache;
history entries never copy them. Restore rebinds available outputs and runs a light compile so
audio and MIDI reach downstream views. Stale, failed, evicted-without-disk, or otherwise missing
artifacts remain unbaked and require Analyze.

Persistence and compile-state dependencies are configured as ports by the composition modules,
so this history state machine does not import pipeline or graph_presets.
"""

from app_logging import append_log
from core.persistence_contracts import ApplyResult, ApplyStatus
from dataclasses import dataclass
import logging
import threading


@dataclass(frozen=True, slots=True)
class _DocumentHistoryEntry:
    document: object
    workspace: object | None = None
    artifacts: tuple = ()

_HISTORY: list = []          # snapshots, oldest..newest
_HISTORY_CURSOR = -1         # index in _HISTORY of the CURRENT state (-1 = uninitialised)
_HISTORY_LIMIT = 50          # cap: drop the oldest beyond this many undo steps
_RESTORING = False           # True while apply_graph runs from undo/redo, so hooks don't re-push
_serialize_graph = None
_clear_graph = None
_apply_graph = None
_apply_error = RuntimeError
_is_compile_busy = lambda: False
_capture_document = None
_capture_workspace = None
_capture_artifacts = None
_restore_artifacts = None
_retain_artifacts = None
_materialize_document = None
_replace_candidate = None
_last_restore_event = None
_HISTORY_LOCK = threading.RLock()
logger = logging.getLogger(__name__)


def configure_persistence(serialize_graph, clear_graph, apply_graph, apply_error) -> None:
    global _serialize_graph, _clear_graph, _apply_graph, _apply_error
    _serialize_graph = serialize_graph
    _clear_graph = clear_graph
    _apply_graph = apply_graph
    _apply_error = apply_error


def configure_compile_busy(is_compile_busy) -> None:
    global _is_compile_busy
    _is_compile_busy = is_compile_busy


def configure_document_history(
    capture_document,
    materialize_document,
    replace_candidate,
    capture_workspace=None,
    capture_artifacts=None,
    restore_artifacts=None,
    retain_artifacts=None,
) -> None:
    global _capture_document, _capture_workspace, _capture_artifacts
    global _materialize_document, _replace_candidate, _restore_artifacts, _retain_artifacts
    _capture_document = capture_document
    _capture_workspace = capture_workspace
    _capture_artifacts = capture_artifacts
    _materialize_document = materialize_document
    _replace_candidate = replace_candidate
    _restore_artifacts = restore_artifacts
    _retain_artifacts = retain_artifacts

def clear_document_history() -> None:
    if _retain_artifacts is not None:
        _retain_artifacts(())
    configure_document_history(None, None, None)


def _require_persistence() -> None:
    if _capture_document is not None:
        if _materialize_document is None or _replace_candidate is None:
            raise RuntimeError("graph history document ports are not configured")
        return
    if _serialize_graph is None or _clear_graph is None or _apply_graph is None:
        raise RuntimeError("graph history persistence ports are not configured")


def reset_history():
    """Seed history with the current graph as the baseline. Call once after startup preset load."""
    global _HISTORY, _HISTORY_CURSOR
    with _HISTORY_LOCK:
        _require_persistence()
        _HISTORY = [_capture()]
        _HISTORY_CURSOR = 0
        _sync_artifact_retention()


def record_history(workspace=None, document=None):
    """Snapshot the current graph as a new history entry after a structural change.

    Truncates any redo tail, appends the new state, and enforces the depth cap. A no-op while a
    restore (undo/redo) is in flight, so replaying a snapshot never records itself.
    """
    global _HISTORY, _HISTORY_CURSOR
    with _HISTORY_LOCK:
        _require_persistence()
        if _RESTORING:
            return
        if _HISTORY_CURSOR < 0:
            reset_history()
            return
        del _HISTORY[_HISTORY_CURSOR + 1:]
        _HISTORY.append(_capture(workspace, document))
        if len(_HISTORY) > _HISTORY_LIMIT:
            drop = len(_HISTORY) - _HISTORY_LIMIT
            del _HISTORY[:drop]
        _HISTORY_CURSOR = len(_HISTORY) - 1
        _sync_artifact_retention()


def refresh_current_artifacts() -> None:
    """Attach newly baked artifacts to the current graph state without adding an undo step."""
    with _HISTORY_LOCK:
        if _RESTORING or _capture_artifacts is None or not (
                0 <= _HISTORY_CURSOR < len(_HISTORY)):
            return
        entry = _HISTORY[_HISTORY_CURSOR]
        if not isinstance(entry, _DocumentHistoryEntry):
            return
        _HISTORY[_HISTORY_CURSOR] = _DocumentHistoryEntry(
            entry.document,
            entry.workspace,
            tuple(_capture_artifacts()),
        )
        _sync_artifact_retention()


def record_compile_checkpoint(artifacts=None) -> None:
    """Append a compile boundary while preserving its artifacts across the first undo."""
    global _HISTORY_CURSOR
    with _HISTORY_LOCK:
        if _RESTORING or _capture_artifacts is None or not (
                0 <= _HISTORY_CURSOR < len(_HISTORY)):
            return
        entry = _HISTORY[_HISTORY_CURSOR]
        if not isinstance(entry, _DocumentHistoryEntry):
            return
        artifacts = tuple(
            _capture_artifacts() if artifacts is None else artifacts)
        del _HISTORY[_HISTORY_CURSOR + 1:]
        _HISTORY[_HISTORY_CURSOR] = _DocumentHistoryEntry(
            entry.document,
            entry.workspace,
            artifacts,
        )
        _HISTORY.append(_DocumentHistoryEntry(
            entry.document,
            entry.workspace,
            artifacts,
        ))
        if len(_HISTORY) > _HISTORY_LIMIT:
            drop = len(_HISTORY) - _HISTORY_LIMIT
            del _HISTORY[:drop]
        _HISTORY_CURSOR = len(_HISTORY) - 1
        _sync_artifact_retention()
        logger.debug(
            "Heavy compile checkpoint recorded. History cursor=%d/%d artifacts=%d ids=%s.",
            _HISTORY_CURSOR,
            len(_HISTORY) - 1,
            len(artifacts),
            [getattr(artifact, "node_id", None) for artifact in artifacts],
        )


def _sync_artifact_retention() -> None:
    if _retain_artifacts is None:
        return
    _retain_artifacts(tuple(
        artifact
        for entry in _HISTORY
        if isinstance(entry, _DocumentHistoryEntry)
        for artifact in entry.artifacts
    ))


def can_undo() -> bool:
    with _HISTORY_LOCK:
        return _HISTORY_CURSOR > 0


def can_redo() -> bool:
    with _HISTORY_LOCK:
        return 0 <= _HISTORY_CURSOR < len(_HISTORY) - 1


def _capture(workspace=None, document=None):
    if _capture_document is not None:
        return _DocumentHistoryEntry(
            document if document is not None else _capture_document(),
            workspace if workspace is not None else (
                _capture_workspace() if _capture_workspace is not None else None),
            tuple(_capture_artifacts()) if _capture_artifacts is not None else (),
        )
    return _serialize_graph()


def _restore(snapshot):
    # Rebuild the graph from a snapshot without recording it as a new history entry.
    global _RESTORING, _last_restore_event
    _require_persistence()
    _RESTORING = True
    _last_restore_event = None
    try:
        if _capture_document is not None:
            entry = snapshot if isinstance(snapshot, _DocumentHistoryEntry) else (
                _DocumentHistoryEntry(snapshot))
            materialized = _materialize_document(entry.document)
            if not getattr(materialized, "ok", False):
                return ApplyResult(ApplyStatus.REJECTED, "history candidate invalid")
            try:
                _last_restore_event = _replace_candidate(
                    materialized.candidate, entry.workspace)
                if _restore_artifacts is not None:
                    _restore_artifacts(entry.artifacts)
            except (TypeError, ValueError):
                return ApplyResult(ApplyStatus.REJECTED, "history candidate rejected")
            return ApplyResult(ApplyStatus.APPLIED)
        try:
            previous = _serialize_graph()
        except Exception:
            return ApplyResult(ApplyStatus.REJECTED, "current graph snapshot failed")
        _clear_graph()
        try:
            _apply_graph(snapshot)
        except _apply_error:
            _clear_graph()
            try:
                _apply_graph(previous)
            except _apply_error:
                return ApplyResult(ApplyStatus.DEGRADED, "history restore and rollback failed")
            return ApplyResult(ApplyStatus.ROLLED_BACK, "history restore failed; graph restored")
        return ApplyResult(ApplyStatus.APPLIED)
    finally:
        _RESTORING = False


def take_restore_event():
    global _last_restore_event
    with _HISTORY_LOCK:
        event = _last_restore_event
        _last_restore_event = None
        return event


def undo():
    """Step back one structural change. No-op at the oldest state or while compiling."""
    global _HISTORY_CURSOR
    with _HISTORY_LOCK:
        if _is_compile_busy():
            append_log("Cannot undo while a compile is running.", level="WARNING")
            return ApplyResult(ApplyStatus.REJECTED, "compile busy")
        if not can_undo():
            append_log("Nothing to undo.", level="INFO")
            return ApplyResult(ApplyStatus.REJECTED, "nothing to undo")
        candidate_cursor = _HISTORY_CURSOR - 1
        result = _restore(_HISTORY[candidate_cursor])
        if not result.ok:
            append_log("Undo failed; the current graph was not changed.", level="ERROR")
            return result
        _HISTORY_CURSOR = candidate_cursor
        append_log(
            f"Undo. History cursor={_HISTORY_CURSOR}/{len(_HISTORY) - 1}.",
            level="INFO")
        return result


def redo():
    """Step forward one undone change. No-op at the newest state or while compiling."""
    global _HISTORY_CURSOR
    with _HISTORY_LOCK:
        if _is_compile_busy():
            append_log("Cannot redo while a compile is running.", level="WARNING")
            return ApplyResult(ApplyStatus.REJECTED, "compile busy")
        if not can_redo():
            append_log("Nothing to redo.", level="INFO")
            return ApplyResult(ApplyStatus.REJECTED, "nothing to redo")
        candidate_cursor = _HISTORY_CURSOR + 1
        result = _restore(_HISTORY[candidate_cursor])
        if not result.ok:
            append_log("Redo failed; the current graph was not changed.", level="ERROR")
            return result
        _HISTORY_CURSOR = candidate_cursor
        append_log(
            f"Redo. History cursor={_HISTORY_CURSOR}/{len(_HISTORY) - 1}.",
            level="INFO")
        return result
