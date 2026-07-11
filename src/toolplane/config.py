"""Configuration models and TOML loading for Toolplane runtimes."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ToolplaneSettings(BaseModel):
    """Top-level runtime settings owned by Toolplane."""

    model_config = ConfigDict(extra="forbid")

    default_backend: str = "monty"


class CliSettings(BaseModel):
    """Ambient CLI exposure policy."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["ambient", "allowlist", "disabled"] = "disabled"
    allow: tuple[str, ...] = ()

    @field_validator("allow")
    @classmethod
    def validate_allow(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for binary in value:
            if not binary or not binary.strip():
                raise ValueError("cli.allow entries must be non-empty strings")
            if binary in seen:
                duplicates.add(binary)
            seen.add(binary)
        if duplicates:
            joined = ", ".join(sorted(duplicates))
            raise ValueError(f"cli.allow contains duplicate entries: {joined}")
        return value

    @model_validator(mode="after")
    def validate_policy(self) -> "CliSettings":
        if self.mode == "allowlist" and not self.allow:
            raise ValueError("cli.allow is required when cli.mode = 'allowlist'")
        if self.mode != "allowlist" and self.allow:
            raise ValueError("cli.allow is only valid when cli.mode = 'allowlist'")
        return self

    @property
    def enabled(self) -> bool:
        return self.mode != "disabled"

    @property
    def allowed_binaries(self) -> frozenset[str] | None:
        if self.mode != "allowlist":
            return None
        return frozenset(self.allow)


class ResultsSettings(BaseModel):
    """Result store policy: in-memory, capped, TTL'd (docs/result-store-design.md)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    max_entries: int = Field(default=64, gt=0)
    max_total_bytes: int = Field(default=32 * 1024 * 1024, gt=0)
    max_entry_bytes: int = Field(default=8 * 1024 * 1024, gt=0)
    ttl_seconds: float = Field(default=3600.0, gt=0)


class ArtifactsSettings(BaseModel):
    """Artifact store policy: disk-backed, capped, TTL'd, session-scoped."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    max_entries: int = Field(default=32, gt=0)
    max_total_bytes: int = Field(default=256 * 1024 * 1024, gt=0)
    max_entry_bytes: int = Field(default=64 * 1024 * 1024, gt=0)
    ttl_seconds: float = Field(default=3600.0, gt=0)


class SessionSettings(BaseModel):
    """Persistent-namespace policy for the monty backend.

    When enabled, variables persist across execute_code runs within one
    served session (stdio only — multi-client transports fail closed, like
    the stores). The memory cap bounds the accumulated interpreter heap.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    max_memory_mb: int = Field(default=512, gt=0)


class HybridSettings(BaseModel):
    """Selective hybrid re-export policy (#125).

    When enabled, the capabilities matched by ``include`` are re-exported as
    ordinary MCP tools alongside the meta-tools, so a deferred-loading
    client can call them natively for single/adaptive tasks while loops and
    joins still use ``execute_code``. Re-exporting the WHOLE registry is the
    worst arm at scale (#114) — this is deliberately a curated allowlist, so
    ``include`` must be non-empty when enabled.

    Each ``include`` token matches a capability by: its exact canonical name
    (``mcp:orders/get_order``), an fnmatch glob on that name
    (``mcp:orders/*``), or ``tag:<name>`` against the capability's tags.
    Choose capabilities whose typical use is a single or adaptive call
    (lookups, actions, per-hop chains); leave bulk/loop/join work behind
    ``execute_code``.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    include: list[str] = Field(default_factory=list)

    @field_validator("include")
    @classmethod
    def _validate_include(cls, value: list[str]) -> list[str]:
        for token in value:
            if not token or not token.strip():
                raise ValueError(
                    "hybrid.include entries must be non-empty, non-whitespace "
                    "patterns (a capability name, a glob like 'mcp:orders/*', "
                    "or 'tag:<name>')"
                )
            # a bare "*" (or "**") re-exports the WHOLE registry — the
            # measured worst arm at scale (#114). Curation means a subset;
            # reject the unambiguous export-all pattern at the contract edge.
            if token.strip("* \t") == "" and "*" in token:
                raise ValueError(
                    "hybrid.include may not be a bare wildcard "
                    f"({token!r}) — that re-exports every capability, the "
                    "measured worst arm at 15 servers (#114). Curate the "
                    "single/adaptive capabilities explicitly."
                )
        return value

    @model_validator(mode="after")
    def _require_include_when_enabled(self) -> "HybridSettings":
        if self.enabled and not self.include:
            raise ValueError(
                "hybrid.include must list at least one capability pattern "
                "when hybrid.enabled is true — re-exporting everything is a "
                "measured regression (#114); curate the single/adaptive "
                "capabilities (names, globs like 'mcp:orders/*', or "
                "'tag:<name>')"
            )
        return self


class AuditSettings(BaseModel):
    """Audit log policy: opt-in JSONL event stream, metadata only.

    Events carry capability names, durations, and outcomes — never call
    arguments or results, which can contain secrets.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    path: str | None = None  # default: ~/.toolplane/audit.jsonl


class McpSettings(BaseModel):
    """MCP adapter settings.

    Server dictionaries are intentionally preserved as mappings. FastMCP owns
    the transport/auth schema, and Toolplane should not strip future fields.
    """

    model_config = ConfigDict(extra="forbid")

    servers: dict[str, dict[str, Any]] = Field(default_factory=dict)

    def to_fastmcp_config(self) -> dict[str, dict[str, dict[str, Any]]]:
        return {
            "mcpServers": {
                name: dict(config) for name, config in self.servers.items()
            }
        }


class ToolplaneConfig(BaseModel):
    """Validated Toolplane-native configuration."""

    model_config = ConfigDict(extra="forbid")

    toolplane: ToolplaneSettings = Field(default_factory=ToolplaneSettings)
    cli: CliSettings = Field(default_factory=CliSettings)
    results: ResultsSettings = Field(default_factory=ResultsSettings)
    artifacts: ArtifactsSettings = Field(default_factory=ArtifactsSettings)
    session: SessionSettings = Field(default_factory=SessionSettings)
    audit: AuditSettings = Field(default_factory=AuditSettings)
    hybrid: HybridSettings = Field(default_factory=HybridSettings)
    mcp: McpSettings = Field(default_factory=McpSettings)


ConfigSource = str | os.PathLike[str] | Mapping[str, Any] | ToolplaneConfig


def load_toolplane_config(source: ConfigSource) -> ToolplaneConfig:
    """Load Toolplane config from TOML, a mapping, or an existing model."""

    if isinstance(source, ToolplaneConfig):
        return source
    if isinstance(source, Mapping):
        return ToolplaneConfig.model_validate(source)

    path = Path(source).expanduser()
    with path.open("rb") as file:
        data = tomllib.load(file)
    return ToolplaneConfig.model_validate(data)
