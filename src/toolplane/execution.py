"""Execution result and backend capability models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


FilesystemMode = Literal["none", "read", "read_write", "mounted", "full"]
NetworkMode = Literal["none", "restricted", "full"]
PersistenceMode = Literal["none", "session", "artifact"]
StartupLatency = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class BackendCapabilities:
    imports: bool
    third_party_packages: bool
    package_install: bool
    filesystem: FilesystemMode
    network: NetworkMode
    resource_limits: frozenset[str] = field(default_factory=frozenset)
    persistence: PersistenceMode = "none"
    startup_latency: StartupLatency = "low"


@dataclass(frozen=True)
class ExecutionError:
    type: str
    message: str
    traceback: str


@dataclass(frozen=True)
class ExecutionResult:
    value: Any = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = 0.0
    backend: str = ""
    error: ExecutionError | None = None
    artifacts: tuple[Any, ...] = ()

    @property
    def ok(self) -> bool:
        return self.error is None
