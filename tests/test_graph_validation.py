"""Unit tests for the pure preset/connection validators (core.graph_validation).

These exercise the DPG-free rule layer in isolation: no runtime registry, no live graph, no
viewport. They lock two contracts:

  * validate_preset rejects structurally-broken presets (so the loader can refuse BEFORE clearing
    the current graph), while accepting well-formed ones and downgrading unknown-class references
    to warnings.
  * validate_connection enforces the SAME wiring rules the interactive editor and the preset loader
    both call through: direction, self-loop, type match, single-input.
"""

from core.datatypes import DataType
from core.graph_validation import (
    ConnectionValidation,
    PresetValidationResult,
    parse_pin_type,
    validate_connection,
    validate_preset,
)


# --- lightweight fake node ---------------------------------------------------------------------

class FakeNode:
    """Minimal stand-in for a BaseNode: only the fields validate_connection reads."""

    def __init__(self, tag, outputs=None, inputs=None):
        self.node_tag = tag
        self.output_spec = dict(outputs or {})
        self.input_spec = dict(inputs or {})


def _audio_out(tag="node_A"):
    pin = f"{tag}_out::AUDIO"
    return FakeNode(tag, outputs={pin: DataType.AUDIO}), pin


def _audio_in(tag="node_B"):
    pin = f"{tag}_in::AUDIO"
    return FakeNode(tag, inputs={pin: DataType.AUDIO}), pin


# --- parse_pin_type ----------------------------------------------------------------------------

def test_parse_pin_type_resolves_suffix():
    assert parse_pin_type("node_Mixer_1_in_ch1::AUDIO") is DataType.AUDIO
    assert parse_pin_type("x::MIDI") is DataType.MIDI


def test_parse_pin_type_rejects_garbage():
    assert parse_pin_type("no_suffix") is None
    assert parse_pin_type("x::NOPE") is None
    assert parse_pin_type(None) is None
    assert parse_pin_type(123) is None


# --- validate_connection -----------------------------------------------------------------------

def test_valid_audio_connection():
    out_node, out_pin = _audio_out()
    in_node, in_pin = _audio_in()
    result = validate_connection(out_pin, in_pin, out_node, in_node, set())
    assert result.ok
    assert result.code == "ok"


def test_type_mismatch_rejected():
    out_node = FakeNode("node_A", outputs={"node_A_out::AUDIO": DataType.AUDIO})
    in_node = FakeNode("node_B", inputs={"node_B_in::MIDI": DataType.MIDI})
    result = validate_connection("node_A_out::AUDIO", "node_B_in::MIDI", out_node, in_node, set())
    assert not result.ok
    assert result.code == "type_mismatch"


def test_output_to_output_rejected():
    out_a, pin_a = _audio_out("node_A")
    out_b, pin_b = _audio_out("node_B")
    # Both pins are outputs: pin_b is not an input pin on out_b.
    result = validate_connection(pin_a, pin_b, out_a, out_b, set())
    assert not result.ok
    assert result.code == "not_an_input"


def test_input_to_input_rejected():
    in_a, pin_a = _audio_in("node_A")
    in_b, pin_b = _audio_in("node_B")
    # First pin is treated as the "output" end but is actually an input.
    result = validate_connection(pin_a, pin_b, in_a, in_b, set())
    assert not result.ok
    assert result.code == "not_an_output"


def test_self_loop_rejected():
    node = FakeNode(
        "node_A",
        outputs={"node_A_out::AUDIO": DataType.AUDIO},
        inputs={"node_A_in::AUDIO": DataType.AUDIO},
    )
    result = validate_connection("node_A_out::AUDIO", "node_A_in::AUDIO", node, node, set())
    assert not result.ok
    assert result.code == "self_loop"


def test_duplicate_input_rejected():
    out_node, out_pin = _audio_out()
    in_node, in_pin = _audio_in()
    result = validate_connection(out_pin, in_pin, out_node, in_node, {in_pin})
    assert not result.ok
    assert result.code == "duplicate_input"


def test_unresolved_pin_rejected():
    out_node, out_pin = _audio_out()
    result = validate_connection(out_pin, "ghost::AUDIO", out_node, None, set())
    assert not result.ok
    assert result.code == "unresolved_pin"


# --- validate_preset: happy path ---------------------------------------------------------------

def _good_preset():
    return {
        "nodes": [
            {"class": "AudioInputNode", "pos": [10.0, 20.0], "params": {}},
            {"class": "GainNode", "pos": [200, 20], "params": {"gain": 0.5},
             "panel": {"pos": [0, 0], "size": [100, 100]}},
        ],
        "links": [[0, "_out::AUDIO", 1, "_in::AUDIO"]],
        "globals": {"speed": 1.0},
    }


def test_valid_preset_passes():
    result = validate_preset(_good_preset())
    assert isinstance(result, PresetValidationResult)
    assert result.ok
    assert result.errors == []


