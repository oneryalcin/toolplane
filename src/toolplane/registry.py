"""Capability registry and dispatch."""

from __future__ import annotations

import inspect
import keyword
import re
from builtins import __dict__ as _builtins
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from .capabilities import Capability, capability_from_function
from .errors import CapabilityNotFoundError, DuplicateCapabilityError


def _ensure_awaitable(fn: Callable[..., Any]) -> Callable[..., Any]:
    if inspect.iscoroutinefunction(fn):
        return fn

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        value = fn(*args, **kwargs)
        if inspect.isawaitable(value):
            return await value
        return value

    return wrapper


class CapabilityRegistry:
    def __init__(self) -> None:
        self._capabilities: dict[str, Capability] = {}
        self._aliases: dict[str, str] = {}

    def register(
        self,
        fn: Callable[..., Any],
        *,
        name: str | None = None,
        description: str | None = None,
        tags: set[str] | frozenset[str] | None = None,
        source: str = "python",
    ) -> Capability:
        capability = capability_from_function(
            fn,
            name=name,
            description=description,
            tags=tags,
            source=source,
        )
        self._add_capability(capability)
        return capability

    def add(self, capability: Capability) -> Capability:
        """Add a prebuilt capability from an adapter."""
        self._add_capability(capability)
        return capability

    def _add_capability(self, capability: Capability) -> None:
        if capability.name in self._capabilities or capability.name in self._aliases:
            raise DuplicateCapabilityError(
                f"Capability already registered: {capability.name}"
            )
        for alias in capability.aliases:
            _validate_alias(alias)
            if alias == capability.name:
                continue
            if alias in self._capabilities or alias in self._aliases:
                raise DuplicateCapabilityError(
                    f"Capability alias already registered: {alias}"
                )
        self._capabilities[capability.name] = capability
        for alias in capability.aliases:
            if alias != capability.name:
                self._aliases[alias] = capability.name

    def all(self) -> list[Capability]:
        return [
            capability
            for capability in self._capabilities.values()
            if not capability.hidden
        ]

    def get(self, name: str) -> Capability:
        canonical_name = self._aliases.get(name, name)
        try:
            return self._capabilities[canonical_name]
        except KeyError as exc:
            raise CapabilityNotFoundError(f"Unknown capability: {name}") from exc

    def search(
        self,
        query: str,
        *,
        tags: Iterable[str] | None = None,
        limit: int | None = None,
    ) -> list[Capability]:
        tag_filter = set(tags or ())
        candidates = [
            capability
            for capability in self._capabilities.values()
            if not capability.hidden
            and (not tag_filter or capability.tags & tag_filter)
        ]
        tokens = _tokenize(query)
        if not tokens:
            return candidates[:limit]

        scored: list[tuple[int, str, Capability]] = []
        for capability in candidates:
            text = capability.searchable_text.lower()
            score = sum(text.count(token) for token in tokens)
            if score:
                scored.append((score, capability.name, capability))
        scored.sort(key=lambda item: (-item[0], item[1]))
        results = [capability for _, _, capability in scored]
        return results[:limit]

    def schemas(self, names: Sequence[str]) -> tuple[list[Capability], list[str]]:
        matched: list[Capability] = []
        missing: list[str] = []
        for name in names:
            capability = self._capabilities.get(self._aliases.get(name, name))
            if capability is None:
                missing.append(name)
            else:
                matched.append(capability)
        return matched, missing

    def callable_namespace(self) -> dict[str, str]:
        """Return safe Python callable names mapped to canonical capability names."""
        namespace: dict[str, str] = {}
        for capability in self._capabilities.values():
            if capability.hidden:
                continue
            if _is_safe_python_name(capability.name):
                namespace[capability.name] = capability.name
            for alias in capability.aliases:
                if _is_safe_python_name(alias):
                    namespace[alias] = capability.name
        return namespace

    async def call(self, name: str, params: dict[str, Any] | None = None) -> Any:
        capability = self.get(name)
        fn = _ensure_awaitable(capability.callable)
        return await fn(**(params or {}))


def _tokenize(text: str) -> list[str]:
    return [token for token in re.split(r"[^a-z0-9_]+", text.lower()) if token]


def _validate_alias(alias: str) -> None:
    if not _is_safe_python_name(alias):
        raise ValueError(f"Capability alias is not a safe Python name: {alias!r}")


def _is_safe_python_name(name: str) -> bool:
    return (
        name.isidentifier()
        and not keyword.iskeyword(name)
        and name not in _builtins
        and not name.startswith("__")
        and name not in {"call_tool"}
    )
