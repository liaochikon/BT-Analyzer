# Developing A New Node With BaseNode

[繁體中文](node_development.zh-TW.md)

This guide explains how to add a node to BT Analyzer. It targets new contributors and coding agents. Every example points to an existing working node under `nodes/`; start from the closest prototype and adapt it.

> TL;DR: create `nodes/<your_node>/node.py`, define `class XxxNode(BaseNode)`, declare its capabilities, override the required hooks, and register its input and output pins. The engine discovers and registers it automatically.

## Public Extension Boundary

The public node extension contract consists of `BaseNode`, immutable `NodeConfig`, `ParameterSpec`, `ParameterControlSpec`, pins, and processor methods. Do not import or mutate pipeline runtime collections. Graph mutations must use graph commands; read-only model access uses immutable `graph_snapshot()`, and GUI-only runtime lookup uses the explicit `runtime_node_view(node_id)` port. DearPyGui calls belong only in GUI projection and hooks, never in a processor or worker.

Compatibility APIs removed in M7 no longer have deprecation shims: `NODE_IN_RUNTIME`, `NODE_CONNECTIONS`, `NODE_CONNECTION_IDS`, `PIN_OWNER`, `NODE_EXECUTION_ORDER`, `STALE_NODES`, `FAILED_NODES`, `CYCLE_NODES`, `_HEAVY_DESCENDANTS_CACHE`, and `CompileCoordinator.add_observer()`. Built-in code that needs owner-side state uses `GRAPH_STATE`. Third-party extensions should use the command and snapshot ports rather than owner internals.

## 1. Choose A Node Prototype

Identify the closest prototype before learning the complete API:

| Prototype | Purpose | Configuration | Required overrides | Reference |
|---|---|---|---|---|
| **Lightweight streaming effect** | Transform audio block by block | `processing=STREAMING` | `build_stream_renderer`, `publish_outputs`, `_process_block` | [gain_node/node.py](../nodes/gain_node/node.py) |
| **Effect with detailed controls** | Add an effect to the Workspace tabs | `processing=STREAMING`, `ui=INSPECTOR` | Streaming methods plus `_draw_inspector` when needed | [eq_node/node.py](../nodes/eq_node/node.py) |
| **Transparent monitor (tap)** | Capture data for a plot without changing audio | `processing=STREAMING`, `realtime=True`, `ui=INSPECTOR_PANEL` | Passthrough renderer, `_draw_panel`, `on_gui_frame`, `publish_outputs` | [spectrum_node/node.py](../nodes/spectrum_node/node.py), [oscilloscope_node/node.py](../nodes/oscilloscope_node/node.py) |
| **Heavy source or analysis** | Load or compute a complete data set | `processing=BAKED` | `bake`, `publish_outputs` | [audio_input_node/node.py](../nodes/audio_input_node/node.py), [bs_roformer_node/node.py](../nodes/bs_roformer_node/node.py) |
| **Exporter or sink** | Write a result during heavy compilation | `processing=BAKED` | `bake`, `publish_outputs` | [audio_export_node/node.py](../nodes/audio_export_node/node.py), [midi_export_node/node.py](../nodes/midi_export_node/node.py) |

## 2. File Layout And Registration

```text
nodes/
  your_node/
    node.py        # contains class YourNode(BaseNode)
```

At startup, `scan_and_register_nodes()` in [core/pipeline.py](../core/pipeline.py) scans `nodes/*/node.py` and registers each `BaseNode` subclass. No manual registry entry is required. `display_name` controls the menu label and `category` controls its group.

A typical node starts with:

```python
import numpy as np
import dearpygui.dearpygui as dpg
from core import *
```

## 3. Capability Flags And Load-Time Contracts

Declare capabilities in an immutable `NodeConfig`. When a non-default capability promises a hook, `BaseNode.__init_subclass__` checks it as soon as the class loads and raises `TypeError` if the override is missing.

| Capability | Default | Required override or behavior |
|---|---|---|
| `processing=NodeProcessingMode.BAKED` | `NONE` | `bake()` |
| `ui=NodeUIMode.INSPECTOR` | `NONE` | Adds a Workspace tab; `_draw_inspector()` is optional |
| `ui=NodeUIMode.INSPECTOR_PANEL` | `NONE` | Adds a Workspace tab and requires `_draw_panel()` |
| `processing=NodeProcessingMode.STREAMING` | `NONE` | `build_stream_renderer()` |
| `realtime=True` | `False` | `on_gui_frame()` |
| `singleton=True` | `False` | Limits the graph to one instance |

