# BT-Analyzer

## 簡介

[English](README.en.md)

BT-Analyzer 是基於 Demucs 和 Basic Pitch 的轉譜工具，主要用來加速我的吉他抓歌流程，所以針對吉他指板的抓譜和彈奏位置顯示做了蠻多工作；此外，也在播放器裡加入一些方便練歌的功能。

![簡介](docs/images/intro.png)

目前版本已用 C++、ONNX Runtime 和 Dear ImGui 重構，在 Windows 11 測試可直接解壓縮即用。若使用上有問題或建議，歡迎留 Issue 或聯絡我。

## 特色

- **AI 分軌**：匯入音樂後用 BS-RoFormer SW 拆成六軌（鼓、貝斯、其他、人聲、吉他、鋼琴）。設定可改分軌模型與推論裝置（自動、CPU、CUDA）。有 `models/htdemucs_6s.onnx` 時可改用 HTDemucs 6s。發行分割檔含找得到的分軌權重。
- **混音控制**：即時調整各音軌音量、左右（pan）、靜音、獨奏
- **多功能播放器**：支援變速播放、AB 循環、數四拍
- **自動節拍**：節拍器可自動推算歌曲節拍，支援浮動 BPM
- **AI 轉譜**：各音軌可自動轉成音符，方便抓歌參考
- **多樂器音符顯示**：支援吉他、貝斯、Piano Roll、鋼琴下落
- **吉他／貝斯指板**：可在指板上顯示建議把位、空弦、泛音、其他可能彈奏位置

## 快速上手

### 0. 下載

1. 打開 [Releases 頁面](https://github.com/liaochikon/BT-Analyzer/releases)。
2. 找到最新一版，在 Assets 底下下載全部 `BT-Analyzer-*-win64.7z.001`、`.002`…（不要下載 Source code）。
3. 這些檔放在同一個資料夾，用 7-Zip 開啟 `.001` 並解壓，打開裡面的 `bt-app.exe`。需要 [7-Zip](https://www.7-zip.org/)。
4. 若 Windows 顯示「已保護你的電腦」，選「其他資訊」，再選「仍要執行」。

請整份資料夾一起保留；`bt-app.exe` 要靠旁邊的檔案才能開。

### 1. 開啟專案

把新歌曲拖進視窗，或按匯入。

![import](docs/images/import.png)

匯入後會自動分軌並轉譜，等進度跑完即可開始使用該專案。

![run](docs/images/run.png)

處理過的專案會出現在專案列表，下次可直接開啟，不用再跑一次。

![project](docs/images/project.png)

### 2. 音軌混音

分軌並轉譜完成後，會以六軌混音器呈現。  
各音軌可獨立調整音量、pan、靜音、獨奏。點選某個音軌後會高亮（旋鈕按右鍵可重置），上方的音符顯示會對應到該音軌已轉出的音符。

![mixer](docs/images/mixer.png)

### 3. 音符顯示

混音器上方是音符顯示視窗。

![midi_viewport](docs/images/midi_viewport.png)

上方按鈕可切換音符顯示方式。

![midi_view_select](docs/images/midi_view_select.png)

目前支援吉他、貝斯、Piano Roll、鋼琴下落。

![midi_view](docs/images/midi_view.png)

每種音符顯示都有更細的設定，例如吉他的偏好把位、顯示的音符範圍，或轉譜模型參數。

![midi_view_option](docs/images/midi_view_option.png)

### 4. 播放器

下方區塊是主播放器，用來控制歌曲播放。

![player](docs/images/player.png)

左邊是主要播放控制，由左至右為：播放／暫停、數四拍、停止、AB 循環、清除 AB 循環區段、AB 循環對拍、播放速度、總音量。  
右邊是節拍器，匯入歌曲時會自動計算拍點並開啟節拍器，由左至右為：節拍器音量、節拍器開關、重測拍點。

![player](docs/images/player_focus.png)

下方波形可當作進度條：左鍵拖曳播放進度，右鍵設定 AB 循環區段。

![player](docs/images/player_ab.png)

## 建置

需要 Windows 10/11（64 位元）、Visual Studio 2022（MSVC）、CMake。轉譜模型另需 Python 3.10+。

把 BS-RoFormer SW 的 ONNX 放到 `models/bs_roformer_sw.onnx`（`bs_roformer_sw_6stem_fp32.onnx` 或 `bs_roformer_sw_6stem_fp16.onnx` 也可以）。這個檔已在 `.gitignore`。`build.bat` 打分割檔時會把它放進去。圖的輸入是 `spec_real`、`spec_imag`，形狀 `[1, 2, 1025, T]`；輸出是 `out_spec_real`、`out_spec_imag`，形狀 `[1, 6, 2, 1025, T]`，`T` 必須固定。切塊長度是 `(T - 1) × 512`。和 UVR 同一套 588800 樣本切塊時，`T` 是 1151。

已裝 Python 3.11+ 時，一鍵裝依賴、下載缺少的權重並匯出全部 ONNX：

```bat
export-onnx.bat
```

會抓轉譜 `nmp.onnx`、匯出 HTDemucs（demucs 自帶下載）、並從 Hugging Face 鏡像下載 `BS-Rofo-SW-Fixed.ckpt` / `.yaml`（約 700 MB，sha256 校驗）後匯出 BS-RoFormer。本機已有合格檔案會跳過下載。也可用 `--checkpoint` / `--config` 指定路徑，或 `--skip-download` 只用地端檔。產出為 `models/nmp.onnx`、`models/htdemucs_6s.onnx`、`models/bs_roformer_sw.onnx`、`models/bs_roformer_sw_fp16.onnx`。

手動分步也行：

```bat
python tools/fetch_bs_roformer_sw.py
python -m pip install -r requirements-bs-roformer-onnx.txt
python -m bs_roformer_sw_onnx export models/bs_roformer_sw.onnx --checkpoint BS-Rofo-SW-Fixed.ckpt --config BS-Rofo-SW-Fixed.yaml
python tools/fetch_nmp_onnx.py
```

```bat
build.bat
```

產出只有分割檔：`dist\BT-Analyzer-<版本>-win64.7z.001` 起，每份 `build.bat` 的 `PART_SIZE_MB`（預設 200 MB），最後一份可能較小。版本號在 `VERSION`。用 7-Zip 開啟第一份即可解壓。內容是同一組檔案：程式、轉譜模型，以及打包時 `models` 裡找得到的分軌權重（`bs_roformer_sw.onnx`、若有則含 `bs_roformer_sw_fp16.onnx` 和 `htdemucs_6s.onnx`）。`cpp\build\cuda-ep` 存在時，CUDA 執行階段也在這組分割檔裡；沒有則這份 release 只用 CPU。

## 授權

[MIT](LICENSE)。第三方見 `packaging/THIRD_PARTY_LICENSES.txt`。
