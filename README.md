# BT Analyzer

**本地、離線、免費的扒歌／練歌工具** —— 給樂手(尤其吉他手)的練歌陪伴。載入一首歌,分離出人聲/鼓/貝斯/吉他/鋼琴各軌,看它的和弦、調性與指板按法,把某個段落循環起來慢速播放(不變調),把這首歌怎麼彈搞清楚。

底層以 [DearPyGui](https://github.com/hoffstadt/DearPyGui) 打造的**節點式音訊引擎**驅動:一首歌的處理流程(載入 → AI 分離 → 效果/混音 → 和弦/調性/指板分析 → 監聽播放)就是一張節點圖。進階使用者可自由改接線路——把單一 stem 路由過 EQ 凸顯某樂器、把清乾淨的 stem 餵進轉錄得到更好的結果;一般使用者則直接載入現成 preset 開練。

> 引擎採「字串型別契約 + 輕/重編譯 + 隱式快取」設計:前端瘋狂連線/剪線/調參時,後台都以最小代價即時重算,重型 AI 推理跑在背景執行緒、不凍結 UI。

---

## 給樂手:練歌功能

- **6 軌 stem 分離**:一鍵把歌拆成人聲 / 鼓 / 貝斯 / 吉他 / 鋼琴 / 其他(Demucs `htdemucs_6s`),或人聲/伴奏二分(BS-RoFormer)。獨立的吉他與鋼琴軌是多數線上工具沒有的。
- **和弦偵測 + 和弦時間軸**:自動辨識整首歌的和弦(chroma template),在卷動的時間軸上呈現(像影片 tab 譜那樣隨播放頭滑動)。
- **調性偵測**:Krumhansl-Schmuckler 分析整首歌的調(如「G major」),可接下游做音階/級數應用。
- **吉他指板**:播放頭同步高亮當下該按的音,把 MIDI/和弦視覺化成指板按法。
- **變速不變調 + A/B 循環**:任意慢放某段練習而不改變音高(WSOLA),設定 A/B 兩點無縫循環該段落。
- **拍格、節拍器、count-in**:偵測 BPM 畫出拍線、播放時可切齊拍點循環;內建節拍器與前導拍,click 音量獨立於主輸出。
- **波形總覽 + 主音量**:全曲波形一目了然、拖曳定位;主輸出音量拉桿。
- **每首歌存檔**:把整套工作環境(節點圖 + node panel + Node Editor / Workspace / Player / Logger / Settings 的位置、大小、顯示狀態與 dock 佈局 + 參數 + A/B loop / 速度等練習狀態)存成 preset,下次開檔即用。內附幾套模板 preset(吉他、人聲、Pop 吉他…)可直接 fork。

## 給進階使用者:節點引擎

- **節點編輯器**:從選單新增節點、拖拉連線即時組管線,內建 minimap。
- **Workspace**:所有節點都會自動成為不可關閉的分頁，可用固定在左上方的名稱欄標示它在目前 preset 的用途；Mixer、指板、頻譜等視覺介面預設直接內嵌，按 Open Panel 後移至獨立視窗，關閉或召回時回到 Workspace。新增/刪除節點、載入 preset、undo/redo 時同步更新。
- **快取感知Undo/Redo**:還原節點圖與面板位置時，會以內容簽章重新綁定session RAM或disk cache中的Demucs、Basic Pitch等已完成結果，再執行light compile讓audio/MIDI回到下游視圖。若參數已使結果失效或快取已不存在，該node維持待Analyze，不會誤用舊輸出。
- **強型別連線守門員**:每個 pin 帶 `::TYPE` 後綴,連線當下即時核對型別,不符直接拒絕。
- **輕 / 重編譯模型**:
  - *Light compile* — 只重算拓樸並沿用既有快取傳播資料(連線/刪節點時自動觸發)。
  - *Heavy compile* — 強制重新 bake 所有重型節點(如 AI 推理),跑在**背景執行緒**,不凍結 UI;執行期間節點編輯器變灰並鎖定編輯。
- **串流播放引擎**:`Audio Output` 單例 sink 即時監聽,搭配上述完整播放控制列。
- **內建節點庫**(放進 `nodes/` 即自動掃描註冊):
  - *Input / Output* — Audio Input(重節點)、Audio Output(單例 sink)、Audio Export、MIDI Export、MIDI to Audio、Beat Grid Output。
  - *Mixing* — Mixer(8 軌、dB 推桿含 mute/solo、單例、可串流)。
  - *Effects* — Gain、EQ、Compressor、Limiter、Noise Gate(皆可串流)、Time Stretch、Pitch Shift、Demucs v6、BS-RoFormer。
  - *Analysis* — Beat / Tempo、Chord Detection(和弦 + 調性)、Basic Pitch、Audio Velocity Map。
  - *Display* — Oscilloscope、Spectrum、MIDI Piano Roll、Guitar Fretboard、Chord Chart。
- **設定視窗**(系統 → 設定),變更即時生效並持久化於 `user_settings.json`:
  - 介面語言(即時切換,不重建視窗)、記錄等級(DEBUG/INFO/WARNING/ERROR)。
  - AI 推論裝置(auto/cuda/cpu)+ **偵測可用裝置**按鈕。
  - 字型大小(slider,附「預設」還原鈕)、預設視窗大小(附「預設」還原鈕)。
  - 一鍵清空磁碟快取。
- **多語系**:介面字串外部化於 [locales/](locales),目前支援 `zh_TW`、`en_US`;支援執行期即時切換。
- **彩色系統日誌**:底部 log 視窗,INFO/WARNING/ERROR 顏色編碼、自動捲動;背景執行緒的日誌會安全派送回 GUI 執行緒。
- **可插拔節點外掛**:把節點放進 `nodes/` 即自動掃描註冊,無需改動核心;重型相依(torch、tensorflow…)皆為延遲匯入,未用到的節點不會拖慢啟動。

---

## 專案結構

想先用白話理解目前系統如何啟動、編輯 Graph、compile、載入 preset 與播放音訊，請閱讀
[目前系統架構與實作說明](docs/current_architecture_guide.md)。文件會明確區分已實作能力、現有限制與
下一階段 headless／AI Agent 目標。後續的 stable ID、owner-only commit、可保存 GraphDocument 與
GUI 共用 editor port 的執行順序，請見
[下一階段 Headless Editor 實作計畫](docs/next_headless_editor_plan.md)。

開發與 review 請先閱讀 [程式碼規範](docs/coding_standards.md)。規範以明確的抽象分類、
最小可組合 primitive、可見的 policy 與單一 lifecycle owner 為核心。

```
main.py              # 進入點:載入設定、建立視窗、跑 render loop
default_layout.ini   # 預設視窗佈局
core/
  bus.py             # bounded message bus、Mailbox 與跨執行緒訊息契約
  settings.py        # 設定層:DEFAULTS 啟動預設值 + user_settings.json 使用者覆寫
  base_node.py       # BaseNode 基底、能力旗標檢查
  datatypes.py       # DataType、DataPacket、NOTE_DTYPE 等型別契約
  node_themes.py     # 節點/按鈕主題樣板
  pipeline.py        # 執行引擎:掃描註冊、拓樸排序、light/heavy compile、連線/節點生命週期
  playback_engine.py # 串流播放引擎(sounddevice):master/click 音量、count-in、metronome overlay
  transport.py       # 播放傳輸狀態(play/stop/pause/seek/loop/rate,command queue)
  time_stretcher.py  # WSOLA 變速不變調
  buffer.py          # 音訊環形緩衝
  mem_cache.py       # 記憶體快取(LRU)
  disk_cache.py      # 磁碟快取(僅存 ndarray,可於設定清空)
  graph_presets.py   # preset 序列化/載入(serialize_graph / apply_graph / globals)
  graph_history.py   # undo / redo
  dsp.py             # 共用 DSP kernel
  constants.py       # 共用常數(取樣率、INFERENCE_DEVICE…)
  theme.py           # 主題樣板與建立函式
nodes/               # 節點外掛(每個資料夾含 node.py,自動掃描)
  _shared/           # 跨節點共用 mixin:電平表、頻譜、librosa bake、MIDI/和弦命名
ui/
  ui.py              # UIConfig、主視窗/節點編輯器/日誌視窗
  pedal_workspace.py # config.ui=INSPECTOR 節點的自動分頁控制層
  transport_bar.py   # 播放控制列:scrubber、拍格、速度/A-B loop/master/metronome、globals 序列化
  settings_window.py # 設定視窗
  preset_menu.py     # preset 選單(存/讀/模板)
  control.py         # 全域鍵盤處理(Delete 刪節點/連線)
  keymap.py          # 快捷鍵對應
  icons.py           # 圖示
  locale.py          # 語系載入
  i18n.py            # 執行期即時翻譯與註冊表
app_logging/
  logging.py         # 彩色日誌、append_log、set_log_level
  __init__.py        # GUI 執行緒綁定與跨執行緒派送
tests/               # unit / integration / semantic runtime 測試與共用runtime harness
locales/             # 介面字串 JSON(zh_TW、en_US)
presets/             # 節點組合預設(含吉他/人聲等模板)
fonts/               # 字型資產
samples/             # 本機測試音訊（不提交版權音樂）
```

---

## 環境與安裝

需要 Python 3.10+(專案使用 conda 環境 `bocchi`)。

```powershell
# 建立並啟用環境
conda create -n bocchi python=3.11
conda activate bocchi

# 核心相依(不含 AI,即可啟動 UI 與基本音訊/效果節點)
pip install -r requirements.txt
```

**選配相依(用到對應節點時才需要,皆為延遲匯入)** — 依硬體二選一:

```powershell
pip install -r requirements-ai-cpu.txt   # 開發用 CPU Demucs / BS-RoFormer stack
# 或
pip install -r requirements-ai-gpu.txt   # GPU 版(CUDA torch + audio-separator[gpu])
```

> GPU 版請先依 [PyTorch 官方指引](https://pytorch.org/get-started/locally/)調整 `requirements-ai-gpu.txt` 中的 CUDA 標籤(cu118/cu121/cu124…)以符合驅動。
> MIDI to Audio 合成音訊需額外於系統安裝 FluidSynth。
> Basic Pitch ONNX模型已隨專案提供。Demucs與BS-RoFormer首次使用時會下載模型。

字型檔請放在 [fonts/](fonts) 下,並於 [core/settings.py](core/settings.py) 的 `font_file` 指向它。

---

## 執行

```powershell
conda activate bocchi
python main.py
```

每次啟動會在`logs/`建立一個`bt_analyzer_日期時間_PID.txt`。檔案包含terminal診斷、
執行緒資訊，以及GUI的滑鼠點擊、雙擊、釋放、滾輪、按鍵、focused item與active window；
不記錄文字輸入內容或所選檔案路徑。`logs/`是runtime產物，不納入Git。

**快速上手(練一首歌):**
1. 從 preset 選單載入一套模板(如 **Guitar** / **Pop Guitar**),或自己拉一張圖。
2. 在 Audio Input 節點選你的歌檔,按 **global bake** 跑分離/分析(重型,跑在背景)。
3. 播放控制列上:設 A/B 兩點循環某段、把速度拉慢、開節拍器與拍格對齊,對著吉他指板與和弦時間軸練。
4. 存成 preset後，視窗位置、大小、顯示狀態與dock佈局都會一起還原；選擇 **with parameters** 時，檔案路徑、A/B loop、速度等內容狀態也會一起保存。舊版preset沒有視窗資料時仍可載入，並沿用當下桌面佈局。

**節點編輯器操作:**
- 選單 **Add Node** 新增節點,拖拉 pin 連線。
- 重型節點上的 **Bake**、或選單的 **global bake** 觸發 heavy compile。
- 選取節點/連線後按 **Delete** 刪除。
- **Window** 選單可開關節點編輯器、日誌、Workspace與Player視窗。
- **System → Settings** 開啟設定視窗(語言、記錄等級、AI 裝置、字型大小、預設視窗大小、清空快取)。

**跑測試(headless,無需音訊裝置):**

```powershell
conda activate bocchi
python -m pytest -q
```

此命令會依 [pytest.ini](pytest.ini) 同時收集 [tests/](tests) 與 [nodes/](nodes) 內貼近
plugin的測試。測試分類、分層執行指令與semantic runtime框架實作，請見
[測試指南](docs/testing.md)。

---

## 設定

設定分兩層:

**1. 啟動預設值** — [core/settings.py](core/settings.py) 的 `DEFAULTS` dict,直接改值即可:

```python
DEFAULTS = {
    "locale": "ZH_TW",                 # 介面語系(對應 locales/ 下的 JSON)
    "default_layout_file": "default_layout.ini",
    "font_file": "fonts/sarasa-gothic-tc-regular-nerd-font.ttf",
    "font_size": 18,
    "viewport_title": "BT Analyzer",
    "viewport_width": 1000,
    "viewport_height": 700,
}
```

缺任何鍵都會回退到 `DEFAULTS`。

**2. 使用者設定(持久化)** — 由「系統 → 設定」視窗調整,寫入專案根目錄自動產生的 `user_settings.json`,並覆寫上述預設值。同一個 `core.settings.get()` 入口先讀使用者覆寫、再回退 `DEFAULTS`。涵蓋:介面語言、記錄等級、AI 推論裝置、字型大小、預設視窗大小。其中語言、記錄等級、字型大小、清空快取為**即時生效**;AI 裝置於下次 bake 生效;預設視窗大小於下次啟動套用。

> `user_settings.json` 屬於個人化檔案,散布原始碼時建議排除(見下方代辦事項)。

---

## 代辦事項 (TODO)

> 完整的產品 / 工程 review 與路線圖見 [docs/review_and_roadmap.md](docs/review_and_roadmap.md)。

### 傳播與安裝(最大阻礙,優先)
- [x] 新增 `requirements.txt` / `requirements-ai.txt`,鎖定版本以利安裝(核心與 AI 相依分離)。
- [x] 新增 `.gitignore`(排除 `user_settings.json`、`__pycache__/`、`.cache/` 等)。
- [x] 將 AI 相依拆成 CPU / GPU 兩版(`requirements-ai-cpu.txt` / `requirements-ai-gpu.txt`)。
- [ ] 模型權重下載/快取策略(首次啟動進度 UI、離線可用性)。
- [ ] 安裝指南補充:FluidSynth(MIDI to Audio 需要)、torch CUDA 版本選擇。
- [ ] 跨平台驗證(目前以 Windows / PowerShell 為主)。
- [ ] 補上授權與第三方授權彙整(Demucs、Basic Pitch、字型)。

### 產品深度(之後)
- [ ] 段落 marker + 分段循環(把單一 A/B loop 泛化成命名、可跳轉的段落)。
- [ ] 吉他 Tab 匯出(指板 position → ASCII / MusicXML)。
- [ ] 延伸和弦(7th / sus / dim / add9)+ 調性應用(指板高亮該調音階、和弦標級數)。

### 測試 / 品質
- [x] headless 測試套件:核心引擎(transport / buffer / cache / WSOLA / dsp)+ 節點行為 + preset 驗證 / transactional load / atomic save round-trip(`pytest -q`,數秒完成,全綠)。
- [ ] 節點整合測試(需先把 pipeline 的 DPG 編輯 callback 拆出核心)。

### 效能 / 技術探索
- [ ] 量測冷啟動時間(torch/tensorflow 已採延遲匯入)。
- [x] Basic Pitch改用獨立ONNX Runtime，不再需要`basic-pitch`或TensorFlow runtime。
- [ ] 評估Demucs原始Torch runtime的模型下載、GPU相容性與記憶體需求。
- [ ] 將BS-RoFormer納入可驗證的純ONNX release runtime。
- [ ] 設定視窗預留「推論後端(torch / onnxruntime)」選項雛形。

---

## 新增節點外掛

> 完整開發指南(原型對照、串流契約、執行緒規則、參數慣例、常見地雷)請見 **[nodes/README.md](nodes/README.md)**。以下為最小上手範例。

1. 在 [nodes/](nodes) 建立資料夾,例如 `nodes/gain_node/`。
2. 內含 `node.py`,定義一個繼承 `BaseNode` 的類別:

```python
import numpy as np
import dearpygui.dearpygui as dpg
from core import *

class GainNode(BaseNode):
    display_name = "Gain"          # 顯示在 Add Node 選單的名稱
    category = "Effects"           # 選單分類
    config = NodeConfig(processing=NodeProcessingMode.STREAMING)

    def __init__(self, node_tag):
        super().__init__(node_tag)
        self.in_audio = self._register_input("in_audio", DataType.AUDIO)
        self.out_audio = self._register_output("out_audio", DataType.AUDIO)
        self.gain_db_default = 0.0
        self.gain_db = self.gain_db_default

    def _draw_node_internal(self):
        ...  # 畫 pin / 控制項

    def get_param_signature(self) -> str:
        return f"gain_db={self.gain_db:.3f}"   # 參與快取指紋

    def _process_block(self, block):
        return block * np.float32(10.0 ** (self.gain_db / 20.0))

    def build_stream_renderer(self, out_pin, input_renderers):
        return self._wrap_single_input(input_renderers, self._process_block)

    def publish_outputs(self) -> bool:
        self.output_values[self.out_audio] = DataPacket(None, DataType.AUDIO)
        return True
```

3. 啟動程式即自動掃描註冊。

**重點契約**
- pin 一律透過 `_register_input` / `_register_output` 取得完整 tag(格式 `{node_tag}_{pin_id}::TYPE`)。
- `publish_outputs()` 把該節點的輸出寫進 `self.output_values`,成功回傳 `True`(所有節點都必須覆寫)。
- **能力旗標**會在載入期由 `__init_subclass__` 強制檢查;旗標開了卻沒實作對應鉤子會直接丟 `TypeError`:
  - `config.processing = NodeProcessingMode.BAKED` → 實作 `bake()`(重算結果存進 `self.cached_outputs`,`publish_outputs()` 只負責曝露快取)。
  - `config.processing = NodeProcessingMode.STREAMING` → 覆寫 `build_stream_renderer()`(逐區塊即時處理)。
  - `config.realtime = True` → 實作 `on_gui_frame()`(每幀在 GUI 執行緒被呼叫)。
  - `config.ui = NodeUIMode.INSPECTOR` → node instance 自動加入 Workspace；可覆寫 `_draw_inspector()` 自訂分頁，否則使用標準介面。
  - `config.ui = NodeUIMode.INSPECTOR_PANEL` → 同樣加入 Workspace，並須實作 `_draw_panel()`；內容預設內嵌，且可在獨立 Panel 與 Workspace 間切換。
  - 所有 node（包含 `ui=NONE` 的 sink）都有共通 metadata 分頁；`custom_name` 按 Enter 或輸入框失焦後套用，只改變 graph、tab、Panel 與狀態顯示，不改變 `node_tag`、registry 或 cache fingerprint，並隨完整／template preset 保存。
  - `NodeConfig` 是 frozen immutable object；processing 與 UI 模式各自互斥，`singleton=True` 表示全圖只能有一個 instance。
  - 常用參數以 immutable `ParamSpec` 宣告；`SliderSpec`、`CheckboxSpec`、`ComboSpec`、`InputFloatSpec`、`InputIntSpec` 統一處理 default、preset、同步、reset 與 stale propagation。
- 串流回傳的 render 函式跑在 **producer 執行緒**:只能讀不可變快照與原子純量,不可碰 `dpg`、不可改共享狀態(細節見 [nodes/README.md](nodes/README.md))。

---

## 授權

未指定。