Every concrete node must override `publish_outputs()` so downstream connections receive data.

Every runtime node appears in the Workspace. Even a node with `ui=NONE` gets the shared metadata editor for `custom_name`. Custom names are preset annotations only: they affect display and are saved with template/full presets, but must not become pin tags, registry keys, or fingerprint input.

Shared Panel and Inspector layout primitives live in `ui.surfaces`:

- `canvas_surface()` creates a child container that fills the remaining space without adding a toolbar or content padding.
- `centered_surface()` centers content on selected `CenterAxes`. The main loop calls `update_surfaces()` on resize, so nodes should not recalculate layout in `on_gui_frame()`.

The surfaces can be nested. Canvas-based displays fill the remaining area when data exists and use a centered empty state otherwise. Workspace metadata and panel actions stay fixed at the top left.

Common class attributes look like this:

```python
display_name = "My Node"
category = "Effects"
config = NodeConfig(
    processing=NodeProcessingMode.STREAMING,
    ui=NodeUIMode.INSPECTOR_PANEL,
    realtime=True,
    singleton=False,
)
panel_width = 460
panel_height = 640
class_node_theme_templates = []
class_button_theme_templates = []
```

Set `is_abstract = True` only for an intermediate abstract base class. It deliberately sits outside `NodeConfig` so a concrete subclass cannot inherit an exemption from contract checks.

## 4. Hook Reference

| Method | Thread or time | Responsibility |
|---|---|---|
| `__init__(self, node_tag)` | Node creation | Register pins and initialize parameters and tags |
| `_draw_node_internal(self)` | GUI | Draw node pins and widgets |
| `_draw_node_additional(self)` | GUI | Draw external UI such as file dialogs |
| `_draw_inspector(self)` | GUI | Draw node-specific content after shared metadata |
| `_draw_panel(self)` | GUI | Draw an embeddable or detachable large panel |
| `bake(self) -> bool` | Worker thread | Run heavy processing and fill `cached_outputs` |
| `publish_outputs(self) -> bool` | Pipeline thread | Publish values to `output_values` |
| `build_stream_renderer(self, out_pin, input_renderers)` | Pipeline compilation | Return a producer-thread block renderer |
| `on_gui_frame(self)` | GUI, each frame | Refresh realtime visualization |
| `get_param_signature(self) -> str` | Any | Describe parameters that affect output |

## 5. Registering Pins

Register pins in `__init__` and retain the full returned tag:

```python
self.in_audio = self._register_input("in_audio", DataType.AUDIO)
self.out_audio = self._register_output("out_audio", DataType.AUDIO)
```

Current `DataType` values include `AUDIO`, `MIDI`, and `TEXT`.

Audio layout conventions:

- Baked and cached arrays use `(channels, samples)`.
- Streaming blocks use `(frames, channels)`, normally stereo `(n, 2)` `float32`.
- `slice_stereo_block(matrix, start, frames)` converts cached audio into streaming blocks and returns `None` past the end.

## 6. Declarative Parameters And Controls

Declare backend policy separately from UI controls. `BaseNode` creates each parameter attribute and default, control tags, preset serialization, validation, widget synchronization, stale propagation, and reset behavior.

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

`ParameterSpec` describes state, presets, signatures, and stale policy without depending on UI. A headless parameter needs no control. `ParameterControlSpec` binds a parameter name to `SliderSpec`, `CheckboxSpec`, `ComboSpec`, `InputFloatSpec`, or `InputIntSpec`.

Draw declared parameters in the current container:

```python
self._draw_declared_param("gain_db")
self._draw_declared_param("bypass")

# Workspace Inspector controls share the same state.
self._draw_declared_param("gain_db", secondary=True, width=360)

# INSPECTOR_PANEL content uses the named panel scope.
self._draw_declared_param("gain_db", scope="panel", width=360)
```

Reserve `secondary=True` for the Workspace Inspector. An `INSPECTOR_PANEL` rebuilds its widget tree when moving between embedded and detached projections, so use `scope="panel"`. The node attribute remains the only source of state.

Command buttons such as Analyze, Export, and Retry are not persistent parameters. Do not put them in `ParameterSpec`. Special widgets may update an attribute through `_set_declared_param(name, value)`.

