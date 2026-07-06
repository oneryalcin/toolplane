"""Append-only JSONL audit log, emitted at the bridge choke point.

Every tool call, CLI invocation, store access, and escalation decision
already flows through one dispatch seam, so one emitter covers the whole
surface. Events are metadata only — capability names, durations, outcomes —
never call arguments or results, because payloads can carry secrets. The
intended consumer is a developer with `tail -f` and `jq`, not a dashboard.
"""

from __future__ import annotations

import json
import secrets
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .config import AuditSettings

DEFAULT_AUDIT_PATH = "~/.toolplane/audit.jsonl"


class AuditLog:
    """Line-buffered JSONL event sink; disabled instances no-op everywhere.

    Auditing must never break execution: a write failure disables the log
    with one stderr warning instead of raising into the run.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        enabled: bool = False,
    ) -> None:
        self.enabled = enabled and path is not None
        self._path = Path(path).expanduser() if path is not None else None
        self._file: Any = None
        self._lock = threading.Lock()
        # set by runtime.execute around a run so dispatch events correlate;
        # single-live-run assumption, same as the escalation handler slot
        self.run_id: str | None = None

    @classmethod
    def from_settings(cls, settings: "AuditSettings") -> "AuditLog":
        return cls(
            settings.path or DEFAULT_AUDIT_PATH,
            enabled=settings.enabled,
        )

    @staticmethod
    def new_run_id() -> str:
        return secrets.token_hex(4)

    def emit(self, event: str, **fields: Any) -> None:
        if not self.enabled:
            return
        record: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "event": event,
        }
        if self.run_id is not None:
            record["run_id"] = self.run_id
        record.update(fields)
        line = json.dumps(record, separators=(",", ":"), default=str)
        try:
            with self._lock:
                if self._file is None:
                    assert self._path is not None
                    self._path.parent.mkdir(parents=True, exist_ok=True)
                    self._file = self._path.open("a", encoding="utf-8")
                self._file.write(line + "\n")
                self._file.flush()
        except OSError as exc:
            self.enabled = False
            print(
                f"toolplane: audit log disabled, cannot write "
                f"{self._path}: {exc}",
                file=sys.stderr,
            )

    def timer(self) -> float:
        return time.perf_counter()

    def elapsed_ms(self, started: float) -> float:
        return round((time.perf_counter() - started) * 1000, 3)
