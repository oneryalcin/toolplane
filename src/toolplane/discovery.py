"""Discovery renderers for capabilities."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Literal

from .capabilities import Capability

DetailLevel = Literal["brief", "detailed", "full"]


def render_capabilities(
    capabilities: Sequence[Capability],
    *,
    detail: DetailLevel = "brief",
    missing: Sequence[str] = (),
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
        text = "\n\n".join(_render_detailed(capability) for capability in capabilities)
    else:
        text = "\n".join(_render_brief(capability) for capability in capabilities)

    if missing:
        text += f"\n\nCapabilities not found: {', '.join(missing)}. {_MISSING_HINT}"
    return text


_MISSING_HINT = (
    "Names must be canonical (as returned by search_capabilities) — search "
    "with an empty query to list them all, or read the toolplane://namespace "
    "resource."
)


def _render_brief(capability: Capability) -> str:
    desc = f" — {capability.description}" if capability.description else ""
    shape = call_shape(capability)
    if shape is None:
        desc = f": {capability.description}" if capability.description else ""
        return f"- {capability.name}{desc}"
    return f"- `{shape}`{desc} [{capability.name}]"


def _render_detailed(capability: Capability) -> str:
    lines = [f"### {capability.name}"]
    if capability.description:
        lines.extend(["", capability.description])
    shape = call_shape(capability)
    if shape is not None:
        lines.extend(["", f"**Call**: `{shape}`"])
    lines.extend(["", *_schema_section(capability.parameters, "Parameters")])
    if capability.returns is not None:
        lines.extend(["", *_schema_section(capability.returns, "Returns")])
    return "\n".join(lines)


def call_shape(capability: Capability) -> str | None:
    """The exact awaitable Python call for a capability's flat binding.

    This is what lets one search turn replace the
    search -> get_capability_schemas -> namespace-manifest ceremony for
    straightforward tasks: the binding name and the keyword arguments (the
    two things an agent otherwise reads two more surfaces for) travel with
    every hit. Keywords only — positional calls fail on the monty backend.

    Binding resolution mirrors registry.callable_namespace: the capability
    name itself when it is a safe identifier, else the first safe alias —
    a shape rendered here must actually resolve in the sandbox.
    """
    from .registry import _is_safe_python_name

    if _is_safe_python_name(capability.name):
        binding = capability.name
    else:
        safe_aliases = sorted(
            alias for alias in capability.aliases if _is_safe_python_name(alias)
        )
        if not safe_aliases:
            return None
        binding = safe_aliases[0]
    schema = capability.parameters if isinstance(capability.parameters, dict) else {}
    properties = schema.get("properties") or {}
    required = set(schema.get("required", []))
    args = [
        f"{name}=<{_schema_type(field)}>{'' if name in required else '?'}"
        for name, field in properties.items()
    ]
    # required first, so a truncated read still sees the mandatory part
    args.sort(key=lambda a: a.endswith("?"))
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