Never serialize widget values or create a second parameter state in the Inspector. `_sync_control_widgets()` restores UI controls from node state after tabs or panels are rebuilt.

## 7. Streaming Model And Threading Rules

During compilation, the engine calls `build_stream_renderer()` and expects a callable with the shape `render(start, frames)`. The producer audio thread calls that function repeatedly during playback.

A producer-thread renderer may read immutable snapshots and simple scalar parameters. It must not:

- call DearPyGui (`dpg.*`);
- mutate a shared dictionary;
- perform cross-thread handoff logic.

### Single-input effect

Use `_wrap_single_input` for ordinary effects:

```python
def _process_block(self, block):
    return block * np.float32(10.0 ** (self.gain_db / 20.0))

def build_stream_renderer(self, out_pin, input_renderers):
    return self._wrap_single_input(input_renderers, self._process_block)
```

### Transparent tap

A monitor returns audio unchanged and retains the latest block for `on_gui_frame`:

```python
def build_stream_renderer(self, out_pin, input_renderers):
    source = next(iter(input_renderers.values()), None)

    def render(start, frames):
        if source is None:
            return None
        block = source(start, frames)
        if block is not None and len(block):
            self._latest = block
        return block

    return render
```

### Heavy source

A `BAKED` node normally does not override `build_stream_renderer`. The `BaseNode` implementation slices arrays from `cached_outputs` for playback.

## 8. Heavy Nodes: Bake And Publish

```python
def bake(self) -> bool:
    data, sample_rate = librosa.load(
        self.file_path, sr=GLOBAL_SAMPLE_RATE, mono=False)
    if data.ndim == 1:
        data = np.expand_dims(data, axis=0)
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

Heavy work runs on a worker thread and may call `self._report_progress()` with a value from `0.0` to `1.0`. When a heavy source changes, call `mark_structural_heavy_stale(self.node_tag)` to invalidate it and all downstream heavy nodes. A streaming effect parameter change calls `mark_downstream_heavy_stale(self.node_tag)`.

See [audio_input_node/node.py](../nodes/audio_input_node/node.py) for a complete example.

## 9. Streaming Publish Outputs

Streaming nodes do not produce complete audio during the pipeline phase. Publish a fingerprint passthrough packet so stale state propagates downstream:

```python
def publish_outputs(self) -> bool:
    self.output_values[self.out_audio] = DataPacket(None, DataType.AUDIO)
    return True
```

## 10. Fingerprints

`get_param_signature()` returns a lightweight string describing values that affect output. The engine combines it with upstream fingerprints to decide whether cached data can be reused.

```python
def get_param_signature(self) -> str:
    return f"gain_db={self.gain_db:.3f}"
```

Never hash large data such as audio arrays here. Include only numbers, strings, and switches. A heavy file source may include path, size, and modification time.

## 11. New Node Checklist

1. Create `nodes/<your_node>/node.py` and import from `core`.
2. Define `class YourNode(BaseNode)` with `display_name`, `category`, and `NodeConfig`.
3. Register pins and declare parameters.
4. Implement `_draw_node_internal`; add `_draw_panel` or `_draw_node_additional` only when needed.
5. Implement the prototype-specific path: `_process_block` and renderer for an effect, `bake` for heavy processing, or a passthrough renderer for a monitor.
6. Implement `publish_outputs`.
7. Return output-affecting parameters from `get_param_signature`.
8. Mark downstream or structural heavy state stale when parameters or source structure change.
9. Start the app. Confirm discovery succeeds without a contract `TypeError`.
10. Add focused behavior tests beside the node.

## 12. Common Failures

- **`TypeError: ... violates the BaseNode contract` at import:** a declared capability is missing its required override.
- **No audio:** `publish_outputs` is missing, or a streaming renderer returned `None` unexpectedly.
- **Panel slider does not move:** the body and panel controls are not synchronized through declared parameter state.
- **Intermittent crash or race:** producer-thread code called DearPyGui or mutated shared state.
- **Downstream nodes do not become stale:** the parameter callback did not call the appropriate stale helper.
- **Reset or preset persistence fails:** the parameter was not declared with its default and serialization policy.

## Minimal Working Example

[gain_node/node.py](../nodes/gain_node/node.py) is the shortest working streaming effect. Copy it and replace the DSP and parameters for a similar effect. Use [eq_node/node.py](../nodes/eq_node/node.py) when the node also needs a plotted control panel.
