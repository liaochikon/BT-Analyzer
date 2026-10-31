"""On-disk cache for heavy node outputs (content-addressed by fingerprint).

This is a *pure cache*: every entry is regenerable derived data, keyed by the
node's content fingerprint. It is safe to delete the whole ``.cache`` directory
at any time (the next compile simply recomputes). It is intentionally NOT a save
/ project system - it stores no graph topology and no user intent, only the heavy
baked arrays so an unchanged Demucs/decode can be reloaded instead of recomputed.

Lifetime: the cache is treated as scratch for the current run. ``clear()`` is called
on startup and shutdown, so nothing survives between runs; within a run it acts as a
RAM-overflow spill (a node evicted from the in-memory LRU reloads from here instead of
re-baking). Disk growth during a run is bounded by ``MAX_BYTES`` via ``prune()`` (C5):
least-recently-used entries are evicted first, where "recently used" is tracked by file
mtime, which ``load()`` refreshes on every hit.

Layout:  .cache/<ClassName>/<fingerprint>.npz

Each ``.npz`` holds the node's ``cached_outputs`` arrays under index keys plus a
``__roles__`` string array that records each array's output role (the pin tag with
the per-session node prefix stripped), so it can be remapped onto a node whose tags
differ within the run. Loaded with ``allow_pickle=False`` (arrays only, no code).
"""

import logging
import os
import shutil

import numpy as np

logger = logging.getLogger(__name__)

# Anchor the cache to the project root (one level above this core/ package) so it lands in a
# predictable place regardless of the process working directory.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_ROOT = os.path.join(_PROJECT_ROOT, ".cache")

# Total on-disk budget for all cached entries, in bytes. The cache is pruned (least
# recently used first) back under this after each save. Adjustable at runtime / tests.
MAX_BYTES = 8 * 1024 * 1024 * 1024  # 8 GiB

# prune() rescans the whole cache directory, so we do not run it on every save. Instead we
# accumulate the bytes written and only prune once this much has been added since the last
# pass. The cache may transiently exceed MAX_BYTES by up to this interval, which is fine for
# a pure scratch cache. Set small in tests to force eager pruning.
PRUNE_INTERVAL_BYTES = 256 * 1024 * 1024  # 256 MiB
_pending_prune_bytes = 0


def _entry_path(class_name: str, fingerprint: str) -> str:
    return os.path.join(CACHE_ROOT, class_name, f"{fingerprint}.npz")


def load(class_name: str, fingerprint: str):
    """Return a role -> ndarray dict for a cached entry, or None on a miss."""
    if not fingerprint:
        return None
    path = _entry_path(class_name, fingerprint)
    if not os.path.exists(path):
        return None
    try:
        with np.load(path, allow_pickle=False) as data:
            roles = [str(r) for r in data["__roles__"]]
            result = {roles[i]: data[f"a{i}"] for i in range(len(roles))}
    except Exception:
        # A corrupt/partial entry is treated as a miss; the caller will recompute.
        logger.debug("Ignoring unreadable cache entry %s", path, exc_info=True)
        return None
    # Mark as most-recently-used for the disk LRU (C5): refresh mtime so prune() keeps it.
    try:
        os.utime(path, None)
    except OSError:
        pass
    return result


def save(class_name: str, fingerprint: str, role_outputs: dict) -> bool:
    """Persist a role -> ndarray dict. Returns True if an entry was written.

    Skips silently when there is nothing storable (empty, or a non-array value),
    so a stub/passthrough node never writes a junk entry.
    """
    if not fingerprint or not role_outputs:
        return False
    if not all(isinstance(arr, np.ndarray) for arr in role_outputs.values()):
        return False

    arrays = {}
    roles = []
    for i, (role, arr) in enumerate(role_outputs.items()):
        # Audio arrays are stored as float32; structured arrays (e.g. MIDI note records)
        # keep their own dtype so np.savez/np.load round-trips them losslessly.
        if arr.dtype.fields is None:
            arr = np.ascontiguousarray(arr, dtype=np.float32)
        else:
            arr = np.ascontiguousarray(arr)
        arrays[f"a{i}"] = arr
        roles.append(role)
    arrays["__roles__"] = np.array(roles)

    path = _entry_path(class_name, fingerprint)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    try:
        # Write to a temp file then rename so a crash mid-write can't leave a half
        # entry that would later be read as a false hit. Pass a file handle so numpy
        # does not append an extra ".npz" to the temp name.
        with open(tmp, "wb") as f:
            np.savez(f, **arrays)
        os.replace(tmp, path)
        _note_write_and_maybe_prune(sum(a.nbytes for a in arrays.values()))
        return True
    except Exception:
        logger.warning("Disk cache write failed for %s", path, exc_info=True)
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        return False


def _note_write_and_maybe_prune(written: int) -> None:
    # Defer the (directory-wide) prune until enough new data has accumulated, keeping repeated
    # saves cheap while still bounding on-disk growth (C5).
    global _pending_prune_bytes
    _pending_prune_bytes += int(written)
    if _pending_prune_bytes >= PRUNE_INTERVAL_BYTES:
        prune()
        _pending_prune_bytes = 0


def _iter_entries():
    """Yield (path, size, mtime) for every cached .npz entry under CACHE_ROOT."""
    if not os.path.isdir(CACHE_ROOT):
        return
    for class_name in os.listdir(CACHE_ROOT):
        class_dir = os.path.join(CACHE_ROOT, class_name)
        if not os.path.isdir(class_dir):
            continue
        for name in os.listdir(class_dir):
            if not name.endswith(".npz"):
                continue
            entry = os.path.join(class_dir, name)
            try:
                st = os.stat(entry)
            except OSError:
                continue
            yield entry, st.st_size, st.st_mtime


def prune(max_bytes: int | None = None) -> int:
    """Evict least-recently-used entries until the cache fits the byte budget.

    "Recently used" is tracked by file mtime, which load() refreshes on every hit, so the
    eviction order is true LRU without any side index. Returns the number of bytes freed.
    """
    budget = MAX_BYTES if max_bytes is None else max_bytes
    entries = list(_iter_entries())
    total = sum(size for _, size, _ in entries)
    if total <= budget:
        return 0
    entries.sort(key=lambda e: e[2])  # oldest mtime first = least recently used
    freed = 0
    for entry, size, _ in entries:
        if total - freed <= budget:
            break
        try:
            os.remove(entry)
            freed += size
        except OSError:
            continue
    return freed


def clear() -> None:
    """Delete the entire on-disk cache. Safe at any time (pure regenerable data)."""
    global _pending_prune_bytes
    _pending_prune_bytes = 0
    shutil.rmtree(CACHE_ROOT, ignore_errors=True)
