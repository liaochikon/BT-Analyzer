# 新增節點開發指南（BaseNode）

這份文件說明如何在 BT Analyzer 裡新增一個節點。對象是新進開發者，也適合給 AI 直接參考做 vibe coding。所有範例都指向 `nodes/` 底下**已存在、可運作**的節點，照抄最接近的那個原型再改即可。

> TL;DR：在 `nodes/<your_node>/node.py` 寫一個 `class XxxNode(BaseNode)`，設定能力旗標、覆寫對應的鉤子、註冊輸入/輸出腳位。引擎會自動掃描並註冊它。

## Public extension boundary

Node extensions以`BaseNode`、immutable `NodeConfig`、`ParameterSpec`、`ParameterControlSpec`、pins與
processor methods為public contract。不要import或修改pipeline runtime collections；graph mutation必須經
Graph commands，read model使用immutable `graph_snapshot()`，GUI-only runtime lookup使用明確
`runtime_node_view(node_id)` port。DearPyGui calls只能出現在GUI projection/hooks，不得放進processor或worker。

M7移除的compatibility APIs不再提供deprecation shim：`NODE_IN_RUNTIME`、`NODE_CONNECTIONS`、
`NODE_CONNECTION_IDS`、`PIN_OWNER`、`NODE_EXECUTION_ORDER`、`STALE_NODES`、`FAILED_NODES`、
`CYCLE_NODES`、`_HEAVY_DESCENDANTS_CACHE`與`CompileCoordinator.add_observer()`。內建程式碼若需要owner-side
state，使用`GRAPH_STATE`；第三方extensions不應依賴owner internals，應使用上述commands/snapshot ports。

---

## 1. 心智模型：節點分成幾種「原型」

不需要記全部 API，只要先判斷你的節點屬於哪一種，再去抄對應範例：

| 原型 | 用途 | 旗標 | 必須覆寫 | 參考檔案 |
|---|---|---|---|---|
| **輕量串流效果** | 即時逐塊轉換音訊（gain、EQ、壓縮…） | `config.processing=STREAMING` | `build_stream_renderer`、`publish_outputs`、`_process_block` | [gain_node/node.py](gain_node/node.py) |
| **效果 + 詳細控制** | 效果自動加入 Workspace 分頁 | `config.processing=STREAMING`、`config.ui=INSPECTOR` | 上述 + `_draw_inspector` | [eq_node/node.py](eq_node/node.py) |
| **透明監看（tap）** | 不改音訊，只擷取畫圖（示波器、頻譜） | `config.processing=STREAMING`、`config.realtime`、`config.ui=INSPECTOR_PANEL` | `build_stream_renderer`(passthrough)、`_draw_panel`、`on_gui_frame`、`publish_outputs` | [spectrum_node/node.py](spectrum_node/node.py)、[oscilloscope_node/node.py](oscilloscope_node/node.py) |
| **重度來源 / 烘焙** | 載入或重運算大資料（讀檔、AI 分離、轉錄） | `config.processing=BAKED` | `bake`、`publish_outputs` | [audio_input_node/node.py](audio_input_node/node.py)、[bs_roformer_node/node.py](bs_roformer_node/node.py) |
| **匯出 / sink** | 在 heavy compile 統一寫出結果 | `config.processing=BAKED` | `bake`、`publish_outputs` | [audio_export_node/node.py](audio_export_node/node.py)、[midi_export_node/node.py](midi_export_node/node.py) |

> 特例：[normalize_node/node.py](normalize_node/node.py) 是 streamable，但需要先掃完整段求峰值，所以它**自己覆寫** `build_stream_renderer`（不走單輸入 helper）。當你的節點需要「看過全段才能決定怎麼處理」時，照它抄。

---

## 2. 檔案結構與註冊

```
nodes/
  your_node/
    node.py        # 內含 class YourNode(BaseNode)
```

- 引擎啟動時 `scan_and_register_nodes()`（在 [core/pipeline.py](../core/pipeline.py)）會掃 `nodes/*/node.py`，把每個 `BaseNode` 子類自動註冊。
- **不需要**手動到任何地方登記。
- 選單顯示名稱來自類別屬性 `display_name`；分組來自 `category`。

每個 `node.py` 開頭固定這樣 import：

```python
import numpy as np
import dearpygui.dearpygui as dpg
from core import *          # 帶入 BaseNode, DataType, DataPacket, GLOBAL_SAMPLE_RATE,
                            # mark_downstream_heavy_stale, mark_structural_heavy_stale,
                            # slice_stereo_block 等
```

---

## 3. 能力旗標與「載入期契約」

