# 測試指南

[English](testing.md)

本專案的測試皆可headless執行，不需要開啟viewport或實體音訊裝置。根目錄
`conftest.py`會先安裝最小的`sounddevice` stub，並為需要DearPyGui的測試建立一個
不含viewport的共用context。

## 快速執行

請在專案根目錄執行：

```powershell
conda activate bt_env
python -m pytest -q
```

`pytest.ini`的`testpaths = tests nodes`會同時收集：

- `tests/test_*.py`：core、application、persistence、projection與runtime測試。
- `nodes/**/test_*.py`：與node plugin放在一起的行為測試。

不要只執行`pytest tests/`作為完整回歸，因為那會漏掉`nodes/`下的測試。建議使用
`python -m pytest`，確保使用目前conda環境中的Python與pytest。

## 測試分類

collection時，`conftest.py`會為每個測試自動加上一個執行層級marker：

| Marker | 範圍 | 典型腳本 |
| --- | --- | --- |
| `unit` | 單一core primitive、DSP、document、contract或node行為 | `test_bus.py`、`test_dsp.py`、`nodes/**/test_node.py` |
| `integration` | 跨application、worker、persistence或GUI projection邊界 | `test_graph_owner.py`、`test_persistence_worker.py`、`test_graph_projection.py` |
| `runtime` | 從semantic user action走完整production command/event路徑 | `test_runtime_simulation.py` |

另有兩個可和上述層級疊加的用途marker：

| Marker | 用途 |
| --- | --- |
| `node` | `nodes/`下與plugin同位置的node測試 |
| `architecture` | 以AST、imports或surface檢查ownership與dependency規則 |

分類規則集中在`conftest.py`的`pytest_collection_modifyitems()`；新增跨邊界測試腳本時，
應將module stem加入`_INTEGRATION_TEST_MODULES`。runtime入口固定集中於
`tests/test_runtime_simulation.py`，framework支援程式則放在`tests/runtime_sim/`。

## 分層執行

```powershell
# 快速unit層，包含node plugin測試
python -m pytest -m unit -q

# application / persistence / projection整合層
python -m pytest -m integration -q

# semantic runtime scenarios
python -m pytest -m runtime -q

# 所有node plugin測試
python -m pytest -m node -q

# ownership與dependency architecture gates
python -m pytest -m architecture -q
```

marker可使用pytest expression組合：

```powershell
python -m pytest -m "integration or runtime" -q
python -m pytest -m "unit and not node" -q
```

也可以縮小到單一檔案或test：

```powershell
python -m pytest tests/test_bus.py -q
python -m pytest tests/test_runtime_simulation.py::test_compile_gate_rejects_user_mutation_until_completion -q
```

需要顯示print或完整traceback時：

```powershell
python -m pytest -m runtime -s -vv --tb=long
```

## Semantic Runtime框架

`tests/runtime_sim/`不是production的替代實作。它組合真實的`ApplicationContext`、
`core.bus.Bus`、`GraphOwner`、pipeline、`PersistenceWorker`、`GraphApplication`與
DearPyGui projection，再以暫存preset目錄隔離使用者資料。

每個action都走和GUI相同的command/event路徑：

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

所有DearPyGui mutation都留在harness所在的GUI測試執行緒；owner command由owner執行緒
處理。harness會記錄`ACTION`、`OWNER`、`EVENT`、`GUI`、`RESULT`與`ASSERT`，並在每個
操作後檢查node/pin ownership、connection唯一性、projection一致性與執行緒歸屬。

### 撰寫情境

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

scenario使用穩定alias，不直接依賴runtime產生的node ID；pin格式為`alias.port_role`。
預期command被拒絕時使用`perform_rejected(action, reason=...)`，讓rejection也成為可觀察
結果，而不是在測試內攔截內部exception。

### 非同步與race測試

runtime測試禁止用`time.sleep()`等待。completion必須綁定projection event、worker
result、compile request或condition gate：

```python
pending = runtime.begin(LoadPreset("practice"))
runtime.wait_for_deferred_persistence("load")
runtime.perform(SetParameter("gain", "gain_db", -3.0))
runtime.release_persistence("load")
runtime.complete_rejected(pending, reason="REVISION_CONFLICT")
```

使用`RuntimeHarness(defer_persistence=True)`可確定性控制load/save worker完成順序；
compile則使用`wait_for_compile_request()`與`complete_compile()`。這些gate讓CI不依賴機器
速度，也能穩定重現revision race。

### Scenario與seeded model

`Scenario`可將action序列序列化成JSON，適合AI agent產生、CI保存與失敗後重播：

```python
payload = scenario.dumps()
Scenario.loads(payload).run(runtime_harness)
trace_json = runtime_harness.trace.dumps()
```

`SeededScenarioGenerator(seed)`使用標準庫`random.Random`建立合法DAG操作，並維護獨立的
nodes、names、parameters、connections與undo/redo history model。每一步都將model與真實
runtime比較：

```python
SeededScenarioGenerator(19).generate(steps=30).run(runtime_harness)
```

失敗訊息包含seed、step、完整Scenario JSON與trace tail，因此可直接重播。固定seed應留在
CI測試；探索新seed時先在本機執行，找到failure後把最小重現情境固化成具名test。

## 擴充原則

1. 純函式或單一class行為放在unit測試；跨owner/worker/projection才列為integration。
2. node演算法測試放在對應`nodes/<plugin>/test_node.py`，並使用`make_node` fixture。
3. 使用者可觀察workflow放在runtime層，action必須呼叫production publisher或callback。
4. 新runtime action需定義最終可觀察的`expected_events`；多階段流程覆寫
   `await_completion()`，不可sleep或直接修改GraphState。
5. 測試不得讀寫使用者preset；使用`tmp_path`或RuntimeHarness的暫存persistence目錄。
6. 修正race或regression時，先加入能失敗的最小測試，再執行對應層級，最後跑完整
   `python -m pytest -q`。

更細的action、controlled race與trace API範例可參考
[繁中Runtime Simulation Framework](../tests/runtime_sim/README.zh-TW.md)。
