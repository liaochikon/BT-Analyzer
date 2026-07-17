import uuid


class PresetStoreError(Exception):
    pass


class PresetStore:
    """JSON preset I/O with atomic replacement and no graph/runtime dependencies."""

    def __init__(self, directory: str, *, os_module, json_module) -> None:
        self._directory = directory
        self._os = os_module
        self._json = json_module

    def list_names(self) -> list[str]:
        if not self._os.path.isdir(self._directory):
            return []
        return sorted(
            self._os.path.splitext(filename)[0]
            for filename in self._os.listdir(self._directory)
            if filename.endswith(".json")
        )

    def read(self, name: str) -> dict:
        path = self._path(name)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return self._json.load(handle)
        except (OSError, self._json.JSONDecodeError) as exc:
            raise PresetStoreError(f"failed to read preset {name!r}") from exc

    def write_atomic(self, name: str, data: dict) -> None:
        path = self._path(name)
        temporary = self._os.path.join(
            self._directory,
            f".{name}.{uuid.uuid4().hex}.tmp",
        )
        try:
            self._os.makedirs(self._directory, exist_ok=True)
            with open(temporary, "w", encoding="utf-8") as handle:
                self._json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.flush()
                self._os.fsync(handle.fileno())
            self._os.replace(temporary, path)
        except (OSError, TypeError, ValueError) as exc:
            raise PresetStoreError(f"failed to write preset {name!r}") from exc
        finally:
            if self._os.path.exists(temporary):
                try:
                    self._os.remove(temporary)
                except OSError:
                    pass

    def exists(self, name: str) -> bool:
        return self._os.path.exists(self._path(name))

    def _path(self, name: str) -> str:
        return self._os.path.join(self._directory, f"{name}.json")
