"""Ambient lazy CLI support for code-mode execution."""

from __future__ import annotations

import asyncio
import builtins
import json
import keyword
import os
from collections.abc import Mapping, Sequence
from typing import Any

from ..bridges.base import HostBridge
from ..capabilities import Capability
from ..errors import CapabilityNotFoundError
from ..registry import CapabilityRegistry
from .cli_to_py import normalize_cli_result


AMBIENT_CLI_CAPABILITY = "toolplane:cli/run"
RESERVED_CLI_NAMES = {"call_tool", "cli"}


class AmbientCliRunner:
    """Run CLI commands through cli-to-py, loading each binary lazily."""

    def __init__(self) -> None:
        self._apis: dict[str, Any] = {}
        self._parsed_subcommands: set[tuple[str, str]] = set()

    async def __call__(
        self,
        binary: str,
        subcommand: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        api = await self._api(binary)
        resolved_subcommand = _normalize_subcommand(subcommand)
        if resolved_subcommand is not None:
            await self._parse_subcommand(api, binary, resolved_subcommand)
        command = (
            api(resolved_subcommand, **dict(options or {}))
            if resolved_subcommand
            else api(**dict(options or {}))
        )
        return normalize_cli_result(await command)

    async def _api(self, binary: str) -> Any:
        if binary not in self._apis:
            try:
                from cli_to_py import convert
            except ImportError as exc:  # pragma: no cover - dependency is required
                raise ImportError(
                    "Ambient CLI support requires cli-to-py in the environment."
                ) from exc

            self._apis[binary] = await convert(binary, subcommands=False)
        return self._apis[binary]

    async def _parse_subcommand(self, api: Any, binary: str, subcommand: str) -> None:
        key = (binary, subcommand)
        if key in self._parsed_subcommands:
            return
        parser = getattr(api, "parse", None)
        if callable(parser):
            await parser(subcommand)
        self._parsed_subcommands.add(key)


def _normalize_subcommand(subcommand: str | None) -> str | None:
    if subcommand is None:
        return None
    try:
        from cli_to_py.case import snake_to_kebab
    except ImportError:  # pragma: no cover - dependency is required
        return subcommand
    return snake_to_kebab(subcommand)


def register_ambient_cli(registry: CapabilityRegistry) -> Capability:
    """Register Toolplane's hidden ambient CLI runner."""
    try:
        return registry.get(AMBIENT_CLI_CAPABILITY)
    except CapabilityNotFoundError:
        pass

    runner = AmbientCliRunner()
    capability = Capability(
        name=AMBIENT_CLI_CAPABILITY,
        callable=runner,
        description="Run a CLI command lazily through cli-to-py.",
        parameters={
            "type": "object",
            "properties": {
                "binary": {"type": "string"},
                "subcommand": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "options": {"type": "object"},
            },
            "required": ["binary"],
        },
        returns={
            "type": "object",
            "properties": {
                "stdout": {"type": "string"},
                "stderr": {"type": "string"},
                "exit_code": {"type": "integer"},
                "ok": {"type": "boolean"},
            },
            "required": ["stdout", "stderr", "exit_code", "ok"],
        },
        tags=frozenset({"toolplane", "cli"}),
        source="toolplane",
        hidden=True,
    )
    return registry.add(capability)


def discover_cli_names() -> tuple[str, ...]:
    """Return safe executable names from PATH without parsing their help output."""
    names: set[str] = set()
    for directory in os.get_exec_path():
        try:
            entries = os.scandir(directory)
        except OSError:
            continue
        with entries:
            for entry in entries:
                name = entry.name
                if _is_safe_cli_name(name) and _is_executable(entry.path):
                    names.add(name)
    return tuple(sorted(names))


def _is_executable(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def _is_safe_cli_name(name: str) -> bool:
    return (
        name.isidentifier()
        and not keyword.iskeyword(name)
        and not name.startswith("__")
        and name not in vars(builtins)
        and name not in RESERVED_CLI_NAMES
    )


def build_local_cli_namespace(
    bridge: HostBridge,
    names: Sequence[str],
    *,
    reserved: set[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    reserved_names = set(reserved or ())
    root = AmbientCliRoot(bridge)
    namespace: dict[str, Any] = {"cli": root}
    for name in names:
        if name not in reserved_names and _is_safe_cli_name(name):
            namespace[name] = AmbientCliBinary(bridge, name)
    return namespace


class AmbientCliRoot:
    def __init__(self, bridge: HostBridge) -> None:
        self._bridge = bridge

    def __call__(self, binary: str) -> AmbientCliBinary:
        return AmbientCliBinary(self._bridge, binary)

    def __getattr__(self, binary: str) -> AmbientCliBinary:
        if binary.startswith("_"):
            raise AttributeError(binary)
        return AmbientCliBinary(self._bridge, binary)


class AmbientCliBinary:
    def __init__(self, bridge: HostBridge, binary: str) -> None:
        self._bridge = bridge
        self._binary = binary

    def __call__(
        self,
        subcommand: str | None = None,
        /,
        **options: Any,
    ) -> AmbientCliCall:
        return AmbientCliCall(
            self._bridge,
            binary=self._binary,
            subcommand=subcommand,
            options=options,
        )

    def __getattr__(self, subcommand: str) -> Any:
        if subcommand.startswith("_"):
            raise AttributeError(subcommand)

        def dispatch(**options: Any) -> AmbientCliCall:
            return AmbientCliCall(
                self._bridge,
                binary=self._binary,
                subcommand=subcommand,
                options=options,
            )

        dispatch.__name__ = subcommand
        return dispatch


class AmbientCliCall:
    def __init__(
        self,
        bridge: HostBridge,
        *,
        binary: str,
        subcommand: str | None,
        options: Mapping[str, Any],
    ) -> None:
        self._bridge = bridge
        self._binary = binary
        self._subcommand = subcommand
        self._options = dict(options)
        self._task: asyncio.Task[Any] | None = None

    def _as_task(self) -> asyncio.Task[Any]:
        if self._task is None:
            self._task = asyncio.create_task(
                self._bridge.call_tool(
                    AMBIENT_CLI_CAPABILITY,
                    {
                        "binary": self._binary,
                        "subcommand": self._subcommand,
                        "options": self._options,
                    },
                )
            )
        return self._task

    def __await__(self) -> Any:
        return self._as_task().__await__()

    async def text(self) -> str:
        result = await self._as_task()
        return str(result.get("stdout", "")).strip()

    async def lines(self) -> list[str]:
        text = await self.text()
        return text.splitlines() if text else []

    async def json(self, **kwargs: Any) -> Any:
        result = await self._as_task()
        return json.loads(str(result.get("stdout", "")), **kwargs)


def render_pyodide_cli_namespace(
    names: Sequence[str],
    *,
    reserved: set[str] | frozenset[str] | None = None,
) -> str:
    reserved_names = set(reserved or ())
    top_level = [
        name
        for name in names
        if name not in reserved_names and _is_safe_cli_name(name)
    ]
    assignments = "\n".join(f"{name} = cli.{name}" for name in top_level)
    return f"""
class _ToolplaneCliCall:
    def __init__(self, binary, subcommand, options):
        self.binary = binary
        self.subcommand = subcommand
        self.options = dict(options)
        self._task = None

    def _as_task(self):
        import asyncio
        if self._task is None:
            self._task = asyncio.ensure_future(call_tool({AMBIENT_CLI_CAPABILITY!r}, {{
                "binary": self.binary,
                "subcommand": self.subcommand,
                "options": self.options,
            }}))
        return self._task

    def __await__(self):
        return self._as_task().__await__()

    async def text(self):
        result = await self._as_task()
        return str(result.get("stdout", "")).strip()

    async def lines(self):
        text = await self.text()
        return text.splitlines() if text else []

    async def json(self, **kwargs):
        import json
        result = await self._as_task()
        return json.loads(str(result.get("stdout", "")), **kwargs)


class _ToolplaneCliBinary:
    def __init__(self, binary):
        self.binary = binary

    def __call__(self, subcommand=None, /, **options):
        return _ToolplaneCliCall(self.binary, subcommand, options)

    def __getattr__(self, subcommand):
        if subcommand.startswith("_"):
            raise AttributeError(subcommand)
        def dispatch(**options):
            return _ToolplaneCliCall(self.binary, subcommand, options)
        return dispatch


class _ToolplaneCliRoot:
    def __call__(self, binary):
        return _ToolplaneCliBinary(binary)

    def __getattr__(self, binary):
        if binary.startswith("_"):
            raise AttributeError(binary)
        return _ToolplaneCliBinary(binary)


cli = _ToolplaneCliRoot()
{assignments}
"""
