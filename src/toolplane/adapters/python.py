"""Adapters for host Python helper namespaces."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

from ..capabilities import Capability, capability_from_function
from ..registry import CapabilityRegistry


def register_python_namespace(
    registry: CapabilityRegistry,
    namespace: str,
    tools: Mapping[str, Callable[..., Any]],
    *,
    tags: set[str] | frozenset[str] | None = None,
    source: str = "python",
) -> list[Capability]:
    """Register host Python callables under one scoped code-mode namespace."""
    capabilities: list[Capability] = []
    namespace_tags = {"python", namespace, *(tags or ())}
    for member, fn in tools.items():
        capability = capability_from_function(
            fn,
            name=f"py:{namespace}/{member}",
            tags=namespace_tags,
            source=f"{source}:{namespace}",
            aliases={_python_alias(namespace, member)},
            namespace=namespace,
            namespace_member=member,
        )
        capabilities.append(registry.add(capability))
    return capabilities


def _python_alias(namespace: str, member: str) -> str:
    raw = f"{namespace}_{member}"
    normalized = re.sub(r"[^0-9a-zA-Z_]+", "_", raw).strip("_").lower()
    normalized = re.sub(r"_+", "_", normalized)
    if not normalized:
        normalized = "python_tool"
    if normalized[0].isdigit():
        normalized = f"py_{normalized}"
    return normalized
