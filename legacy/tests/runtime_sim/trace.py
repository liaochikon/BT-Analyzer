from dataclasses import dataclass
import json
import threading


@dataclass(frozen=True, slots=True)
class TraceEntry:
    sequence: int
    kind: str
    payload: object
    thread_id: int
    thread_name: str


class RuntimeTrace:
    def __init__(self) -> None:
        self._entries: list[TraceEntry] = []

    @property
    def entries(self) -> tuple[TraceEntry, ...]:
        return tuple(self._entries)

    def append(self, kind: str, payload: object) -> TraceEntry:
        thread = threading.current_thread()
        entry = TraceEntry(
            len(self._entries) + 1,
            kind,
            payload,
            threading.get_ident(),
            thread.name,
        )
        self._entries.append(entry)
        return entry

    def format_tail(self, count: int = 20) -> str:
        return "\n".join(
            f"{entry.sequence:03d} {entry.kind:<9} "
            f"[{entry.thread_name}:{entry.thread_id}] {entry.payload!r}"
            for entry in self._entries[-count:]
        )

    def to_records(self) -> list[dict]:
        return [
            {
                "sequence": entry.sequence,
                "kind": entry.kind,
                "thread_id": entry.thread_id,
                "thread_name": entry.thread_name,
                "payload_type": type(entry.payload).__name__,
                "payload": repr(entry.payload),
            }
            for entry in self._entries
        ]

    def dumps(self) -> str:
        return json.dumps(self.to_records(), ensure_ascii=False, sort_keys=True)