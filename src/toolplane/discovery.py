"""Discovery renderers for capabilities."""

from __future__ import annotations

import json
import keyword
from collections.abc import Sequence
from typing import Any, Literal

from .capabilities import Capability

DetailLevel = Literal["brief", "detailed", "full"]


def render_capabilities(
    capabilities: Sequence[Capability],
    *,
    detail: DetailLevel = "brief",
    missing: Sequence[str] = (),
    reserved: frozenset[str] = frozenset(),
) -> str:
    if detail == "full":
        data: list[dict[str, Any]] = [
            capability.to_schema() for capability in capabilities
        ]
        if missing:
            # dead ends signpost on every detail level, JSON included
            data.append({"not_found": list(missing), "hint": _MISSING_HINT})
        return json.dumps(data, indent=2)

    if not capabilities:
        text = "No capabilities matched the query."
    elif detail == "detailed":
        text = "\n\n".join(
            _render_detailed(capability, reserved) for capability in capabilities
        )
    else:
        text = "\n".join(
            _render_brief(capability, reserved) for capability in capabilities
        )

    if missing:
        text += f"\n\nCapabilities not found: {', '.join(missing)}. {_MISSING_HINT}"
    return text


_MISSING_HINT = (
    "Names must be canonical (as returned by search_capabilities) — search "
    "with an empty query to list them all, or read the toolplane://namespace "
    "resource."
)


def domain_hint(
    capabilities: Sequence[Capability],
    *,
    max_chars: int = 1500,
    reserved: frozenset[str] = frozenset(),
) -> str:
    """Domain vocabulary for the facade tool descriptions.

    Clients with deferred tool loading index MCP tools by name and
    description and load them via keyword search. An agent's first search
    is for the DOMAIN ("order status"), not for "toolplane" — and a facade
    that only talks about itself is invisible to that query, costing an
    extra model request per run (#115, transcript-measured). This renders
    what is BEHIND the facade, bounded and deterministic.

    Each entry is the capability's EXACT call shape, not its leaf name: a
    bare name reads as the binding, the agent skips search and guesses
    `await get_order(...)`, and the NameError retry costs back the request
    the hint saved (measured on the first attempt of this fix). With the
    real shape in the description, going straight to execute_code is
    correct rather than a trap.
    """
    visible = sorted(
        (c for c in capabilities if not c.hidden), key=lambda c: c.name
    )
    if not visible:
        return ""
    # every domain's name up front, unconditionally: at scale the shape
    # budget truncates, and a domain whose vocabulary is entirely absent is
    # invisible to the first keyword search — measured at M=15, where an
    # agent concluded no order tool existed and answered WRONG rather than
    # searching again
    domains = sorted({_domain(capability) for capability in visible})
    prefix = (
        f"Serves capabilities for: {', '.join(domains)}. "
        "Call them in execute_code exactly as shown: "
    )
    # round-robin across domains, not alphabetical fill: every domain gets
    # its first shape into the budget before any domain gets its second
    by_domain: dict[str, list[Capability]] = {}
    for capability in visible:
        by_domain.setdefault(_domain(capability), []).append(capability)
    interleaved: list[Capability] = []
    queues = [by_domain[domain] for domain in domains]
    while queues:
        queues = [q for q in queues if q]
        interleaved.extend(q.pop(0) for q in queues)
    entries = []
    used = len(prefix)
    for capability in interleaved:
        shape = call_shape(capability, reserved=reserved)
        desc = capability.description.strip().rstrip(".")
        entry = f"`{shape}` ({desc})" if desc else f"`{shape}`"
        # +2 for the "; " separator; truncation is per-capability so the
        # hint never ends mid-sentence
        if used + len(entry) + 2 > max_chars:
            remaining = len(visible) - len(entries)
            entries.append(
                f"plus {remaining} more — search_capabilities lists all"
            )
            break
        entries.append(entry)
        used += len(entry) + 2
    return prefix + "; ".join(entries) + "."


def _domain(capability: Capability) -> str:
    """The capability's domain word: 'payments' from 'mcp:payments/refund'."""
    name = capability.name
    if "/" in name:
        return name.rsplit("/", 1)[0].rsplit(":", 1)[-1]
    return name.rsplit(":", 1)[-1]


def _render_brief(
    capability: Capability, reserved: frozenset[str] = frozenset()
) -> str:
    shape = call_shape(capability, reserved=reserved)
    desc = f" — {capability.description}" if capability.description else ""
    return f"- `{shape}`{desc} [{capability.name}]"


