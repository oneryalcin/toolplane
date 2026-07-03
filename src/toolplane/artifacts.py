"""Host-side artifact store: file/blob persistence across runs.

The second lane promised by docs/result-store-design.md: the result store
holds JSON-shaped values in memory; artifacts hold bytes on disk. Siblings by
design — unguessable handles as the sole authority, loud caps at write time,
session scope, bridge-owned dispatch.

Transport is base64 over the bridge on every backend (the narrowest common
shape — pyodide's RPC is JSON-only). A monty MountDir fast path is a later
optimization, not part of this contract.
"""

from __future__ import annotations

import atexit
import base64
import binascii
import re
import secrets
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .capabilities import Capability
from .errors import ArtifactStoreError, CapabilityNotFoundError

if TYPE_CHECKING:
    from .bridges.base import HostBridge
    from .config import ArtifactsSettings
    from .registry import CapabilityRegistry

ARTIFACTS_SAVE_CAPABILITY = "toolplane:artifacts/save"
ARTIFACTS_LOAD_CAPABILITY = "toolplane:artifacts/load"
ARTIFACT_HANDLE_PREFIX = "art_"

_DISABLED_MESSAGE = "artifact store is disabled"
_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class _Entry:
    label: str | None
    filename: str
    size_bytes: int
    saved_at: float


