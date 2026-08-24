"""Adapter for exposing MCP tools as Toolplane capabilities."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from typing import Any

from pydantic import BaseModel

from ..capabilities import Capability, JsonSchema
from ..registry import CapabilityRegistry

# per-server discovery bound for config-driven registration (#118): one
# hung upstream must not hold facade startup hostage. Generous enough for
# npx-style cold spawns; per-call override via register_mcp_config.
_REGISTER_TIMEOUT_SECS = 10.0


async def register_mcp_server(
    registry: CapabilityRegistry,
    name: str,
    server: Any,
    *,
    tags: set[str] | frozenset[str] | None = None,
    source: str = "mcp",
) -> list[Capability]:
    """Register all tools from one FastMCP-compatible server or transport.

    `server` is passed to `fastmcp.Client`, so this supports in-process
    `FastMCP` apps, URLs, script paths, transport objects, and single-server
    config dictionaries.

    Dict-shaped servers are credential-prepared exactly like config-file
    entries: without this, ``auth = "oauth"`` through the public
    ``runtime.register_mcp()`` silently fell back to fastmcp's in-memory
    token storage — the failure the credential layer exists to prevent
    (reviewer finding on #95). Preparation is idempotent, so entries that
    came through ``register_mcp_config`` are unaffected.
    """
    if isinstance(server, Mapping):
        from ..credentials import prepare_server_config

        raw = dict(server)
        if "mcpServers" in raw and isinstance(raw["mcpServers"], Mapping):
            raw["mcpServers"] = {
                entry_name: prepare_server_config(dict(entry))
                for entry_name, entry in raw["mcpServers"].items()
            }
            server = raw
        elif "url" in raw or "command" in raw:
            server = prepare_server_config(raw)
    client = _client(server)
    capabilities: list[Capability] = []
    async with client:
        tools = await client.list_tools()
    for tool in tools:
        capability = _capability_from_mcp_tool(
            client=client,
            server_name=name,
            tool=tool,
            tags=tags,
            source=source,
        )
        capabilities.append(registry.add(capability))
    return capabilities


async def register_mcp_config(
    registry: CapabilityRegistry,
    config: Any,
    *,
    tags: set[str] | frozenset[str] | None = None,
    source: str = "mcp",
    timeout_seconds: float = _REGISTER_TIMEOUT_SECS,
) -> list[Capability]:
    """Register all tools from a standard `mcpServers` config dictionary.

    Each entry is credential-prepared first: ``env://`` / ``keyring://``
    references in headers and env resolve to real values, and
    ``auth = "oauth"`` on url servers becomes a live fastmcp OAuth object
    backed by Toolplane's encrypted token store — so tokens persist across
    restarts without ever touching the config file or process output.

    Servers are discovered concurrently: sequential ``list_tools()`` calls
    compound into a many-server cold start, and one slow upstream stalls
    the whole facade (#118). Registry insertion order stays deterministic
    (config order, not completion order). A server that errors or exceeds
    ``timeout_seconds`` is skipped with a warning instead of failing
    startup — the facade comes up with N-1 servers and says so.
    """
    import warnings

    parsed = _mcp_config(config)
    entries = list(parsed.mcpServers.items())

    async def discover_one(
        server_name: str, server_config: Any
    ) -> list[Capability]:
        client = _client(_prepare_server_entry(server_name, server_config))
        async with client:
            tools = await client.list_tools()
        return [
            _capability_from_mcp_tool(
                client=client,
                server_name=server_name,
                tool=tool,
                tags=tags,
                source=source,
            )
            for tool in tools
        ]

    outcomes = await asyncio.gather(
        *[
            asyncio.wait_for(discover_one(name, cfg), timeout=timeout_seconds)
            for name, cfg in entries
        ],
        return_exceptions=True,
    )

    capabilities: list[Capability] = []
    for (server_name, _), outcome in zip(entries, outcomes):
        if isinstance(outcome, BaseException):
            reason = (
                f"timed out after {timeout_seconds:g}s"
                if isinstance(outcome, TimeoutError)
                else _one_line_exception(outcome)
            )
            warnings.warn(
                f"toolplane: MCP server {server_name!r} unavailable "
                f"during registration ({reason}); skipping it. The "
                f"facade is starting without this server.",
                stacklevel=2,
            )
            continue
        capabilities.extend(registry.add(cap) for cap in outcome)
    return capabilities


def _prepare_server_entry(server_name: str, server_config: Any) -> Any:
    """Credential-prepare one config entry and wrap it as a single-server
    config — the boundary where ``env://``/``keyring://`` references and
    ``auth = "oauth"`` become live credentials before any connection."""
    from ..credentials import prepare_server_config

    raw = (
        server_config.model_dump(exclude_none=True)
        if isinstance(server_config, BaseModel)
        else dict(server_config)
    )
    return _single_server_config(server_name, prepare_server_config(raw))


def _one_line_exception(exc: BaseException) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    return " ".join(message.split())


def _client(server: Any) -> Any:
    try:
        from fastmcp import Client
    except ImportError as exc:  # pragma: no cover - exercised only without dependency
        raise ImportError(
            "Toolplane MCP support requires FastMCP. Install Toolplane with "
            "its dependencies or add `fastmcp` to the environment."
        ) from exc
    return Client(server)


def _mcp_config(config: Any) -> Any:
    try:
        from fastmcp.mcp_config import MCPConfig
    except ImportError as exc:  # pragma: no cover - exercised only without dependency
        raise ImportError(
            "Toolplane MCP config support requires FastMCP. Install Toolplane "
            "with its dependencies or add `fastmcp` to the environment."
        ) from exc

    if isinstance(config, MCPConfig):
        return config
    if isinstance(config, Mapping):
        return MCPConfig.from_dict(dict(config))
    raise TypeError("MCP config must be a FastMCP MCPConfig or mapping")


def _single_server_config(server_name: str, server_config: Any) -> Any:
    from fastmcp.mcp_config import MCPConfig

    return MCPConfig(mcpServers={server_name: server_config})


def _capability_from_mcp_tool(
    *,
    client: Any,
    server_name: str,
    tool: Any,
    tags: set[str] | frozenset[str] | None,
    source: str,
) -> Capability:
    tool_name = str(tool.name)
    canonical_name = f"mcp:{server_name}/{tool_name}"
    namespace = _python_identifier(server_name, prefix="mcp")
    namespace_member = _python_identifier(tool_name, prefix="tool")
    aliases = frozenset({_python_alias(server_name, tool_name)})
    tool_tags = {"mcp", server_name, *(tags or ()), *_fastmcp_tags(tool)}

    async def call_mcp_tool(**params: Any) -> Any:
        async with client:
            result = await client.call_tool(tool_name, params)
        return _normalize_result(result)

    call_mcp_tool.__name__ = aliases and next(iter(aliases)) or "mcp_tool"

    return Capability(
        name=canonical_name,
        aliases=aliases,
        callable=call_mcp_tool,
        description=str(tool.description or ""),
        parameters=_tool_schema(tool, "inputSchema"),
        returns=_tool_schema(tool, "outputSchema"),
        tags=frozenset(tool_tags),
        source=f"{source}:{server_name}",
        namespace=namespace,
        namespace_member=namespace_member,
    )


def _tool_schema(tool: Any, attr: str) -> JsonSchema | None:
    schema = getattr(tool, attr, None)
    if schema is None:
        return None
    if isinstance(schema, Mapping):
        return dict(schema)
    return None


def _fastmcp_tags(tool: Any) -> set[str]:
    meta = getattr(tool, "meta", None)
    if not isinstance(meta, Mapping):
        return set()
    fastmcp_meta = meta.get("fastmcp")
    if not isinstance(fastmcp_meta, Mapping):
        return set()
    tags = fastmcp_meta.get("tags")
    if not isinstance(tags, list | tuple | set | frozenset):
        return set()
    return {str(tag) for tag in tags}


def _normalize_result(result: Any) -> Any:
    if getattr(result, "is_error", False):
        raise RuntimeError(_content_text(result) or "MCP tool returned an error")

    data = getattr(result, "data", None)
    if data is not None:
        return _to_python_value(data)

    structured = getattr(result, "structured_content", None)
    if structured is not None:
        if isinstance(structured, Mapping) and set(structured) == {"result"}:
            return _to_python_value(structured["result"])
        return _to_python_value(structured)

    return _content_text(result)


def _content_text(result: Any) -> str | None:
    content = getattr(result, "content", None)
    if not content:
        return None
    parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        parts.append(str(text if text is not None else block))
    return "\n".join(parts)


def _to_python_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        # fastmcp's client deserializes structured content against the
        # tool's output schema, and composed schemas ($ref/$defs/oneOf —
        # full JSON Schema 2020-12 in the 2026-07-28 spec) come back as
        # generated dataclasses, not pydantic models (#132 finding 4)
        return {
            field.name: _to_python_value(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): _to_python_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_to_python_value(item) for item in value]
    return value


def _python_alias(server_name: str, tool_name: str) -> str:
    return _python_identifier(f"{server_name}_{tool_name}", prefix="mcp")


def _python_identifier(raw: str, *, prefix: str) -> str:
    normalized = re.sub(r"[^0-9a-zA-Z_]+", "_", raw).strip("_").lower()
    normalized = re.sub(r"_+", "_", normalized)
    if not normalized:
        normalized = f"{prefix}_tool"
    if normalized[0].isdigit():
        normalized = f"{prefix}_{normalized}"
    return normalized
