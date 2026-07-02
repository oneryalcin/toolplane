"""Host-side result store: persist data, not interpreters, across runs.

Design record: docs/result-store-design.md. Values are canonicalized to JSON
at save time; the serialization test is the admission rule, so live objects
are rejected on every backend, including local_unsafe where the in-process
bridge would otherwise let them cross.
"""

from __future__ import annotations

import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .capabilities import Capability
from .errors import CapabilityNotFoundError, ResultStoreError

if TYPE_CHECKING:
    from .bridges.base import HostBridge
    from .config import ResultsSettings
    from .registry import CapabilityRegistry

RESULTS_SAVE_CAPABILITY = "toolplane:results/save"
RESULTS_LOAD_CAPABILITY = "toolplane:results/load"
RESULT_HANDLE_PREFIX = "res_"

_DISABLED_MESSAGE = "results store is disabled"

# Single source for the non-JSON guidance: the host store enforces the
# admission rule, but on pyodide the value must serialize in-sandbox just to
# cross the callback RPC, so the rendered bindings pre-check with the same
# message — otherwise a bare json.dumps TypeError preempts this guidance.
_NON_JSON_GUIDANCE = "save a JSON-shaped projection instead"


@dataclass(frozen=True)
class _Entry:
    label: str | None
    payload: str
    size_bytes: int
    saved_at: float


