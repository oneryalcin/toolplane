"""Adapter for exposing cli-to-py commands as Toolplane capabilities."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict

from ..capabilities import Capability, JsonSchema
from ..registry import CapabilityRegistry


class CliCommandResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    stdout: str
    stderr: str
    exit_code: int
    ok: bool


def register_cli(
    registry: CapabilityRegistry,
    name: str,
    command: Any,
    *,
    subcommand: str | None = None,
    description: str | None = None,
    parameters: JsonSchema | None = None,
    tags: set[str] | frozenset[str] | None = None,
    source: str = "cli-to-py",
) -> Capability:
    """Register a cli-to-py API object or command wrapper as a capability."""
    callable_ = _make_callable(command, subcommand=subcommand)
    capability = Capability(
        name=name,
        callable=callable_,
        description=description
        if description is not None
        else _description(command, subcommand=subcommand),
        parameters=parameters
        if parameters is not None
        else _parameters(command, subcommand=subcommand),
        returns=_command_result_schema(),
        tags=frozenset(tags or ()),
        source=source,
    )
    return registry.add(capability)


def _make_callable(command: Any, *, subcommand: str | None) -> Callable[..., Any]:
    async def call_cli(**options: Any) -> dict[str, Any]:
        if _is_cli_api(command):
            value = command(subcommand, **options) if subcommand else command(**options)
        else:
            if subcommand is not None:
                raise TypeError("subcommand is only valid for cli-to-py API objects")
            value = command(**options)
        if inspect.isawaitable(value):
            value = await value
        return _normalize_result(value)

    call_cli.__name__ = _callable_name(command, subcommand=subcommand)
    return call_cli


def _is_cli_api(value: Any) -> bool:
    return hasattr(value, "schema") and hasattr(value, "binary_name")


def _callable_name(command: Any, *, subcommand: str | None) -> str:
    if _is_cli_api(command):
        binary = str(getattr(command, "binary_name", "cli")).replace("-", "_")
        suffix = (subcommand or "run").replace("-", "_")
        return f"{binary}_{suffix}"
    return getattr(command, "__name__", "cli_command")


def _description(command: Any, *, subcommand: str | None) -> str:
    parsed = _parsed_command(command, subcommand=subcommand)
    if parsed is not None and getattr(parsed, "description", ""):
        return str(parsed.description)

    if _is_cli_api(command):
        binary = getattr(command, "binary_name", "cli")
        return f"Run `{binary}{f' {subcommand}' if subcommand else ''}`."

    doc = inspect.getdoc(command) if callable(command) else None
    if doc:
        return doc.splitlines()[0].strip()
    return "Run a CLI command."


def _parameters(command: Any, *, subcommand: str | None) -> JsonSchema:
    parsed = _parsed_command(command, subcommand=subcommand)
    if parsed is None:
        return {
            "type": "object",
            "additionalProperties": True,
        }

    properties: dict[str, Any] = {}
    required: list[str] = []

    for flag in getattr(parsed, "flags", None) or ():
        key = _flag_key(flag)
        schema = _flag_schema(flag)
        properties[key] = schema
        if getattr(flag, "is_required", False):
            required.append(key)

    positional_args = getattr(parsed, "positional_args", None) or ()
    if positional_args:
        properties["_"] = {
            "type": "array",
            "items": {"type": "string"},
            "description": "Positional arguments.",
        }

    schema: JsonSchema = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


def _parsed_command(command: Any, *, subcommand: str | None) -> Any | None:
    if not _is_cli_api(command):
        return None
    schema = getattr(command, "schema", None)
    root = getattr(schema, "command", None)
    if root is None:
        return None
    if subcommand is None:
        return root

    finder = getattr(command, "_find_subcommand", None)
    if callable(finder):
        found = finder(subcommand)
        if found is not None:
            return found

    for candidate in getattr(root, "subcommands", None) or ():
        if getattr(candidate, "name", None) == subcommand:
            return candidate
    return None


def _flag_key(flag: Any) -> str:
    long_name = getattr(flag, "long_name", "")
    if long_name:
        return str(long_name).replace("-", "_")
    short_name = str(getattr(flag, "short_name", "")).lstrip("-")
    return short_name.replace("-", "_")


def _flag_schema(flag: Any) -> JsonSchema:
    takes_value = bool(getattr(flag, "takes_value", False))
    schema: JsonSchema = {"type": "string"} if takes_value else {"type": "boolean"}

    description = getattr(flag, "description", "")
    if description:
        schema["description"] = str(description)

    choices = getattr(flag, "choices", None)
    if choices:
        schema["enum"] = list(choices)

    default = getattr(flag, "default_value", None)
    if default is not None:
        schema["default"] = default

    return schema


def _normalize_result(value: Any) -> dict[str, Any]:
    return normalize_cli_result(value)


def normalize_cli_result(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        data = dict(value)
        if {"stdout", "stderr", "exit_code"} <= data.keys():
            exit_code = int(data["exit_code"])
            return CliCommandResult(
                stdout=str(data["stdout"]),
                stderr=str(data["stderr"]),
                exit_code=exit_code,
                ok=bool(data.get("ok", exit_code == 0)),
            ).model_dump()

    if all(hasattr(value, attr) for attr in ("stdout", "stderr", "exit_code")):
        exit_code = int(getattr(value, "exit_code"))
        ok = value.ok() if callable(getattr(value, "ok", None)) else exit_code == 0
        return CliCommandResult(
            stdout=str(getattr(value, "stdout")),
            stderr=str(getattr(value, "stderr")),
            exit_code=exit_code,
            ok=bool(ok),
        ).model_dump()

    raise TypeError(
        "cli-to-py adapter expected a command result with stdout, stderr, and exit_code"
    )


def _command_result_schema() -> JsonSchema:
    return {
        "type": "object",
        "properties": {
            "stdout": {"type": "string"},
            "stderr": {"type": "string"},
            "exit_code": {"type": "integer"},
            "ok": {"type": "boolean"},
        },
        "required": ["stdout", "stderr", "exit_code", "ok"],
        "additionalProperties": False,
    }
