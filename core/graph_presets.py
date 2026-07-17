"""Save / load an entire working environment as a preset.

A preset captures the live graph, node panels, managed window geometry and visibility, native
dock layout, and optional practice state. This module owns the graph snapshot/restore machinery
(``serialize_graph`` / ``clear_all_nodes`` / ``apply_graph``) that both the preset files
and the undo/redo history (``core.graph_history``) are built on.

Cross-module references to ``core.pipeline`` (and ``core.graph_history``) are imported lazily
inside the functions to avoid a circular import at module load time; ``core.pipeline`` imports
this module's callers indirectly via ``core.graph_history``.
"""

import os
import json
import logging
import dearpygui.dearpygui as dpg
from app_logging import append_log
from core.base_node import NodeProcessingMode, NodeUIMode
from core.graph_validation import validate_connection, validate_preset
from core.graph_serializer import GraphSerializer
from core.graph_history import configure_persistence, reset_history
from core.graph_documents import (
    GraphDocument,
    NodeDocument,
    PanelDocument,
    Position,
    PracticeDocument,
    PROJECT_DOCUMENT_SCHEMA_VERSION,
    ProjectDocument,
    ProjectMetadata,
    WorkspaceDocument,
    migrate_legacy_preset,
)
from core.persistence_contracts import (
    ApplyResult,
    ApplyStatus,
    RestoreToken,
    WorkspaceSnapshot,
)
from core.preset_store import PresetStore, PresetStoreError


logger = logging.getLogger(__name__)


class PresetApplyError(Exception):
    """Raised when reconstructing a graph from a serialized dict fails partway.

    Signals ``load_preset`` that the editor now holds a half-built graph and must be cleared and
    rolled back to the pre-load snapshot. Structural problems are caught earlier by
    ``core.graph_validation.validate_preset``; this covers runtime failures during node/link
    creation (a node constructor or ``draw_node`` throwing, a compile pass failing, ...).
    """

# Absolute project root (one level above this core/ package) so resource folders such as
# ``presets`` resolve regardless of the process working directory.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Presets live next to the project root so they ship and persist alongside the layout file.
PRESETS_DIR = os.path.join(_PROJECT_ROOT, "presets")
DEFAULT_PRESET_NAME = "default"


def create_preset_store() -> PresetStore:
    return PresetStore(PRESETS_DIR, os_module=os, json_module=json)


def _node_class_lookup():
    # NODE_REGISTRY is keyed by display_name; presets reference the stable class name instead.
    from core.pipeline import NODE_REGISTRY
    return {cls.__name__: cls for cls in NODE_REGISTRY.values()}


def _restore_baked(node, signature: str):
    """Reload a heavy node's baked output from the disk cache by its saved fingerprint.

    Sets ``is_baked`` + ``baked_signature`` + ``cached_outputs`` on a hit so the node reads as
    baked (green) and streams real audio without a re-bake. Silent no-op on a miss.
    """
    from core import disk_cache, mem_cache
    from core.pipeline import _roles_to_outputs
    disk_roles = disk_cache.load(type(node).__name__, signature)
    if disk_roles is None:
        return
    try:
        node.cached_outputs = _roles_to_outputs(node, disk_roles)
    except Exception:
        logger.exception("Failed to rehydrate baked output for %s", type(node).__name__)
        return
    node.is_baked = True
    node.baked_signature = signature
    mem_cache.touch(node)   # register in the LRU so the budget accounts for the reloaded bytes


