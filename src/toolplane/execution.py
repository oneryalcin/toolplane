"""Execution result and backend capability models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


FilesystemMode = Literal["none", "read", "read_write", "mounted", "full"]
NetworkMode = Literal["none", "restricted", "full"]
PersistenceMode = Literal["none", "session", "artifact"]
StartupLatency = Literal["low", "medium", "high"]


class BackendCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True)

    imports: bool
    third_party_packages: bool
    package_install: bool
    filesystem: FilesystemMode
    network: NetworkMode
    resource_limits: frozenset[str] = Field(default_factory=frozenset)
    persistence: PersistenceMode = "none"
    startup_latency: StartupLatency = "low"


class ExecutionError(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: str
    message: str = ""
    traceback: str = ""


class ExecutionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: Any = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = 0.0
    backend: str = ""
    error: ExecutionError | None = None
    artifacts: tuple[Any, ...] = Field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.error is None
