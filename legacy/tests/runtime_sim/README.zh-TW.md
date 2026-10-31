# Runtime Simulation Framework

[English](README.md)

此package會讓語意化使用者action走過真實的callback publisher、bounded bus、`GraphOwner`、pipeline state、persistence worker與DearPyGui projection。它使用不含viewport的DPG context與暫存preset目錄，因此scenario適合headless CI，也不會碰觸使用者已儲存的preset。

## 基本Scenario

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

Node type可使用registry顯示名稱（`Gain`）或class名稱（`GainNode`）。Alias是scenario中的穩定名稱，pin reference使用`alias.port_role`。

## 支援的Action

- Graph編輯：`SpawnNode`、`Connect`、`Disconnect`、`SetParameter`、`RenameNode`、`DeleteNode`
- History：`Undo`、`Redo`
- Workspace：`MoveNode`
- Persistence：`SavePreset`、`LoadPreset`
- Compile lifecycle：`Analyze`，接著呼叫`runtime.complete_compile()`

預期出現使用者可見的拒絕結果時，使用`perform_rejected(action, reason=...)`。Scenario不可呼叫`time.sleep()`；action completion必須綁定projection event、worker result、compile request或`pump_until()`。

## 可控制的Race

Scenario需要控制worker completion時，建立`RuntimeHarness(defer_persistence=True)`。使用`begin()` dispatch但不等待，等候具名gate、執行並行使用者action，再release並完成原action：

```python
pending = runtime.begin(LoadPreset("practice"))
runtime.wait_for_deferred_persistence("load")
runtime.perform(SetParameter("gain", "gain_db", -3.0))
runtime.release_persistence("load")
runtime.complete_rejected(pending, reason="REVISION_CONFLICT")
```

Save在persistence request前有GUI capture階段。先pump `PresetCaptureRequested`，再等候`save` gate。Gate使用condition variable，不會sleep。

## Scenario Artifact

`Scenario`會把支援的語意action序列化成JSON，並可在任何harness重播。這是AI產生測試及CI failure artifact的首選交換格式：

```python
scenario = Scenario("gain-edit", (
    SpawnNode("GainNode", as_name="gain"),
    SetParameter("gain", "gain_db", -6.0),
))
payload = scenario.dumps()
Scenario.loads(payload).run(runtime)
```

`runtime.trace.dumps()`會回傳machine-readable JSON，包含sequence、kind、payload type、payload representation、thread ID及thread name。

## Seeded Model Testing

`SeededScenarioGenerator(seed)`使用標準庫RNG建立合法DAG編輯，並維護獨立的node、名稱、參數、connection與undo/redo history model。產生的plan會在每個action後比較model與真實runtime：

```python
plan = SeededScenarioGenerator(19).generate(steps=30)
plan.run(runtime)
```

Failure會包含seed、可重播的Scenario JSON與trace tail。CI使用少量固定seed；可在本機執行其他seed，而不需改變framework行為或加入Hypothesis。

## 診斷與Invariant

每個action都會記錄含thread identity的結構化`ACTION`、`OWNER`、`EVENT`、`GUI`、`RESULT`及`ASSERT`項目。Assertion failure會附上trace tail。每個action後，harness會檢查：

- runtime node ID與pin ownership一致；
- 每個input最多只有一條connection；
- 每個runtime node與connection都有DPG projection；
- owner operation在owner thread執行；
- action與GUI projection在harness GUI thread執行。

## 擴充規則

新action應透過production UI使用的同一個callback或publisher dispatch。將`expected_events`設為讓action可觀察的最終projection event。多階段flow應覆寫`await_completion()`，不可sleep。Native滑鼠/鍵盤自動化與Hypothesis state-machine generation刻意保留為未來的獨立layer；semantic scenario仍是快速的主要CI suite。
