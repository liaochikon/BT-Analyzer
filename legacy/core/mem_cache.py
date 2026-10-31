"""In-memory LRU eviction for heavy node outputs (cache track C4).

``NodeProcessingMode.BAKED`` nodes keep their outputs in ``node.cached_outputs`` so the
pipeline can reuse them without re-running expensive work. Without a bound this grows
without limit as more heavy nodes are loaded. This module caps the total in-memory size
and evicts the least-recently-used node's outputs when the cap is exceeded.

Eviction is safe because it only drops the *in-memory* copy: the data still lives in the
on-disk cache (track C3), so the next compile reloads it from ``disk_cache`` by fingerprint.
Evicting clears ``cached_outputs`` and resets ``is_baked`` so the C2 in-memory check misses
and the disk path is taken.

This is purely a memory manager, not a correctness mechanism; if a node is missing from the
LRU table it simply will not be evicted.

Concurrency: the ``_LRU`` / ``_BYTES`` tables are plain dicts with no lock. This is safe only
because access is serialized by the pipeline's single-flight gate -- ``touch`` runs on the one
compile worker thread, and ``forget`` runs from graph-edit callbacks that are refused while a
compile is in progress (``is_compile_busy``), so the two never overlap. Do NOT call ``touch``
from any path that can run concurrently with a compile.
"""

from collections import OrderedDict
import numpy as np
from app_logging import append_log

# Total in-memory budget for all heavy node outputs, in bytes. Adjustable at runtime / tests.
MAX_BYTES = 512 * 1024 * 1024  # 512 MiB

# node_tag -> node instance, ordered oldest..newest (most-recently-used at the end).
_LRU: "OrderedDict[str, object]" = OrderedDict()
# node_tag -> in-memory size in bytes of that node's cached_outputs.
_BYTES: "dict[str, int]" = {}


def touch(node_instance) -> None:
    """Record that ``node_instance``'s in-memory outputs were just used or produced.

    Normalises cached arrays to float32, refreshes the LRU position, recomputes the node's
    byte footprint, then evicts other least-recently-used nodes if the budget is exceeded.
    """
    tag = getattr(node_instance, "node_tag", None)
    if tag is None:
        return

    outputs = getattr(node_instance, "cached_outputs", None)
    if not outputs:
        forget(tag)
        return

    nbytes = 0
    for key, arr in list(outputs.items()):
        if isinstance(arr, np.ndarray):
            # Audio outputs are normalised to float32; structured arrays (e.g. MIDI note
            # records) are a non-float payload and are kept exactly as-is.
            if arr.dtype.fields is None and arr.dtype != np.float32:
                arr = arr.astype(np.float32)
                outputs[key] = arr
            nbytes += arr.nbytes

    _LRU.pop(tag, None)
    _LRU[tag] = node_instance  # re-insert at the end = most recently used
    _BYTES[tag] = nbytes
    _evict(protect=tag)


def forget(tag: str) -> None:
    """Drop a node from the LRU bookkeeping (e.g. when it is deleted) without evicting data."""
    _LRU.pop(tag, None)
    _BYTES.pop(tag, None)


def total_bytes() -> int:
    return sum(_BYTES.values())


def _evict(protect: str | None = None) -> None:
    total = total_bytes()
    if total <= MAX_BYTES:
        return
    # Walk from the oldest entry forward, evicting until we are back under budget. Never evict
    # the node we just touched (protect) so a single oversized node cannot evict itself.
    for tag in list(_LRU.keys()):
        if total <= MAX_BYTES:
            break
        if tag == protect:
            continue
        node = _LRU.pop(tag)
        total -= _BYTES.pop(tag, 0)
        node.cached_outputs = {}
        node.is_baked = False
        append_log(
            f"Node [{tag}] evicted from memory cache (LRU); will reload from disk on next compile.",
            level="INFO",
        )