def clear_all_nodes():
    """Tear down every node and link currently in the editor (no per-node recompiles)."""
    from core import mem_cache
    from core.pipeline import GRAPH_STATE
    from ui.graph_projection import NODE_EDITOR_LINKS
    NODE_EDITOR_LINKS.clear()
    GRAPH_STATE.clear_connections()

    for node_tag in list(GRAPH_STATE.nodes.keys()):
        node = GRAPH_STATE.nodes[node_tag]
        if node.config.ui is NodeUIMode.INSPECTOR_PANEL and dpg.does_item_exist(node.panel_window_tag):
            dpg.delete_item(node.panel_window_tag)
        node._clear_ui_resources()
        mem_cache.forget(node_tag)
        GRAPH_STATE.stale_nodes.discard(node_tag)
        GRAPH_STATE.failed_nodes.discard(node_tag)
        if dpg.does_item_exist(node_tag):
            dpg.delete_item(node_tag)
        del GRAPH_STATE.nodes[node_tag]
    GRAPH_STATE.pin_owners.clear()
    GRAPH_STATE.cycle_nodes.clear()
    GRAPH_STATE.publish_snapshot()


def serialize_graph() -> dict:
    """Snapshot the live graph (nodes, params, positions, links) into a plain dict."""
    from core.pipeline import GRAPH_STATE, get_node_tag_from_pin
    nodes = []
    index_of = {}
    for idx, (node_tag, node) in enumerate(GRAPH_STATE.nodes.items()):
        index_of[node_tag] = idx
        pos = dpg.get_item_pos(node_tag) if dpg.does_item_exist(node_tag) else [0, 0]
        entry = {
            "class": type(node).__name__,
            "pos": [float(pos[0]), float(pos[1])],
            "params": node.serialize_params(),
        }
        if node.custom_name:
            entry["custom_name"] = node.custom_name
        # Non-slider state (e.g. an Audio Input's file path). Only emitted when non-empty so
        # presets stay lean and old readers ignore it. See BaseNode.serialize_extra.
        extra = node.serialize_extra()
        if extra:
            entry["extra"] = extra
        # Baked heavy-output identity: the fingerprint of the last successful bake. On restore
        # (undo/redo) we rehydrate the cached output from the disk cache by this key so heavy
        # nodes stay green instead of needing a manual re-bake. See apply_graph.
        if node.config.processing is NodeProcessingMode.BAKED and getattr(node, "is_baked", False):
            sig = getattr(node, "baked_signature", None)
            if sig:
                entry["baked"] = sig
        # Panel windows are separate floating windows opened on demand. Record which ones are
        # open and where, so a preset restores the arranged workspace (fretboard / chord / mixer
        # tiled the way the user left it). Absent key => panel was closed. See open_panel().
        panel = node.serialize_panel()
        if panel is not None:
            entry["panel"] = panel
        nodes.append(entry)

    links = []
    for connection in sorted(
            GRAPH_STATE.iter_connections(), key=lambda item: item.connection_id):
        out_tag = get_node_tag_from_pin(connection.output_pin)
        in_tag = get_node_tag_from_pin(connection.input_pin)
        if out_tag not in index_of or in_tag not in index_of:
            continue
        # Store the pin suffix (everything after the node tag, e.g. "_out_audio::AUDIO")
        # so the link survives node-tag renumbering on reload.
        links.append([
            index_of[out_tag], connection.output_pin[len(out_tag):],
            index_of[in_tag], connection.input_pin[len(in_tag):],
            connection.connection_id,
        ])

    return {"nodes": nodes, "links": links}


def _discard_partial_node(node):
    """Delete any DPG items a node created before it finished drawing / entered the runtime.

    Mirrors the teardown ``clear_all_nodes()`` does for a *registered* node, but for a node that
    failed partway through ``draw_node()`` and never entered ``GraphState.nodes`` (so the normal
    teardown can't reach it). Best-effort and never raises, so it is safe on the exception path.
    """
    try:
        if node.config.ui is NodeUIMode.INSPECTOR_PANEL and dpg.does_item_exist(node.panel_window_tag):
            dpg.delete_item(node.panel_window_tag)
        node._clear_ui_resources()
        if dpg.does_item_exist(node.node_tag):
            dpg.delete_item(node.node_tag)
    except Exception:
        logger.exception("Failed to discard partially-created node %s", getattr(node, "node_tag", "?"))


