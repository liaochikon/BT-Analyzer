import hashlib
from dataclasses import dataclass
from enum import Enum, auto
import dearpygui.dearpygui as dpg
from core.theme import *
# The pin/packet vocabulary lives in core.datatypes now; re-exported here so every
# `from core.base_node import *` / `from core import *` call site is unchanged.
from core.datatypes import DataType, NOTE_DTYPE, DataPacket, slice_stereo_block
from core.node_themes import GLOBAL_NODE_THEME_TEMPLATES, GLOBAL_BUTTON_THEME_TEMPLATES
from core.parameters import *
from core.graph_contracts import CompileKind
from app_logging.dispatch import run_on_gui
from app_logging.logging import append_log


class NodeUIMode(Enum):
    NONE = auto()
    INSPECTOR = auto()
    INSPECTOR_PANEL = auto()


class NodeProcessingMode(Enum):
    NONE = auto()
    BAKED = auto()
    STREAMING = auto()


@dataclass(frozen=True, slots=True)
class NodeConfig:
    processing: NodeProcessingMode = NodeProcessingMode.NONE
    ui: NodeUIMode = NodeUIMode.NONE
    realtime: bool = False
    singleton: bool = False

    def __post_init__(self):
        if not isinstance(self.processing, NodeProcessingMode):
            raise TypeError("NodeConfig.processing must be a NodeProcessingMode value")
        if not isinstance(self.ui, NodeUIMode):
            raise TypeError("NodeConfig.ui must be a NodeUIMode value")
        if type(self.realtime) is not bool:
            raise TypeError("NodeConfig.realtime must be bool")
        if type(self.singleton) is not bool:
            raise TypeError("NodeConfig.singleton must be bool")


