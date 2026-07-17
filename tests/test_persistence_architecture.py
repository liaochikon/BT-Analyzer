"""Dependency gates for pure persistence infrastructure."""

import ast
from pathlib import Path


def test_pure_persistence_modules_do_not_import_runtime_or_ui():
    root = Path(__file__).parents[1]
    forbidden = ("core.pipeline", "core.graph_presets", "ui", "dearpygui")
    violations = []

    for relative in ("core/graph_serializer.py", "core/preset_store.py",
                     "core/persistence_contracts.py", "application/persistence_worker.py"):
        path = root / relative
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(forbidden):
                        violations.append(f"{relative}:{node.lineno} imports {alias.name}")
            if module and module.startswith(forbidden):
                violations.append(f"{relative}:{node.lineno} imports {module}")

    assert violations == []


def test_graph_history_does_not_import_pipeline_or_presets():
    root = Path(__file__).parents[1]
    path = root / "core" / "graph_history.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    forbidden = {"core.pipeline", "core.graph_presets"}
    imports = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
        elif isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)

    assert imports.isdisjoint(forbidden)


def test_production_composition_uses_project_document_persistence():
    source = (Path(__file__).parents[1] / "main.py").read_text(encoding="utf-8")

    assert "request_load_project(" in source
    assert "request_save_project(" in source
    assert "persistence_worker.request_load(" not in source
    assert "persistence_worker.request_save(" not in source
    assert "apply_startup_preset()" not in source
    assert "graph_commands.load_preset(startup_preset)" in source