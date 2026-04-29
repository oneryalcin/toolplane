"""Capability registry and dispatch."""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from .capabilities import Capability, capability_from_function
from .errors import CapabilityNotFoundError, DuplicateCapabilityError


def _ensure_awaitable(fn: Callable[..., Any]) -> Callable[..., Any]:
    if inspect.iscoroutinefunction(fn):
        return fn

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        return fn(*args, **kwargs)

    return wrapper


class CapabilityRegistry:
    def __init__(self) -> None:
        self._capabilities: dict[str, Capability] = {}

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
        if capability.name in self._capabilities:
            raise DuplicateCapabilityError(
                f"Capability already registered: {capability.name}"
            )
        self._capabilities[capability.name] = capability
        return capability

    def all(self) -> list[Capability]:
        return list(self._capabilities.values())

    def get(self, name: str) -> Capability:
        try:
            return self._capabilities[name]
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
            if not tag_filter or capability.tags & tag_filter
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
            capability = self._capabilities.get(name)
            if capability is None:
                missing.append(name)
            else:
                matched.append(capability)
        return matched, missing

    async def call(self, name: str, params: dict[str, Any] | None = None) -> Any:
        capability = self.get(name)
        fn = _ensure_awaitable(capability.callable)
        return await fn(**(params or {}))


def _tokenize(text: str) -> list[str]:
    return [token for token in re.split(r"[^a-z0-9_]+", text.lower()) if token]
