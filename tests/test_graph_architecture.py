"""Architecture gates for the GraphApplication ownership boundary."""

import ast
from pathlib import Path


_MUTABLE_GRAPH_GLOBALS = {
    "GRAPH_STATE",
    "NODE_CONNECTIONS",
    "NODE_CONNECTION_IDS",
    "NODE_IN_RUNTIME",
    "PIN_OWNER",
    "NODE_EXECUTION_ORDER",
    "STALE_NODES",
    "FAILED_NODES",
    "CYCLE_NODES",
}

_REMOVED_GRAPH_ALIASES = _MUTABLE_GRAPH_GLOBALS - {"GRAPH_STATE"}


def test_core_plugin_surface_does_not_reexport_persistence_implementations():
    path = Path(__file__).parents[1] / "core" / "__init__.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules = {
        node.module
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
    }

    assert "core.graph_presets" not in modules
    assert "core.graph_history" not in modules


def test_message_bus_lives_in_core():
    root = Path(__file__).parents[1]
    assert not (root / "bus.py").exists()

    violations = []
    for source_root in (root / "application", root / "core", root / "ui"):
        for path in sorted(source_root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import) and any(
                        alias.name == "bus" for alias in node.names):
                    violations.append(
                        f"{path.relative_to(root)}:{node.lineno} imports root bus")
                elif isinstance(node, ast.ImportFrom) and node.module == "bus":
                    violations.append(
                        f"{path.relative_to(root)}:{node.lineno} imports root bus")

    assert violations == []


def test_ui_does_not_reference_mutable_graph_registries():
    ui_root = Path(__file__).parents[1] / "ui"
    violations = []

    for path in sorted(ui_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            name = None
            if isinstance(node, ast.Name):
                name = node.id
            elif isinstance(node, ast.Attribute):
                name = node.attr
            if name in _MUTABLE_GRAPH_GLOBALS:
                violations.append(f"{path.relative_to(ui_root.parent)}:{node.lineno} uses {name}")

    assert violations == []


def test_ui_does_not_use_runtime_registry_projection_helpers():
    ui_root = Path(__file__).parents[1] / "ui"
    violations = []

    for path in sorted(ui_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "runtime_node_views":
                violations.append(
                    f"{path.relative_to(ui_root.parent)}:{node.lineno} uses runtime_node_views")

    assert violations == []


def test_ui_graph_mutations_do_not_bypass_command_publisher():
    ui_root = Path(__file__).parents[1] / "ui"
    forbidden_calls = {
        "heavy_node_compile",
        "load_preset",
        "record_history",
        "request_compile",
        "save_preset",
    }
    violations = []

    for path in sorted(ui_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in forbidden_calls
            ):
                violations.append(
                    f"{path.relative_to(ui_root.parent)}:{node.lineno} calls {node.func.id}")

    assert violations == []


def test_node_modules_do_not_bypass_parameter_command_adapter():
    nodes_root = Path(__file__).parents[1] / "nodes"
    violations = []

    for path in sorted(nodes_root.rglob("*.py")):
        if path.name.startswith("test_"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "_set_declared_param"
            ):
                violations.append(
                    f"{path.relative_to(nodes_root.parent)}:{node.lineno} mutates parameter directly")

    assert violations == []


def test_pipeline_does_not_redeclare_graph_state_aliases():
    path = Path(__file__).parents[1] / "core" / "pipeline.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and target.id in _REMOVED_GRAPH_ALIASES:
                violations.append(f"core/pipeline.py:{node.lineno} declares {target.id}")

    assert violations == []


def test_pipeline_does_not_depend_on_gui_modules():
    path = Path(__file__).parents[1] / "core" / "pipeline.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations = []

    for node in ast.walk(tree):
        modules = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]
        for module in modules:
            if module == "dearpygui" or module.startswith(("dearpygui.", "ui.")):
                violations.append(f"core/pipeline.py:{node.lineno} imports {module}")

    assert violations == []


def test_graph_documents_do_not_depend_on_gui_modules():
    path = Path(__file__).parents[1] / "core" / "graph_documents.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations = []

    for node in ast.walk(tree):
        modules = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]
        for module in modules:
            if module == "dearpygui" or module.startswith(("dearpygui.", "ui.")):
                violations.append(f"core/graph_documents.py:{node.lineno} imports {module}")

    assert violations == []


