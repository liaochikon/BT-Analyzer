import logging
import dearpygui.dearpygui as dpg
from application.compile_events import (
    CompileCompletionPublisher,
    CompileEndpoint,
    CompileEventPublisher,
    CompileLifecyclePublisher,
)
from application.context import ApplicationContext
from application.graph_application import GraphApplication, GraphCommandPublisher
from application.graph_owner import GraphOwner
from application.graph_projection_barrier import GraphProjectionBarrier
from application.persistence_worker import PersistenceWorker
from ui import *
from ui import i18n
import core
from core import settings
from core import constants
from core.playback_engine import ENGINE
from core.pipeline import (
    COMPILE_COORDINATOR,
    capture_history_workspace,
    capture_history_artifacts,
    capture_graph_render_state,
    connect_pins_state,
    commit_node_deleted,
    commit_graph_document,
    commit_compile_metadata,
    commit_node_renamed,
    commit_node_parameter_changed,
    commit_node_state_changed,
    commit_node_spawned,
    commit_topology_changed,
    disconnect_connection_state,
    delete_node_state,
    graph_snapshot,
    export_current_graph_document,
    finalize_graph_replacement,
    request_compile,
    runtime_node_view,
    runtime_node_views,
    publish_graph_snapshot,
    replace_graph_candidate,
    restore_history_artifacts,
    known_node_types,
    set_node_parameter_state,
    mutate_node_state,
    spawn_node_state,
)
from ui.graph_projection import (
    bind_graph_node_projection,
    project_connection_added,
    project_connection_removed,
    project_compile_metadata,
    project_graph_replaced,
    project_practice_settings,
    project_node_deleted,
    project_node_parameter,
    project_node_state,
    project_node_rename,
    project_node_spawned,
    sync_live_graph_workspace,
)
from core.graph_contracts import GraphEndpoint
from core.graph_documents import materialize_graph_document
from core.graph_history import (
    clear_document_history,
    configure_document_history,
    redo,
    take_restore_event,
    undo,
    reset_history,
)
from core.graph_presets import (
    apply_preset_candidate,
    build_preset_project_document,
    create_preset_store,
    reject_preset_load,
    startup_preset_name_if_available,
)
from ui.preset_menu import handle_preset_save_completed, handle_preset_save_failed
from core.transport_contracts import TransportEndpoint
from core.transport import TRANSPORT
from core import disk_cache
from core import mem_cache
from core import runtime_artifacts
from app_logging import (
    bind_gui_thread,
    drain_ui,
    setup_logging,
    set_log_level,
    shutdown_logging,
)
from app_logging.logging import append_log


logger = logging.getLogger(__name__)


class _ApplicationRuntime:
    def __init__(self) -> None:
        self.application = None
        self.persistence_worker = None
        self.graph_owner = None
        self.graph_projection_barrier = None
        self.dpg_context_created = False
        self.logging_configured = False
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        def cleanup(name, operation):
            try:
                operation()
            except Exception:
                logger.exception("Shutdown step %s failed", name)

        cleanup("audio engine", ENGINE.stop)
        cleanup("compile cancellation", COMPILE_COORDINATOR.request_cancel)
        cleanup("compile worker", COMPILE_COORDINATOR.wait_for_worker)
        cleanup("graph commands", lambda: bind_graph_commands(None))
        cleanup("preset commands", lambda: bind_preset_graph_commands(None))
        cleanup("transport bus", lambda: TRANSPORT.bind_control_bus(None))
        cleanup("compile lifecycle", lambda: COMPILE_COORDINATOR.configure_lifecycle(None))
        cleanup("graph history", clear_document_history)
        cleanup("runtime artifacts", runtime_artifacts.clear)
        if self.persistence_worker is not None:
            cleanup("persistence worker", self.persistence_worker.close)
        if self.graph_projection_barrier is not None:
            cleanup("graph projection barrier", self.graph_projection_barrier.release)
        if self.graph_owner is not None:
            cleanup("graph owner", self.graph_owner.close)
        if self.dpg_context_created and self.application is not None:
            cleanup("final GUI drain", self.application.drain_gui_all)
        if self.dpg_context_created:
            cleanup("graph projection", lambda: bind_graph_projection(None, None))
        if self.application is not None:
            cleanup("application context", self.application.close)
        cleanup("disk cache", disk_cache.clear)
        if self.dpg_context_created:
            cleanup("DearPyGui context", dpg.destroy_context)
        if self.logging_configured:
            cleanup("logging", shutdown_logging)


