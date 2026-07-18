# Runtime Simulation Framework

[繁體中文](README.zh-TW.md)

This package drives semantic user actions through the real callback publisher, bounded bus,
`GraphOwner`, pipeline state, persistence worker, and DearPyGui projection. It uses a bare DPG
context and a temporary preset directory, so scenarios are suitable for headless CI and never
touch the user's saved presets.

## Basic scenario

```python
from tests.runtime_sim import (
    Connect, ConnectionExists, GraphHasNodes, RuntimeHarness, SpawnNode,
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

Node types accept either a registry display name (`Gain`) or class name (`GainNode`). Aliases are
stable scenario names and pin references use `alias.port_role`.

## Supported actions

- Graph editing: `SpawnNode`, `Connect`, `Disconnect`, `SetParameter`, `RenameNode`, `DeleteNode`
- History: `Undo`, `Redo`
- Workspace: `MoveNode`
- Persistence: `SavePreset`, `LoadPreset`
- Compile lifecycle: `Analyze`, followed by `runtime.complete_compile()`

Use `perform_rejected(action, reason=...)` when rejection is the expected user-visible result.
No scenario should call `time.sleep()`: action completion is tied to projection events, worker
results, compile requests, or `pump_until()`.

## Controlled races

Create `RuntimeHarness(defer_persistence=True)` when a scenario must control worker completion.
Use `begin()` to dispatch without waiting, wait for the named gate, perform concurrent user
actions, then release and complete the original action:

```python
pending = runtime.begin(LoadPreset("practice"))
runtime.wait_for_deferred_persistence("load")
runtime.perform(SetParameter("gain", "gain_db", -3.0))
runtime.release_persistence("load")
runtime.complete_rejected(pending, reason="REVISION_CONFLICT")
```

Save has a GUI capture stage before its persistence request. Pump `PresetCaptureRequested` first,
then wait for the `save` gate. The gate uses a condition variable and never sleeps.

## Scenario artifacts

`Scenario` serializes supported semantic actions to JSON and replays them against any harness.
This is the preferred interchange format for AI-generated tests and CI failure artifacts:

```python
scenario = Scenario("gain-edit", (
    SpawnNode("GainNode", as_name="gain"),
    SetParameter("gain", "gain_db", -6.0),
))
payload = scenario.dumps()
Scenario.loads(payload).run(runtime)
```

`runtime.trace.dumps()` returns machine-readable JSON with sequence, kind, payload type, payload
representation, thread ID, and thread name.

## Seeded model testing

`SeededScenarioGenerator(seed)` creates valid DAG edits from the standard library RNG and keeps an
independent model of nodes, names, parameters, connections, and undo/redo history. The generated
plan compares that model with the real runtime after every action:

```python
plan = SeededScenarioGenerator(19).generate(steps=30)
plan.run(runtime)
```

Failures include the seed, replayable Scenario JSON, and trace tail. CI uses a small fixed seed set;
additional seeds can be run locally without changing framework behavior or adding Hypothesis.

## Diagnostics and invariants

Every action records structured `ACTION`, `OWNER`, `EVENT`, `GUI`, `RESULT`, and `ASSERT` entries
with thread identity. Assertion failures include the trace tail. After each action the harness
checks:

- runtime node IDs and pin ownership are coherent;
- every input has at most one connection;
- every runtime node and connection has a DPG projection;
- owner operations ran on the owner thread;
- actions and GUI projection ran on the harness GUI thread.

## Extension rules

New actions should dispatch through the same callback or publisher used by production UI. Set
`expected_events` to the final projection event that makes the action observable. For multi-stage
flows, override `await_completion()` instead of sleeping. Native mouse/keyboard automation and
Hypothesis state-machine generation are intentionally separate future layers; semantic scenarios
remain the fast primary CI suite.