class ResultStore:
    """In-memory, capped, TTL'd store keyed by unguessable handles.

    The handle is the sole authority: labels are debugging metadata and are
    never lookup keys. Process lifetime is the privacy boundary — nothing is
    ever written to disk, so restart is the clear operation.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        max_entries: int = 64,
        max_total_bytes: int = 32 * 1024 * 1024,
        max_entry_bytes: int = 8 * 1024 * 1024,
        ttl_seconds: float = 3600.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_entries = max_entries
        self.max_total_bytes = max_total_bytes
        self.max_entry_bytes = max_entry_bytes
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._disabled_reason = None if enabled else _DISABLED_MESSAGE
        self._entries: dict[str, _Entry] = {}
        self._total_bytes = 0

    @classmethod
    def from_settings(cls, settings: "ResultsSettings") -> "ResultStore":
        return cls(
            enabled=settings.enabled,
            max_entries=settings.max_entries,
            max_total_bytes=settings.max_total_bytes,
            max_entry_bytes=settings.max_entry_bytes,
            ttl_seconds=settings.ttl_seconds,
        )

    @property
    def enabled(self) -> bool:
        return self._disabled_reason is None

    def disable(self, reason: str = _DISABLED_MESSAGE) -> None:
        self._disabled_reason = reason
        self._entries.clear()
        self._total_bytes = 0

    def save(self, value: Any, label: str | None = None) -> str:
        """Canonicalize a value to JSON and store it; returns the handle."""
        self._ensure_enabled()
        if label is not None and not isinstance(label, str):
            raise ResultStoreError(
                f"result label must be a string, got {type(label).__name__!r}"
            )
        payload = self._serialize(value)
        # labels are host-side memory too; count them so they cannot smuggle
        # uncounted bytes past the caps
        size = len(payload.encode("utf-8")) + (
            len(label.encode("utf-8")) if label else 0
        )
        self._purge_expired()
        if size > self.max_entry_bytes:
            raise ResultStoreError(
                f"result is {size} bytes, over the per-entry limit of "
                f"{self.max_entry_bytes} bytes; save a smaller projection"
            )
        if len(self._entries) >= self.max_entries:
            raise ResultStoreError(
                f"result store is full ({self.max_entries} entries); "
                "re-use an existing handle or save less"
            )
        if self._total_bytes + size > self.max_total_bytes:
            raise ResultStoreError(
                f"result store would exceed its total limit of "
                f"{self.max_total_bytes} bytes; save a smaller projection "
                "or re-use an existing handle"
            )
        handle = RESULT_HANDLE_PREFIX + secrets.token_urlsafe(16)
        self._entries[handle] = _Entry(
            label=label,
            payload=payload,
            size_bytes=size,
            saved_at=self._clock(),
        )
        self._total_bytes += size
        return handle

    def load(self, handle: str) -> Any:
        """Return the canonicalized value for a handle saved in an earlier run."""
        self._ensure_enabled()
        self._purge_expired()
        entry = self._entries.get(handle) if isinstance(handle, str) else None
        if entry is None:
            raise ResultStoreError(f"unknown or expired result handle: {handle!r}")
        return json.loads(entry.payload)

    def _ensure_enabled(self) -> None:
        if self._disabled_reason is not None:
            raise ResultStoreError(self._disabled_reason)

    def _serialize(self, value: Any) -> str:
        try:
            return json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ResultStoreError(
                f"value of type {type(value).__name__!r} is not "
                f"JSON-serializable ({exc}); {_NON_JSON_GUIDANCE}"
            ) from exc

    def _purge_expired(self) -> None:
        cutoff = self._clock() - self.ttl_seconds
        expired = [
            handle
            for handle, entry in self._entries.items()
            if entry.saved_at < cutoff
        ]
        for handle in expired:
            self._total_bytes -= self._entries.pop(handle).size_bytes


def register_result_capabilities(
    registry: "CapabilityRegistry",
) -> tuple[Capability, Capability]:
    """Register the hidden save/load capabilities for schema discovery.

    These entries are discovery-only: dispatch is owned by each runtime's
    bridge, which resolves the names against its own store. Binding a store
    into the registry would leak data across runtimes that share a registry
    (first registration would win), so the registered callables refuse
    direct calls instead.
    """
    try:
        return (
            registry.get(RESULTS_SAVE_CAPABILITY),
            registry.get(RESULTS_LOAD_CAPABILITY),
        )
    except CapabilityNotFoundError:
        pass

    save = Capability(
        name=RESULTS_SAVE_CAPABILITY,
        callable=_bridge_dispatch_only,
        description=(
            "Save a JSON-shaped value to the host result store; "
            "returns a handle usable in later runs."
        ),
        parameters={
            "type": "object",
            "properties": {
                "value": {},
                "label": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            },
            "required": ["value"],
        },
        returns={"type": "string"},
        tags=frozenset({"toolplane", "results"}),
        source="toolplane",
        hidden=True,
    )
    load = Capability(
        name=RESULTS_LOAD_CAPABILITY,
        callable=_bridge_dispatch_only,
        description="Load a value saved to the host result store by its handle.",
        parameters={
            "type": "object",
            "properties": {"handle": {"type": "string"}},
            "required": ["handle"],
        },
        returns={},
        tags=frozenset({"toolplane", "results"}),
        source="toolplane",
        hidden=True,
    )
    registry.add(save)
    registry.add(load)
    return save, load


def _bridge_dispatch_only(**_params: Any) -> Any:
    raise ResultStoreError(
        "result capabilities are dispatched by the runtime bridge, "
        "not the registry"
    )


def build_result_bindings(
    bridge: "HostBridge",
    *,
    reserved: set[str] | frozenset[str],
) -> dict[str, Any]:
    """Sugar bindings dispatched through the bridge, like the CLI surface."""
    bindings: dict[str, Any] = {}
    if "save_result" not in reserved:

        async def save_result(value: Any, label: str | None = None) -> str:
            return await bridge.call_tool(
                RESULTS_SAVE_CAPABILITY,
                {"value": value, "label": label},
            )

        bindings["save_result"] = save_result
    if "load_result" not in reserved:

        async def load_result(handle: str) -> Any:
            return await bridge.call_tool(
                RESULTS_LOAD_CAPABILITY,
                {"handle": handle},
            )

        bindings["load_result"] = load_result
    return bindings


def render_pyodide_result_bindings(
    *,
    reserved: set[str] | frozenset[str],
) -> str:
    lines: list[str] = []
    if "save_result" not in reserved:
        lines += [
            "async def save_result(value, label=None):",
            "    import json as _tp_json",
            "    try:",
            "        _tp_json.dumps(value, allow_nan=False)",
            "    except (TypeError, ValueError) as exc:",
            "        raise Exception(",
            '            "value of type " + repr(type(value).__name__)',
            '            + " is not JSON-serializable (" + str(exc) + "); "',
            f"            + {_NON_JSON_GUIDANCE!r}",
            "        )",
            f"    return await call_tool({RESULTS_SAVE_CAPABILITY!r}, "
            '{"value": value, "label": label})',
            "",
        ]
    if "load_result" not in reserved:
        lines += [
            "async def load_result(handle):",
            f"    return await call_tool({RESULTS_LOAD_CAPABILITY!r}, "
            '{"handle": handle})',
            "",
        ]
    return "\n".join(lines)