class BaseNode:
    display_name = "Base Node"
    max_custom_name_length = 64
    config = NodeConfig()
    parameter_specs: tuple[ParameterSpec, ...] = ()
    parameter_control_specs: tuple[ParameterControlSpec, ...] = ()

    category = "Other" # Grouping label used by the "Add Node" dropdown menu

    panel_width = 400 # Only used with config.ui=INSPECTOR_PANEL; subclasses may override the window size
    panel_height = 300
    
    top_bar_spacer_width = 220
    top_bar_spacer_height = 2

    global_node_themes = {}
    global_button_themes = {}
    class_node_themes = {}
    class_button_themes = {}

    # Theme template DATA lives in core.node_themes (edit colours there); build_global_themes
    # consumes these lists. Bound as class attributes so cls.<...>_templates lookups are unchanged.
    global_node_theme_templates = GLOBAL_NODE_THEME_TEMPLATES
    global_button_theme_templates = GLOBAL_BUTTON_THEME_TEMPLATES

    class_node_theme_templates = [] # Need to be defined in subclasses if they want to add custom node themes
    class_button_theme_templates = [] # Need to be defined in subclasses if they want to add custom node themes

    # If True, this class is an intermediate/abstract base (not a spawnable node) and is
    # exempt from the contract check below. Concrete nodes leave it False.
    is_abstract = False

    # ===== Contract enforcement (load time) =====

    def __init_subclass__(cls, **kwargs):
        """Fail loudly at class-definition (import) time when a capability flag is set but its
        promised hook was not overridden, instead of silently producing no audio / no UI at
        runtime. Intermediate base classes set ``is_abstract = True`` to opt out.
        """
        super().__init_subclass__(**kwargs)
        if cls.__dict__.get("is_abstract", False):
            return

        violations = []
        config = getattr(cls, "config", None)
        if not isinstance(config, NodeConfig):
            violations.append("config must be a NodeConfig instance")
        else:
            if config.processing is NodeProcessingMode.BAKED and cls.bake is BaseNode.bake:
                violations.append("config.processing=BAKED requires overriding bake()")
            elif config.processing is NodeProcessingMode.STREAMING and cls.build_stream_renderer is BaseNode.build_stream_renderer:
                violations.append("config.processing=STREAMING requires overriding build_stream_renderer()")
            if config.ui is NodeUIMode.INSPECTOR_PANEL and cls._draw_panel is BaseNode._draw_panel:
                violations.append("config.ui=INSPECTOR_PANEL requires overriding _draw_panel()")
            if config.realtime and cls.on_gui_frame is BaseNode.on_gui_frame:
                violations.append("config.realtime=True requires overriding on_gui_frame()")
        # Every concrete node must publish its outputs so the pipeline can wire downstream.
        if cls.publish_outputs is BaseNode.publish_outputs:
            violations.append("every node must override publish_outputs()")
        if violations:
            raise TypeError(
                f"{cls.__name__} violates the BaseNode contract: " + "; ".join(violations))

    # ===== Theme management =====

    @classmethod
    def build_global_themes(cls):
        for btn_theme in cls.global_button_theme_templates:
            cls.global_button_themes[btn_theme.theme_name] = init_btn_theme(btn_theme)
        for node_theme in cls.global_node_theme_templates:
            cls.global_node_themes[node_theme.theme_name] = init_node_theme(node_theme)
    
    @classmethod
    def build_class_themes(cls):
        for btn_theme in cls.class_button_theme_templates:
            cls.class_button_themes[f"{cls.__name__}_{btn_theme.theme_name}"] = init_btn_theme(btn_theme)
        for node_theme in cls.class_node_theme_templates:
            cls.class_node_themes[f"{cls.__name__}_{node_theme.theme_name}"] = init_node_theme(node_theme)
            
    def get_class_button_theme(self, theme_name: str):
        return self.class_button_themes.get(f"{self.__class__.__name__}_{theme_name}")

    def get_class_node_theme(self, theme_name: str):
        return self.class_node_themes.get(f"{self.__class__.__name__}_{theme_name}")

    def get_global_button_theme(self, theme_name: str):
        return self.global_button_themes.get(theme_name)

    def get_global_node_theme(self, theme_name: str):
        return self.global_node_themes.get(theme_name)

    # ===== Construction & pin registration =====

    def __init__(self, node_tag: str):
        self.node_tag = node_tag
        self.custom_name = ""
        self.input_spec = {}
        self.output_spec = {}
        self.input_values = {}
        self.output_values = {}

        self.is_baked = False
        self.cached_outputs = {}
        self.baked_signature = None  # fingerprint captured at the last successful bake
        self.progress_bar_tag = f"progress_bar_{node_tag}"

        self.panel_window_tag = f"panel_{node_tag}"
        self._ui_resource_tags = set()
        self._param_specs = {}
        self._param_controls = {}
        self._param_control_names = []
        self._param_widget_tags = {}
        self._param_projection_tags = {}
        self._bind_param_specs()
        self._parameter_ui_adapter = None
        self._node_panel_controller = None

    @property
    def _parameter_ui(self):
        if self._parameter_ui_adapter is None:
            from ui.node_controls import ParameterUIAdapter
            self._parameter_ui_adapter = ParameterUIAdapter(self)
        return self._parameter_ui_adapter

    @property
    def _panel_controller(self):
        if self._node_panel_controller is None:
            from ui.node_panel import NodePanelController
            self._node_panel_controller = NodePanelController(self)
        return self._node_panel_controller

    @property
    def title(self) -> str:
        """User-facing instance title; runtime identity remains ``node_tag``."""
        return self.custom_name or self.display_name

    def set_custom_name(self, value, *, sync_ui=True) -> bool:
        if not isinstance(value, str):
            value = ""
        normalized = " ".join(value.splitlines()).strip()[:self.max_custom_name_length].strip()
        if normalized == self.custom_name:
            return False
        self.custom_name = normalized
        if sync_ui:
            self.sync_custom_name_ui()
        return True

    def _on_custom_name(self, sender, value, user_data=None):
        from ui.node_controls import request_node_rename
        request_node_rename(self, value)

    def _on_custom_name_deactivated(self, sender=None, app_data=None, user_data=None):
        input_tag = f"custom_name_input::{self.node_tag}"
        if dpg.does_item_exist(input_tag):
            from ui.node_controls import request_node_rename
            request_node_rename(self, dpg.get_value(input_tag))

    def _draw_custom_name_control(self):
        from ui import i18n
        input_tag = f"custom_name_input::{self.node_tag}"
        handler_tag = f"custom_name_commit_handler::{self.node_tag}"
        dpg.add_input_text(
            label=i18n.tr("node_custom_name_label"),
            tag=input_tag,
            default_value=self.custom_name,
            hint=self.display_name,
            width=360,
            on_enter=True,
            callback=self._on_custom_name,
        )
        with dpg.item_handler_registry(tag=self._track_ui_resource(handler_tag)):
            dpg.add_item_deactivated_after_edit_handler(
                callback=self._on_custom_name_deactivated)
        dpg.bind_item_handler_registry(input_tag, handler_tag)

    def _register_input(self, pin_id: str, data_type: DataType) -> str:
        full_tag = f"{self.node_tag}_{pin_id}::{data_type.name}"
        self.input_spec[full_tag] = data_type
        return full_tag

    def _register_output(self, pin_id: str, data_type: DataType) -> str:
        full_tag = f"{self.node_tag}_{pin_id}::{data_type.name}"
        self.output_spec[full_tag] = data_type
        return full_tag
    
    def _report_progress(self, progress):
        # May be called from a bake/compile worker thread: defer the DPG write to the GUI thread.
        def _do():
            if dpg.does_item_exist(self.progress_bar_tag):
                dpg.set_value(self.progress_bar_tag, progress)
        run_on_gui(_do)

    def _request_single_bake(self, *args):
        # Per-node Bake button. Routed through the engine so the bake runs on a worker
        # thread and, on success, a light pass propagates the fresh output downstream.
        # Lazy import avoids a core <-> pipeline circular import at module load.
        from ui.node_controls import request_node_compile
        request_node_compile(CompileKind.BAKE_NODE, self.node_tag)

    # ===== Preset (de)serialization =====

    def _bind_param_specs(self):
        declarations = []
        controls = []
        for spec in self.parameter_specs:
            if not isinstance(spec, ParameterSpec):
                raise TypeError(
                    f"{type(self).__name__}.parameter_specs must contain ParameterSpec values")
            declarations.append(spec)
        for control_spec in self.parameter_control_specs:
            if not isinstance(control_spec, ParameterControlSpec):
                raise TypeError(
                    f"{type(self).__name__}.parameter_control_specs must contain "
                    "ParameterControlSpec values")
            controls.append(control_spec)

        for spec in declarations:
            if spec.name in self._param_specs:
                raise ValueError(f"Duplicate parameter spec '{spec.name}'")
            self._param_specs[spec.name] = spec
            value = spec.normalize(spec.default)
            setattr(self, spec.name, value)
            setattr(self, f"{spec.name}_default", value)
            body_tag = f"{spec.name}_control_{self.node_tag}"
            secondary_tag = f"{spec.name}_secondary_control_{self.node_tag}"
            self._param_widget_tags[spec.name] = (body_tag, secondary_tag)
            self._param_projection_tags[spec.name] = {body_tag, secondary_tag}
            # Compatibility aliases for existing node helpers and tests during incremental migration.
            setattr(self, f"{spec.name}_slider_tag", body_tag)
            setattr(self, f"{spec.name}_panel_slider_tag", secondary_tag)
        for control_spec in controls:
            name = control_spec.parameter
            if name not in self._param_specs:
                raise ValueError(f"Control references unknown parameter '{name}'")
            if name in self._param_controls:
                raise ValueError(f"Duplicate control spec for parameter '{name}'")
            self._param_controls[name] = control_spec.control
            self._param_control_names.append(name)

    def _set_declared_param(self, name: str, value, *, notify=True):
        value = self._set_declared_param_state(name, value)
        if notify:
            self._apply_declared_param_state_effects(name)
        self._project_declared_param(name, value, notify=notify)
        return value

    def _set_declared_param_state(self, name: str, value):
        spec = self._param_specs[name]
        value = spec.normalize(value)
        setattr(self, name, value)
        return value

    def _apply_declared_param_state_effects(self, name: str) -> None:
        spec = self._param_specs[name]
        if spec.stale is StalePolicy.NONE:
            return
        from core.pipeline import mark_downstream_heavy_stale, mark_structural_heavy_stale
        mark_stale = (mark_structural_heavy_stale
                      if spec.stale is StalePolicy.STRUCTURAL
                      else mark_downstream_heavy_stale)
        mark_stale(self.node_tag)

    def _project_declared_param(self, name: str, value, *, notify=True) -> bool:
        if getattr(self, name) != value:
            return False
        for tag in self._param_projection_tags[name]:
            if dpg.does_item_exist(tag):
                dpg.set_value(tag, value)
        if not notify:
            return True
        spec = self._param_specs[name]
        if spec.on_change is not None:
            getattr(self, spec.on_change)()
        return True

    def _declared_param_signature(self) -> str:
        parts = []
        for spec in self._param_specs.values():
            if not spec.signature:
                continue
            value = getattr(self, spec.name)
            if spec.signature_precision is not None and isinstance(value, float):
                rendered = f"{value:.{spec.signature_precision}f}"
            else:
                rendered = str(value)
            parts.append(f"{spec.name}={rendered}")
        return ",".join(parts)

    def serialize_params(self) -> dict:
        """Capture declared, serializable node parameters for a preset."""
        return {
            spec.name: getattr(self, spec.name)
            for spec in self._param_specs.values()
            if spec.serialize
        }

    def serialize_template_extra(self) -> dict:
        """Return non-tunable identity metadata retained by lean template presets."""
        return {}

    def apply_params(self, params: dict):
        """Restore declared parameters captured by :meth:`serialize_params`."""
        for name, value in params.items():
            if name in self._param_specs:
                self._set_declared_param(name, value, notify=False)

    def apply_params_state(self, params: dict):
        """Restore declared parameters without projecting controls."""
        for name, value in params.items():
            if name in self._param_specs:
                self._set_declared_param_state(name, value)

    def serialize_extra(self) -> dict:
        """Extra per-node state to persist beyond the ``X`` / ``X_default`` slider convention.

        Default: nothing. Nodes with non-slider state that must survive undo/redo and (optionally)
        preset save -- e.g. an Audio Input's chosen file path -- override this and its inverse
        :meth:`apply_extra`. Kept separate from :meth:`serialize_params` so the preset "save
        parameters" toggle can gate both together while undo/redo always captures the full state.
        """
        return {}

    def apply_extra(self, data: dict):
        """Restore state captured by :meth:`serialize_extra`. Default: no-op.

        Called AFTER ``draw_node`` (unlike ``apply_params``), so overrides may update node-body
        widgets that only exist once the node is drawn.
        """
        pass

    def apply_extra_state(self, data: dict):
        """Restore non-parameter domain state without projecting widgets."""
        pass

    def mutate_extra_state(self, field, value, *, index=None, action=None):
        """Apply one owner-thread mutation to custom node state."""
        return None

    def project_extra_state(self, field, value, *, index=None):
        """Project one custom-state event onto existing GUI controls."""
        pass

    # ===== Node & panel drawing =====

    def _draw_declared_param(
            self, name: str, *, secondary=False, width=None, scope=None):
        return self._parameter_ui.draw_declared(
            name, secondary=secondary, width=width, scope=scope)

    def _draw_pin(self, pin_label: str, is_output: bool, pin_theme=None, label_width=60, label_height=22):
        # Colour the pin by its DataType so the type is readable at a glance (input vs output is
        # already clear from position). The enclosing dpg.node_attribute was created by the node
        # with a tag ending in "::TYPE" and recorded in input_spec/output_spec; we resolve the
        # type from that and pick the "pin_<TYPE>" colour. The legacy `pin_theme` arg (still
        # passed by existing nodes) is only a fallback if the type can't be resolved.
        with dpg.group(horizontal=True):
            if is_output:
                dpg.add_spacer(width=self.top_bar_spacer_width - label_width)
            label_btn = dpg.add_button(label=pin_label, width=label_width, height=label_height)
            theme = self._resolve_pin_theme(label_btn, is_output) or pin_theme
            if theme is not None:
                dpg.bind_item_theme(label_btn, theme)

    def _resolve_pin_theme(self, label_btn, is_output: bool):
        """Find the DataType-coloured theme for the pin whose button is ``label_btn``.

        Walks up from the button to its enclosing node_attribute, whose tag is one of this node's
        registered pins (``..._pin::TYPE``); maps that to a ``pin_<TYPE>`` global theme. Returns
        None if the enclosing attribute / type can't be determined (caller falls back).
        """
        try:
            # button -> group (_draw_pin's own) -> node_attribute
            parent = dpg.get_item_info(label_btn)["parent"]
            attr = dpg.get_item_info(parent)["parent"]
            attr_tag = dpg.get_item_alias(attr)
        except Exception:
            return None
        spec = self.output_spec if is_output else self.input_spec
        dtype = spec.get(attr_tag)
        if dtype is None:
            return None
        return self.get_global_button_theme(f"pin_{dtype.name}")

    def _draw_node_base(self):
        b_theme = self.get_global_button_theme("bake_btn")
        p_theme = self.get_global_button_theme("panel_btn")

        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
            with dpg.group(horizontal=True):

                # bake_btn = dpg.add_button(label="Bake", callback=self._request_single_bake)
                # dpg.bind_item_theme(bake_btn, b_theme)

                dpg.add_progress_bar(
                    tag=self.progress_bar_tag,
                    width=100, height=25,
                    default_value=0.0, overlay="",
                    show=self.config.processing is NodeProcessingMode.BAKED)

                from ui import i18n
                panel_btn = dpg.add_button(
                    label=i18n.tr("node_controls_btn"),
                    callback=self._open_inspector,
                    show=True)
                dpg.bind_item_theme(panel_btn, p_theme)

        with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
            dpg.add_spacer(width=self.top_bar_spacer_width)
            dpg.add_spacer(height=self.top_bar_spacer_height)

    def _sync_control_widgets(self):
        """Re-push every parameter's current value into its node-body and secondary controls.

        A panel window is deleted on close and rebuilt on reopen, so its widgets must be
        realigned to the current parameter attributes (the source of truth). Calling this
        after a panel is (re)opened avoids stale/default widget state across close/reopen
        cycles. Uses the same ``X`` / ``X_default`` / ``*_slider_tag`` convention as
        :meth:`serialize_params` / :meth:`apply_params`.
        """
        self._parameter_ui.sync()

    def _sync_panel_widgets(self):
        """Compatibility hook for independent panels with custom synchronization."""
        self._sync_control_widgets()

    def _draw_panel_base(self):
        # Node-body "Panel" button: open (or focus) the panel at DPG's default position.
        self.open_panel()

    def _open_panel_from_ui(self, *args):
        """DearPyGui callback adapter; discard sender/app_data instead of treating them as geometry."""
        self.open_panel()

    def _open_inspector(self, *args):
        # Lazy UI import keeps BaseNode usable in headless/core-only contexts.
        from ui.pedal_workspace import show_pedal_workspace
        show_pedal_workspace(self.node_tag)

    def _track_ui_resource(self, tag):
        """Own a non-child DPG resource (for example a theme) for teardown with this node."""
        self._ui_resource_tags.add(tag)
        return tag

    def _clear_ui_resources(self):
        for tag in list(self._ui_resource_tags):
            if dpg.does_item_exist(tag):
                dpg.delete_item(tag)
        self._ui_resource_tags.clear()

    def open_panel(self, pos=None, size=None):
        """Open (or focus) this node's panel window.

        The Panel button calls this with no arguments (DPG picks the position). Preset loading
        passes ``pos``/``size`` to restore the saved layout — see ``core.graph_presets``. A live
        panel is a top-level ``dpg.window`` created lazily and deleted on close, so its existence
        is the "is it open?" signal that :func:`serialize_graph` records.
        """
        return self._panel_controller.open(pos=pos, size=size)

    def serialize_panel(self):
        """Snapshot this panel's open-state + geometry for a preset, or None if it isn't open."""
        return self._panel_controller.serialize(
            enabled=self.config.ui is NodeUIMode.INSPECTOR_PANEL)

    def _draw_highlighted_node(self):
        # Called from both the GUI thread and compile workers: marshal the theme bind onto the GUI thread.
        def _do():
            if dpg.does_item_exist(self.node_tag):
                dpg.bind_item_theme(self.node_tag, self.get_global_node_theme("node_error"))
        run_on_gui(_do)

    def _draw_warning_node(self):
        def _do():
            if dpg.does_item_exist(self.node_tag):
                dpg.bind_item_theme(self.node_tag, self.get_global_node_theme("node_warning"))
        run_on_gui(_do)

    def _draw_default_node(self):
        def _do():
            if dpg.does_item_exist(self.node_tag):
                theme = self.get_class_node_theme("node_default") or self.get_global_node_theme("node_default")
                dpg.bind_item_theme(self.node_tag, theme)
        run_on_gui(_do)

    def draw_node(self, *, parent=None):
        node_options = {"label": self.title, "tag": self.node_tag}
        if parent is not None:
            node_options["parent"] = parent
        with dpg.node(**node_options):
            self._draw_node_base()
            self._draw_node_internal()
        self._draw_node_additional()
        self._draw_default_node()

    # ===== Fingerprinting =====

    def get_param_signature(self) -> str:
        """Override: return a string of node parameters that affect the output.

        Only include lightweight, value-based parameters (gains, model names,
        toggles...). Do NOT hash heavy data here.
        """
        return ""

    def compute_fingerprint(self, input_fingerprints: list[str]) -> str:
        """Derive a fingerprint from upstream fingerprints + this node's params.

        Cheap by design: depends only on source metadata + parameters, so it can
        propagate downstream even when the heavy data has not been recomputed.
        """
        sig = "|".join((
            self.__class__.__name__,
            "+".join(sorted(input_fingerprints)),
            self.get_param_signature(),
        ))
        return hashlib.md5(sig.encode("utf-8")).hexdigest()

    # ===== Subclass hooks (override as needed) =====

    def _draw_node_internal(self): pass    # node body UI (pins, widgets)
    def _draw_node_additional(self): pass  # extra UI outside the node body
    def _draw_inspector(self):
        """Draw the standard inspector; specialized nodes may prepend/replace this view."""
        dpg.add_text(self.display_name, color=[150, 180, 210])
        if self.config.ui is NodeUIMode.INSPECTOR_PANEL:
            from ui import i18n
            dpg.add_button(
                label=i18n.tr("node_open_panel_btn"),
                tag=f"inspector_open_panel::{self.node_tag}",
                callback=self._open_panel_from_ui,
                width=180,
            )
        if self._param_control_names:
            for name in self._param_control_names:
                self._draw_declared_param(name, secondary=True, width=360)
        elif self.config.processing is NodeProcessingMode.BAKED:
            dpg.add_text("No adjustable parameters.", color=[150, 150, 150])
        if self.config.processing is NodeProcessingMode.BAKED:
            status = "Analyzed" if self.is_baked else "Analysis required"
            dpg.add_text(status, tag=f"inspector_status_{self.node_tag}")
    def _draw_panel(self): pass            # independent window; required for config.ui=INSPECTOR_PANEL

    def publish_outputs(self) -> bool: pass        # run node logic; return True on success
    def bake(self) -> bool: pass           # heavy precompute; required for config.processing=BAKED
    def on_gui_frame(self): pass           # per-frame tick; called when config.realtime is true

    # Not a no-op hook: build_stream_renderer() (in the "Streaming" section below) is the
    # live per-block entry point. It ships a working default (slice the baked leaf array),
    # so non-streamable nodes need NOT override it; streamable nodes override to transform.

    # ===== Streaming (per-block render; producer-thread only) =====

    def build_baked_renderer(self, out_pin: str):
        """Return (render_fn, length) for a non-streaming leaf output.

        Binds this output's frozen array ONCE and slices it per block. Reading
        cached_outputs/current_audio a single time keeps the bound array and the reported
        length consistent (no TOCTOU between "which array" and "how long").
        """
        arr = (self.cached_outputs.get(out_pin)
               if self.config.processing is NodeProcessingMode.BAKED
               else getattr(self, "current_audio", None))
        length = arr.shape[-1] if arr is not None else 0
        return (lambda start, frames, a=arr: slice_stereo_block(a, start, frames)), length

    def build_stream_renderer(self, out_pin: str, input_renderers: dict):
        """Return a frozen callable(start, frames) -> (n, 2) float32 | None for `out_pin`.

        `input_renderers` maps each connected input pin to an upstream renderer callable,
        captured at compile time so the wiring is frozen. Default (a baked / non-streamable
        leaf): ignore inputs and slice this output's baked array. Streamable nodes override
        to pull their inputs and apply a per-block transform using their live parameters.

        Contract: the returned callable runs on the engine producer thread. It may read only
        immutable snapshots + atomic scalar params -- no DPG, no shared-dict mutation, no
        cross-thread handoff.
        """
        fn, _ = self.build_baked_renderer(out_pin)
        return fn

    @staticmethod
    def _wrap_single_input(input_renderers: dict, process):
        """Helper for single-input streamable effects: pull the one upstream block and run
        `process(block) -> block`. Returns None when unconnected or past the end."""
        src = next(iter(input_renderers.values()), None)

        def render(start, frames):
            if src is None:
                return None
            block = src(start, frames)
            if block is None or len(block) == 0:
                return None
            return process(block)

        return render

    @staticmethod
    def _wrap_single_input_tap(input_renderers: dict, on_block):
        """Like :meth:`_wrap_single_input` but for a transparent TAP: run ``on_block(block)`` for
        its side effect (e.g. feed an analyzer's rolling buffer) and pass the block through
        UNCHANGED. Used by the Oscilloscope / Spectrum monitors that sit inline without altering
        the signal. Returns the upstream block (or None) verbatim."""
        src = next(iter(input_renderers.values()), None)

        def render(start, frames):
            if src is None:
                return None
            block = src(start, frames)
            on_block(block)   # side-effect only; on_block must tolerate None/empty
            return block

        return render

    # ---- publish_outputs shorthands -------------------------------------------------
    # Two publish shapes recur across nodes. These fold them to a one-liner while keeping the
    # explicit override (the pipeline calls publish_outputs) -- a node just delegates to one.

    def _publish_stream_passthrough(self, *out_pins) -> bool:
        """For streamable effects/taps: audio is produced live per block by build_stream_renderer,
        so each output just carries a fingerprint-only packet (data=None) to propagate staleness."""
        for pin in out_pins:
            self.output_values[pin] = DataPacket(None, DataType.AUDIO)
        return True

    def _publish_cached(self, *out_pins, data_type: "DataType" = None) -> bool:
        """For BAKED nodes: publish each output's cached array, or fail with a log if the
        node hasn't been baked yet. ``data_type`` defaults to each pin's registered output type."""
        missing = [p for p in out_pins if p not in self.cached_outputs]
        if not self.is_baked or missing:
            append_log(f"[{self.display_name}] Not baked. Bake this node (heavy compile) before running.", level="ERROR")
            return False
        for pin in out_pins:
            dt = data_type if data_type is not None else self.output_spec.get(pin, DataType.AUDIO)
            self.output_values[pin] = DataPacket(self.cached_outputs[pin], dt)
        return True

    def sync_custom_name_ui(self) -> None:
        input_tag = f"custom_name_input::{self.node_tag}"
        if dpg.does_item_exist(input_tag):
            dpg.set_value(input_tag, self.custom_name)
        if dpg.does_item_exist(self.node_tag):
            dpg.configure_item(self.node_tag, label=self.title)
        if dpg.does_item_exist(self.panel_window_tag):
            dpg.configure_item(self.panel_window_tag, label=self.title)
        from ui.pedal_workspace import update_pedal_workspace
        update_pedal_workspace(force=True)