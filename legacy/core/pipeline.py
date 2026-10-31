"""Execution engine for the node graph.

Responsibilities:
  * Discover and register node plugins from the ``nodes`` package.
  * Resolve pin ownership / types and build the runtime topology.
  * Run the graph in light (propagate only) or heavy (re-bake) compile passes.
    * Apply runtime graph mutations requested by the Graph owner.

Node-specific behaviour lives in the individual plugins under ``nodes`` and is
intentionally out of scope for this module.
"""

from collections import deque
import contextlib
from dataclasses import dataclass
import importlib
import inspect
import os
import logging
import threading
import numpy as np
from app_logging import append_log
from core.base_node import *
from core import disk_cache
from core import mem_cache
from core.compile_coordinator import CompileCancelled, CompileCoordinator
from core.graph_state import GraphConnection, GraphState
from core.graph_documents import PanelDocument, Position, WorkspaceDocument, export_graph_document
from core.graph_contracts import (
    CompileKind,
    GraphNodePosition,
    GraphPanelGeometry,
    GraphReplaced,
    GraphRenderState,
    GraphWindowState,
    NodeDeleted,
    NodeSpawned,
)
from core.graph_validation import validate_connection
from core.transport import TRANSPORT
from core.graph_history import (
    configure_compile_busy,
    record_compile_checkpoint,
    record_history,
    refresh_current_artifacts,
)
from core import runtime_artifacts


logger = logging.getLogger(__name__)

# Absolute project root (one level above this core/ package) so the ``nodes`` plugin folder
# resolves regardless of the process working directory.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Runtime registry remains separate from per-graph state: it describes installed node classes.
NODE_REGISTRY: dict[str, type[BaseNode]] = {}   # display_name -> node class (used to spawn new nodes)
GRAPH_STATE = GraphState()
_HISTORY_NODE_POSITIONS: dict[str, Position] = {}
_HISTORY_NODE_PANELS: dict[str, PanelDocument] = {}
_HISTORY_WORKSPACE_LOCK = threading.RLock()

# Cascade placement of newly spawned nodes.
SPAWN_BASE = [40, 40]
SPAWN_STEP = 30
SPAWN_WRAP = 20  # number of steps before the cascade wraps back to the base

COMPILE_COORDINATOR = CompileCoordinator()


@dataclass
class _CompileMetadata:
    execution_order: list[str]
    stale_nodes: set[str]
    failed_nodes: set[str]
    cycle_nodes: set[str]
    heavy_descendants_cache: dict[str, frozenset[str]]
    success: bool = True


@dataclass(frozen=True, slots=True)
class CompileMetadataResult:
    success: bool
    execution_order: tuple[str, ...]
    node_ids: frozenset[str]
    stale_node_ids: frozenset[str]
    failed_node_ids: frozenset[str]
    cycle_node_ids: frozenset[str]


_COMPILE_LOCAL = threading.local()


def _metadata():
    return getattr(_COMPILE_LOCAL, "metadata", GRAPH_STATE)


def _is_detached_compile() -> bool:
    return hasattr(_COMPILE_LOCAL, "metadata")


# --------------------------------------------------------------------------- #
# Node registration & themes
# --------------------------------------------------------------------------- #

def scan_and_register_nodes():
    plugin_dir = os.path.join(_PROJECT_ROOT, "nodes")
    if not os.path.exists(plugin_dir):
        os.makedirs(plugin_dir)
        return

    for entry in os.listdir(plugin_dir):
        node_pkg_dir = os.path.join(plugin_dir, entry)
        if not os.path.isdir(node_pkg_dir) or entry == "__pycache__":
            continue

        node_file = os.path.join(node_pkg_dir, "node.py")
        if not os.path.exists(node_file):
            continue

        module_name = f"nodes.{entry}.node"
        try:
            module = importlib.import_module(module_name)

            for name, obj in inspect.getmembers(module, inspect.isclass):
                if issubclass(obj, BaseNode) and obj != BaseNode:
                    display_name = getattr(obj, "display_name", name)
                    if display_name in NODE_REGISTRY:
                        logger.warning(f"Duplicate node display_name '{display_name}' from '{module_name}'; overwriting previous registration.")
                    NODE_REGISTRY[display_name] = obj
                    logger.info(f"Registered node plugin '{display_name}' from '{module_name}'.")
        except Exception:
            logger.exception(f"Failed to load node plugin '{module_name}'")
            continue

def build_all_node_themes():
    BaseNode.build_global_themes()
    for node_cls in set(NODE_REGISTRY.values()):
        node_cls.build_class_themes()

# --------------------------------------------------------------------------- #
# Pin resolution helpers
# --------------------------------------------------------------------------- #

def get_pin_type_from_pin(pin_tag: str) -> DataType:
    try:
        if "::" not in pin_tag:
            logger.warning(f"Pin tag '{pin_tag}' does not contain a type specifier.")
            return None

        # Example: "node_Mixer_1_in_ch1::AUDIO" -> "AUDIO"
        type_name = pin_tag.split("::")[-1]
        return DataType[type_name]  # Convert back to Enum type
    except (KeyError, ValueError):
        logger.warning(f"Unable to resolve pin type from pin '{pin_tag}'.")
    return None

def get_node_tag_from_pin(pin_tag: str) -> str:
    # Fast path: O(1) lookup in the pin-ownership index. Pin tags embed a monotonic node id
    # and are never reused, so an entry stays valid while the node remains in runtime.
    owner = GRAPH_STATE.pin_owners.get(pin_tag)
    if owner is not None and owner in GRAPH_STATE.nodes:
        return owner

    # Fallback: resolve by exact pin ownership and self-heal the index so the next lookup is
    # O(1). Prefix matching is avoided: it is ambiguous once node indices share a prefix
    # (e.g. node_Mixer_1 vs node_Mixer_10).
    for node_tag, node in GRAPH_STATE.nodes.items():
        if pin_tag in node.input_spec or pin_tag in node.output_spec:
            GRAPH_STATE.pin_owners[pin_tag] = node_tag
            return node_tag

    logger.warning(f"No matching node found for pin '{pin_tag}'.")
    return None


def _index_node_pins(node: BaseNode) -> None:
    # Register every pin this node owns so get_node_tag_from_pin resolves it in O(1).
    for pin in node.input_spec:
        GRAPH_STATE.pin_owners[pin] = node.node_tag
    for pin in node.output_spec:
        GRAPH_STATE.pin_owners[pin] = node.node_tag


def _deindex_node_pins(node: BaseNode) -> None:
    # Drop a node's pins from the reverse index when it is torn down.
    for pin in node.input_spec:
        GRAPH_STATE.pin_owners.pop(pin, None)
    for pin in node.output_spec:
        GRAPH_STATE.pin_owners.pop(pin, None)


