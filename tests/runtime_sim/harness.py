import json
import os
import queue
import tempfile
import threading
import time
from dataclasses import dataclass

from application.context import ApplicationContext
from application.graph_application import GraphApplication, GraphCommandPublisher
from application.graph_owner import GraphOwner
from application.graph_projection_barrier import GraphProjectionBarrier
from application.persistence_worker import PersistenceWorker
from core.bus import Message
from core.graph_documents import materialize_graph_document
from core.graph_history import (
    clear_document_history,
    configure_document_history,
    record_history,
    redo,
    reset_history,
    take_restore_event,
    undo,
)
from core.pipeline import (
    GRAPH_STATE,
    NODE_REGISTRY,
    commit_compile_metadata,
    commit_graph_document,
    commit_node_deleted,
    commit_node_renamed,
    commit_node_parameter_changed,
    commit_node_state_changed,
    commit_node_spawned,
    commit_topology_changed,
    capture_history_workspace,
    capture_history_artifacts,
    capture_graph_render_state,
    connect_pins_state,
    delete_node_state,
    disconnect_connection_state,
    export_current_graph_document,
    finalize_graph_replacement,
    graph_snapshot,
    known_node_types,
    publish_graph_snapshot,
    rename_node_state,
    restore_history_artifacts,
    replace_graph_candidate,
    runtime_node_view,
    set_node_parameter_state,
    mutate_node_state,
    sync_history_workspace,
    spawn_node_state,
)
from core.graph_contracts import (
    CompileCompleted,
    CompileKind,
    CompileStatus,
    GraphEndpoint,
    GraphTopic,
    MutationRejected,
)
from core.preset_store import PresetStore
from core import graph_presets
from core import runtime_artifacts
from tests.runtime_sim.trace import RuntimeTrace
from ui.graph_projection import (
    bind_graph_node_projection,
    project_compile_metadata,
    project_connection_added,
    project_connection_removed,
    project_graph_replaced,
    project_node_deleted,
    project_node_parameter,
    project_node_state,
    project_node_rename,
    project_node_spawned,
    project_practice_settings,
    NODE_EDITOR_LINKS,
)
import dearpygui.dearpygui as dpg


@dataclass(frozen=True, slots=True)
class PendingAction:
    action: object