def apply_graph(data: dict):
    """Rebuild a graph from a serialized dict. The editor is assumed already cleared."""
    from core.pipeline import (
        GRAPH_STATE, SPAWN_BASE, _index_node_pins, get_node_unique_tag,
        light_node_compile,
    )
    lookup = _node_class_lookup()
    created = []

    try:
        for nd in data.get("nodes", []):
            cls = lookup.get(nd.get("class"))
            if cls is None:
                append_log(f"Preset skipped unknown node class '{nd.get('class')}'.", level="WARNING")
                created.append(None)
                continue
            tag = get_node_unique_tag(cls)
            node = cls(tag)
            try:
                node.set_custom_name(nd.get("custom_name", ""), sync_ui=False)
                # Apply params before draw_node so sliders render with the restored values.
                node.apply_params(nd.get("params", {}))
                node.draw_node(parent="node_editor")
                # Extra (non-slider) state after draw_node, so overrides can touch node-body widgets.
                extra = nd.get("extra")
                if extra:
                    node.apply_extra(extra)
                pos = nd.get("pos", SPAWN_BASE)
                dpg.set_item_pos(tag, [pos[0], pos[1]])
            except Exception:
                # draw_node() may have already created the dpg.node (and possibly a panel
                # window) before throwing. The node is not yet in GraphState.nodes, so the
                # rollback's clear_all_nodes() cannot see it -- delete its items here so a
                # mid-draw failure leaves no orphan DPG item behind, then re-raise.
                _discard_partial_node(node)
                raise
            GRAPH_STATE.nodes[tag] = node
            _index_node_pins(node)
            created.append(node)

        # Rehydrate baked heavy outputs from the disk cache so restored heavy nodes stay green (no
        # manual re-bake after undo/redo). The disk cache is content-addressed by the saved
        # fingerprint, and a restored snapshot reproduces the same graph, so we trust the stored
        # signature directly (no fingerprint recompute -> no dependence on link/traversal order). A
        # miss (e.g. the disk cache was wiped at startup) just leaves the node not-baked, as before.
        for node, nd in zip(created, data.get("nodes", [])):
            if node is not None and nd.get("baked"):
                _restore_baked(node, nd["baked"])

        # Restore panel windows after drawing nodes. A panel is a top-level dpg.window and must
        # not be parented into the node editor. `created` is index-aligned
        # with data["nodes"], so we can pair each saved panel spec back to its node.
        for node, nd in zip(created, data.get("nodes", [])):
            if node is None:
                continue
            panel = nd.get("panel")
            if panel and node.config.ui is NodeUIMode.INSPECTOR_PANEL:
                node.open_panel(pos=panel.get("pos"), size=panel.get("size"))

        for link in data.get("links", []):
            try:
                out_idx, out_suffix, in_idx, in_suffix = link[:4]
            except (ValueError, TypeError):
                continue
            connection_id = link[4] if len(link) == 5 else None
            if not isinstance(out_suffix, str) or not isinstance(in_suffix, str):
                continue
            out_node = created[out_idx] if isinstance(out_idx, int) and 0 <= out_idx < len(created) else None
            in_node = created[in_idx] if isinstance(in_idx, int) and 0 <= in_idx < len(created) else None
            if out_node is None or in_node is None:
                continue
            output_pin = out_node.node_tag + out_suffix
            input_pin = in_node.node_tag + in_suffix
            # Same connection rules as interactive wiring (core.graph_validation): a hand-edited or
            # third-party preset cannot smuggle in a type-mismatched, mis-directed, self-loop or
            # double-fed link. The loader accumulates these as warnings rather than aborting.
            result = validate_connection(
                output_pin,
                input_pin,
                out_node,
                in_node,
                GRAPH_STATE.connection_id_by_input,
            )
            if not result.ok:
                append_log(f"Preset skipped invalid link {output_pin} -> {input_pin}: {result.message}", level="WARNING")
                continue
            connection = GRAPH_STATE.add_connection(
                output_pin,
                input_pin,
                connection_id=connection_id,
            )
            from ui.graph_projection import NODE_EDITOR_LINKS
            NODE_EDITOR_LINKS.add("node_editor", connection)

        light_node_compile()
        GRAPH_STATE.publish_snapshot()
    except PresetApplyError:
        raise
    except Exception as exc:
        # A partial graph now exists; surface it to load_preset so it can clear + roll back.
        raise PresetApplyError(str(exc)) from exc