def test_empty_preset_is_valid():
    assert validate_preset({}).ok
    assert validate_preset({"nodes": [], "links": []}).ok


# --- validate_preset: structural failures ------------------------------------------------------

def test_non_dict_root_rejected():
    for bad in ([], "x", 5, None):
        result = validate_preset(bad)
        assert not result.ok
        assert result.errors


def test_nodes_must_be_list():
    result = validate_preset({"nodes": {"not": "a list"}})
    assert not result.ok


def test_node_must_be_dict():
    result = validate_preset({"nodes": [None]})
    assert not result.ok
    assert any("must be an object" in e for e in result.errors)


def test_class_must_be_non_empty_string():
    assert not validate_preset({"nodes": [{"class": ""}]}).ok
    assert not validate_preset({"nodes": [{"class": 5}]}).ok
    assert not validate_preset({"nodes": [{}]}).ok


def test_pos_must_be_two_finite_numbers():
    assert not validate_preset({"nodes": [{"class": "X", "pos": [1]}]}).ok
    assert not validate_preset({"nodes": [{"class": "X", "pos": [1, 2, 3]}]}).ok
    assert not validate_preset({"nodes": [{"class": "X", "pos": ["a", "b"]}]}).ok
    assert not validate_preset({"nodes": [{"class": "X", "pos": [float("inf"), 0]}]}).ok


def test_params_and_extra_must_be_dict():
    assert not validate_preset({"nodes": [{"class": "X", "params": []}]}).ok
    assert not validate_preset({"nodes": [{"class": "X", "extra": "s"}]}).ok


def test_panel_geometry_validated():
    node = {"class": "X", "panel": {"pos": [1], "size": [2, 3]}}
    assert not validate_preset({"nodes": [node]}).ok


def test_links_must_be_list():
    assert not validate_preset({"nodes": [], "links": {"bad": 1}}).ok


def test_link_shape_must_be_four_or_five_elements():
    preset = {"nodes": [{"class": "X"}], "links": [[0, "_out::AUDIO", 0]]}
    result = validate_preset(preset)
    assert not result.ok
    assert any("four or five elements" in e for e in result.errors)


def test_legacy_and_stable_id_link_shapes_are_accepted():
    node = {"class": "X"}
    legacy = {"nodes": [node, node], "links": [[0, "_out::AUDIO", 1, "_in::AUDIO"]]}
    stable = {
        "nodes": [node, node],
        "links": [[0, "_out::AUDIO", 1, "_in::AUDIO", "connection_4"]],
    }

    assert validate_preset(legacy).ok
    assert validate_preset(stable).ok


def test_duplicate_connection_ids_are_rejected():
    nodes = [{"class": "X"}, {"class": "X"}, {"class": "X"}]
    preset = {
        "nodes": nodes,
        "links": [
            [0, "_out_a::AUDIO", 1, "_in_a::AUDIO", "connection_1"],
            [0, "_out_b::AUDIO", 2, "_in_b::AUDIO", "connection_1"],
        ],
    }

    result = validate_preset(preset)

    assert not result.ok
    assert any("duplicated" in error for error in result.errors)


def test_link_index_out_of_range_rejected():
    preset = {"nodes": [{"class": "X"}], "links": [[0, "_out::AUDIO", 5, "_in::AUDIO"]]}
    result = validate_preset(preset)
    assert not result.ok
    assert any("out of range" in e for e in result.errors)


def test_link_index_must_be_int():
    preset = {"nodes": [{"class": "X"}], "links": [["0", "_out::AUDIO", 0, "_in::AUDIO"]]}
    assert not validate_preset(preset).ok


def test_link_suffix_must_be_string():
    preset = {"nodes": [{"class": "X"}], "links": [[0, 123, 0, "_in::AUDIO"]]}
    assert not validate_preset(preset).ok


def test_globals_must_be_dict():
    assert not validate_preset({"nodes": [], "globals": []}).ok


# --- validate_preset: unknown-class policy -----------------------------------------------------

def test_unknown_class_is_warning_not_error():
    preset = {"nodes": [{"class": "TotallyMadeUpNode", "pos": [0, 0]}], "links": []}
    result = validate_preset(preset, known_classes={"GainNode"})
    assert result.ok  # still loadable
    assert result.warnings


def test_link_onto_skipped_node_is_warning():
    preset = {
        "nodes": [
            {"class": "GainNode", "pos": [0, 0]},
            {"class": "MadeUp", "pos": [0, 0]},
        ],
        "links": [[0, "_out::AUDIO", 1, "_in::AUDIO"]],
    }
    result = validate_preset(preset, known_classes={"GainNode"})
    assert result.ok
    assert any("skipped node" in w for w in result.warnings)


def test_known_class_not_flagged():
    preset = {"nodes": [{"class": "GainNode", "pos": [0, 0]}], "links": []}
    result = validate_preset(preset, known_classes={"GainNode"})
    assert result.ok
    assert result.warnings == []
