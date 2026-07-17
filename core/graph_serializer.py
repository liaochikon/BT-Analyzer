import copy

from core.persistence_contracts import WorkspaceSnapshot


class GraphSerializer:
    """Pure transformations for detached graph and workspace documents."""

    @staticmethod
    def snapshot(graph: dict, globals_state: dict | None = None) -> WorkspaceSnapshot:
        return WorkspaceSnapshot(
            graph=copy.deepcopy(graph),
            globals=copy.deepcopy(globals_state) if globals_state is not None else None,
        )

    @staticmethod
    def template(graph: dict, template_extras=()) -> dict:
        template = {"nodes": [], "links": copy.deepcopy(graph.get("links", []))}
        extras = iter(template_extras)
        for node in graph.get("nodes", []):
            item = {
                "class": node.get("class"),
                "pos": copy.deepcopy(node.get("pos", [0, 0])),
                "params": {},
            }
            extra = copy.deepcopy(next(extras, {}))
            if extra:
                item["extra"] = extra
            if node.get("custom_name"):
                item["custom_name"] = node["custom_name"]
            if "panel" in node:
                item["panel"] = copy.deepcopy(node["panel"])
            template["nodes"].append(item)
        return template
