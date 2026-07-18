# BT Analyzer

BT Analyzer 是給樂手使用的本機離線練歌與歌曲分析工具，目前有針對吉他開發了幾個專用分析插件，所以特別適合吉他手。

理論上來說可以直接抓出不同樂器的音符並直接以吉他指板顯示，外加彈奏手型推算等功能。

載入一首歌後，可以拆出不同樂器、慢放困難段落而不變調、設定 A/B 循環，並在播放時查看和弦或吉他指板。BT Analyzer 使用節點圖；想快速開始可以直接載入 preset，熟悉後也能自己接出需要的處理流程。

BTW，目前這是隨便做好玩的原型，也還沒有隨開隨用的發行版，之後會開發到什麼樣子還不清楚，但這個專案很好自己添加或開發延伸插件，可以直接把專案丟給AI或自己寫Python做擴充功能，有興趣的人可以自己試試看。

[English](README.md)

## 可以做什麼

- 使用音軌分離模型拆出人聲、鼓、貝斯、吉他、鋼琴與其他聲部。
- 偵測和弦、調性、速度與拍點。
- 慢放歌曲而不改變音高。
- 設定 A/B 循環，重複練習指定段落。
- 在和弦時間軸、鋼琴捲軸或吉他指板上查看目前音符。
- 使用 Gain、EQ、Compressor、Limiter 與 Noise Gate 混音。
- 匯出音訊或 MIDI 結果。
- 將節點圖、參數、播放狀態與視窗配置存成 preset。
- 還有很多其他有的沒的功能...

## 快速開始

目前主要支援 Windows 與 Python 3.11，需要自己用conda來建環境。

```powershell
conda create -n bt_env python=3.11
conda activate bt_env
pip install -r requirements.txt
```

內附的 **Guitar** preset會使用Basic Pitch與Demucs。使用前請選擇一組AI相依：

```powershell
# CPU
pip install -r requirements-ai-cpu.txt

# 或 NVIDIA GPU (安裝這套歌曲分析會快很多，但安裝比較麻煩，要看電腦顯卡哪張，裝什麼驅動)
pip install -r requirements-ai-gpu.txt
```

安裝所需相依後啟動程式：

```powershell
python main.py
```

開啟後：

1. 已安裝AI相依時載入 **Guitar** preset，否則從空白graph開始。
2. 在 **Audio Input** 節點選擇歌曲。
3. 按 **Global Bake** 執行需要較長時間的分析。
4. 開始播放，設定 A/B 循環並調整速度。
5. 工作區整理完成後，存成自己的 preset。

完整的preset抓歌流程與所有內建節點用法，請見[抓歌與節點使用指南](docs/song_workflow_and_nodes.zh-TW.md)。

重型分析會在背景執行，不會凍結介面。第一次使用 Demucs 或 BS-RoFormer 時，可能需要下載模型權重。

## 節點編輯器基本操作

- 從 **Add Node** 新增節點。
- 從一個 pin 拖到另一個 pin，即可連接相容的資料型別。
- 選取節點或連線後按 **Delete** 刪除。
- 按單一重型節點的 **Bake**，或使用 **Global Bake** 重建整張圖。
- 從 **Window** 選單開關編輯器、播放器、Workspace 與 Logger。

## 選配 AI 相依

只安裝 `requirements.txt` 即可啟動基本程式。需要 Basic Pitch、Demucs 或 BS-RoFormer 時，再依硬體選一組：

```powershell
# CPU
pip install -r requirements-ai-cpu.txt

# NVIDIA GPU
pip install -r requirements-ai-gpu.txt
```

GPU requirements 使用指定的 PyTorch CUDA build。若顯示卡驅動不相容，請在 [requirements-ai-gpu.txt](requirements-ai-gpu.txt) 改用PyTorch官方支援的CUDA套件來源。

Basic Pitch 使用官方 `basic-pitch` 函式庫，並由該package自動選擇runtime。AI推論裝置設定只套用於Demucs與BS-RoFormer。Demucs使用原始PyTorch/Demucs runtime，第一次使用時下載模型。

## 建置與測試

這個 repository 只放原始碼；Release 打包流程放在其他地方維護。

執行程式：

```powershell
conda activate bt_env
python main.py
```

執行 headless 測試：

```powershell
python -m pytest -q
```

測試不需要音訊裝置。測試分類與 semantic runtime harness 請見[測試指南](docs/testing.zh-TW.md)。

## 專案結構

```text
main.py          程式進入點
application/     應用程式生命週期與 Graph owner
core/            音訊引擎、Graph 契約、播放、快取與 DSP
nodes/           自動掃描的節點外掛
ui/              DearPyGui 畫面與控制項
locales/         英文與繁中介面字串
presets/         可重用的節點圖
fonts/           介面字型
tests/           Headless 與整合測試
```

程式會自動掃描 `nodes/`。節點契約、執行緒規則與實作範例請見[節點開發指南](docs/node_development.zh-TW.md)。

## 執行時產生的檔案

以下是本機使用資料，不會提交到 Git：

- `user_settings.json`：個人設定
- `.cache/`：已完成的節點結果
- `logs/`：診斷日誌
- `samples/`：自己的本機測試音訊

## 授權

[MIT](LICENSE)