class ArtifactStore:
    """Disk-backed, capped, TTL'd store keyed by unguessable handles.

    Files live in a private tempdir owned by this store and are deleted on
    close(); the store registers close() to run at process exit, so restart
    is the clear operation — the same privacy boundary as the result store,
    extended to disk for the process lifetime.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        max_entries: int = 32,
        max_total_bytes: int = 256 * 1024 * 1024,
        max_entry_bytes: int = 64 * 1024 * 1024,
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
        self._root: Path | None = None

    @classmethod
    def from_settings(cls, settings: "ArtifactsSettings") -> "ArtifactStore":
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
        self.close()

    def close(self) -> None:
        """Delete every stored file and forget the entries."""
        self._entries.clear()
        self._total_bytes = 0
        if self._root is not None:
            shutil.rmtree(self._root, ignore_errors=True)
            self._root = None

    def save(
        self,
        data: bytes,
        *,
        filename: str | None = None,
        label: str | None = None,
    ) -> str:
        """Store bytes under a fresh handle; returns the handle."""
        self._ensure_enabled()
        if not isinstance(data, bytes):
            raise ArtifactStoreError(
                f"artifact data must be bytes, got {type(data).__name__!r}; "
                "JSON-shaped values belong in the result store (save_result)"
            )
        if label is not None and not isinstance(label, str):
            raise ArtifactStoreError(
                f"artifact label must be a string, got {type(label).__name__!r}"
            )
        resolved_filename = _validate_filename(filename)
        size = len(data)
        self._purge_expired()
        if size > self.max_entry_bytes:
            raise ArtifactStoreError(
                f"artifact is {size} bytes, over the per-artifact limit of "
                f"{self.max_entry_bytes} bytes; save a smaller file"
            )
        if len(self._entries) >= self.max_entries:
            raise ArtifactStoreError(
                f"artifact store is full ({self.max_entries} artifacts); "
                "re-use an existing handle or save less"
            )
        if self._total_bytes + size > self.max_total_bytes:
            raise ArtifactStoreError(
                f"artifact store would exceed its total limit of "
                f"{self.max_total_bytes} bytes; save a smaller file"
            )
        handle = ARTIFACT_HANDLE_PREFIX + secrets.token_urlsafe(16)
        self._entry_dir(handle).mkdir(parents=True)
        self._entry_path(handle, resolved_filename).write_bytes(data)
        self._entries[handle] = _Entry(
            label=label,
            filename=resolved_filename,
            size_bytes=size,
            saved_at=self._clock(),
        )
        self._total_bytes += size
        return handle

    def load(self, handle: str) -> bytes:
        """Return the bytes for a handle saved in an earlier run."""
        entry = self._get_entry(handle)
        return self._entry_path(handle, entry.filename).read_bytes()

    def describe(self, handle: str) -> dict[str, Any]:
        """Metadata for a handle: filename, size, label."""
        entry = self._get_entry(handle)
        return {
            "handle": handle,
            "filename": entry.filename,
            "size_bytes": entry.size_bytes,
            "label": entry.label,
        }

    def handles(self) -> tuple[str, ...]:
        self._purge_expired()
        return tuple(self._entries)

    def _get_entry(self, handle: str) -> _Entry:
        self._ensure_enabled()
        self._purge_expired()
        entry = self._entries.get(handle) if isinstance(handle, str) else None
        if entry is None:
            raise ArtifactStoreError(
                f"unknown or expired artifact handle: {handle!r}"
            )
        return entry

    def _ensure_enabled(self) -> None:
        if self._disabled_reason is not None:
            raise ArtifactStoreError(self._disabled_reason)

    def _entry_dir(self, handle: str) -> Path:
        if self._root is None:
            self._root = Path(
                tempfile.mkdtemp(prefix="toolplane-artifacts-")
            )
            # restart is the clear operation: nothing outlives the process
            atexit.register(self.close)
        return self._root / handle

    def _entry_path(self, handle: str, filename: str) -> Path:
        return self._entry_dir(handle) / filename

    def _purge_expired(self) -> None:
        cutoff = self._clock() - self.ttl_seconds
        expired = [
            handle
            for handle, entry in self._entries.items()
            if entry.saved_at < cutoff
        ]
        for handle in expired:
            entry = self._entries.pop(handle)
            self._total_bytes -= entry.size_bytes
            shutil.rmtree(self._entry_dir(handle), ignore_errors=True)


def _validate_filename(filename: str | None) -> str:
    if filename is None:
        return "artifact.bin"
    if not isinstance(filename, str) or not _SAFE_FILENAME.match(filename):
        raise ArtifactStoreError(
            f"artifact filename must be a simple name like 'report.parquet' "
            f"(letters, digits, dot, dash, underscore), got {filename!r}"
        )
    return filename


def decode_artifact_b64(data_b64: Any) -> bytes:
    if not isinstance(data_b64, str):
        raise ArtifactStoreError(
            f"artifact data_b64 must be a base64 string, got "
            f"{type(data_b64).__name__!r}"
        )
    try:
        return base64.b64decode(data_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ArtifactStoreError(
            f"artifact data_b64 is not valid base64: {exc}"
        ) from exc


def register_artifact_capabilities(
    registry: "CapabilityRegistry",
) -> tuple[Capability, Capability]:
    """Register the hidden save/load capabilities for schema discovery.

    Discovery-only, like the result store: dispatch is owned by each
    runtime's bridge, which resolves the names against its own store.
    """
    try:
        return (
            registry.get(ARTIFACTS_SAVE_CAPABILITY),
            registry.get(ARTIFACTS_LOAD_CAPABILITY),
        )
    except CapabilityNotFoundError:
        pass

    save = Capability(
        name=ARTIFACTS_SAVE_CAPABILITY,
        callable=_bridge_dispatch_only,
        description=(
            "Save bytes (base64-encoded) to the host artifact store; "
            "returns a handle usable in later runs. Saved artifacts are "
            "also readable as the MCP resource toolplane://artifacts/<handle>."
        ),
        parameters={
            "type": "object",
            "properties": {
                "data_b64": {"type": "string"},
                "filename": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "label": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            },
            "required": ["data_b64"],
        },
        returns={"type": "string"},
        tags=frozenset({"toolplane", "artifacts"}),
        source="toolplane",
        hidden=True,
    )
    load = Capability(
        name=ARTIFACTS_LOAD_CAPABILITY,
        callable=_bridge_dispatch_only,
        description=(
            "Load an artifact saved to the host artifact store by its "
            "handle; returns its bytes base64-encoded with metadata."
        ),
        parameters={
            "type": "object",
            "properties": {"handle": {"type": "string"}},
            "required": ["handle"],
        },
        returns={
            "type": "object",
            "properties": {
                "data_b64": {"type": "string"},
                "filename": {"type": "string"},
                "size_bytes": {"type": "integer"},
            },
            "required": ["data_b64", "filename", "size_bytes"],
        },
        tags=frozenset({"toolplane", "artifacts"}),
        source="toolplane",
        hidden=True,
    )
    registry.add(save)
    registry.add(load)
    return save, load


def _bridge_dispatch_only(**_params: Any) -> Any:
    raise ArtifactStoreError(
        "artifact capabilities are dispatched by the runtime bridge, "
        "not the registry"
    )


def build_artifact_bindings(
    bridge: "HostBridge",
    *,
    reserved: set[str] | frozenset[str],
) -> dict[str, Any]:
    """Sugar bindings dispatched through the bridge (monty/local).

    These closures run host-side, so they take real bytes from the snippet
    and do the base64 framing themselves — snippets never see base64.
    """
    bindings: dict[str, Any] = {}
    if "save_artifact" not in reserved:

        async def save_artifact(
            data: bytes,
            filename: str | None = None,
            label: str | None = None,
        ) -> str:
            if not isinstance(data, bytes):
                raise ArtifactStoreError(
                    f"save_artifact takes bytes, got "
                    f"{type(data).__name__!r}; JSON-shaped values belong in "
                    "the result store (save_result)"
                )
            return await bridge.call_tool(
                ARTIFACTS_SAVE_CAPABILITY,
                {
                    "data_b64": base64.b64encode(data).decode("ascii"),
                    "filename": filename,
                    "label": label,
                },
            )

        bindings["save_artifact"] = save_artifact
    if "load_artifact" not in reserved:

        async def load_artifact(handle: str) -> bytes:
            payload = await bridge.call_tool(
                ARTIFACTS_LOAD_CAPABILITY,
                {"handle": handle},
            )
            return base64.b64decode(payload["data_b64"])

        bindings["load_artifact"] = load_artifact
    return bindings


def render_pyodide_artifact_bindings(
    *,
    reserved: set[str] | frozenset[str],
) -> str:
    """In-sandbox bindings for pyodide: base64 framing happens in-sandbox."""
    lines: list[str] = []
    if "save_artifact" not in reserved:
        lines += [
            "async def save_artifact(data, filename=None, label=None):",
            "    import base64 as _tp_b64",
            "    if not isinstance(data, bytes):",
            "        raise ValueError(",
            '            "save_artifact takes bytes, got "',
            "            + repr(type(data).__name__)",
            '            + "; JSON-shaped values belong in the result store'
            ' (save_result)"',
            "        )",
            f"    return await call_tool({ARTIFACTS_SAVE_CAPABILITY!r}, "
            '{"data_b64": _tp_b64.b64encode(data).decode("ascii"), '
            '"filename": filename, "label": label})',
            "",
        ]
    if "load_artifact" not in reserved:
        lines += [
            "async def load_artifact(handle):",
            "    import base64 as _tp_b64",
            f"    payload = await call_tool({ARTIFACTS_LOAD_CAPABILITY!r}, "
            '{"handle": handle})',
            '    return _tp_b64.b64decode(payload["data_b64"])',
            "",
        ]
    return "\n".join(lines)
