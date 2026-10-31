"""Shared test fixtures / import shims.

The core audio modules import ``sounddevice`` at module load, which needs a real audio backend.
Tests are pure-logic and headless, so we install a minimal stub BEFORE any core import so
``import core.playback_engine`` (and everything under ``core``) works on a CI box / no-audio
machine. This does NOT open a device -- the stub's OutputStream is never started in these tests.
"""

import sys
import types

import pytest


_INTEGRATION_TEST_MODULES = frozenset({
    "test_analyzer_status",
    "test_application_context",
    "test_compile_coordinator",
    "test_compile_metadata",
    "test_graph_application",
    "test_graph_owner",
    "test_graph_projection",
    "test_main_lifecycle",
    "test_pedal_workspace",
    "test_persistence_worker",
    "test_pipeline_materialize",
    "test_presets",
    "test_preset_menu_async",
})

_ARCHITECTURE_TEST_MODULES = frozenset({
    "test_graph_architecture",
    "test_node_contracts",
    "test_node_processor_architecture",
    "test_persistence_architecture",
    "test_surfaces",
})


def pytest_collection_modifyitems(items):
    """Apply one execution tier plus optional purpose markers to every test."""
    for item in items:
        module_name = item.path.stem
        path_parts = set(item.path.parts)

        if module_name == "test_runtime_simulation":
            item.add_marker(pytest.mark.runtime)
        elif module_name in _INTEGRATION_TEST_MODULES:
            item.add_marker(pytest.mark.integration)
        else:
            item.add_marker(pytest.mark.unit)

        if "nodes" in path_parts:
            item.add_marker(pytest.mark.node)
        if module_name in _ARCHITECTURE_TEST_MODULES:
            item.add_marker(pytest.mark.architecture)


def _install_sounddevice_stub():
    if "sounddevice" in sys.modules:
        return
    sd = types.ModuleType("sounddevice")

    class _FakeStream:
        def __init__(self, **kwargs):
            self.active = False

        def start(self):
            self.active = True

        def stop(self):
            self.active = False

        def close(self):
            self.active = False

    sd.OutputStream = _FakeStream
    sd.default = types.SimpleNamespace(device=[-1, 1])
    sd.query_devices = lambda *a, **k: {"default_samplerate": 44100.0, "max_output_channels": 2}
    sd.query_hostapis = lambda: []
    sd.check_output_settings = lambda **k: None
    sys.modules["sounddevice"] = sd


_install_sounddevice_stub()


# ----- Node-test scaffolding --------------------------------------------------------------------
# Node logic (execute / bake / serialize_params / serialize_extra / DSP helpers) can be exercised
# WITHOUT drawing: BaseNode.__init__ is dpg-free, and the panel-sync helpers guard every dpg call
# with does_item_exist (which returns False safely under a bare context). We therefore create ONE
# headless DPG context for the whole node-test session (no viewport) and register the node plugins
# once, then hand tests a factory that constructs a fresh node from its display name.

@pytest.fixture(scope="session")
def dpg_context():
    """A bare DPG context (no viewport) shared by all node tests. does_item_exist works; nothing
    is drawn, so panel-sync paths no-op instead of segfaulting on a missing context."""
    import dearpygui.dearpygui as dpg
    dpg.create_context()
    yield dpg
    dpg.destroy_context()


@pytest.fixture(scope="session")
def node_registry(dpg_context):
    """The scanned NODE_REGISTRY (display_name -> class), populated once."""
    from core.pipeline import scan_and_register_nodes, NODE_REGISTRY
    scan_and_register_nodes()
    return NODE_REGISTRY


@pytest.fixture
def make_node(node_registry):
    """Factory: make_node("Gain") -> a fresh Gain instance with a unique tag, NOT drawn.

    Exercises the node's pure logic (DSP, serialize, param callbacks) in isolation. draw_node is
    intentionally not called, so no widgets exist -- the node's panel-sync guards handle that.
    """
    from core.pipeline import get_node_unique_tag

    def _make(display_name: str):
        cls = node_registry[display_name]
        return cls(get_node_unique_tag(cls))

    return _make


@pytest.fixture
def graph_editor(dpg_context, node_registry):
    """A live but headless node graph for preset round-trip tests.

    Provides the ``node_editor`` container that draw_node / apply_graph parent into (works under a
    bare context, no viewport), and tears the graph down after each test so runtime registries
    don't leak between tests. serialize_graph / apply_graph then behave exactly as in the app.
    """
    dpg = dpg_context
    from core import graph_presets as GP
    from core.pipeline import graph_snapshot, runtime_node_view
    from ui.pedal_workspace import bind_graph_projection
    bind_graph_projection(graph_snapshot, runtime_node_view)
    if not dpg.does_item_exist("win_main"):
        with dpg.window(tag="win_main"):
            dpg.add_node_editor(tag="node_editor")
    GP.clear_all_nodes()
    yield GP
    GP.clear_all_nodes()


@pytest.fixture
def runtime_harness(graph_editor, node_registry):
    """A semantic user runtime backed by real owner, pipeline, projection, and temp I/O."""
    from tests.runtime_sim import RuntimeHarness

    with RuntimeHarness() as runtime:
        yield runtime