class RuntimeHarness:
    """Run semantic user actions through the real bus, owner, pipeline, and GUI projection."""

    def __init__(self, *, event_timeout: float = 1.0,
                 defer_persistence: bool = False) -> None:
        self.event_timeout = event_timeout
        self.gui_thread_id = threading.get_ident()
        self.trace = RuntimeTrace()
        self.aliases: dict[str, str] = {}
        self._compile_requests = queue.Queue()
        self._published_snapshots = queue.Queue()
        self._defer_persistence = defer_persistence
        self._persistence_condition = threading.Condition()
        self._deferred_persistence = []
        self.context = ApplicationContext()
        self._temporary_directory = tempfile.TemporaryDirectory(
            prefix="bt-analyzer-runtime-")
        self.store = PresetStore(
            self._temporary_directory.name,
            os_module=os,
            json_module=json,
        )
        self.persistence_worker = PersistenceWorker(self.context.bus, self.store)
        self.publisher = GraphCommandPublisher(self.context.bus)
        self.graph_projection_barrier = GraphProjectionBarrier()
        self._gui_mailbox = self.context.register_endpoint(
            GraphEndpoint.GUI, maxsize=256)
        service_mailbox = self.context.register_endpoint(
            GraphEndpoint.SERVICE, maxsize=256)
        self.application = GraphApplication(
            apply_preset_candidate=lambda *_args: None,
            capture_preset_document=graph_presets.build_preset_project_document,
            preset_save_completed=lambda *_args: None,
            preset_save_failed=lambda *_args: None,
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
            submit_preset_document=self.publisher.submit_preset_document,
            projection_completed=self.graph_projection_barrier.release,
        )
        self.owner = GraphOwner(
            bus=self.context.bus,
            mailbox=service_mailbox,
            rename_node=lambda *args: self._owner_call(
                "rename_node", rename_node_state, *args),
            publish_snapshot=self._publish_snapshot,
            commit_document=commit_graph_document,
            document_revision=lambda: graph_snapshot().document_revision,
            commit_compile_metadata=commit_compile_metadata,
            replace_graph_candidate=lambda *args: self._owner_call(
                "replace_graph_candidate", replace_graph_candidate, *args),
            undo_graph=lambda: self._owner_call("undo", undo),
            redo_graph=lambda: self._owner_call("redo", redo),
            take_history_event=take_restore_event,
            reset_history=reset_history,
            capture_graph_document=export_current_graph_document,
            capture_graph_render_state=capture_graph_render_state,
            finalize_graph_replacement=lambda: self._owner_call(
                "finalize_graph_replacement", finalize_graph_replacement),
            set_parameter=lambda *args: self._owner_call(
                "set_parameter", set_node_parameter_state, *args),
            mutate_node_state=lambda *args: self._owner_call(
                "mutate_node_state", mutate_node_state, *args),
            connect_pins=lambda *args: self._owner_call(
                "connect_pins", connect_pins_state, *args),
            disconnect_connection=lambda *args: self._owner_call(
                "disconnect_connection", disconnect_connection_state, *args),
            spawn_node=lambda *args: self._owner_call(
                "spawn_node", spawn_node_state, *args),
            delete_node=lambda *args: self._owner_call(
                "delete_node", delete_node_state, *args),
            commit_topology_changed=lambda *args: self._owner_call(
                "commit_topology_changed", commit_topology_changed, *args),
            commit_node_spawned=lambda *args: self._owner_call(
                "commit_node_spawned", commit_node_spawned, *args),
            commit_node_deleted=lambda *args: self._owner_call(
                "commit_node_deleted", commit_node_deleted, *args),
            commit_node_renamed=lambda *args: self._owner_call(
                "commit_node_renamed", commit_node_renamed, *args),
            commit_node_parameter_changed=lambda *args: self._owner_call(
                "commit_node_parameter_changed",
                commit_node_parameter_changed,
                *args,
            ),
            commit_node_state_changed=lambda *args: self._owner_call(
                "commit_node_state_changed", commit_node_state_changed, *args),
            commit_workspace_gesture=lambda workspace: self._owner_call(
                "commit_workspace_gesture", record_history, workspace),
            request_compile=self._request_compile,
            request_preset_load=self._request_preset_load,
            reject_preset_load=lambda *_args: None,
            request_preset_save=self._request_preset_save,
            projection_barrier=self.graph_projection_barrier,
        )

    @property
    def state(self):
        return GRAPH_STATE

    def start(self) -> "RuntimeHarness":
        graph_presets.clear_all_nodes()
        bind_graph_node_projection(runtime_node_view)
        configure_document_history(
            export_current_graph_document,
            lambda document: materialize_graph_document(
                document, known_node_types()),
            replace_graph_candidate,
            capture_history_workspace,
            capture_history_artifacts,
            restore_history_artifacts,
            runtime_artifacts.retain,
        )
        reset_history()
        self.owner.start()
        self.trace.append("LIFECYCLE", "started")
        return self

    def close(self) -> None:
        self.persistence_worker.close()
        self.owner.close()
        clear_document_history()
        runtime_artifacts.clear()
        graph_presets.clear_all_nodes()
        self.context.close()
        self._temporary_directory.cleanup()
        self.trace.append("LIFECYCLE", "closed")

    def __enter__(self) -> "RuntimeHarness":
        return self.start()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def perform(self, *actions) -> tuple[object, ...]:
        completed = []
        for action in actions:
            completed.append(self.complete(self.begin(action)))
        return tuple(completed)

    def begin(self, action) -> PendingAction:
        self.trace.append("ACTION", action)
        action.dispatch(self)
        return PendingAction(action)

    def complete(self, pending: PendingAction):
        action = pending.action
        event = action.await_completion(self)
        self.trace.append("RESULT", event)
        action.completed(self, event)
        if isinstance(event, action.failure_events):
            raise AssertionError(
                f"action failed: {event}\n{self.trace.format_tail()}")
        self.assert_invariants()
        return event

    def perform_rejected(self, action, *, reason: str) -> MutationRejected:
        return self.complete_rejected(self.begin(action), reason=reason)

    def complete_rejected(
            self, pending: PendingAction, *, reason: str) -> MutationRejected:
        action = pending.action
        event = action.await_completion(self)
        self.trace.append("RESULT", event)
        assert isinstance(event, MutationRejected), (
            f"expected rejection, received {event!r}\n{self.trace.format_tail()}")
        assert event.reason == reason, (
            f"expected rejection {reason!r}, received {event.reason!r}\n"
            f"{self.trace.format_tail()}")
        self.assert_invariants()
        return event

    def wait_for_deferred_persistence(self, kind: str) -> None:
        deadline = time.monotonic() + self.event_timeout
        with self._persistence_condition:
            while not any(item[0] == kind for item in self._deferred_persistence):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"timed out waiting for deferred {kind}\n"
                        f"{self.trace.format_tail()}")
                self._persistence_condition.wait(timeout=remaining)

    def release_persistence(self, kind: str) -> None:
        with self._persistence_condition:
            index = next((
                index for index, item in enumerate(self._deferred_persistence)
                if item[0] == kind
            ), None)
            if index is None:
                raise LookupError(f"no deferred {kind} persistence operation")
            _, operation = self._deferred_persistence.pop(index)
        self.trace.append("GUI", ("release_persistence", kind))
        operation()

    def expect(self, *assertions) -> None:
        for assertion in assertions:
            try:
                assertion.check(self)
            except AssertionError as exc:
                raise AssertionError(
                    f"{exc}\nRuntime trace:\n{self.trace.format_tail()}") from exc
            self.trace.append("ASSERT", assertion)

    def pump_for(self, event_types: tuple[type, ...]):
        deadline = time.monotonic() + self.event_timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"timed out waiting for {[item.__name__ for item in event_types]}\n"
                    f"{self.trace.format_tail()}")
            try:
                message = self._gui_mailbox.get(timeout=remaining)
            except queue.Empty as exc:
                raise TimeoutError(
                    f"GUI mailbox was idle\n{self.trace.format_tail()}") from exc
            event = message.payload
            self.trace.append("EVENT", event)
            self.application.handle_message(message)
            if isinstance(event, event_types):
                return event

    def pump_until(self, predicate, *, description: str = "condition",
                   timeout: float | None = None) -> None:
        deadline = time.monotonic() + (timeout or self.event_timeout)
        while not predicate():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"timed out waiting for {description}\n{self.trace.format_tail()}")
            try:
                message = self._gui_mailbox.get(timeout=remaining)
            except queue.Empty as exc:
                raise TimeoutError(
                    f"GUI mailbox was idle while waiting for {description}\n"
                    f"{self.trace.format_tail()}") from exc
            self.trace.append("EVENT", message.payload)
            self.application.handle_message(message)

    def wait_for_compile_request(self, kind: CompileKind):
        try:
            request = self._compile_requests.get(timeout=self.event_timeout)
        except queue.Empty as exc:
            raise TimeoutError(
                f"owner did not request {kind.name} compile\n"
                f"{self.trace.format_tail()}") from exc
        assert request[0] is kind
        return request

    def complete_compile(self, *, success: bool = True) -> None:
        completion = self.owner.compile_completion(success=success)
        result = self.context.bus.publish_wait(Message(
            source="tests.runtime_sim",
            destination=GraphEndpoint.SERVICE,
            topic=GraphTopic.COMMAND,
            payload=completion,
        ), timeout=self.event_timeout)
        self.trace.append("COMMAND", completion)
        if result.name != "ENQUEUED":
            raise RuntimeError(result.describe(GraphEndpoint.SERVICE))
        try:
            self._published_snapshots.get(timeout=self.event_timeout)
        except queue.Empty as exc:
            raise TimeoutError(
                f"compile completion was not committed\n{self.trace.format_tail()}") from exc
        expected = CompileStatus.VALID if success else CompileStatus.FAILED
        assert self.owner.compile_status is expected

    def _request_compile(self, kind, node_tag):
        self.trace.append("OWNER", ("request_compile", kind, node_tag))
        self._compile_requests.put((kind, node_tag))
        return True

    def _request_preset_load(
            self, name, project_id, revision, request_id):
        return self._submit_persistence("load", lambda:
            self.persistence_worker.request_load_project(
                name,
                known_node_types(),
                project_id=project_id,
                expected_revision=revision,
                request_id=request_id,
            ))

    def _request_preset_save(
            self, name, document, revision, request_id, as_default):
        return self._submit_persistence("save", lambda:
            self.persistence_worker.request_save_project(
                name,
                document,
                revision=revision,
                request_id=request_id,
                as_default=as_default,
            ))

    def _submit_persistence(self, kind, operation):
        if not self._defer_persistence:
            return operation()
        with self._persistence_condition:
            self._deferred_persistence.append((kind, operation))
            self._persistence_condition.notify_all()
        self.trace.append("OWNER", ("defer_persistence", kind))
        return object()

    def _owner_call(self, name, operation, *args):
        self.trace.append("OWNER", (name, args))
        return operation(*args)

    def _publish_snapshot(self):
        snapshot = publish_graph_snapshot()
        self.trace.append("OWNER", ("publish_snapshot", snapshot))
        self._published_snapshots.put(snapshot)
        return snapshot

    def move_node(self, alias_or_id: str, x: float, y: float) -> None:
        node_id = self.resolve_node(alias_or_id)
        dpg.set_item_pos(node_id, (float(x), float(y)))
        sync_history_workspace({
            node.node_tag: dpg.get_item_pos(node.node_tag)
            for node in GRAPH_STATE.nodes.values()
            if dpg.does_item_exist(node.node_tag)
        })
        self.publisher.commit_workspace_gesture(capture_history_workspace())
        self.trace.append("GUI", ("move_node", node_id, float(x), float(y)))

    def node_position(self, alias_or_id: str) -> tuple[float, float]:
        position = dpg.get_item_pos(self.resolve_node(alias_or_id))
        return float(position[0]), float(position[1])

    def preset_has_practice(self, name: str) -> bool:
        data = self.store.read(name)
        return data.get("practice") is not None or data.get("globals") is not None

    def resolve_node_type(self, requested: str) -> str:
        if requested in NODE_REGISTRY:
            return requested
        for display_name, node_class in NODE_REGISTRY.items():
            if node_class.__name__ == requested:
                return display_name
        raise KeyError(f"unknown node type {requested!r}")

    def bind_alias(self, alias: str, node_id: str) -> None:
        if alias in self.aliases:
            raise ValueError(f"alias {alias!r} is already bound")
        stale_aliases = [
            existing_alias
            for existing_alias, existing_id in self.aliases.items()
            if existing_id == node_id
        ]
        for stale_alias in stale_aliases:
            del self.aliases[stale_alias]
            self.trace.append(
                "ALIAS", f"unbound {stale_alias} from reused {node_id}")
        self.aliases[alias] = node_id

    def resolve_node(self, alias_or_id: str) -> str:
        return self.aliases.get(alias_or_id, alias_or_id)

    def node(self, alias_or_id: str):
        return self.state.nodes[self.resolve_node(alias_or_id)]

    def resolve_pin(self, reference: str, *, output: bool) -> str:
        alias, role = reference.split(".", 1)
        node_id = self.resolve_node(alias)
        node = self.state.nodes[node_id]
        spec = node.output_spec if output else node.input_spec
        prefix = f"{node_id}_{role}::"
        matches = [pin for pin in spec if pin.startswith(prefix)]
        if len(matches) != 1:
            direction = "output" if output else "input"
            raise KeyError(
                f"expected one {direction} pin for {reference!r}, found {matches}")
        return matches[0]

    def resolve_connection(self, output: str, input: str):
        output_pin = self.resolve_pin(output, output=True)
        input_pin = self.resolve_pin(input, output=False)
        for connection in self.state.iter_connections():
            if (connection.output_pin == output_pin
                    and connection.input_pin == input_pin):
                return connection
        raise KeyError(f"connection {output} -> {input} does not exist")

    def assert_invariants(self) -> None:
        input_pins = set()
        for node_id, node in self.state.nodes.items():
            assert node.node_tag == node_id
            for pin in (*node.input_spec, *node.output_spec):
                assert self.state.pin_owners.get(pin) == node_id
        for connection in self.state.iter_connections():
            assert connection.output_pin in self.state.pin_owners
            assert connection.input_pin in self.state.pin_owners
            assert connection.input_pin not in input_pins
            input_pins.add(connection.input_pin)
        assert all(dpg.does_item_exist(node_id) for node_id in self.state.nodes)
        assert set(NODE_EDITOR_LINKS._items_by_connection) == {
            connection.connection_id for connection in self.state.iter_connections()
        }
        owner_thread_id = self.owner._thread.ident
        assert owner_thread_id is not None
        assert all(
            entry.thread_id == owner_thread_id
            for entry in self.trace.entries
            if entry.kind == "OWNER"
        )
        assert all(
            entry.thread_id == self.gui_thread_id
            for entry in self.trace.entries
            if entry.kind in {"ACTION", "EVENT", "GUI"}
        )
        for alias, node_id in tuple(self.aliases.items()):
            if node_id not in self.state.nodes:
                self.trace.append("ALIAS", f"{alias} -> deleted {node_id}")