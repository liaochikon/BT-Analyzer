"""Session artifact handles for restoring baked outputs through graph history."""

from collections import OrderedDict
from dataclasses import dataclass
import threading

import numpy as np

from core import disk_cache


MAX_BYTES = 512 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class RuntimeArtifactRef:
    node_id: str
    class_name: str
    signature: str


_RAM: "OrderedDict[tuple[str, str], dict[str, np.ndarray]]" = OrderedDict()
_BYTES: dict[tuple[str, str], int] = {}
_LOCK = threading.RLock()


def _outputs_to_roles(node) -> dict[str, np.ndarray]:
    prefix = node.node_tag + "_"
    return {
        tag[len(prefix):] if tag.startswith(prefix) else tag: value
        for tag, value in node.cached_outputs.items()
        if isinstance(value, np.ndarray)
    }


def _roles_to_outputs(node, roles) -> dict[str, np.ndarray]:
    prefix = node.node_tag + "_"
    return {prefix + role: value for role, value in roles.items()}


def capture(nodes, unavailable_node_ids=()) -> tuple[RuntimeArtifactRef, ...]:
    refs = []
    unavailable = set(unavailable_node_ids)
    with _LOCK:
        for node_id, node in nodes.items():
            if node_id in unavailable:
                continue
            signature = getattr(node, "baked_signature", None)
            outputs = getattr(node, "cached_outputs", None)
            if not signature:
                continue
            class_name = type(node).__name__
            key = (class_name, signature)
            roles = _outputs_to_roles(node) if outputs else {}
            if roles:
                _RAM.pop(key, None)
                _RAM[key] = roles
                _BYTES[key] = sum(value.nbytes for value in roles.values())
            refs.append(RuntimeArtifactRef(node_id, class_name, signature))
        _evict_locked()
    return tuple(refs)


def restore(nodes, refs) -> int:
    restored = 0
    for ref in refs or ():
        node = nodes.get(ref.node_id)
        if node is None or type(node).__name__ != ref.class_name:
            continue
        key = (ref.class_name, ref.signature)
        with _LOCK:
            roles = _RAM.pop(key, None)
            if roles is not None:
                _RAM[key] = roles
        if roles is None:
            roles = disk_cache.load(ref.class_name, ref.signature)
        if not roles:
            continue
        node.cached_outputs = _roles_to_outputs(node, roles)
        node.baked_signature = ref.signature
        node.is_baked = True
        restored += 1
    return restored


def clear() -> None:
    with _LOCK:
        _RAM.clear()
        _BYTES.clear()


def retain(refs) -> None:
    active = {(ref.class_name, ref.signature) for ref in refs}
    with _LOCK:
        for key in tuple(_RAM):
            if key not in active:
                _RAM.pop(key, None)
                _BYTES.pop(key, None)


def total_bytes() -> int:
    with _LOCK:
        return sum(_BYTES.values())


def _evict_locked() -> None:
    total = sum(_BYTES.values())
    while total > MAX_BYTES and _RAM:
        key, _roles = _RAM.popitem(last=False)
        total -= _BYTES.pop(key, 0)