# --------------------------------------------------------------------------- #
# Topology analysis
# --------------------------------------------------------------------------- #

def kahn_topological_sort(nodes_in_runtime, node_connections) -> dict:
    in_degree = {node: 0 for node in nodes_in_runtime}
    adj_list = {node: [] for node in nodes_in_runtime}

    for connection in node_connections:
        input_pin = connection.input_pin
        output_pin = connection.output_pin
        src_node = get_node_tag_from_pin(output_pin)
        dst_node = get_node_tag_from_pin(input_pin)

        if src_node and dst_node and src_node != dst_node:
            adj_list[src_node].append(dst_node)
            in_degree[dst_node] += 1

    queue = deque([node for node, deg in in_degree.items() if deg == 0])
    sorted_order = []

    while queue:
        curr_node = queue.popleft()
        sorted_order.append(curr_node)

        for neighbor in adj_list[curr_node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    has_cycle = len(sorted_order) != len(nodes_in_runtime)
    # Nodes that never reached in_degree 0 are the ones trapped in the cycle.
    cycle_nodes = [node for node, deg in in_degree.items() if deg > 0] if has_cycle else []

    return {
        "has_cycle": has_cycle,
        "order": [] if has_cycle else sorted_order,
        "cycle_nodes": cycle_nodes,
    }

def refresh_node_highlights(cycle_nodes):
    metadata = _metadata()
    cycle_set = set(cycle_nodes)
    metadata.cycle_nodes.clear()
    metadata.cycle_nodes.update(cycle_set)
    if _is_detached_compile():
        return
    for tag, node in GRAPH_STATE.nodes.items():
        # A trapped cycle is an error (red); a stale heavy node OR a failed bake is a warning
        # (yellow) -- both are local problems sharing the same skin (see _restore_node_theme).
        # Cycles take priority over staleness/failure.
        if tag in cycle_set:
            node._draw_highlighted_node()
        elif tag in metadata.stale_nodes or tag in metadata.failed_nodes:
            node._draw_warning_node()
        else:
            node._draw_default_node()

def _resolve_execution_order() -> bool:
    # Topological sort + cycle highlight + update GRAPH_STATE.execution_order.
    # Returns True when a valid order exists (including the empty graph), False on a cycle.
    # Topology is about to change shape: drop the cached heavy-descendant sets.
    metadata = _metadata()
    metadata.heavy_descendants_cache.clear()

    if not GRAPH_STATE.nodes:
        metadata.execution_order.clear()
        logger.debug("No nodes in runtime; execution order cleared.")
        return True

    result = kahn_topological_sort(GRAPH_STATE.nodes, GRAPH_STATE.iter_connections())
    refresh_node_highlights(result["cycle_nodes"])

    if result["has_cycle"]:
        metadata.execution_order.clear()
        append_log(f"Cycle detected involving {result['cycle_nodes']}. Break the loop to run.", level="ERROR")
        return False

    metadata.execution_order.clear()
    metadata.execution_order.extend(result["order"])
    logger.debug(f"Topological sort OK; execution order: {metadata.execution_order}")
    return True


# --------------------------------------------------------------------------- #
# Pipeline execution
# --------------------------------------------------------------------------- #

def _gather_inputs(node_instance: BaseNode):
    # Pull upstream outputs into this node's input_values. Unconnected inputs are normal and
    # skipped silently; only genuine resolution failures are logged.
    node_instance.input_values.clear()

    for input_pin in node_instance.input_spec.keys():
        upstream_output_pin = GRAPH_STATE.source_for_input(input_pin)
        if upstream_output_pin is None:
            continue

        upstream_node_tag = get_node_tag_from_pin(upstream_output_pin)
        if not upstream_node_tag:
            logger.error(f"Failed to resolve upstream node for pin '{upstream_output_pin}' feeding '{input_pin}' of node [{node_instance.node_tag}].")
            continue

        upstream_node = GRAPH_STATE.nodes[upstream_node_tag]
        if upstream_output_pin in upstream_node.output_values:
            node_instance.input_values[input_pin] = upstream_node.output_values[upstream_output_pin]

def _collect_ancestors(node_tag: str) -> set:
    # All nodes that feed (directly or transitively) into node_tag, walking upstream via
    # GraphState's input index. Excludes node_tag itself.
    ancestors = set()
    stack = [node_tag]
    while stack:
        current = stack.pop()
        node = GRAPH_STATE.nodes.get(current)
        if node is None:
            continue
        for input_pin in node.input_spec.keys():
            upstream_output_pin = GRAPH_STATE.source_for_input(input_pin)
            if upstream_output_pin is None:
                continue
            up_tag = get_node_tag_from_pin(upstream_output_pin)
            if up_tag and up_tag != current and up_tag not in ancestors:
                ancestors.add(up_tag)
                stack.append(up_tag)
    return ancestors

def _collect_descendants(node_tag: str) -> set:
    # All nodes that node_tag feeds (directly or transitively). Symmetric to _collect_ancestors:
    # that walk reads each node's input pins via its input_spec; here we read each node's OUTPUT
    # pins via output_spec and follow them forward. Build the reverse index
    # (output_pin -> [input_pins]) ONCE up front -- otherwise
    # the walk rescans the whole connection dict per node popped (O(V*E)); with the index it is
    # O(V+E), matching the ancestor walk's cost. Excludes node_tag itself.
    consumers: dict[str, list[str]] = {}
    for connection in GRAPH_STATE.iter_connections():
        consumers.setdefault(connection.output_pin, []).append(connection.input_pin)

    descendants = set()
    stack = [node_tag]
    while stack:
        current = stack.pop()
        node = GRAPH_STATE.nodes.get(current)
        if node is None:
            continue
        for output_pin in node.output_spec.keys():
            for input_pin in consumers.get(output_pin, ()):
                down_tag = get_node_tag_from_pin(input_pin)
                if down_tag and down_tag != current and down_tag not in descendants:
                    descendants.add(down_tag)
                    stack.append(down_tag)
    return descendants

def mark_downstream_heavy_stale(node_tag: str):
    # An upstream streamable (live) param changed: every heavy node downstream now holds a
    # stale baked output. Flag them yellow (same theme as a cycle/error node) until re-baked.
    # Early-out keeps a slider drag cheap once everything affected is already flagged.
    # A live param also changed what the playback chain sounds like -> the waveform overview is
    # now out of date; flag it (O(1)) so the GUI's poll recomputes it. Done before the early-out
    # so it still fires when the heavy nodes are already flagged stale.
    TRANSPORT.mark_waveform_dirty()
    heavy = _heavy_descendants(node_tag)
    if heavy <= _metadata().stale_nodes:
        return
    _apply_stale(heavy)

def mark_structural_heavy_stale(node_tag: str):
    # A connection touching node_tag changed (wire added/removed, or an upstream node deleted):
    # node_tag's own baked output (if heavy) and every heavy node downstream are now stale.
    TRANSPORT.mark_waveform_dirty()   # the audible chain may have changed -> refresh the waveform
    affected = set(_heavy_descendants(node_tag))
    node = GRAPH_STATE.nodes.get(node_tag)
    if node is not None and node.config.processing is NodeProcessingMode.BAKED:
        affected.add(node_tag)
    _apply_stale(affected)

def _heavy_descendants(node_tag: str) -> frozenset:
    # BAKED nodes downstream of node_tag. Cached per source; the cache is cleared by
    # _resolve_execution_order() whenever the topology changes.
    metadata = _metadata()
    cached = metadata.heavy_descendants_cache.get(node_tag)
    if cached is not None:
        return cached
    result = frozenset(
        tag for tag in _collect_descendants(node_tag)
        if (n := GRAPH_STATE.nodes.get(tag)) is not None and n.config.processing is NodeProcessingMode.BAKED
    )
    metadata.heavy_descendants_cache[node_tag] = result
    return result

def _restore_node_theme(node_tag: str):
    # Repaint a node to its correct theme after a stale/failed flag changed. Both conditions share
    # the same yellow warning skin, so a node stays yellow while EITHER holds; only when neither
    # remains does it go back to default. (Cycle red is owned by refresh_node_highlights.)
    node = GRAPH_STATE.nodes.get(node_tag)
    if node is None:
        return
    metadata = _metadata()
    if _is_detached_compile():
        return
    if node_tag in metadata.stale_nodes or node_tag in metadata.failed_nodes:
        node._draw_warning_node()
    else:
        node._draw_default_node()

def _apply_stale(tags):
    # Flag each not-yet-stale heavy node yellow (idempotent: already-flagged nodes are skipped,
    # so repeated calls during a drag do no redraw).
    metadata = _metadata()
    for tag in tags:
        if tag in metadata.stale_nodes:
            continue
        metadata.stale_nodes.add(tag)
        if _is_detached_compile():
            continue
        node = GRAPH_STATE.nodes.get(tag)
        if node is not None:
            node._draw_warning_node()

def _clear_stale(node_tag: str):
    # A heavy node was just (re)baked or confirmed fresh: drop its stale flag and restore the
    # theme (stays yellow if it's still failed; cycle red is redrawn by a later resolve).
    metadata = _metadata()
    if node_tag not in metadata.stale_nodes:
        return
    metadata.stale_nodes.discard(node_tag)
    _restore_node_theme(node_tag)

def _mark_failed(node_tag: str):
    # A heavy node's bake() returned False / raised: flag it yellow (same warning skin as stale --
    # both are local problems) and record it so the Analyzer Status window can surface it. Does
    # NOT stop the pipeline; downstream simply gets no data.
    metadata = _metadata()
    if node_tag in metadata.failed_nodes:
        return
    metadata.failed_nodes.add(node_tag)
    if _is_detached_compile():
        metadata.success = False
        return
    node = GRAPH_STATE.nodes.get(node_tag)
    if node is not None:
        node._draw_warning_node()

def _clear_failed(node_tag: str):
    # A bake succeeded (or the node no longer exists / was re-wired): drop the failure flag and
    # restore the theme (stays yellow if it's still stale).
    metadata = _metadata()
    if node_tag not in metadata.failed_nodes:
        return
    metadata.failed_nodes.discard(node_tag)
    _restore_node_theme(node_tag)


# Analyzer-status issue categories (blast radius -> colour):
ISSUE_WIRING = "wiring"      # RED  : a node is in a cycle -> the whole graph can't run
ISSUE_FAILED = "failed"      # YELLOW: a node's bake() failed -> only its branch loses data
ISSUE_STALE = "stale"        # YELLOW: a node's baked output is out of date -> needs a re-bake
_ISSUE_SEVERITY = {ISSUE_WIRING: 0, ISSUE_FAILED: 1, ISSUE_STALE: 2}   # sort key: red first


def get_analyzer_issues() -> list[dict]:
    """Snapshot of everything the Analyzer Status window shows, as UI-agnostic data.

    One dict per problem node: {"category", "node_tag", "name"} where name is the node's
    user-facing title. Called from the GUI thread every frame, but the STALE/FAILED/CYCLE sets are
    mutated by the compile WORKER thread (_mark_failed/_clear_failed/_clear_stale/_apply_stale), so
    we snapshot each to a tuple up front -- iterating a live set while the worker add()s/discard()s
    would raise "Set changed size during iteration". A node in a cycle is reported ONLY as wiring
    (the loop is why it can't run; stale/failed are moot). Sorted red first, then by editor
    y-position so the list is stable frame to frame.
    """
    # One atomic snapshot per set (tuple() over a set is a single C-level copy under the GIL).
    cycle_tags = tuple(GRAPH_STATE.cycle_nodes)
    failed_tags = tuple(GRAPH_STATE.failed_nodes)
    stale_tags = tuple(GRAPH_STATE.stale_nodes)

    issues = []
    seen = set()

    def _name(tag):
        node = GRAPH_STATE.nodes.get(tag)
        return getattr(node, "title", tag) if node is not None else tag

    for tag in cycle_tags:
        issues.append({"category": ISSUE_WIRING, "node_tag": tag, "name": _name(tag)})
        seen.add(tag)
    for tag in failed_tags:
        if tag not in seen:
            issues.append({"category": ISSUE_FAILED, "node_tag": tag, "name": _name(tag)})
            seen.add(tag)
    for tag in stale_tags:
        if tag not in seen:
            issues.append({"category": ISSUE_STALE, "node_tag": tag, "name": _name(tag)})
            seen.add(tag)

    issues.sort(key=lambda it: (
        _ISSUE_SEVERITY[it["category"]], it["name"], it["node_tag"]))
    return issues


def graph_snapshot():
    return GRAPH_STATE.snapshot()


def capture_graph_render_state() -> GraphRenderState:
    unavailable_node_ids = GRAPH_STATE.stale_nodes | GRAPH_STATE.failed_nodes
    return GraphRenderState(
        node_ids=frozenset(GRAPH_STATE.nodes),
        baked_node_ids=frozenset(
            node_id
            for node_id, node in GRAPH_STATE.nodes.items()
            if node_id not in unavailable_node_ids
            and bool(getattr(node, "baked_signature", None))
        ),
        stale_node_ids=frozenset(GRAPH_STATE.stale_nodes),
        failed_node_ids=frozenset(GRAPH_STATE.failed_nodes),
        cycle_node_ids=frozenset(GRAPH_STATE.cycle_nodes),
    )


def export_current_graph_document(graph_id: str = "graph_1"):
    return export_graph_document(GRAPH_STATE, graph_id)


def capture_history_workspace() -> WorkspaceDocument:
    with _HISTORY_WORKSPACE_LOCK:
        return WorkspaceDocument(
            dict(_HISTORY_NODE_POSITIONS),
            dict(_HISTORY_NODE_PANELS),
        )


def capture_history_artifacts(unavailable_node_ids=None):
    return runtime_artifacts.capture(
        GRAPH_STATE.nodes,
        GRAPH_STATE.stale_nodes | GRAPH_STATE.failed_nodes
        if unavailable_node_ids is None else unavailable_node_ids,
    )


def restore_history_artifacts(refs) -> int:
    restored = runtime_artifacts.restore(GRAPH_STATE.nodes, refs)
    logger.debug(
        "History artifacts restored requested=%d restored=%d ids=%s.",
        len(refs or ()),
        restored,
        [getattr(ref, "node_id", None) for ref in refs or ()],
    )
    return restored


def finalize_graph_replacement() -> bool:
    from core.playback_engine import ENGINE

    ENGINE.set_source(None, 0)
    return light_node_compile()


def sync_history_workspace(positions, panels=None) -> None:
    node_ids = set(GRAPH_STATE.nodes)
    with _HISTORY_WORKSPACE_LOCK:
        _HISTORY_NODE_POSITIONS.clear()
        _HISTORY_NODE_POSITIONS.update({
            node_id: Position(float(position[0]), float(position[1]))
            for node_id, position in positions.items()
            if node_id in node_ids
        })
        if panels is None:
            return
        _HISTORY_NODE_PANELS.clear()
        _HISTORY_NODE_PANELS.update({
            node_id: panel
            for node_id, panel in panels.items()
            if node_id in node_ids
        })


def publish_graph_snapshot():
    return GRAPH_STATE.publish_snapshot()


def commit_graph_document():
    return GRAPH_STATE.commit_document()

def _light_execute_subset(order_subset):
    # Execute a subset of nodes (already in topo order) in light mode: gather inputs and run
    # execute(), skipping un-baked heavy nodes. Used to freshen a target's ancestors before a
    # single-node bake without re-baking anything.
    #
    # Unlike _run_pipeline, this deliberately does NOT mem_cache.touch the heavy nodes it runs:
    # publish_outputs only re-copies already-cached outputs (it never produces or resizes them), so
    # their in-memory footprint is unchanged, and the sole caller (_bake_node_worker) follows a
    # successful bake with a full _run_pipeline(force_bake=False) that touches every heavy node
    # anyway. Skipping touch here therefore only skips an LRU recency refresh on the bake-fail
    # early-return path -- harmless, since mem_cache is a memory manager, not a correctness gate.
    for node_tag in order_subset:
        node_instance = GRAPH_STATE.nodes[node_tag]
        _gather_inputs(node_instance)
        if node_instance.config.processing is NodeProcessingMode.BAKED and not getattr(node_instance, "is_baked", False):
            continue
        try:
            if not node_instance.publish_outputs():
                logger.debug(f"Node [{node_tag}] execution reported failure (light subset).")
        except Exception:
            logger.exception(f"Node [{node_tag}] crashed during execution (light subset)")
            append_log(f"Node [{node_tag}] crashed during execution.", level="ERROR")
        _stamp_output_fingerprints(node_instance)

def _node_input_fingerprints(node_instance: BaseNode) -> list:
    # Collect the fingerprints of the packets currently feeding this node.
    return [pkt.fingerprint for pkt in node_instance.input_values.values() if pkt is not None]

def _stamp_output_fingerprints(node_instance: BaseNode):
    # Cheap fingerprint propagation: derive this node's fingerprint from its upstream input
    # fingerprints + own params, then stamp every output packet with it. Runs after execute()
    # so staleness flows downstream even when the heavy data was not recomputed.
    if not node_instance.output_values:
        return
    fp = node_instance.compute_fingerprint(_node_input_fingerprints(node_instance))
    for pkt in node_instance.output_values.values():
        if pkt is not None:
            pkt.fingerprint = fp

def _outputs_to_roles(node_instance: BaseNode) -> dict:
    # Map this node's cached_outputs (keyed by per-session full pin tags) to stable role keys
    # (the tag with the node prefix stripped) for cross-session disk caching.
    prefix = node_instance.node_tag + "_"
    roles = {}
    for tag, arr in node_instance.cached_outputs.items():
        role = tag[len(prefix):] if tag.startswith(prefix) else tag
        roles[role] = arr
    return roles

def _roles_to_outputs(node_instance: BaseNode, role_outputs: dict) -> dict:
    # Inverse of _outputs_to_roles: rebuild full-tag cached_outputs for the current session.
    prefix = node_instance.node_tag + "_"
    return {prefix + role: arr for role, arr in role_outputs.items()}


def _ensure_heavy_outputs_resident(node: BaseNode) -> None:
    """Reload an evicted heavy node's baked outputs from the disk cache, in place.

    The in-memory LRU (mem_cache) may evict a heavy node's ``cached_outputs`` to stay under
    budget, expecting the *next heavy compile* to reload them from disk (track C3). But the
    streaming/playback path reads ``cached_outputs`` directly and never triggers a recompile,
    so an upstream node evicted during this same compile (e.g. an earlier-baked separator
    pushed out when a later heavy node bakes) would stream silence. Rehydrate it here, on the
    compile thread before its array is bound, without calling ``mem_cache.touch`` so we do not
    set off another eviction that would just move the silence to a sibling node.
    """
    if node.config.processing is not NodeProcessingMode.BAKED:
        return
    if getattr(node, "cached_outputs", None):
        return
    sig = getattr(node, "baked_signature", None)
    if not sig:
        return
    disk_roles = disk_cache.load(node.__class__.__name__, sig)
    if disk_roles is None:
        return
    node.cached_outputs = _roles_to_outputs(node, disk_roles)
    node.is_baked = True
    logger.debug(f"Node [{node.node_tag}] rehydrated from disk cache for streaming.")


# Frames pulled per render() call while materialising a streamable input for a heavy bake.
_MATERIALIZE_BLOCK = 8192


def _materialize_streamed_inputs(node_instance: BaseNode) -> None:
    """Render any streamable AUDIO input into a concrete array before a heavy bake.

    Light streamable nodes (Gain, EQ, ...) do not materialise their output in ``execute()``;
    they only transform per block at stream time, so their output packet carries ``data=None``.
    A heavy node baking from such an input would see no audio and fail. Pull the same frozen
    render chain the playback/export path uses, render it whole, and inject a concrete
    ``(channels, samples)`` packet so ``bake()`` has real audio to work with.
    """
    # Playback may be running through the SAME streamable node instances while this heavy bake
    # materializes their output; those nodes carry per-instance DSP state (filter memory,
    # envelopes) that both threads would read-modify-write. Hold the engine's render lock around
    # each render() so the materialize scan never traverses the chain at the same instant as the
    # live producer. Lazy import avoids a core import cycle (pipeline <-> playback_engine).
    from core.playback_engine import ENGINE
    for in_pin, pkt in list(node_instance.input_values.items()):
        if pkt is None or pkt.data_type != DataType.AUDIO or pkt.data is not None:
            continue
        render, length = build_stream_source(in_pin)
        if render is None or length <= 0:
            continue
        chunks = []
        start = 0
        while start < length:
            COMPILE_COORDINATOR.raise_if_cancelled()
            with ENGINE.render_lock:
                block = render(start, _MATERIALIZE_BLOCK)
            if block is None or len(block) == 0:
                break
            chunks.append(block)
            start += len(block)
        if not chunks:
            continue
        # Stream blocks are (frames, channels); heavy bakes expect (channels, samples).
        buf = np.ascontiguousarray(np.concatenate(chunks, axis=0).T, dtype=np.float32)
        node_instance.input_values[in_pin] = DataPacket(buf, DataType.AUDIO, pkt.fingerprint)


def _run_pipeline(force_bake: bool):
    # Walk the execution order, gathering inputs and executing each node. When force_bake is
    # set, BAKED nodes are re-baked from scratch before being read.
    mode = "Heavy" if force_bake else "Light"
    execution_order = _metadata().execution_order
    logger.info(f"{mode} compile started; execution order: {execution_order}")

    for node_tag in execution_order:
        COMPILE_COORDINATOR.raise_if_cancelled()
        node_instance = GRAPH_STATE.nodes[node_tag]
        _gather_inputs(node_instance)

        if force_bake and node_instance.config.processing is NodeProcessingMode.BAKED:
            current_sig = node_instance.compute_fingerprint(_node_input_fingerprints(node_instance))
            if (getattr(node_instance, "is_baked", False)
                    and current_sig == getattr(node_instance, "baked_signature", None)
                    and node_instance.cached_outputs):
                # In-memory cache hit (C2): inputs + params unchanged since the last bake.
                logger.debug(f"Node [{node_tag}] cache hit (memory); skipping bake.")
                # bake() is skipped, so its terminal progress report never runs: fill the bar.
                node_instance._report_progress(1.0)
            else:
                # Disk cache hit (C3): same fingerprint produced in a previous session.
                disk_roles = disk_cache.load(node_instance.__class__.__name__, current_sig)
                if disk_roles is not None:
                    node_instance.cached_outputs = _roles_to_outputs(node_instance, disk_roles)
                    node_instance.is_baked = True
                    node_instance.baked_signature = current_sig
                    logger.debug(f"Node [{node_tag}] cache hit (disk); loaded without re-baking.")
                    # Loaded without baking; report completion so the progress bar fills.
                    node_instance._report_progress(1.0)
                else:
                    node_instance.is_baked = False
                    _materialize_streamed_inputs(node_instance)
                    try:
                        if not node_instance.bake():
                            append_log(f"Node [{node_tag}] heavy bake failed.", level="WARNING")
                            _mark_failed(node_tag)
                            continue
                    except CompileCancelled:
                        raise
                    except Exception:
                        logger.exception(f"Node [{node_tag}] crashed during heavy bake")
                        append_log(f"Node [{node_tag}] crashed during heavy bake.", level="ERROR")
                        _mark_failed(node_tag)
                        continue
                    COMPILE_COORDINATOR.raise_if_cancelled()
                    node_instance.baked_signature = current_sig
                    if disk_cache.save(node_instance.__class__.__name__, current_sig, _outputs_to_roles(node_instance)):
                        logger.debug(f"Node [{node_tag}] baked output written to disk cache.")
        elif (not force_bake) and node_instance.config.processing is NodeProcessingMode.BAKED and not getattr(node_instance, "is_baked", False):
            # Light pass: an un-baked heavy node has no cached output to propagate yet. Skip it
            # quietly instead of letting execute() report an expected (noisy) failure.
            continue

        if force_bake and node_instance.config.processing is NodeProcessingMode.BAKED:
            # Reached here only when the heavy node is fresh (re-baked / cache-confirmed); a
            # failed bake would have continued out above. Clear its stale + failed flags.
            _clear_stale(node_tag)
            _clear_failed(node_tag)

        if node_instance.config.processing is NodeProcessingMode.BAKED and getattr(node_instance, "cached_outputs", None):
            # Heavy node's in-memory outputs were just used/produced: refresh LRU + enforce the
            # memory budget (C4). Evicted nodes reload from the disk cache on a later compile.
            mem_cache.touch(node_instance)

        try:
            if not node_instance.publish_outputs():
                logger.debug(f"Node [{node_tag}] execution reported failure.")
        except Exception:
            logger.exception(f"Node [{node_tag}] crashed during execution")
            append_log(f"Node [{node_tag}] crashed during execution.", level="ERROR")
        _stamp_output_fingerprints(node_instance)

    logger.info(f"{mode} compile finished.")


# --------------------------------------------------------------------------- #
# Streaming render plan (the live "monitoring segment")
# --------------------------------------------------------------------------- #

def _build_stream_renderer(node: BaseNode, out_pin: str, _visiting=None):
    """Recursively build a frozen (render_fn, length) for `node`'s output `out_pin`.

    A non-streamable node is a leaf: it slices its own baked array. A streamable node
    recurses into the upstream renderer for each connected input, then asks the node to
    assemble its per-block transform. All wiring is captured here on the compile thread;
    the returned callables run on the producer thread and read only frozen data + live
    scalar params (so no graph dict is ever touched from the audio thread).

    `_visiting` is the set of node tags on the current recursion path; it is a defensive
    cycle guard (topology is already acyclic by the time we get here).
    """
    if _visiting and node.node_tag in _visiting:
        append_log(f"Stream build aborted: cycle through [{node.node_tag}].", level="ERROR")
        return (lambda start, frames: None), 0

    if node.config.processing is not NodeProcessingMode.STREAMING:
        # Non-streamable leaf: bind its frozen array once (array + length read together).
        # Rehydrate first in case the in-memory LRU evicted this node during the compile.
        _ensure_heavy_outputs_resident(node)
        return node.build_baked_renderer(out_pin)

    path = (_visiting | {node.node_tag}) if _visiting else {node.node_tag}
    input_renderers = {}
    in_lengths = []
    for in_pin in node.input_spec.keys():
        up_pin = GRAPH_STATE.source_for_input(in_pin)
        if up_pin is None:
            continue
        up_tag = get_node_tag_from_pin(up_pin)
        if not up_tag:
            continue
        up_node = GRAPH_STATE.nodes.get(up_tag)
        if up_node is None:
            continue
        fn, length = _build_stream_renderer(up_node, up_pin, path)
        input_renderers[in_pin] = fn
        in_lengths.append(length)

    render = node.build_stream_renderer(out_pin, input_renderers)
    return render, (max(in_lengths) if in_lengths else 0)


def build_stream_source(input_pin):
    """Build a frozen (render_fn, length) for whatever feeds `input_pin`, else (None, 0).

    Called by the playback sink on the compile thread; reads the live graph here so the
    producer thread never has to.
    """
    up_pin = GRAPH_STATE.source_for_input(input_pin)
    if up_pin is None:
        return None, 0
    up_tag = get_node_tag_from_pin(up_pin)
    if not up_tag:
        return None, 0
    up_node = GRAPH_STATE.nodes.get(up_tag)
    if up_node is None:
        return None, 0
    return _build_stream_renderer(up_node, up_pin)


# --------------------------------------------------------------------------- #
# Compile entry points
# --------------------------------------------------------------------------- #

def light_node_compile() -> bool:
    if not _resolve_execution_order():
        return False
    if GRAPH_STATE.execution_order:
        _run_pipeline(force_bake=False)
    return True

# --------------------------------------------------------------------------- #
# Single-flight compile dispatch
# --------------------------------------------------------------------------- #
# Both compile entry points (heavy / single-node bake) share one discipline: only
# one compile at a time (COMPILE_LOCK + COMPILE_BUSY), dim the editor while it runs, and run the
# work on a daemon thread. These two helpers hold that discipline so the entry points and their
# workers don't each re-implement it.

def _dispatch_compile(worker, *args) -> bool:
    """Acquire the single-flight gate and spawn ``worker(*args)`` on a daemon thread. Returns False
    (without spawning) if a compile is already running. Callers do their own node/precondition
    checks BEFORE calling this, so the gate is taken only once work is certain to start."""
    if COMPILE_COORDINATOR.dispatch(worker, *args):
        return True
    append_log("A compile is already running. Please wait for it to finish.", level="WARNING")
    return False

@contextlib.contextmanager
def _compile_session():
    """Worker-side teardown: whatever happens, release the single-flight gate and un-dim the editor.
    Wrap each compile worker's body in ``with _compile_session():``."""
    metadata = _CompileMetadata(
        execution_order=list(GRAPH_STATE.execution_order),
        stale_nodes=set(GRAPH_STATE.stale_nodes),
        failed_nodes=set(GRAPH_STATE.failed_nodes),
        cycle_nodes=set(GRAPH_STATE.cycle_nodes),
        heavy_descendants_cache=dict(GRAPH_STATE.heavy_descendants_cache),
    )
    with COMPILE_COORDINATOR.session():
        _COMPILE_LOCAL.metadata = metadata
        try:
            yield metadata
        except Exception:
            metadata.success = False
            raise
        finally:
            COMPILE_COORDINATOR.set_result(CompileMetadataResult(
                success=metadata.success and not metadata.cycle_nodes,
                execution_order=tuple(metadata.execution_order),
                node_ids=frozenset(GRAPH_STATE.nodes),
                stale_node_ids=frozenset(metadata.stale_nodes),
                failed_node_ids=frozenset(metadata.failed_nodes),
                cycle_node_ids=frozenset(metadata.cycle_nodes),
            ))
            del _COMPILE_LOCAL.metadata


def heavy_node_compile():
    # Dispatch the heavy (re-bake) pass onto a background thread so a long bake never
    # freezes the render loop. Only one compile may run at a time.
    return _dispatch_compile(_heavy_compile_worker)


def request_compile(kind: CompileKind, node_tag: str | None = None):
    if kind is CompileKind.ANALYZE:
        return heavy_node_compile()
    if kind is CompileKind.BAKE_NODE:
        return bake_node_compile(node_tag)
    raise ValueError(f"unsupported compile kind: {kind!r}")


def _heavy_compile_worker():
    with _compile_session() as metadata:
        try:
            COMPILE_COORDINATOR.raise_if_cancelled()
            if _resolve_execution_order() and _metadata().execution_order:
                _run_pipeline(force_bake=True)
                COMPILE_COORDINATOR.raise_if_cancelled()
                metadata = _metadata()
                if not metadata.failed_nodes and not metadata.cycle_nodes:
                    artifacts = capture_history_artifacts(
                        metadata.stale_nodes | metadata.failed_nodes)
                    record_compile_checkpoint(artifacts)
        except CompileCancelled:
            metadata.success = False
            logger.info("Heavy compile cancelled")
        except Exception:
            metadata.success = False
            logger.exception("Heavy compile crashed")
            append_log("Heavy compile crashed.", level="ERROR")

def bake_node_compile(node_tag: str):
    # Re-bake a single node on a worker thread, then light-propagate downstream.
    # Mirrors heavy_node_compile's single-flight + editor-dim behaviour, but only this
    # one node is re-baked; everything downstream is refreshed with a light (execute-only)
    # pass so the freshly baked output reaches the sinks (mixer, monitor, engine).
    node = GRAPH_STATE.nodes.get(node_tag)
    if node is None:
        return False
    if node.config.processing is not NodeProcessingMode.BAKED:
        append_log(f"Node [{node_tag}] has nothing to bake.", level="WARNING")
        return False

    return _dispatch_compile(_bake_node_worker, node_tag)

def _bake_node_worker(node_tag: str):
    with _compile_session() as metadata:
        try:
            COMPILE_COORDINATOR.raise_if_cancelled()
            node = GRAPH_STATE.nodes.get(node_tag)
            if node is None:
                return

            # Resolve topology so we can walk this node's ancestors in dependency order.
            if not _resolve_execution_order() or not _metadata().execution_order:
                return

            # Freshen this node's inputs by light-propagating its upstream ancestors (execute
            # only; their heavy outputs are already cached). This traces back through the nearest
            # upstream heavy nodes so the bake sees up-to-date input data, not a stale snapshot.
            ancestors = _collect_ancestors(node_tag)
            if ancestors:
                _light_execute_subset([
                    node_id for node_id in _metadata().execution_order
                    if node_id in ancestors
                ])
            COMPILE_COORDINATOR.raise_if_cancelled()

            _gather_inputs(node)
            node.is_baked = False
            try:
                ok = node.bake()
            except CompileCancelled:
                raise
            except Exception:
                logger.exception(f"Node [{node_tag}] crashed during bake")
                append_log(f"Node [{node_tag}] crashed during bake.", level="ERROR")
                _mark_failed(node_tag)
                return
            COMPILE_COORDINATOR.raise_if_cancelled()
            if not ok:
                append_log(f"Node [{node_tag}] bake failed.", level="WARNING")
                _mark_failed(node_tag)
                return
            _clear_failed(node_tag)

            # Record the fingerprint captured at this bake so a later heavy compile can detect that
            # nothing changed and skip re-baking (C2 in-memory staleness).
            node.baked_signature = node.compute_fingerprint(_node_input_fingerprints(node))
            if disk_cache.save(node.__class__.__name__, node.baked_signature, _outputs_to_roles(node)):
                logger.debug(f"Node [{node_tag}] baked output written to disk cache.")

            # This node is fresh now: drop its stale flag (downstream heavy nodes stay stale until
            # they too are re-baked).
            _clear_stale(node_tag)

            logger.debug(f"Node [{node_tag}] baked; propagating downstream (light pass).")

            # Light pass: re-execute the graph (no re-bake) so the new output flows to the sinks.
            _run_pipeline(force_bake=False)
            COMPILE_COORDINATOR.raise_if_cancelled()
            refresh_current_artifacts()
        except CompileCancelled:
            metadata.success = False
            logger.info("Node [%s] bake cancelled", node_tag)
        except Exception:
            metadata.success = False
            logger.exception("Single-node bake crashed")
            append_log("Single-node bake crashed.", level="ERROR")

def is_compile_busy() -> bool:
    return COMPILE_COORDINATOR.is_busy


configure_compile_busy(is_compile_busy)


def has_node_of_class(class_name: str) -> bool:
    """True if any live node is an instance of the named class. Lets the UI tell whether a
    driver node (e.g. the Beat Grid sink) is present without importing the node module."""
    return any(node.node_type == class_name for node in graph_snapshot().nodes)


def known_node_class_names() -> frozenset[str]:
    return frozenset(node_class.__name__ for node_class in NODE_REGISTRY.values())


def known_node_types() -> dict[str, type[BaseNode]]:
    return {node_class.__name__: node_class for node_class in NODE_REGISTRY.values()}


def runtime_node_views() -> tuple[BaseNode, ...]:
    """Explicit GUI projection port for views that render node-owned UI surfaces."""
    return tuple(GRAPH_STATE.nodes.values())


def runtime_node_view(node_tag: str) -> BaseNode | None:
    """Resolve one runtime object explicitly requested by an immutable projection snapshot."""
    return GRAPH_STATE.nodes.get(node_tag)


def set_node_parameter_state(node_tag: str, name: str, value):
    node = GRAPH_STATE.nodes.get(node_tag)
    if node is None:
        append_log(f"Cannot set parameter on missing node [{node_tag}].", level="WARNING")
        return None
    if name not in node._param_specs:
        append_log(f"Node [{node_tag}] has no parameter [{name}].", level="WARNING")
        return None
    normalized = node._set_declared_param_state(name, value)
    node._apply_declared_param_state_effects(name)
    return normalized


def mutate_node_state(node_tag: str, field: str, value, index=None, action=None):
    node = GRAPH_STATE.nodes.get(node_tag)
    if node is None:
        append_log(f"Cannot mutate state on missing node [{node_tag}].", level="WARNING")
        return None
    return node.mutate_extra_state(field, value, index=index, action=action)


def rename_node_state(node_tag: str, name: str) -> str | None:
    node = GRAPH_STATE.nodes.get(node_tag)
    if node is None:
        append_log(f"Cannot rename missing node [{node_tag}].", level="WARNING")
        return None
    if not node.set_custom_name(name, sync_ui=False):
        return None
    return node.custom_name


def commit_compile_metadata(command) -> None:
    GRAPH_STATE.execution_order[:] = command.execution_order or ()
    GRAPH_STATE.stale_nodes.clear()
    GRAPH_STATE.stale_nodes.update(command.stale_node_ids or ())
    GRAPH_STATE.failed_nodes.clear()
    GRAPH_STATE.failed_nodes.update(command.failed_node_ids or ())
    GRAPH_STATE.cycle_nodes.clear()
    GRAPH_STATE.cycle_nodes.update(command.cycle_node_ids or ())
    GRAPH_STATE.heavy_descendants_cache.clear()


def replace_graph_candidate(candidate, workspace=None) -> GraphReplaced:
    old_node_ids = tuple(GRAPH_STATE.nodes)
    old_ui_resource_tags = tuple(sorted(
        {
            tag
            for node in GRAPH_STATE.nodes.values()
            for tag in node._ui_resource_tags
        },
        key=lambda tag: (type(tag).__name__, str(tag)),
    ))
    for node in GRAPH_STATE.nodes.values():
        node._ui_resource_tags.clear()
    GRAPH_STATE.replace_runtime(candidate)
    for node_id in old_node_ids:
        mem_cache.forget(node_id)

    positions = getattr(workspace, "node_positions", {}) if workspace else {}
    node_positions = tuple(
        GraphNodePosition(
            node_id,
            float(getattr(positions.get(node_id), "x", SPAWN_BASE[0])),
            float(getattr(positions.get(node_id), "y", SPAWN_BASE[1])),
        )
        for node_id in candidate.nodes
    )
    with _HISTORY_WORKSPACE_LOCK:
        _HISTORY_NODE_POSITIONS.clear()
        _HISTORY_NODE_POSITIONS.update({
            position.node_id: Position(position.x, position.y)
            for position in node_positions
        })
    panels = getattr(workspace, "node_panels", {}) if workspace else {}
    panel_geometries = tuple(
        GraphPanelGeometry(
            node_id,
            None if panel.position is None else (
                float(panel.position.x), float(panel.position.y)),
            None if panel.size is None else (
                float(panel.size.x), float(panel.size.y)),
        )
        for node_id, panel in sorted(panels.items())
        if node_id in candidate.nodes
    )
    with _HISTORY_WORKSPACE_LOCK:
        _HISTORY_NODE_PANELS.clear()
        _HISTORY_NODE_PANELS.update({
            geometry.node_id: PanelDocument(
                None if geometry.position is None else Position(*geometry.position),
                None if geometry.size is None else Position(*geometry.size),
            )
            for geometry in panel_geometries
        })
    windows = getattr(workspace, "windows", {}) if workspace else {}
    window_states = tuple(
        GraphWindowState(
            tag,
            None if window.position is None else (
                float(window.position.x), float(window.position.y)),
            None if window.size is None else (
                float(window.size.x), float(window.size.y)),
            bool(window.visible),
        )
        for tag, window in sorted(windows.items())
    )
    return GraphReplaced(
        old_node_ids=old_node_ids,
        node_positions=node_positions,
        connections=tuple(sorted(
            candidate.connections, key=lambda item: item.connection_id)),
        panel_geometries=panel_geometries,
        window_states=window_states,
        dock_layout=getattr(workspace, "dock_layout", None) if workspace else None,
        old_ui_resource_tags=old_ui_resource_tags,
    )


# --------------------------------------------------------------------------- #
# Connection management
# --------------------------------------------------------------------------- #


def connect_pins_state(output_pin: str, input_pin: str) -> GraphConnection | None:
    if is_compile_busy():
        append_log("Cannot create a connection while a compile is running.", level="WARNING")
        return None

    out_node = GRAPH_STATE.nodes.get(get_node_tag_from_pin(output_pin))
    in_node = GRAPH_STATE.nodes.get(get_node_tag_from_pin(input_pin))

    # Same rule set the preset loader uses (core.graph_validation): resolve, direction, self-loop,
    # type match, duplicate input. Interactive wiring surfaces the message immediately.
    result = validate_connection(
        output_pin,
        input_pin,
        out_node,
        in_node,
        GRAPH_STATE.connection_id_by_input,
    )
    if not result.ok:
        level = "WARNING" if result.code == "duplicate_input" else "ERROR"
        append_log(result.message, level=level)
        return None

    connection = GRAPH_STATE.add_connection(output_pin, input_pin)
    logger.debug(f"Connection established: {output_pin} -> {input_pin}.")
    return connection


def disconnect_connection_state(connection_id: str) -> GraphConnection | None:
    if is_compile_busy():
        append_log("Cannot remove a connection while a compile is running.", level="WARNING")
        return None
    connection = GRAPH_STATE.remove_connection(connection_id)
    if connection is not None:
        logger.info(
            "Deleted connection: %s -> %s.",
            connection.output_pin,
            connection.input_pin,
        )
    return connection


def disconnect_pins_state(
    output_pin: str, input_pin: str
) -> GraphConnection | None:
    connection = GRAPH_STATE.connection_for_input(input_pin)
    if connection is None or connection.output_pin != output_pin:
        return None
    return disconnect_connection_state(connection.connection_id)


def commit_topology_changed(input_pin: str, reason: str) -> None:
    if light_node_compile():
        logger.debug("Pipeline analysis triggered due to %s.", reason)
    else:
        logger.debug("Pipeline analysis skipped: invalid topology after %s.", reason)
    in_owner = get_node_tag_from_pin(input_pin)
    if in_owner:
        mark_structural_heavy_stale(in_owner)
    record_history()

# --------------------------------------------------------------------------- #
# Node teardown
# --------------------------------------------------------------------------- #


def delete_node_state(node_tag: str) -> NodeDeleted | None:
    # Registries are only mutated in place below (del / .discard / .remove), never rebound,
    # so no `global` declaration is needed.
    if is_compile_busy():
        append_log("Cannot delete a node while a compile is running.", level="WARNING")
        return None

    if node_tag not in GRAPH_STATE.nodes:
        return None

    node_instance = GRAPH_STATE.nodes[node_tag]
    logger.info(f"Deleting [{node_instance.node_tag}]...")

    # Capture downstream heavy nodes BEFORE tearing down this node's wires; once the links are
    # gone the descendant walk can't reach them. They lose an input, so they become stale.
    downstream_heavy = set(_heavy_descendants(node_tag))

    removed_connections = []
    for connection in GRAPH_STATE.iter_connections():
        if (connection.output_pin in node_instance.output_spec
                or connection.input_pin in node_instance.input_spec):
            removed_connections.append(connection)
            GRAPH_STATE.remove_connection(connection.connection_id)

    ui_resource_tags = tuple(sorted(
        node_instance._ui_resource_tags,
        key=lambda tag: (type(tag).__name__, str(tag)),
    ))
    node_instance._ui_resource_tags.clear()
    del GRAPH_STATE.nodes[node_tag]
    with _HISTORY_WORKSPACE_LOCK:
        _HISTORY_NODE_POSITIONS.pop(node_tag, None)
        _HISTORY_NODE_PANELS.pop(node_tag, None)
    mem_cache.forget(node_tag)
    GRAPH_STATE.stale_nodes.discard(node_tag)
    GRAPH_STATE.failed_nodes.discard(node_tag)
    GRAPH_STATE.cycle_nodes.discard(node_tag)
    _deindex_node_pins(node_instance)

    return NodeDeleted(
        node_tag=node_tag,
        panel_tag=node_instance.panel_window_tag,
        ui_resource_tags=ui_resource_tags,
        removed_connections=tuple(
            sorted(removed_connections, key=lambda item: item.connection_id)),
        downstream_stale=tuple(sorted(downstream_heavy)),
    )


def commit_node_deleted(event: NodeDeleted) -> None:
    if light_node_compile():
        logger.debug("Pipeline analysis triggered due to node removal.")
    else:
        logger.debug("Pipeline analysis skipped: invalid topology after node removal.")

    # The deleted node's downstream heavy nodes lost an input -> flag them stale (yellow).
    _apply_stale(event.downstream_stale)

    record_history()


# --------------------------------------------------------------------------- #
# Node spawning
# --------------------------------------------------------------------------- #

def get_node_unique_tag(node_class):
    return GRAPH_STATE.next_node_tag(node_class.__name__)

def spawn_node_state(node_type: str) -> NodeSpawned | None:
    node_class = NODE_REGISTRY.get(node_type)
    if node_class is None:
        append_log(f"Unknown node type [{node_type}].", level="ERROR")
        return None
    return _create_node_state(node_class)


def _create_node_state(node_class) -> NodeSpawned | None:
    if is_compile_busy():
        append_log("Cannot spawn a node while a compile is running.", level="WARNING")
        return None

    # Singleton nodes may only exist once in the runtime.
    if node_class.config.singleton:
        for existing_node in GRAPH_STATE.nodes.values():
            if isinstance(existing_node, node_class):
                append_log(f"[{node_class.display_name}] is a singleton; only one allowed.", level="WARNING")
                return None

    unique_tag = get_node_unique_tag(node_class)
    node_instance = node_class(unique_tag)
    offset = (GRAPH_STATE.node_counter % SPAWN_WRAP) * SPAWN_STEP
    GRAPH_STATE.nodes[unique_tag] = node_instance
    _index_node_pins(node_instance)
    spawn_position = (
        float(SPAWN_BASE[0] + offset), float(SPAWN_BASE[1] + offset))
    with _HISTORY_WORKSPACE_LOCK:
        _HISTORY_NODE_POSITIONS[unique_tag] = Position(*spawn_position)
    return NodeSpawned(
        unique_tag,
        spawn_position,
    )


def commit_node_spawned(_event: NodeSpawned) -> None:
    record_history()


def commit_node_renamed(_node_tag: str, _name: str) -> None:
    record_history()


def commit_node_parameter_changed(
        _node_tag: str, _name: str, _value: object) -> None:
    record_history()


def commit_node_state_changed(
        _node_tag: str, _field: str, _value: object, _index: int | None) -> None:
    record_history()
