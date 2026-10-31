"""Architecture gates for headless node processors."""

import ast
from pathlib import Path


def test_node_processors_do_not_import_ui_or_dearpygui():
    nodes_root = Path(__file__).parents[1] / "nodes"
    forbidden = ("ui", "dearpygui")
    violations = []

    for path in sorted(nodes_root.rglob("processor.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            for module in modules:
                if module == forbidden[0] or module.startswith("ui.") \
                        or module == forbidden[1] or module.startswith("dearpygui."):
                    violations.append(
                        f"{path.relative_to(nodes_root.parent)}:{node.lineno} imports {module}")

    assert violations == []


def test_nodes_do_not_use_legacy_mirrored_parameter_helper():
    nodes_root = Path(__file__).parents[1] / "nodes"
    violations = []

    for path in sorted(nodes_root.rglob("node.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            if isinstance(function, ast.Attribute) \
                    and function.attr == "_declare_mirrored_param":
                violations.append(
                    f"{path.relative_to(nodes_root.parent)}:{node.lineno} uses legacy helper")

    assert violations == []


def test_nodes_use_split_parameter_and_control_specs():
    nodes_root = Path(__file__).parents[1] / "nodes"
    violations = []

    for path in sorted(nodes_root.rglob("node.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "ParamSpec":
                violations.append(
                    f"{path.relative_to(nodes_root.parent)}:{node.lineno} uses ParamSpec")
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(isinstance(target, ast.Name) and target.id == "param_specs"
                       for target in targets):
                    violations.append(
                        f"{path.relative_to(nodes_root.parent)}:{node.lineno} declares param_specs")

    assert violations == []