def _refresh_graph_views() -> None:
    for name, refresh in (
        ("Analyzer Status", update_analyzer_status),
        ("Pedal Workspace", update_pedal_workspace),
    ):
        try:
            refresh()
        except Exception:
            logger.exception("%s graph projection refresh crashed", name)


def _run_application(runtime: _ApplicationRuntime):
    # The terminal is the full debug log (file-tagged, unified format); the on-screen panel is
    # the user prompt. See app_logging.logging for the split. Done here (not at import time) so
    # importing this module has no global side effects.
    setup_logging(logging.INFO)
    runtime.logging_configured = True

    # Record this (the main loop) thread as the GUI thread so the UI dispatch queue knows
    # which calls are already safe and which must be deferred from worker threads.
    bind_gui_thread()

    application = ApplicationContext()
    runtime.application = application
    application.register_gui_endpoint(
        CompileEndpoint.NODE_EDITOR,
        handle_node_editor_message,
    )
    application.register_gui_endpoint(
        CompileEndpoint.ANALYZER_STATUS,
        handle_analyzer_status_message,
    )
    persistence_worker = PersistenceWorker(application.bus, create_preset_store())
    runtime.persistence_worker = persistence_worker
    graph_commands = GraphCommandPublisher(application.bus)
    graph_projection_barrier = GraphProjectionBarrier()
    runtime.graph_projection_barrier = graph_projection_barrier
    graph_application = GraphApplication(
        apply_preset_candidate=apply_preset_candidate,
        capture_preset_document=build_preset_project_document,
        preset_save_completed=handle_preset_save_completed,
        preset_save_failed=handle_preset_save_failed,
        project_node_rename=project_node_rename,
        project_node_parameter=project_node_parameter,
        project_node_state=project_node_state,
        project_connection_added=project_connection_added,
        project_connection_removed=project_connection_removed,
        project_node_spawned=project_node_spawned,
        project_node_deleted=project_node_deleted,
        project_compile_metadata=project_compile_metadata,
        project_graph_replaced=project_graph_replaced,
        project_practice_settings=project_practice_settings,
        project_saved=lambda event: handle_preset_save_completed(
            event.name, event.as_default, event.node_count),
        project_save_failed=lambda event: handle_preset_save_failed(
            event.name, event.message),
        complete_preset_apply=graph_commands.complete_preset_apply,
        submit_preset_document=graph_commands.submit_preset_document,
        projection_changed=_refresh_graph_views,
        projection_completed=graph_projection_barrier.release,
    )
    application.register_gui_endpoint(
        GraphEndpoint.GUI,
        graph_application.handle_message,
        maxsize=256,
    )
    graph_mailbox = application.register_endpoint(GraphEndpoint.SERVICE, maxsize=256)
    graph_owner = GraphOwner(
        application.bus,
        graph_mailbox,
        rename_node_state,
        publish_graph_snapshot,
        commit_graph_document,
        lambda: graph_snapshot().document_revision,
        commit_compile_metadata,
        replace_graph_candidate,
        undo,
        redo,
        take_restore_event,
        reset_history,
        export_current_graph_document,
        capture_graph_render_state,
        finalize_graph_replacement,
        set_parameter=set_node_parameter_state,
        mutate_node_state=mutate_node_state,
        connect_pins=connect_pins_state,
        disconnect_connection=disconnect_connection_state,
        spawn_node=spawn_node_state,
        delete_node=delete_node_state,
        commit_topology_changed=commit_topology_changed,
        commit_node_spawned=commit_node_spawned,
        commit_node_deleted=commit_node_deleted,
        commit_node_renamed=commit_node_renamed,
        commit_node_parameter_changed=commit_node_parameter_changed,
        commit_node_state_changed=commit_node_state_changed,
        commit_workspace_gesture=lambda workspace: record_history(workspace),
        request_compile=request_compile,
        request_preset_load=lambda name, project_id, revision, request_id:
            persistence_worker.request_load_project(
                name,
                known_node_types(),
                project_id=project_id,
                expected_revision=revision,
                request_id=request_id,
            ),
        reject_preset_load=reject_preset_load,
        request_preset_save=lambda name, document, revision, request_id, as_default:
            persistence_worker.request_save_project(
                name,
                document,
                revision=revision,
                request_id=request_id,
                as_default=as_default,
            ),
            projection_barrier=graph_projection_barrier,
    )
    runtime.graph_owner = graph_owner
    graph_owner.start()
    transport_mailbox = application.register_endpoint(
        TransportEndpoint.SERVICE,
        maxsize=512,
    )
    ENGINE.bind_control_mailbox(transport_mailbox)
    TRANSPORT.bind_control_bus(application.bus)
    COMPILE_COORDINATOR.configure_lifecycle(
        CompileLifecyclePublisher(
            application.bus,
            graph_owner.compile_completion,
            COMPILE_COORDINATOR.take_result,
        ))

    # Load persisted user settings, then apply the saved log verbosity before much is logged.
    settings.load()
    set_log_level(getattr(logging, settings.get("log_level", "INFO"), logging.INFO))

    # Apply the saved internal sample rate BEFORE anything reads it. Node modules copy
    # GLOBAL_SAMPLE_RATE by value at import time (scan happens in ui_init below) and the
    # engine/transport singletons are built from it, so this must run before both. Changing
    # the rate therefore only takes effect from launch (surfaced as "restart to apply" in UI).
    saved_sr = int(settings.get("sample_rate", constants.GLOBAL_SAMPLE_RATE))
    if saved_sr in constants.SUPPORTED_SAMPLE_RATES and saved_sr != constants.GLOBAL_SAMPLE_RATE:
        constants.GLOBAL_SAMPLE_RATE = saved_sr
        # Nodes read GLOBAL_SAMPLE_RATE via `from core import *`, which binds against the `core`
        # package namespace (populated when core/__init__.py ran `from core.constants import *`).
        # Rebind it there too, before the node scan in ui_init, or nodes would still see 44100.
        core.GLOBAL_SAMPLE_RATE = saved_sr
        ENGINE.sample_rate = saved_sr
        TRANSPORT.sample_rate = saved_sr
        logger.info("Applied saved sample rate: %d Hz", saved_sr)

    # Apply the saved in-memory heavy-output cache budget before any node bakes.
    mem_cache.MAX_BYTES = max(1, int(settings.get("mem_cache_mb", 512))) * 1024 * 1024

    # The disk cache is scratch for this run only: wipe any leftovers from a previous run
    # before we start so stale entries never accumulate across runs.
    disk_cache.clear()

    # Initialize UI configuration and load locale. A remembered language choice (from a
    # previous run) wins over the shipped default; i18n then drives every translated label.
    ui_config = UIConfig()
    i18n.init_locale(ui_config.locale)

    # Initialize Dear PyGui and create the main window
    dpg.create_context()
    runtime.dpg_context_created = True
    ui_init(ui_config, graph_commands)
    configure_document_history(
        export_current_graph_document,
        lambda document: materialize_graph_document(document, known_node_types()),
        replace_graph_candidate,
        capture_history_workspace,
        capture_history_artifacts,
        restore_history_artifacts,
        runtime_artifacts.retain,
    )
    ui_create_main_window(ui_config, graph_commands)
    ui_create_node_editor(graph_commands)
    ui_create_log_window()
    ui_create_transport_bar()
    ui_create_pedal_workspace()
    bind_graph_node_projection(runtime_node_view)
    bind_graph_projection(graph_snapshot, runtime_node_view)
    ui_create_settings_window()

    dpg.setup_dearpygui()
    dpg.show_viewport()
    # Optionally open maximised (must be after show_viewport). The width/height still seed the
    # pre-maximise size, so unchecking the setting restores that size on the next launch.
    if settings.get("start_maximized", False):
        dpg.maximize_viewport()
    dpg.set_primary_window("win_main", True)
    dpg.show_item("win_node_editor")

    # Apply the remembered font scale once the viewport exists.
    dpg.set_global_font_scale(float(settings.get("font_scale", 1.0)))

    # Open the audio stream once and keep it open (Method A). It outputs silence
    # until the playback sink (Audio Output) registers a source and the transport
    # is set to PLAYING. A machine with no output device (headless / some VMs /
    # RDP) must not take the whole app down: analysis and visualization still work
    # without audio, so log it and carry on.
    # Apply the remembered output device. It's persisted as a device LABEL (stable across
    # unplug/replug/reboot, unlike the index), resolved to a current index here; an absent label
    # or a now-missing device falls back to the system default. Set before start().
    saved_label = settings.get("output_device", None)
    resolved = resolve_output_device(saved_label)
    if resolved is not None:
        ENGINE.preset_device(resolved)
    try:
        ENGINE.start()
    except Exception:
        logger.exception("Audio engine failed to start; continuing without audio output")
        append_log("No audio output device available; playback is disabled.", level="WARNING")

    # Populate the empty editor with the bundled default node preset (if one ships), then seed
    # the undo/redo history with that starting graph as the baseline.
    startup_preset = startup_preset_name_if_available()
    if startup_preset is None:
        reset_history()
    else:
        graph_commands.load_preset(startup_preset)

    # From here the audio stream and worker threads are live, so teardown MUST run even if the
    # render loop raises. The finally block is the single place the engine is stopped, the
    # scratch cache is wiped, and the DPG context is destroyed.
    try:
        while dpg.is_dearpygui_running():
            application.drain_gui(limit_per_endpoint=32)
            # Run any UI updates that worker threads (bake/compile/export) queued, so every DPG
            # call happens on this thread just before the frame is drawn.
            drain_ui()
            # Tick realtime nodes (e.g. the playback sink) once per frame. Snapshot the
            # registry so nodes added/removed mid-iteration never disturb the loop. Isolate
            # each node: a single misbehaving on_gui_frame() must not kill the render loop.
            for node in runtime_node_views():
                if node.config.realtime:
                    try:
                        node.on_gui_frame()
                    except Exception:
                        logger.exception("Realtime node [%s] on_gui_frame() crashed", node.node_tag)
            # Isolate the transport-bar tick like the node loop: one bad frame (e.g. a draw call
            # racing a resize) must not kill the render loop or starve the per-frame engine syncs
            # (metronome push, playhead) that live inside it.
            try:
                update_transport_bar()
            except Exception:
                logger.exception("update_transport_bar() crashed; continuing")
            try:
                update_surfaces()
            except Exception:
                logger.exception("update_surfaces() crashed; continuing")
            try:
                update_node_editor_view()
                update_pedal_workspace()
                sync_live_graph_workspace()
            except Exception:
                logger.exception("responsive workspace update crashed; continuing")
            dpg.render_dearpygui_frame()
    finally:
        runtime.close()


def main():
    runtime = _ApplicationRuntime()
    try:
        _run_application(runtime)
    finally:
        runtime.close()


if __name__ == "__main__":
    main()