def _render_detailed(
    capability: Capability, reserved: frozenset[str] = frozenset()
) -> str:
    lines = [f"### {capability.name}"]
    if capability.description:
        lines.extend(["", capability.description])
    lines.extend(
        ["", f"**Call**: `{call_shape(capability, reserved=reserved)}`"]
    )
    lines.extend(["", *_schema_section(capability.parameters, "Parameters")])
    if capability.returns is not None:
        lines.extend(["", *_schema_section(capability.returns, "Returns")])
    return "\n".join(lines)


def call_shape(
    capability: Capability, *, reserved: frozenset[str] = frozenset()
) -> str:
    """The exact awaitable Python call for a capability.

    This is what lets one search turn replace the
    search -> get_capability_schemas -> namespace-manifest ceremony for
    straightforward tasks: the binding name and the keyword arguments (the
    two things an agent otherwise reads two more surfaces for) travel with
    every hit. Keywords only — positional calls fail on the monty backend.

    A rendered shape must actually resolve in the sandbox — and to the
    capability, not to something else. Flat-binding resolution mirrors
    registry.callable_namespace (the capability name when it is a safe
    identifier, else the first safe alias), minus names the runtime's
    backend reserves for itself (``reserved``): a sessioned monty backend
    installs reset_session first, so a capability with that name is
    shadowed and must not be advertised as flat-callable. Whenever the
    flat form cannot be rendered faithfully — no unshadowed safe binding,
    a parameter name that is not a valid Python keyword argument (``from``,
    ``user-id``), or a parameter surface the schema does not describe —
    the shape falls back to ``await call_tool("canonical", {...})``, which
    is valid for every capability on every backend.
    """
    from .registry import _is_safe_python_name

    binding = None
    if _is_safe_python_name(capability.name) and capability.name not in reserved:
        binding = capability.name
    else:
        for alias in sorted(capability.aliases):
            if _is_safe_python_name(alias) and alias not in reserved:
                binding = alias
                break

    schema = (
        capability.parameters if isinstance(capability.parameters, dict) else None
    )
    properties = schema.get("properties") if schema is not None else None
    if not isinstance(properties, dict):
        # schema does not describe the parameters — advertising `await x()`
        # would affirmatively claim zero arguments
        return f'await call_tool("{capability.name}", {{...}})'
    required = set(schema.get("required", []))

    def _sorted(items: list[str]) -> list[str]:
        # required first, so a truncated read still sees the mandatory part
        return sorted(items, key=lambda item: item.endswith("?"))

    kwargs_ok = binding is not None and all(
        name.isidentifier() and not keyword.iskeyword(name) for name in properties
    )
    if not kwargs_ok:
        pairs = _sorted(
            [
                f'"{name}": <{_schema_type(field)}>'
                f"{'' if name in required else '?'}"
                for name, field in properties.items()
            ]
        )
        return f'await call_tool("{capability.name}", {{{", ".join(pairs)}}})'
    args = _sorted(
        [
            f"{name}=<{_schema_type(field)}>{'' if name in required else '?'}"
            for name, field in properties.items()
        ]
    )
    return f"await {binding}({', '.join(args)})"


def _schema_section(schema: dict[str, Any] | None, title: str) -> list[str]:
    lines = [f"**{title}**"]
    if not isinstance(schema, dict):
        lines.append("- `value` (any)")
        return lines

    properties = schema.get("properties")
    required = set(schema.get("required", []))
    if properties is None:
        lines.append(f"- `value` ({_schema_type(schema)})")
        return lines
    if not properties:
        lines.append("*(no parameters)*")
        return lines

    for name, field in properties.items():
        marker = ", required" if name in required else ""
        lines.append(f"- `{name}` ({_schema_type(field)}{marker})")
    return lines


def _schema_type(schema: Any) -> str:
    if not isinstance(schema, dict) or not schema:
        return "any"
    schema_type = schema.get("type")
    if schema_type == "array":
        return f"{_schema_type(schema.get('items'))}[]"
    if isinstance(schema_type, str):
        return schema_type
    if "anyOf" in schema:
        parts = [_schema_type(item) for item in schema["anyOf"]]
        if "null" in parts and len(parts) == 2:
            return f"{next(part for part in parts if part != 'null')}?"
        return " | ".join(parts)
    if "properties" in schema:
        return "object"
    return "any"