在類別最上方以 immutable `NodeConfig` 集中宣告能力。非預設能力必須覆寫其承諾的鉤子，否則 class 一被載入就丟 `TypeError`（由 [core/base_node.py](../core/base_node.py) 的 `__init_subclass__` 強制）：

| 旗標 | 預設 | 設 True 時必須覆寫 |
|---|---|---|
| `config.processing=NodeProcessingMode.BAKED` | `NONE` | `bake()` |
| `config.ui=NodeUIMode.INSPECTOR` | `NONE` | 自動成為 Workspace 分頁；可選擇覆寫 `_draw_inspector()` |
| `config.ui=NodeUIMode.INSPECTOR_PANEL` | `NONE` | 同時成為 Workspace 分頁並要求 `_draw_panel()`；內容預設內嵌且可移至獨立視窗 |
| `config.processing=NodeProcessingMode.STREAMING` | `NONE` | `build_stream_renderer()` |
| `config.realtime=True` | False | `on_gui_frame()` |
| `config.singleton=True` | False | 無；限制全圖只有一個 instance |

此外，**任何節點都必須覆寫 `publish_outputs()`**（不然下游接不到資料）。

每個 runtime node 都會出現在 Workspace，即使 `config.ui=NONE` 也有共通 metadata
介面可編輯 `custom_name`。自訂名稱是 preset 用途說明，只影響顯示並隨 template/full preset
保存；不得拿來當 pin tag、registry key 或 fingerprint 輸入。

Panel/Inspector 的共用 layout primitive 位於 `ui.surfaces`：

- `canvas_surface()` 只建立填滿剩餘空間的 child container，不附帶 toolbar、padding 或內容。
- `centered_surface()` 只依 `CenterAxes` 將內容水平、垂直或雙軸置中；resize 更新由主迴圈的
    `update_surfaces()` 集中處理，node 不需要在 `on_gui_frame()` 自行重算。空間不足時保留
    最小 padding 並允許捲動，不會裁切長表單。

兩者可自由嵌套。目前 MIDI Piano Roll、Spectrum、Oscilloscope、Chord Chart 會在有資料時
以 plot/drawlist 填滿 canvas，無資料時切換為 centered empty state；Guitar Fretboard 使用
canvas 承載固定存在的指板。Workspace 的名稱欄與panel action固定在左上方，不參與置中；
其餘固定 controls依內容需要使用雙軸 centered surface；
Mixer 的 channel strips，以及 Chord Chart、Guitar Fretboard 的固定 controls 區也使用相同
primitive，後兩者下方 canvas 仍獨立填滿剩餘空間。

其他常用屬性：

```python
display_name = "My Node"     # 選單顯示名（必填，否則用類別名）
category = "Effects"         # 選單分組：Effects / Input / Output / Other ...
config = NodeConfig(
    processing=NodeProcessingMode.STREAMING,
    ui=NodeUIMode.INSPECTOR_PANEL,
    realtime=True,
    singleton=False,
)
panel_width = 460            # config.ui=INSPECTOR_PANEL 時的面板尺寸
panel_height = 640
class_node_theme_templates = []     # 自訂節點主題（多半留空）
class_button_theme_templates = []
```

> 想做一個「中間抽象基底類別」而不是可生成的節點時，設 `is_abstract = True` 可豁免契約檢查。它刻意不放進 `NodeConfig`，避免 concrete subclass 繼承抽象豁免；一般節點不要設。

---

## 4. 覆寫鉤子總覽

| 方法 | 在哪個執行緒 / 時機 | 做什麼 |
|---|---|---|
| `__init__(self, node_tag)` | 建立節點時 | 註冊腳位、初始化參數與 tag |
| `_draw_node_internal(self)` | GUI | 畫節點本體（腳位、widget） |
| `_draw_node_additional(self)` | GUI | 畫節點外的東西（如檔案對話框） |
| `_draw_inspector(self)` | GUI | 畫共通 metadata 之後的 node-specific 分頁內容；未覆寫時使用標準介面 |
| `_draw_panel(self)` | GUI（`config.ui=INSPECTOR_PANEL`） | 畫可從 Workspace 分頁開啟的獨立視窗，適合大型視覺介面 |
| `bake(self) -> bool` | worker thread（`config.processing=BAKED`） | 重度預運算，結果存進 `self.cached_outputs` |
| `publish_outputs(self) -> bool` | pipeline thread | 把輸出塞進 `self.output_values` 供下游接線 |
| `build_stream_renderer(self, out_pin, input_renderers)` | pipeline（編譯期） | 回傳一個 producer-thread 用的逐塊渲染函式 |
| `on_gui_frame(self)` | GUI（`config.realtime=True`，每畫面） | 更新面板上的即時繪圖 |
| `get_param_signature(self) -> str` | 任意 | 回傳影響輸出的參數字串（給指紋用） |

---

