# Testing Guide

[繁體中文](testing.zh-TW.md)

All tests can run headlessly without opening a viewport or using a physical audio device. The root `conftest.py` installs a minimal `sounddevice` stub and creates a shared DearPyGui context without a viewport for tests that need it.

## Quick Start

Run from the repository root:

```powershell
conda activate bt_env
python -m pytest -q
```

The `testpaths = tests nodes` setting in `pytest.ini` collects both groups:

- `tests/test_*.py`: core, application, persistence, projection, and runtime tests.
- `nodes/**/test_*.py`: behavioral tests located beside node plugins.

Do not use only `pytest tests/` as a full regression run because it omits tests under `nodes/`. Prefer `python -m pytest` so Python and pytest come from the active Conda environment.

## Test Categories

During collection, `pytest_collection_modifyitems()` in `conftest.py` assigns an execution-level marker to every test:

| Marker | Scope | Typical files |
|---|---|---|
| `unit` | One core primitive, DSP operation, document, contract, or node behavior | `test_bus.py`, `test_dsp.py`, `nodes/**/test_node.py` |
| `integration` | Crosses application, worker, persistence, or GUI projection boundaries | `test_graph_owner.py`, `test_persistence_worker.py`, `test_graph_projection.py` |
| `runtime` | Follows a semantic user action through the complete production command/event path | `test_runtime_simulation.py` |

Two purpose markers may be combined with those execution levels:

| Marker | Purpose |
|---|---|
| `node` | A node test located under `nodes/` |
| `architecture` | AST, import, or surface checks for ownership and dependency rules |

When adding a cross-boundary test module, add its module stem to `_INTEGRATION_TEST_MODULES`. Runtime entry tests stay in `tests/test_runtime_simulation.py`, while framework support code stays in `tests/runtime_sim/`.

## Running Individual Layers

```powershell
# Fast unit layer, including node plugin tests
python -m pytest -m unit -q

# Application, persistence, and projection integration layer
python -m pytest -m integration -q

# Semantic runtime scenarios
python -m pytest -m runtime -q

# All node plugin tests
python -m pytest -m node -q

# Ownership and dependency architecture gates
python -m pytest -m architecture -q
```

Markers support normal pytest expressions:

```powershell
python -m pytest -m "integration or runtime" -q
python -m pytest -m "unit and not node" -q
```

You can also narrow execution to one file or test:

```powershell
python -m pytest tests/test_bus.py -q
python -m pytest tests/test_runtime_simulation.py::test_compile_gate_rejects_user_mutation_until_completion -q
```

For printed output and complete tracebacks:

```powershell
python -m pytest -m runtime -s -vv --tb=long
```

## Semantic Runtime Framework

`tests/runtime_sim/` is not a replacement implementation. It composes the real `ApplicationContext`, `core.bus.Bus`, `GraphOwner`, pipeline, `PersistenceWorker`, `GraphApplication`, and DearPyGui projection while isolating user data in a temporary preset directory.

Every action follows the same command/event path as the GUI:

```text
Semantic Action
  -> production callback / GraphCommandPublisher
  -> bounded core.bus.Bus
  -> GraphOwner
  -> GraphState / pipeline / persistence worker
  -> application event
  -> GraphApplication
  -> DearPyGui projection
  -> assertion + invariant checks
```

All DearPyGui mutations remain on the harness GUI test thread, and owner commands run on the owner thread. The harness records `ACTION`, `OWNER`, `EVENT`, `GUI`, `RESULT`, and `ASSERT`, then checks node and pin ownership, connection uniqueness, projection consistency, and thread ownership after every operation.

### Writing Scenarios

```python
from tests.runtime_sim import (
    Connect,
    ConnectionExists,
    GraphHasNodes,
    SpawnNode,
)


def test_signal_chain(runtime_harness):
    runtime_harness.perform(
        SpawnNode("GainNode", as_name="source"),
        SpawnNode("EqNode", as_name="effect"),
        Connect("source.out_audio", "effect.in_audio"),
    )
    runtime_harness.expect(
        GraphHasNodes(2),
        ConnectionExists("source.out_audio", "effect.in_audio"),
    )
```

Scenarios use stable aliases rather than runtime-generated node IDs. Pin references use `alias.port_role`. When a command should be rejected, call `perform_rejected(action, reason=...)` so rejection remains an observable result instead of catching an internal exception in the test.

### Asynchronous And Race Tests

Runtime tests must not wait with `time.sleep()`. Completion must be tied to a projection event, worker result, compile request, or condition gate:

```python
pending = runtime.begin(LoadPreset("practice"))
runtime.wait_for_deferred_persistence("load")
runtime.perform(SetParameter("gain", "gain_db", -3.0))
runtime.release_persistence("load")
runtime.complete_rejected(pending, reason="REVISION_CONFLICT")
```

Use `RuntimeHarness(defer_persistence=True)` to control load/save worker completion deterministically. Compile operations use `wait_for_compile_request()` and `complete_compile()`. These gates make race tests independent of machine speed.

### Scenarios And The Seeded Model

`Scenario` serializes action sequences to JSON for AI generation, CI artifacts, and failure replay:

```python
payload = scenario.dumps()
Scenario.loads(payload).run(runtime_harness)
trace_json = runtime_harness.trace.dumps()
```

`SeededScenarioGenerator(seed)` uses the standard library `random.Random` and maintains an independent model of nodes, names, parameters, connections, and undo/redo history. Every operation compares that model with the real runtime:

```python
SeededScenarioGenerator(19).generate(steps=30).run(runtime_harness)
```

Failures include the seed, step, complete Scenario JSON, and trace tail. Keep fixed seeds in CI. When exploration finds a failure, reduce it to a named regression test.

## Extension Rules

1. Put pure functions and single-class behavior in unit tests; use integration tests only when crossing owner, worker, or projection boundaries.
2. Put node algorithm tests in the matching `nodes/<plugin>/test_node.py` and use the `make_node` fixture.
3. Put user-observable workflows in the runtime layer; actions must call a production publisher or callback.
4. New runtime actions must define their final observable `expected_events`. Override `await_completion()` for multi-stage flows instead of sleeping or directly modifying `GraphState`.
5. Tests must not read or write user presets. Use `tmp_path` or the RuntimeHarness temporary persistence directory.
6. For a race or regression fix, add the smallest failing test first, run the affected layer, then run the complete `python -m pytest -q` suite.

For detailed actions, controlled races, and trace API examples, see the [Runtime Simulation Framework](../tests/runtime_sim/README.md).