def list_presets() -> list:
    return create_preset_store().list_names()


# --- Transport-bar "globals" injection --------------------------------------------------------
# The playbar settings (speed / trims / count-in / metronome / snap) live in the ui layer, but
# core must not import ui (layering). The UI registers a getter + applier at startup; presets
# saved "with parameters" store the getter's dict and loading calls the applier. Both stay None
# in headless/tests, so save/load degrade to graph-only cleanly.
_globals_getter = None   # () -> dict
_globals_applier = None  # (dict) -> None


def register_globals_hooks(getter, applier):
    global _globals_getter, _globals_applier
    _globals_getter = getter
    _globals_applier = applier


def _strip_params(data: dict) -> dict:
    """Return a copy of a serialized graph with per-node tunables removed.

    Drops params / extra (file paths) / baked identity, keeping only structure + positions +
    panel layout. This is the "template" form a preset takes when saved WITHOUT parameters:
    the workspace (which nodes, how wired, panels arranged) without any song-specific state.
    """
    from core.pipeline import GRAPH_STATE
    return GraphSerializer.template(
        data,
        (node.serialize_template_extra() for node in GRAPH_STATE.nodes.values()),
    )


def save_preset(name: str = DEFAULT_PRESET_NAME, include_params: bool = False) -> bool:
    """Save the current graph as a preset. Returns True only when the file was fully written.

    ``include_params=False`` (default) saves a lean TEMPLATE: structure + node positions + panel
    layout only. ``include_params=True`` also stores every node's slider values, file paths, baked
    identity, and the transport-bar globals -- a fully restorable practice setup.

    The write is atomic: the JSON is flushed + fsync'd to a uniquely-named temp file in the same
    directory, then ``os.replace()``d over the target. A crash, full disk or sync-tool interference
    mid-write therefore leaves the previous preset intact instead of a half-written file. Callers
    must gate any follow-up (set-as-startup, close dialog) on the returned bool.
    """
    data = build_preset_document(include_params)
    if data is None:
        return False
    try:
        create_preset_store().write_atomic(name, data)
    except PresetStoreError:
        logger.exception("Failed to save preset '%s'", name)
        append_log(f"Failed to save preset '{name}'.", level="ERROR")
        return False
    kind = "with parameters" if include_params else "template"
    append_log(f"Saved node preset '{name}' ({len(data['nodes'])} nodes, {kind}).", level="INFO")
    return True


def build_preset_document(include_params: bool) -> dict | None:
    """Capture a detached save document on the GUI thread without performing file I/O."""
    from core.pipeline import is_compile_busy
    if is_compile_busy():
        append_log("Cannot save a preset while a compile is running.", level="WARNING")
        return None
    try:
        data = serialize_graph()
        if include_params:
            if _globals_getter is not None:
                data["globals"] = _globals_getter()
        else:
            data = _strip_params(data)
        return GraphSerializer.snapshot(data).graph
    except Exception:
        logger.exception("Failed to capture preset document")
        append_log("Failed to capture the current workspace for saving.", level="ERROR")
        return None