## 5. 註冊腳位（pins）

在 `__init__` 用 helper 註冊，回傳值是完整 tag，存成 `self.xxx` 後續引用：

```python
self.in_audio  = self._register_input("in_audio",  DataType.AUDIO)
self.out_audio = self._register_output("out_audio", DataType.AUDIO)
```

資料型別 `DataType` 目前支援：`AUDIO`、`MIDI`、`TEXT`。

音訊資料慣例：
- **烘焙 / 快取的陣列**：`(channels, samples)`。
- **串流逐塊（block）**：`(frames, channels)`，多為 stereo `(n, 2)` float32。
- 轉換用 `slice_stereo_block(matrix, start, frames)`：把 `(channels, samples)` 切成 `(n, 2)` float32，超過長度回 `None`。

---

## 6. 宣告式參數與控制元件（重要）

新增參數應將 backend policy 與 UI control 分開宣告。BaseNode 會建立 `X`、`X_default` 與
body/secondary widget tags，並驅動 preset 存讀與 R 重設按鈕。

```python
parameter_specs = (
    ParameterSpec("gain_db", 0.0, signature_precision=3),
    ParameterSpec("bypass", False),
)
parameter_control_specs = (
    ParameterControlSpec(
        "gain_db", SliderSpec("Gain", -60.0, 12.0, format="%.1f dB")),
    ParameterControlSpec("bypass", CheckboxSpec("Bypass")),
)
```

`ParameterSpec` 是 state/preset/signature/stale policy 的描述，不依賴 UI；headless parameter
可以不宣告 control。`ParameterControlSpec` 將 parameter name 綁到 `SliderSpec`、
`CheckboxSpec`、`ComboSpec`、`InputFloatSpec` 或 `InputIntSpec`。BaseNode 會自動初始化
attribute/default、驗證值、序列化、同步 widgets 並標記下游 stale。

所有 node 都必須使用分離 API，避免 backend contract 依賴 UI control 類型。

在 node body 或 Inspector/Panel 決定 layout 時，再把指定參數畫到當前 container：

```python
self._draw_declared_param("gain_db")
self._draw_declared_param("bypass")

# Inspector 使用同一份 state，但建立 secondary control。
self._draw_declared_param("gain_db", secondary=True, width=360)
self._draw_declared_param("bypass", secondary=True)

# INSPECTOR_PANEL 內容會在 Workspace與獨立視窗間重建，使用panel scope。
self._draw_declared_param("gain_db", scope="panel", width=360)
self._draw_declared_param("bypass", scope="panel")
```

`secondary=True`保留給Workspace inspector。`INSPECTOR_PANEL`的`_draw_panel()`應使用具名`panel`
scope；內容在內嵌與獨立projection之間切換前會先刪除舊widget tree，因此同一時間只有一份panel
widgets，且仍由同一份node parameter state同步，不會產生第二份狀態。

Command button（Analyze、Export、Retry）不是持久參數，不要放進 `ParameterSpec`。特殊元件或圖表可
直接讀寫 attribute，或呼叫 `_set_declared_param(name, value)`，不必強迫所有 UI 自動生成。

Node attribute 是唯一狀態來源；切換或重建分頁時 `_sync_control_widgets()` 會把 state 回填到
widgets。不要序列化 widget value，也不要在 Inspector 建立第二份參數狀態。

---

## 7. 串流模型與執行緒規則（最容易踩雷的地方）

即時播放時，引擎在**編譯期**呼叫 `build_stream_renderer()`，要求你回傳一個 `callable(start, frames)`。這個函式之後會在 **producer（音訊）執行緒**上被反覆呼叫。

**Producer-thread 契約（務必遵守）**：回傳的函式裡**只能**讀不可變快照與單純的純量參數，**不可以**：
- 碰任何 DearPyGui（`dpg.*`）
- 改共享 dict
- 做跨執行緒交接

### 單輸入效果：用 helper

最常見的情況，用 `_wrap_single_input` 把你的純 DSP 包起來：

```python
def _process_block(self, block):
    # block: (frames, channels) float32 → 回傳同形狀
    return block * np.float32(10.0 ** (self.gain_db / 20.0))

def build_stream_renderer(self, out_pin, input_renderers):
    return self._wrap_single_input(input_renderers, self._process_block)
```

### 透明 tap：擷取但不改

示波器/頻譜這種「不改音訊、只擷取畫圖」的節點，自己寫 passthrough，把最新 block 存起來給 `on_gui_frame` 用：

```python
def build_stream_renderer(self, out_pin, input_renderers):
    src = next(iter(input_renderers.values()), None)
    def render(start, frames):
        if src is None:
            return None
        block = src(start, frames)
        if block is not None and len(block):
            self._latest = block      # 存給 GUI thread 讀（單一參考交接，OK）
        return block                  # 原樣往下傳
    return render
```

