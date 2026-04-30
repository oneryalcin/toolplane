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

    default_backend: str = "local_unsafe"


class CliSettings(BaseModel):
    """Ambient CLI exposure policy."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["ambient", "allowlist", "disabled"] = "ambient"
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
