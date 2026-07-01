"""Effective policy reporting for Toolplane host surfaces."""

from __future__ import annotations

from dataclasses import dataclass

from .config import ToolplaneConfig
from .errors import UnsafeFacadeConfigError


@dataclass(frozen=True)
class EffectivePolicy:
    """Host-visible policy resolved from Toolplane config."""

    default_backend: str
    cli_mode: str
    cli_allowed_binaries: tuple[str, ...]
    mcp_server_names: tuple[str, ...]
    unsafe_allowed: bool
    unsafe_reasons: tuple[str, ...]
    blocked_backend_overrides: tuple[str, ...]

    @classmethod
    def from_config(
        cls,
        config: ToolplaneConfig,
        *,
        allow_unsafe: bool = False,
    ) -> "EffectivePolicy":
        unsafe_reasons: list[str] = []
        if config.toolplane.default_backend == "local_unsafe":
            unsafe_reasons.append("local_unsafe")
        if config.cli.mode == "ambient":
            unsafe_reasons.append("ambient_cli")

        return cls(
            default_backend=config.toolplane.default_backend,
            cli_mode=config.cli.mode,
            cli_allowed_binaries=tuple(sorted(config.cli.allow)),
            mcp_server_names=tuple(sorted(config.mcp.servers)),
            unsafe_allowed=allow_unsafe,
            unsafe_reasons=tuple(unsafe_reasons),
            blocked_backend_overrides=()
            if allow_unsafe
            else ("local_unsafe",),
        )


def ensure_safe_facade_policy(policy: EffectivePolicy) -> None:
    """Reject unsafe MCP facade policy unless explicitly allowed."""
    if not policy.unsafe_reasons or policy.unsafe_allowed:
        return
    joined = ", ".join(policy.unsafe_reasons)
    raise UnsafeFacadeConfigError(
        "Refusing to serve Toolplane MCP facade with unsafe policy: "
        f"{joined}. Use an explicit safe config or pass --unsafe for trusted "
        "local development."
    )


def format_effective_policy(policy: EffectivePolicy) -> str:
    """Render a compact operator-facing policy summary."""
    if policy.cli_mode == "ambient":
        allowed = "ALL"
    elif policy.cli_mode == "allowlist":
        allowed = _join_or_none(policy.cli_allowed_binaries)
    else:
        allowed = "none"

    unsafe = bool(policy.unsafe_reasons and policy.unsafe_allowed)
    parts = [
        f"backend={policy.default_backend}",
        f"cli={policy.cli_mode}",
        f"allow={allowed}",
        f"mcp_servers={_join_or_none(policy.mcp_server_names)}",
        f"blocked_backends={_join_or_none(policy.blocked_backend_overrides)}",
        f"unsafe={str(unsafe).lower()}",
    ]
    if unsafe:
        parts.append(f"reasons={','.join(policy.unsafe_reasons)}")
    return "Toolplane MCP policy: " + " ".join(parts)


def _join_or_none(values: tuple[str, ...]) -> str:
    if not values:
        return "none"
    return ",".join(values)