def build_preset_project_document(
    include_params: bool,
    graph: GraphDocument | None = None,
    project_id: str = "project_1",
) -> ProjectDocument | None:
    """Combine an owner-captured graph with GUI-owned workspace and practice state."""
    if graph is not None:
        try:
            from core.pipeline import runtime_node_view

            positions = {}
            panels = {}
            from ui.workspace_layout import capture_dock_layout, capture_windows

            for node_document in graph.nodes:
                node = runtime_node_view(node_document.node_id)
                if node is None:
                    continue
                if dpg.does_item_exist(node_document.node_id):
                    position = dpg.get_item_pos(node_document.node_id)
                    positions[node_document.node_id] = Position(
                        float(position[0]), float(position[1]))
                panel = node.serialize_panel()
                if panel is not None:
                    panel_position = panel.get("pos")
                    panel_size = panel.get("size")
                    panels[node_document.node_id] = PanelDocument(
                        None if panel_position is None else Position(
                            float(panel_position[0]), float(panel_position[1])),
                        None if panel_size is None else Position(
                            float(panel_size[0]), float(panel_size[1])),
                    )
            practice = None
            if include_params and _globals_getter is not None:
                practice = PracticeDocument(_globals_getter())
            if not include_params:
                from core.pipeline import runtime_node_view

                def template_extra(node_id):
                    runtime = runtime_node_view(node_id)
                    return runtime.serialize_template_extra() if runtime is not None else {}

                graph = GraphDocument(
                    graph.schema_version,
                    graph.graph_id,
                    tuple(NodeDocument(
                        node.node_id,
                        node.node_type,
                        node.node_version,
                        node.display_name,
                        extra=template_extra(node.node_id),
                    ) for node in graph.nodes),
                    graph.connections,
                    graph.inputs,
                    graph.outputs,
                )
            return ProjectDocument(
                PROJECT_DOCUMENT_SCHEMA_VERSION,
                project_id,
                graph,
                WorkspaceDocument(
                    positions,
                    panels,
                    capture_windows(),
                    capture_dock_layout(),
                ),
                practice,
                ProjectMetadata({"preset_kind": (
                    "practice" if include_params else "template")}),
            )
        except Exception:
            logger.exception("Failed to capture project workspace")
            append_log("Failed to capture the current workspace for saving.", level="ERROR")
            return None

    # Compatibility path for callers that still request a complete legacy GUI snapshot.
    data = build_preset_document(include_params)
    if data is None:
        return None
    migrated = migrate_legacy_preset(data, graph_id="project_1.graph")
    if not migrated.ok:
        logger.error("Failed to migrate captured preset: %s", migrated.diagnostics)
        return None
    return ProjectDocument(
        schema_version=PROJECT_DOCUMENT_SCHEMA_VERSION,
        project_id=project_id,
        graph=migrated.graph,
        workspace=migrated.workspace,
        practice=migrated.practice,
        metadata=ProjectMetadata({"preset_kind": (
            "practice" if include_params else "template")}),
    )


def load_preset(name: str = DEFAULT_PRESET_NAME):
    from core.pipeline import is_compile_busy
    if is_compile_busy():
        append_log("Cannot load a preset while a compile is running.", level="WARNING")
        return ApplyResult(ApplyStatus.REJECTED, "compile busy")
    store = create_preset_store()
    if not store.exists(name):
        append_log(f"Preset '{name}' not found.", level="ERROR")
        return ApplyResult(ApplyStatus.REJECTED, "preset not found")
    # A malformed or unreadable preset must not take the app down. This compatibility path can
    # still be called directly by tests and integrations that have not moved to ProjectDocument.
    try:
        data = store.read(name)
    except PresetStoreError:
        logger.exception("Failed to read preset '%s'", name)
        append_log(f"Preset '{name}' is missing or corrupt; skipped.", level="ERROR")
        return ApplyResult(ApplyStatus.REJECTED, "preset unreadable")

    # Structural validation happens BEFORE we touch the live graph: a malformed-but-parseable
    # preset (wrong types, broken link shape, ...) must leave the current work untouched rather
    # than fail halfway after the editor has already been cleared.
    validation = validate_preset(data, known_classes=set(_node_class_lookup()))
    if not validation.ok:
        for err in validation.errors:
            append_log(f"Preset '{name}': {err}", level="ERROR")
        append_log(f"Preset '{name}' is invalid; current graph left unchanged.", level="ERROR")
        return ApplyResult(ApplyStatus.REJECTED, "preset invalid")
    return apply_preset_candidate(name, data, tuple(validation.warnings))


def reject_preset_load(name: str, errors: tuple[str, ...]) -> ApplyResult:
    for error in errors:
        append_log(f"Preset '{name}': {error}", level="ERROR")
    append_log(f"Preset '{name}' was not loaded; current graph left unchanged.", level="ERROR")
    return ApplyResult(ApplyStatus.REJECTED, "; ".join(errors))


