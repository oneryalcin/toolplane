"""Ambient lazy CLI support for code-mode execution."""

from __future__ import annotations

import asyncio
import builtins
import json
import keyword
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from ..bridges.base import HostBridge
from ..capabilities import Capability
from ..errors import CapabilityNotFoundError, CliPolicyError
from ..registry import CapabilityRegistry
from .cli_to_py import normalize_cli_result


AMBIENT_CLI_CAPABILITY = "toolplane:cli/run"
RESERVED_CLI_NAMES = {"call_tool", "cli"}

# one shape lesson for every CLI failure path: a wrong guess must teach the
# right call, not leak the internals it tripped over
CLI_SHAPE_GUIDANCE = (
    "CLI call shape: subcommand as the first positional argument, flags as "
    "keyword arguments — e.g. await git('log', oneline=True, max_count=3) "
    "or await cli_run('git', 'log', {'oneline': True, 'max_count': 3})"
)


class AmbientCliPolicy:
    """Session-scoped CLI allowlist with optional human escalation.

    The configured allowlist stays the durable policy (toolplane.toml);
    grants made through the escalation handler live only on this object,
    so they die with the process. Escalation is an enhancement, never a
    new failure mode: any handler outcome other than an explicit grant —
    decline, cancel, unsupported client, handler crash — produces exactly
    the refusal that having no handler produces.
    """

    def __init__(
        self,
        allowed_binaries: Sequence[str] | set[str] | frozenset[str] | None = None,
        *,
        audit_log: Any | None = None,
    ) -> None:
        self.configured = (
            frozenset(allowed_binaries) if allowed_binaries is not None else None
        )
        self._audit_log = audit_log
        # async (binary: str) -> bool; installed per-request by the MCP
        # facade because the elicitation needs that request's client context
        self.escalation_handler: (
            Callable[[str], Awaitable[bool]] | None
        ) = None
        # set once by surfaces that can escalate, read by the namespace
        # manifest — the live handler is transient, this flag is not
        self.escalation_available = False
        self._session_grants: set[str] = set()
        self._asked: set[str] = set()
        self._inflight: dict[str, asyncio.Task[bool]] = {}

    @property
    def restricted(self) -> bool:
        return self.configured is not None

    def effective_allowlist(self) -> frozenset[str] | None:
        if self.configured is None:
            return None
        return frozenset(self.configured | self._session_grants)

    def is_allowed(self, binary: str) -> bool:
        effective = self.effective_allowlist()
        return effective is None or binary in effective

    async def ensure_allowed(self, binary: str) -> None:
        if self.is_allowed(binary):
            return
        handler = self.escalation_handler
        if handler is not None and binary not in self._asked:
            # once per (session, binary): the human's answer is cached
            # either way, so a denied binary never re-prompts
            self._asked.add(binary)
            # the handler runs as its own task so the run that asked can
            # abandon it (cancel_pending_escalations): backend timeouts do
            # not reliably cancel detached dispatch coroutines, and a human
            # answer that lands after its run died must not mutate policy
            task = asyncio.ensure_future(handler(binary))
            self._inflight[binary] = task
            granted = False
            try:
                granted = bool(await task)
            except asyncio.CancelledError:
                # abandoned question, not a decision: forget it so a retry
                # re-prompts the human, whose earlier form is now stale
                self._asked.discard(binary)
                self._emit_escalation(binary, "abandoned")
                if not task.cancelled():
                    task.cancel()
                    raise  # our own caller is being cancelled
            except Exception:
                granted = False
                self._emit_escalation(binary, "error")
            else:
                self._emit_escalation(
                    binary, "granted" if granted else "declined"
                )
            finally:
                self._inflight.pop(binary, None)
            if granted:
                self._session_grants.add(binary)
                return
        _ensure_binary_allowed(binary, self.effective_allowlist())

    def _emit_escalation(self, binary: str, outcome: str) -> None:
        if self._audit_log is not None:
            self._audit_log.emit("escalation", binary=binary, outcome=outcome)

    def cancel_pending_escalations(self) -> tuple[str, ...]:
        """Abandon escalations whose requesting run has ended.

        Returns the binaries that were still waiting on a human. Their
        `_asked` marks are cleared by the unwinding tasks, so a later run
        asks again instead of silently inheriting a stale answer.
        """
        pending = tuple(sorted(self._inflight))
        for task in self._inflight.values():
            task.cancel()
        return pending


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
        if options is not None and not isinstance(options, Mapping):
            raise TypeError(
                f"CLI options must be a dict of flags, got "
                f"{type(options).__name__!r}. {CLI_SHAPE_GUIDANCE}"
            )
        resolved_subcommand = _normalize_subcommand(subcommand)
        api = await self._api(binary)
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
    if not isinstance(subcommand, str):
        raise TypeError(
            f"CLI subcommand must be a string, got "
            f"{type(subcommand).__name__!r}. {CLI_SHAPE_GUIDANCE}"
        )
    if any(char.isspace() for char in subcommand):
        raise ValueError(
            f"CLI subcommand {subcommand!r} contains whitespace — it is "
            f"passed as a single argv token, so flags do not belong in it. "
            f"{CLI_SHAPE_GUIDANCE}"
        )
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
                if is_safe_cli_name(name) and _is_executable(entry.path):
                    names.add(name)
    return tuple(sorted(names))


