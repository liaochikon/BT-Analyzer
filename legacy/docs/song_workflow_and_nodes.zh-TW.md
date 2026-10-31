# 抓歌流程與節點使用指南

[English](song_workflow_and_nodes.md)

本指南說明如何使用preset分析與練習歌曲，以及所有內建節點的用途。

## 使用Guitar Preset抓歌

內附的`guitar.json` preset是分析吉他聲部最快的起點。它已接好分軌、速度與和弦分析、播放、混音、轉錄及指板顯示所需節點。

### 1. 載入preset

開啟preset選單並載入 **Guitar**。載入preset只會建立graph，不會選擇歌曲或執行重型分析。

主要訊號路徑如下：

```text
Audio Input -> Demucs -> Mixer -> Audio Output
            -> Beat / Tempo -> Beat Grid Output
            -> Beat / Tempo -> Chord Detection -> Chord Chart

Demucs Guitar -> Basic Pitch -> Guitar Fretboard
```

Demucs的六個stem也會分別接到Mixer，因此播放時可以個別Mute、Solo或調整音量。

### 2. 選擇歌曲

在 **Audio Input** 選擇音訊檔。Bake graph時，程式會解碼檔案並轉換成專案取樣率。

第一次嘗試時，建議使用較短的片段或編曲清楚的歌曲。完整歌曲的分軌與轉錄需要較長處理時間。

### 3. 執行Global Bake

按下 **Global Bake**。Baked節點會依graph順序執行：

1. Audio Input載入歌曲。
2. Demucs產生鼓、貝斯、其他、人聲、吉他與鋼琴stem。
3. Beat / Tempo找出拍格與BPM。
4. Chord Detection依音訊與拍點建立和弦時間軸。
5. Basic Pitch把分離出的吉他stem轉成MIDI音符。
6. 顯示與播放節點取得完成的結果。

重型工作會在背景執行。Demucs第一次執行時會下載官方模型，因此等待時間較長。可從狀態區與Logger查看進度或錯誤。

### 4. 練習聲部

按下 **Play**，再使用transport控制：

- 在要重複的段落設定 **A** 與 **B**。
- 啟用循環播放。
- 降低播放速度；音高不會跟著改變。
- 需要節奏提示時啟用拍格或節拍器。
- 從 **Chord Chart** 看和聲，從 **Guitar Fretboard** 看音符位置。
- 在Mixer Solo吉他stem可仔細聽原始聲部；Mute吉他則可跟其餘樂器一起練習。

Mixer及串流效果的控制會在播放時更新。修改baked分析節點的參數後，需要再次Bake或Global Bake。

### 5. 改善分析結果

- 指板資訊太密時，提高Basic Pitch threshold或縮小頻率範圍。
- 和弦變化太粗時，提高Chord Detection granularity並重新Bake。
- 分軌artifact較多時，提高Demucs shifts可換取較穩定但較慢的推論。
- 想凸顯某項樂器時，在stem後加入EQ或Gain。
- 使用指板的Suggested模式與Preferred Position，減少不實際的按法。

### 6. 儲存工作區

整理好graph與panel後另存新preset。需要一併還原節點參數、所選路徑、播放速度與循環狀態時，使用 **with parameters** 儲存。不要散布含有個人絕對檔案路徑的preset。

## 處理規則

BT Analyzer實務上有兩種節點：

- **Baked節點**處理整首歌曲。修改輸入或參數後要再次Bake。
- **Streaming節點**在播放時逐塊處理音訊，控制通常會立即更新。

Display節點只顯示資料，不會修改資料。Output節點播放或寫出資料，通常位於訊號鏈末端。

## 內建節點參考

### 輸入、輸出與轉換

| 節點 | 接線 | 使用方式 | 結果 |
|---|---|---|---|
| **Audio Input** | 無輸入 | 選擇音訊檔後Bake。 | 輸出給graph其餘節點使用的音訊。 |
| **Audio Output** | Audio輸入 | 接上最終混音；graph中只應使用一個。 | 把音訊送到播放器與音訊裝置。 |
| **Audio Export** | Audio輸入 | Bake前先選擇`.wav`目的地。 | 把完整輸入音訊寫成WAV。 |
| **MIDI Export** | MIDI輸入 | Bake前先選擇`.mid`目的地。 | 把音符或和弦事件寫成MIDI。 |
| **MIDI to Audio** | MIDI輸入 | 接上MIDI並Bake。 | 產生可混音或匯出的簡單合成音訊。 |
| **Beat Grid Output** | Tempo與time offset輸入 | 接上Beat / Tempo的兩個對應輸出。 | 更新transport拍格與吸附參考。 |

