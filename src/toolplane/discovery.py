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
            data.append({"not_found": list(missing)})
        return json.dumps(data, indent=2)

    if not capabilities:
        text = "No capabilities matched the query."
    elif detail == "detailed":
        text = "\n\n".join(_render_detailed(capability) for capability in capabilities)
    else:
        text = "\n".join(_render_brief(capability) for capability in capabilities)

    if missing:
        text += f"\n\nCapabilities not found: {', '.join(missing)}"
    return text


def _render_brief(capability: Capability) -> str:
    desc = f": {capability.description}" if capability.description else ""
    return f"- {capability.name}{desc}"


def _render_detailed(capability: Capability) -> str:
    lines = [f"### {capability.name}"]
    if capability.description:
        lines.extend(["", capability.description])
    lines.extend(["", *_schema_section(capability.parameters, "Parameters")])
    if capability.returns is not None:
        lines.extend(["", *_schema_section(capability.returns, "Returns")])
    return "\n".join(lines)


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