### 重度來源：不串流，靠 build_baked_renderer 預設

`config.processing=NodeProcessingMode.BAKED` 的節點**不用**覆寫 `build_stream_renderer`，BaseNode 的預設會把你烘焙好的 `cached_outputs` 陣列切塊播放。你只要把資料烘進 `cached_outputs` 即可。

---

## 8. 重度節點：`bake()` 與 `publish_outputs()`

```python
def bake(self) -> bool:
    # 在 worker thread 跑，可以很慢。用 self._report_progress(0~1) 更新進度條。
    data, sr = librosa.load(self.file_path, sr=GLOBAL_SAMPLE_RATE, mono=False)
    if data.ndim == 1:
        data = np.expand_dims(data, axis=0)         # → (channels, samples)
    self.cached_outputs[self.out_audio] = data
    self.is_baked = True
    return True

def publish_outputs(self) -> bool:
    if self.is_baked and self.out_audio in self.cached_outputs:
        self.output_values[self.out_audio] = DataPacket(
            self.cached_outputs[self.out_audio], DataType.AUDIO)
        return True
    append_log(f"[{self.display_name}] No baked data. Bake first.", level="ERROR")
    return False
```

- 重度來源變動時（例如換檔案）呼叫 `mark_structural_heavy_stale(self.node_tag)`，把自己與所有下游 heavy 節點標記為過期。
- 串流效果類的參數變動則呼叫 `mark_downstream_heavy_stale(self.node_tag)`。

完整範例見 [audio_input_node/node.py](audio_input_node/node.py)。

---

## 9. 串流效果的 `publish_outputs`

串流節點不在 pipeline 階段產生音訊（音訊是 producer thread 逐塊算的），所以 `publish_outputs` 只發布一個「指紋直通」封包讓過期狀態能往下游傳：

```python
def publish_outputs(self) -> bool:
    self.output_values[self.out_audio] = DataPacket(None, DataType.AUDIO)
    return True
```

---

## 10. 指紋（fingerprint）

`get_param_signature()` 回傳一個**只含輕量參數**的字串，描述「什麼會改變輸出」。引擎用它 + 上游指紋算出便宜的內容指紋來決定要不要重算/重用快取。

```python
def get_param_signature(self) -> str:
    return f"gain_db={self.gain_db:.3f}"
```

**絕不要**在這裡雜湊大資料（音訊陣列等）。只放數值/字串/開關。重度來源可放檔案的 size + mtime（見 audio_input 的 `f"{path}|{size}|{mtime_ns}"`）。

---

## 11. 新增節點檢查清單

1. 建 `nodes/<your_node>/node.py`，`from core import *`。
2. `class YourNode(BaseNode)`，設 `display_name`、`category`、能力旗標。
3. `__init__` 註冊腳位、初始化每個參數的 `X` + `X_default` + slider tag。
4. `_draw_node_internal`（必畫腳位）；需要面板就 `_draw_panel`，需要對話框等就 `_draw_node_additional`。
5. 依原型覆寫：`_process_block`+`build_stream_renderer`（效果）/ `bake`（重度）/ tap 的 passthrough。
6. `publish_outputs`（一定要）。
7. `get_param_signature` 回傳影響輸出的參數。
8. 參數變動的 callback 裡呼叫 `mark_downstream_heavy_stale` 或 `mark_structural_heavy_stale`。
9. 啟動 app，節點會自動出現在選單；確認載入沒有 `TypeError`（契約檢查）。

---

## 12. 常見地雷

- **載入就 `TypeError: ... violates the BaseNode contract`**：你設了某旗標卻沒覆寫對應方法。對照第 3 節的表。
- **沒聲音**：忘了覆寫 `publish_outputs`，或串流節點的 `build_stream_renderer` 回傳了 `None`。
- **面板滑桿卡住不動**：第 6 節的 body/panel 同步陷阱，補上 `else` 分支。
- **偶發崩潰 / 競態**：在 `build_stream_renderer` 回傳的函式裡碰了 `dpg.*` 或改了共享狀態，違反 producer-thread 契約。
- **改參數下游沒更新成黃色**：忘了在 callback 呼叫 `mark_downstream_heavy_stale` / `mark_structural_heavy_stale`。
- **缺 `X_default`**：該參數不會被 preset 存到，R 按鈕也可能失效。

---

## 最小完整範例

最短的可運作節點是 [gain_node/node.py](gain_node/node.py)（輕量串流效果，約 55 行）。要新增同類效果，從它複製、改 `_process_block` 的 DSP 與參數即可。需要面板繪圖就改參考 [eq_node/node.py](eq_node/node.py)。