def test_compile_workers_do_not_access_live_graph_metadata():
    path = Path(__file__).parents[1] / "core" / "pipeline.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    protected = {
        "execution_order", "stale_nodes", "failed_nodes", "cycle_nodes",
        "heavy_descendants_cache",
    }
    worker_path = {
        "_heavy_compile_worker", "_bake_node_worker",
        "_run_pipeline", "_resolve_execution_order", "_apply_stale",
        "_clear_stale", "_mark_failed", "_clear_failed",
    }
    violations = []

    for function in (
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in worker_path
    ):
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Attribute)
                and node.attr in protected
                and isinstance(node.value, ast.Name)
                and node.value.id == "GRAPH_STATE"
            ):
                violations.append(
                    f"core/pipeline.py:{node.lineno} {function.name} accesses {node.attr}")

    assert violations == []


def test_persistence_worker_does_not_import_live_graph_or_gui():
    path = Path(__file__).parents[1] / "application" / "persistence_worker.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    forbidden = {"core.pipeline", "core.graph_presets", "ui"}
    violations = []

    for node in ast.walk(tree):
        module = None
        if isinstance(node, ast.ImportFrom):
            module = node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in forbidden or alias.name.startswith("ui."):
                    violations.append(
                        f"application/persistence_worker.py:{node.lineno} imports {alias.name}")
        if module in forbidden or (module and module.startswith("ui.")):
            violations.append(
                f"application/persistence_worker.py:{node.lineno} imports {module}")

    assert violations == []


def test_graph_state_does_not_store_gui_connection_ids():
    path = Path(__file__).parents[1] / "core" / "graph_state.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    graph_state = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "GraphState"
    )
    fields = {
        node.target.id
        for node in graph_state.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }

    assert "connection_ids" not in fields
    assert "dearpygui" not in source


def test_composition_root_owns_service_and_worker_creation():
    root = Path(__file__).parents[1]
    allowed = {
        "ApplicationContext": {"main.py"},
        "GraphOwner": {"main.py"},
        "PersistenceWorker": {"main.py"},
        "Bus": {"application/context.py"},
    }
    violations = []

    for directory in ("application", "core", "ui", "nodes"):
        paths = (root / directory).rglob("*.py")
        for path in paths:
            relative = path.relative_to(root).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                    continue
                owners = allowed.get(node.func.id)
                if owners is not None and relative not in owners:
                    violations.append(f"{relative}:{node.lineno} creates {node.func.id}")

    assert violations == []


def test_graph_projection_binds_after_workspace_creation():
    path = Path(__file__).parents[1] / "main.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    calls = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        else:
            continue
        if name in {
            "create_context", "ui_init", "ui_create_pedal_workspace", "bind_graph_projection",
        }:
            calls.setdefault(name, node.lineno)

    assert (
        calls["create_context"]
        < calls["ui_init"]
        < calls["ui_create_pedal_workspace"]
        < calls["bind_graph_projection"]
    )


def test_graph_application_is_projection_only():
    path = Path(__file__).parents[1] / "application" / "graph_application.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    graph_class = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "GraphApplication"
    )
    handle = next(
        node for node in graph_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "handle_message"
    )
    command_references = [
        node.lineno for node in ast.walk(handle)
        if isinstance(node, ast.Attribute) and node.attr == "COMMAND"
    ]
    snapshot_references = [
        node.lineno for node in ast.walk(graph_class)
        if (
            isinstance(node, ast.Name) and "snapshot" in node.id.lower()
            or isinstance(node, ast.Attribute) and "snapshot" in node.attr.lower()
        )
    ]

    assert command_references == []
    assert snapshot_references == []