def apply_preset_candidate(
        name: str, data: dict, warnings: tuple[str, ...] = ()) -> ApplyResult:
    from core.pipeline import is_compile_busy

    if is_compile_busy():
        append_log("Cannot load a preset while a compile is running.", level="WARNING")
        return ApplyResult(ApplyStatus.REJECTED, "compile busy")
    for warning in warnings:
        append_log(f"Preset '{name}': {warning}", level="WARNING")

    # Snapshot the current graph so a mid-apply runtime failure can be rolled back to exactly what
    # the user had open, instead of leaving a half-built or empty editor.
    try:
        previous_globals = _globals_getter() if _globals_getter is not None else None
        restore_token = RestoreToken(GraphSerializer.snapshot(
            serialize_graph(),
            previous_globals if isinstance(previous_globals, dict) else None,
        ))
    except Exception:
        logger.exception("Failed to snapshot current graph before loading preset '%s'", name)
        append_log(f"Preset '{name}' was not loaded because the current graph could not be saved.", level="ERROR")
        return ApplyResult(ApplyStatus.REJECTED, "current workspace snapshot failed")
    clear_all_nodes()
    try:
        apply_graph(data)
    except PresetApplyError:
        logger.exception("Preset '%s' failed to apply; rolling back", name)
        append_log(f"Preset '{name}' failed to load; restoring the previous graph.", level="ERROR")
        clear_all_nodes()
        try:
            apply_graph(restore_token.snapshot.graph)
        except PresetApplyError:
            logger.exception("Rollback of preset '%s' also failed", name)
            append_log("Rollback failed; the editor may be empty.", level="ERROR")
            return ApplyResult(ApplyStatus.DEGRADED, "apply and rollback failed")
        return ApplyResult(ApplyStatus.ROLLED_BACK, "apply failed; previous graph restored")

    # Restore the transport-bar globals if this preset carried them (saved "with parameters").
    if isinstance(data.get("globals"), dict) and _globals_applier is not None:
        try:
            _globals_applier(data["globals"])
        except Exception:
            logger.exception("Preset globals applier failed")
            append_log("Preset globals failed to apply; restoring the previous workspace.", level="ERROR")
            clear_all_nodes()
            try:
                apply_graph(restore_token.snapshot.graph)
                if restore_token.snapshot.globals is not None:
                    _globals_applier(restore_token.snapshot.globals)
            except Exception:
                logger.exception("Workspace rollback after globals failure also failed")
                append_log("Rollback failed; the workspace may be partially restored.", level="ERROR")
                return ApplyResult(ApplyStatus.DEGRADED, "globals apply and rollback failed")
            return ApplyResult(ApplyStatus.ROLLED_BACK, "globals apply failed; workspace restored")
    # A loaded preset is a fresh baseline, not an undo step: reseed history so undo can't step
    # back into the graph that was open before the load.
    reset_history()
    append_log(f"Loaded node preset '{name}'.", level="INFO")
    return ApplyResult(ApplyStatus.APPLIED)


def get_startup_preset_name() -> str:
    """The preset auto-loaded on launch. User-chosen (via 'set as default'), else the bundled one."""
    from core import settings
    return settings.get("startup_preset", DEFAULT_PRESET_NAME)


def set_startup_preset_name(name: str):
    """Mark ``name`` as the preset to auto-load on launch (persisted in user settings)."""
    from core import settings
    settings.set("startup_preset", name)


def startup_preset_name_if_available() -> str | None:
    """Resolve the configured startup preset without reading or applying its document."""
    name = get_startup_preset_name()
    store = create_preset_store()
    if store.exists(name):
        return name
    if name != DEFAULT_PRESET_NAME and store.exists(DEFAULT_PRESET_NAME):
        return DEFAULT_PRESET_NAME
    return None


configure_persistence(serialize_graph, clear_all_nodes, apply_graph, PresetApplyError)
