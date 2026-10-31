"""Persistent owner-managed file paths for file-backed I/O nodes."""

from core.graph_contracts import NodeStateAction


class FilePathStateMixin:
    @staticmethod
    def _normalize_file_path(value) -> str:
        return value if isinstance(value, str) else ""

    def serialize_extra(self) -> dict:
        return {"file_path": self.file_path}

    def apply_extra(self, data: dict):
        self.file_path = self._normalize_file_path(data.get("file_path"))

    def apply_extra_state(self, data: dict):
        self.file_path = self._normalize_file_path(data.get("file_path"))

    def mutate_extra_state(self, field, value, *, index=None, action=None):
        if field != "file_path" or action is not NodeStateAction.SET:
            return super().mutate_extra_state(
                field, value, index=index, action=action)
        self.file_path = self._normalize_file_path(value)
        return self.file_path