def _is_executable(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def is_safe_cli_name(name: str) -> bool:
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
    allowed_binaries: set[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    reserved_names = set(reserved or ())
    allowed = frozenset(allowed_binaries) if allowed_binaries is not None else None
    root = AmbientCliRoot(bridge, allowed_binaries=allowed)
    namespace: dict[str, Any] = {"cli": root}
    for name in names:
        if name not in reserved_names and is_safe_cli_name(name):
            namespace[name] = AmbientCliBinary(bridge, name, allowed_binaries=allowed)
    return namespace


class AmbientCliRoot:
    def __init__(
        self,
        bridge: HostBridge,
        *,
        allowed_binaries: frozenset[str] | None = None,
    ) -> None:
        self._bridge = bridge
        self._allowed_binaries = allowed_binaries

    def __call__(self, binary: str) -> AmbientCliBinary:
        _ensure_binary_allowed(binary, self._allowed_binaries)
        return AmbientCliBinary(
            self._bridge,
            binary,
            allowed_binaries=self._allowed_binaries,
        )

    def __getattr__(self, binary: str) -> AmbientCliBinary:
        if binary.startswith("_"):
            raise AttributeError(binary)
        _ensure_binary_allowed(binary, self._allowed_binaries)
        return AmbientCliBinary(
            self._bridge,
            binary,
            allowed_binaries=self._allowed_binaries,
        )


class AmbientCliBinary:
    def __init__(
        self,
        bridge: HostBridge,
        binary: str,
        *,
        allowed_binaries: frozenset[str] | None = None,
    ) -> None:
        _ensure_binary_allowed(binary, allowed_binaries)
        self._bridge = bridge
        self._binary = binary

    def __call__(
        self,
        subcommand: str | None = None,
        /,
        *args: Any,
        **options: Any,
    ) -> AmbientCliCall:
        if args:
            raise TypeError(
                f"{self._binary}() takes at most one positional argument "
                f"(the subcommand); flags are keyword arguments. "
                f"{CLI_SHAPE_GUIDANCE}"
            )
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
    allowed_binaries: set[str] | frozenset[str] | None = None,
) -> str:
    reserved_names = set(reserved or ())
    allowed_json = (
        "None" if allowed_binaries is None else json.dumps(sorted(allowed_binaries))
    )
    cli_shape_guidance = CLI_SHAPE_GUIDANCE
    top_level = [
        name
        for name in names
        if name not in reserved_names and is_safe_cli_name(name)
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
        _toolplane_ensure_cli_allowed(binary)
        self.binary = binary

    def __call__(self, subcommand=None, /, *args, **options):
        if args:
            raise TypeError(
                self.binary + "() takes at most one positional argument "
                "(the subcommand); flags are keyword arguments. "
                + {cli_shape_guidance!r}
            )
        return _ToolplaneCliCall(self.binary, subcommand, options)

    def __getattr__(self, subcommand):
        if subcommand.startswith("_"):
            raise AttributeError(subcommand)
        def dispatch(**options):
            return _ToolplaneCliCall(self.binary, subcommand, options)
        return dispatch


class _ToolplaneCliRoot:
    def __call__(self, binary):
        _toolplane_ensure_cli_allowed(binary)
        return _ToolplaneCliBinary(binary)

    def __getattr__(self, binary):
        if binary.startswith("_"):
            raise AttributeError(binary)
        _toolplane_ensure_cli_allowed(binary)
        return _ToolplaneCliBinary(binary)


_TOOLPLANE_ALLOWED_CLI_BINARIES = {allowed_json}

def _toolplane_ensure_cli_allowed(binary):
    if (
        _TOOLPLANE_ALLOWED_CLI_BINARIES is not None
        and binary not in _TOOLPLANE_ALLOWED_CLI_BINARIES
    ):
        allowed = ", ".join(sorted(_TOOLPLANE_ALLOWED_CLI_BINARIES)) or "none"
        # PermissionError, matching the host-side CliPolicyError mapping, so
        # the same catch pattern works on the in-sandbox pre-check path too
        raise PermissionError(
            "CLI binary is not allowed by Toolplane policy: " + binary
            + ". Allowed binaries: " + allowed + "."
        )

cli = _ToolplaneCliRoot()
{assignments}
"""


def _ensure_binary_allowed(
    binary: str,
    allowed_binaries: frozenset[str] | None,
) -> None:
    if allowed_binaries is not None and binary not in allowed_binaries:
        allowed = ", ".join(sorted(allowed_binaries)) or "none"
        raise CliPolicyError(
            f"CLI binary is not allowed by Toolplane policy: {binary}. "
            f"Allowed binaries: {allowed}."
        )
