"""Pure, DPG-free validation for graph presets and pin connections.

Both the interactive link path (``core.pipeline.link_callback``) and the preset loader
(``core.graph_presets.apply_graph``) route their "can these two pins connect?" decision through
``validate_connection`` here, so the connection rules live in exactly one place: interactive
wiring shows an immediate message, the preset loader accumulates a warning summary, but neither
maintains its own copy of the rules.

``validate_preset`` checks a decoded preset dict's *structure* BEFORE the loader touches the live
graph, so a syntactically-valid-but-structurally-broken file is rejected while the current work is
still intact (rather than failing halfway after the editor has already been cleared).

This module imports only ``core.datatypes`` (a plain Enum) -- no dearpygui, no runtime registry --
so it is unit-testable in isolation and safe to call from any thread.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Optional

from core.datatypes import DataType


# Guard against absurd pin suffixes smuggled in via a hand-edited preset: a real suffix is short
# (e.g. "_out_audio::AUDIO"). This is a sanity bound, not a precise limit.
_MAX_PIN_SUFFIX_LEN = 200
_MAX_CUSTOM_NAME_LENGTH = 64


# --------------------------------------------------------------------------- #
# Pin connection validation
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ConnectionValidation:
    """Outcome of a single output->input connection check.

    ``code`` is a stable machine-readable tag (e.g. ``"type_mismatch"``) so callers can decide
    severity/presentation without parsing ``message``. ``ok`` is the only field that gates whether
    the wire should be created.
    """
    ok: bool
    code: str = "ok"
    message: str = ""


def parse_pin_type(pin_tag) -> Optional[DataType]:
    """Resolve the ``DataType`` encoded in a pin tag suffix (``"..._out::AUDIO"`` -> ``AUDIO``).

    Pure string parse (no logging, no side effects); returns ``None`` for a non-string tag or a
    missing/unknown ``::TYPE`` suffix.
    """
    if not isinstance(pin_tag, str) or "::" not in pin_tag:
        return None
    try:
        return DataType[pin_tag.rsplit("::", 1)[-1]]
    except KeyError:
        return None


def validate_connection(
    output_pin: str,
    input_pin: str,
    out_node,
    in_node,
    existing_inputs: Iterable[str],
) -> ConnectionValidation:
    """Decide whether ``output_pin`` may feed ``input_pin``. Pure: no DPG, no side effects.

    ``out_node`` / ``in_node`` are the owning node instances (or ``None`` when a pin does not
    resolve to a live node). Direction is verified against the nodes' ``output_spec`` /
    ``input_spec`` so a hand-edited preset cannot smuggle in an output->output or input->input
    wire. ``existing_inputs`` is any container of already-connected input pin tags (an input
    accepts a single wire). Self-loops (a node wired to itself) are rejected outright rather than
    left for the topology pass to strip.
    """
    if out_node is None or in_node is None:
        return ConnectionValidation(
            False, "unresolved_pin",
            "Connection aborted. Unable to identify pin owner.",
        )

    # Direction: the "output" pin must really be an output, and the "input" an input. This single
    # pair of checks rejects output->output and input->input regardless of drag order / hand edits.
    if output_pin not in out_node.output_spec:
        return ConnectionValidation(
            False, "not_an_output",
            f"'{output_pin}' is not an output pin.",
        )
    if input_pin not in in_node.input_spec:
        return ConnectionValidation(
            False, "not_an_input",
            f"'{input_pin}' is not an input pin.",
        )

    # Self-loop policy: a node may not feed its own input. Kahn's sort already ignores self-loops
    # for edge counting, so rejecting here keeps the graph honest instead of relying on a
    # downstream side effect.
    if out_node is in_node:
        return ConnectionValidation(
            False, "self_loop",
            "A node cannot be connected to itself.",
        )

    out_type = out_node.output_spec[output_pin]
    in_type = in_node.input_spec[input_pin]
    if out_type != in_type:
        return ConnectionValidation(
            False, "type_mismatch",
            f"Type mismatch: output is {out_type.name}, input expects {in_type.name}.",
        )

    if input_pin in existing_inputs:
        return ConnectionValidation(
            False, "duplicate_input",
            "Input already connected. Remove the existing wire first.",
        )

    return ConnectionValidation(True, "ok", "")


# --------------------------------------------------------------------------- #
# Preset structure validation
# --------------------------------------------------------------------------- #

@dataclass
class PresetValidationResult:
    """Outcome of a whole-preset structural check.

    ``ok`` is ``True`` only when ``errors`` is empty; a preset with just ``warnings`` (e.g. an
    unknown node class that will be skipped) is still loadable. The loader must treat ``ok`` as the
    gate that decides whether it is safe to clear the current graph.
    """
    ok: bool
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def _is_finite_number(value) -> bool:
    # bool is an int subclass but never a meaningful coordinate/size, so exclude it explicitly.
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _is_number_pair(value) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) == 2
        and all(_is_finite_number(v) for v in value)
    )


def validate_preset(data, known_classes: Optional[Iterable[str]] = None) -> PresetValidationResult:
    """Structurally validate a decoded preset dict WITHOUT touching DPG or the runtime registry.

    Collects every problem (rather than failing on the first) so callers can surface a complete
    summary. ``known_classes`` is optional: when provided, a node referencing an unregistered class
    becomes a *warning* (the loader may skip it and any links that reference it) instead of an
    error, matching the "warn + skip" policy for forward/backward-compatible presets.
    """
    result = PresetValidationResult(ok=True)
    errors = result.errors
    warnings = result.warnings

    if not isinstance(data, dict):
        errors.append("Preset root must be a JSON object.")
        result.ok = False
        return result

    known = set(known_classes) if known_classes is not None else None

    # --- nodes --------------------------------------------------------------------------------
    nodes = data.get("nodes", [])
    node_count = 0
    unknown_indices: set = set()
    if not isinstance(nodes, list):
        errors.append("'nodes' must be a list.")
    else:
        node_count = len(nodes)
        for i, nd in enumerate(nodes):
            if not isinstance(nd, dict):
                errors.append(f"nodes[{i}] must be an object.")
                continue

            cls = nd.get("class")
            if not isinstance(cls, str) or not cls:
                errors.append(f"nodes[{i}].class must be a non-empty string.")
            elif known is not None and cls not in known:
                unknown_indices.add(i)
                warnings.append(f"nodes[{i}] references unknown class '{cls}'; it will be skipped.")

            if "pos" in nd and not _is_number_pair(nd["pos"]):
                errors.append(f"nodes[{i}].pos must be two finite numbers.")

            custom_name = nd.get("custom_name")
            if custom_name is not None:
                if not isinstance(custom_name, str):
                    errors.append(f"nodes[{i}].custom_name must be a string.")
                elif len(custom_name) > _MAX_CUSTOM_NAME_LENGTH:
                    errors.append(
                        f"nodes[{i}].custom_name must be at most {_MAX_CUSTOM_NAME_LENGTH} characters.")

            for key in ("params", "extra"):
                if key in nd and not isinstance(nd[key], dict):
                    errors.append(f"nodes[{i}].{key} must be an object.")

            panel = nd.get("panel")
            if panel is not None:
                if not isinstance(panel, dict):
                    errors.append(f"nodes[{i}].panel must be an object.")
                else:
                    for key in ("pos", "size"):
                        if key in panel and not _is_number_pair(panel[key]):
                            errors.append(f"nodes[{i}].panel.{key} must be two finite numbers.")

    # --- links --------------------------------------------------------------------------------
    links = data.get("links", [])
    if not isinstance(links, list):
        errors.append("'links' must be a list.")
    else:
        connection_ids = set()
        for i, link in enumerate(links):
            if not isinstance(link, (list, tuple)) or len(link) not in (4, 5):
                errors.append(f"links[{i}] must have four or five elements.")
                continue
            out_idx, out_suffix, in_idx, in_suffix = link[:4]
            connection_id = link[4] if len(link) == 5 else None

            if connection_id is not None:
                if not isinstance(connection_id, str) or not connection_id:
                    errors.append(f"links[{i}] connection ID must be a non-empty string.")
                elif connection_id in connection_ids:
                    errors.append(f"links[{i}] connection ID {connection_id!r} is duplicated.")
                else:
                    connection_ids.add(connection_id)

            for label, idx in (("output", out_idx), ("input", in_idx)):
                if not isinstance(idx, int) or isinstance(idx, bool):
                    errors.append(f"links[{i}] {label} node index must be an integer.")
                elif not (0 <= idx < node_count):
                    errors.append(f"links[{i}] {label} node index {idx} is out of range.")

            for label, suffix in (("output", out_suffix), ("input", in_suffix)):
                if not isinstance(suffix, str):
                    errors.append(f"links[{i}] {label} pin suffix must be a string.")
                elif len(suffix) > _MAX_PIN_SUFFIX_LEN:
                    errors.append(f"links[{i}] {label} pin suffix is unreasonably long.")

            # A link onto a to-be-skipped (unknown-class) node cannot be restored: downgrade it to
            # a warning so the whole preset is not rejected for a compatibility gap.
            if isinstance(out_idx, int) and not isinstance(out_idx, bool) and out_idx in unknown_indices:
                warnings.append(f"links[{i}] references skipped node {out_idx}; it will be dropped.")
            if isinstance(in_idx, int) and not isinstance(in_idx, bool) and in_idx in unknown_indices:
                warnings.append(f"links[{i}] references skipped node {in_idx}; it will be dropped.")

    # --- globals ------------------------------------------------------------------------------
    if "globals" in data and not isinstance(data["globals"], dict):
        errors.append("'globals' must be an object.")

    result.ok = not errors
    return result
