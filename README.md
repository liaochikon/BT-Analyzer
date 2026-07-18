# BT Analyzer

BT Analyzer is a local, offline practice and song-analysis workspace for musicians. It currently includes several guitar-specific analysis plugins, making it especially useful for guitarists.

In theory, it can extract notes played by different instruments, display them directly on a guitar fretboard, and even estimate playable fingerings.

Load a song, separate its instruments, slow down a difficult section without changing pitch, loop it, and follow the chords or guitar fretboard while it plays. BT Analyzer uses a node graph, so you can start from a preset or build your own audio workflow.

For now, this is an experimental prototype built for fun, without a ready-to-use release. Its future direction is still open, but the project is designed to be easy to extend with custom plugins. You can hand the project to an AI coding tool or write your own extensions in Python if you would like to experiment with it.

[Traditional Chinese / 繁體中文](README.zh-TW.md)

## What You Can Do

- Separate a song into vocals, drums, bass, guitar, piano, and other stems with source-separation models.
- Detect chords, key, tempo, and beat positions.
- Slow down playback without changing pitch.
- Set an A/B loop for a section you want to practise repeatedly.
- Follow the current notes on a chord timeline, piano roll, or guitar fretboard.
- Mix stems with gain, EQ, compression, limiting, and noise gating.
- Export audio or MIDI results.
- Save the graph, parameters, playback state, and window layout as a preset.
- Explore plenty of other experimental features.

## Quick Start

BT Analyzer currently targets Windows and Python 3.11. Create the environment yourself with Conda:

```powershell
conda create -n bt_env python=3.11
conda activate bt_env
pip install -r requirements.txt
```

The included **Guitar** preset uses Basic Pitch and Demucs. Install one AI dependency set before using it:

```powershell
# CPU
pip install -r requirements-ai-cpu.txt

# Or NVIDIA GPU (much faster for song analysis, but installation depends on your GPU and driver)
pip install -r requirements-ai-gpu.txt
```

Start the application after installing the dependencies you need:

```powershell
python main.py
```

Then:

1. Load the **Guitar** preset when AI dependencies are installed, or start from an empty graph.
2. Choose a song in the **Audio Input** node.
3. Click **Global Bake** to run the heavy analysis nodes.
4. Press play, set an A/B loop, and adjust playback speed.
5. Save the result as a preset when the workspace is arranged the way you want.

For a complete song-learning walkthrough and a practical reference for every built-in node, see the [song workflow and node guide](docs/song_workflow_and_nodes.md).

Heavy analysis runs in the background, so the interface remains responsive. The first Demucs or BS-RoFormer run may download model weights.

## Node Editor Basics

- Use **Add Node** to add tools to the graph.
- Drag from one pin to another to connect compatible data types.
- Select a node or connection and press **Delete** to remove it.
- Use **Bake** on one heavy node, or **Global Bake** to rebuild the complete graph.
- Open **System > Settings** to change language, logging, the Demucs / BS-RoFormer device, font size, and cache settings.
- Use the **Window** menu to show or hide the editor, player, workspace, and logger.

## Optional AI Dependencies

The basic application can start with `requirements.txt`. Install one additional AI set when you need Basic Pitch, Demucs, or BS-RoFormer:

```powershell
# CPU
pip install -r requirements-ai-cpu.txt

# NVIDIA GPU
pip install -r requirements-ai-gpu.txt
```

The GPU requirements use a specific PyTorch CUDA build. Adjust the CUDA package index in [requirements-ai-gpu.txt](requirements-ai-gpu.txt) when your driver requires a different supported build.

Basic Pitch uses the official `basic-pitch` library and automatically selects a runtime supplied by that package. The AI Device setting applies to Demucs and BS-RoFormer. Demucs uses the original PyTorch/Demucs runtime and downloads its model on first use.

## Build And Test

This repository contains the source application only. Release packaging is maintained separately.

Run the application:

```powershell
conda activate bt_env
python main.py
```

Run the headless test suite:

```powershell
python -m pytest -q
```

Tests do not require an audio device. See the [testing guide](docs/testing.md) for test groups and the semantic runtime harness.

## Project Layout

```text
main.py          Application entry point
application/     Application lifecycle and graph ownership
core/            Audio engine, graph contracts, transport, cache, and DSP
nodes/           Auto-discovered node plugins
ui/              DearPyGui views and controls
locales/         English and Traditional Chinese UI strings
presets/         Reusable node graphs
fonts/           UI font assets
tests/           Headless and integration tests
```

Node plugins are discovered from `nodes/`. See the [node development guide](docs/node_development.md) for node contracts, threading rules, and implementation examples.

## Runtime Files

BT Analyzer creates local runtime files that are excluded from Git:

- `user_settings.json` for personal settings
- `.cache/` for baked node results
- `logs/` for diagnostic logs
- `samples/` for your own local test audio

## License

[MIT](LICENSE)