### 分離與分析

| 節點 | 接線 | 主要控制 | 使用方式 |
|---|---|---|---|
| **Demucs v6** | 混合音訊輸入 | Split、Shifts、Overlap、Segment | Bake後產生六個stem。較多shifts可能提高一致性，但會增加處理時間；第一次使用會下載模型。 |
| **BS-RoFormer** | 混合音訊輸入 | Model | Bake後產生人聲與伴奏stem；第一次使用會下載所選checkpoint。 |
| **Beat / Tempo** | Audio輸入 | 無必要控制 | Bake後產生BPM、beat offset與拍點MIDI。把MIDI接到Chord Detection，tempo/offset接到Beat Grid Output。 |
| **Chord Detection** | Audio與拍點MIDI輸入 | Granularity | 在Beat / Tempo之後Bake，輸出和弦MIDI與調性；至少需要兩個偵測到的拍點。 |
| **Basic Pitch** | Audio輸入 | Melodia Trick、onset/frame threshold、最短音符、頻率範圍 | 優先使用分離後的樂器stem。Bake後產生MIDI；提高threshold可減少誤判音符。 |
| **Audio Velocity Map** | MIDI與對應Audio輸入 | Band、Harmonics、Dynamic Range、Gain | 依音訊中對應音高的能量重新寫入MIDI velocity。 |

### 混音與效果

| 節點 | 處理方式 | 主要控制 | 使用方式 |
|---|---|---|---|
| **Mixer** | Streaming | 八個音量推桿、Mute、Solo、標籤 | 最多接八路音訊。每個stem使用一個channel，再把Master Out接到Audio Output。 |
| **Gain** | Streaming | Gain、Bypass | 放在音訊鏈中做簡單音量調整。 |
| **EQ** | Streaming | Low shelf、mid peak、high shelf | 調整單一stem或完整混音；panel會顯示response與即時頻譜。 |
| **Compressor** | Streaming | Threshold、Ratio、Makeup、Attack、Release | 縮小動態範圍。可先用中等ratio，再降低threshold直到出現gain reduction。 |
| **Limiter** | Streaming | Pre Gain、Output Gain、Ceiling、Attack、Release | 放在訊號鏈尾端附近，避免峰值超過ceiling。 |
| **Noise Gate** | Streaming | Threshold、Attack、Hold、Release、Floor | 降低樂句間的底噪；把threshold設在不需要的噪音稍上方。 |
| **Pitch Shift** | Baked | Semitones | Bake後改變音高，並盡量維持原本長度。 |
| **Time Stretch** | Baked | Speed | Bake後改變長度並保留音高；一般練習通常直接使用transport speed較方便。 |

### 顯示

| 節點 | 接線 | 主要控制 | 顯示內容 |
|---|---|---|---|
| **Chord Chart** | 和弦MIDI輸入 | 可見時間範圍 | 與播放頭同步捲動的和弦時間軸。 |
| **Guitar Fretboard** | MIDI輸入 | Frets、velocity與pitch filter、fingering mode、alternatives、open strings、harmonics、preferred position | 目前MIDI音符與可能的吉他位置；Suggested模式會提供較精簡的按法。 |
| **MIDI Piano Roll** | MIDI輸入 | Plot平移與縮放 | 依時間與音高顯示MIDI音符。 |
| **Spectrum** | Audio輸入及passthrough輸出 | 無必要控制 | 即時頻譜；可插在音訊鏈中而不改變訊號。 |
| **Oscilloscope** | Audio輸入及passthrough輸出 | 無必要控制 | 最新的波形sample；音訊會原樣往下傳。 |

## 常見問題

- **Baked節點顯示沒有輸入：** 檢查pin接線，再執行Global Bake。
- **Chord Detection失敗：** 把Beat / Tempo的MIDI輸出接到beat輸入，並確認有偵測到拍點。
- **沒有聲音：** 把最終音訊鏈接到Audio Output，確認Mixer channel未被Mute，並檢查音訊裝置。
- **匯出節點失敗：** Bake前先選擇輸出路徑。
- **AI顯示缺少dependency：** 在`bt_env`安裝`requirements-ai-cpu.txt`或`requirements-ai-gpu.txt`。
- **AI第一次執行很慢：** Demucs與BS-RoFormer可能正在下載模型權重。
- **修改參數後結果沒有改變：** Baked節點需要Bake；streaming節點會在播放時更新。
