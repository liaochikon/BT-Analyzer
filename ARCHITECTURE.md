# Architecture

BT-Analyzer is split so the same business pipeline can run **with or without a GUI**.
Agents and CI use the headless path; `bt-app` is one client of that path.

## Blocks

```text
models-toolchain  →  produces models/*.onnx (build-time only)
bt_stem           →  audio, inference, session, player, project I/O
bt_core           →  headless orchestration + TAB/quantize/fingering domain
bt_app            →  ImGui shell (optional UI client)
tools-apps        →  bt-separate, bt-agent, eval CLIs
product-shell     →  assets, locales, fonts, packaging, docs
```

Dependency direction is strictly downward:

| From | May depend on | Must not depend on |
|------|---------------|--------------------|
| `bt_stem` | system / ORT / soxr | ImGui, D3D, `bt_app`, `bt_core` |
| `bt_core` | `bt_stem` | ImGui, D3D, `bt_app`, HWND |
| `bt_app` | `bt_core`, `bt_stem`, ImGui | (nothing above) |
| `tools-apps` / `bt-agent` | `bt_core` and/or `bt_stem` | ImGui, D3D |
| `models-toolchain` | Python only | C++ compile graph |

Hard tests: `tools/check_architecture.py` (CTest `architecture.boundaries`) scans sources for forbidden includes.

## bt_stem sub-blocks

| ID | Role | Headers / sources |
|----|------|-------------------|
| A1 Contract / Audio | rates, stems, buffers | `contract`, `audio` / `audio_io` |
| A2 Inference | ONNX separate + pitch | `onnx_model`, `separator`, `roformer`, `basic_pitch` |
| A3 Analysis | pure DSP / scoring | `beat_track`, `note_score`, `harmonic_deghost`, `pitch_mask`, `midi_synth`, `time_stretch` |
| A4 Runtime | song lifecycle + playback + disk projects | `session`, `player`, `project` |

## bt_core

Stable **headless** surface (`bt::core::Engine` + domain):

| Piece | Path |
|-------|------|
| Types / workspace | `include/bt/core/types.hpp` |
| Grid helpers | `include/bt/core/grid.hpp` |
| TAB / fingering / quantize / score | `include/bt/core/tab.hpp`, `src/core/tab.cpp` |
| Engine | `include/bt/core/engine.hpp`, `src/core/engine.cpp` |

Engine: import / wait / mixer / `transcribe_stem` / `track_beats` / `quantize` / `resolve_tab` / `build_tab` / `convert_to_tab` / `export_tab` / `save_notes`.

## bt_app layout (GUI only)

```text
apps/bt_app/
  shell/     main.cpp
  input/     pointer, keys
  views/     ui, midi_view
  domain/    notes, tab, beats, history, playback, prefs, separate, transport, monitor
  widgets/   icons, i18n
  state/     app_state.hpp
```

| ID | Role | Folder |
|----|------|--------|
| B1 shell | frame loop | `shell/` |
| B2 widgets | i18n, icons (+ controls in `views/ui.hpp`) | `widgets/` |
| B3 input | pointer + keymap | `input/` |
| B4 domain facades | App& wrappers / history / workers | `domain/` |
| B5 views | canvas draw + hit | `views/` |
| B6 state | `App` bag | `state/` |

New **agent-usable** behavior must land in `bt_core` or `bt_stem`, with tests in `bt_core_tests` (no ImGui / D3D).

## Headless / agent

```bat
bt-agent --cache <dir> --model models/bs_roformer_sw.onnx --pitch models/nmp.onnx [--stem 4] [--out <dir>] <audio>
```

Pipeline: import → Ready → track beats → transcribe → quantize → TAB resolve → `.notes` + `.tab.txt`.

## Placement checklist

1. Pure audio / ML / disk? → `bt_stem`.
2. Orchestration / TAB / quantize without a window? → `bt_core`.
3. Draw / mouse / keyboard chrome? → `bt_app`.
4. Matching tests (`bt_core_tests` or `bt_stem_tests`).
5. `architecture.boundaries` before merge.
