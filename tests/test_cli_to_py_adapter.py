from __future__ import annotations

import asyncio
import shutil
from typing import Any

import pytest

from toolplane import PyodideDenoBackend, Toolplane

pytest.importorskip("cli_to_py")
from cli_to_py import convert  # noqa: E402
from cli_to_py.schema import (  # noqa: E402
    CliSchema,
    CommandResult,
    ParsedCommand,
    ParsedFlag,
    ParsedSubcommand,
)


def run(coro):
    return asyncio.run(coro)


class FakeCliApi:
    binary_name = "git"

    def __init__(self) -> None:
        self.calls: list[tuple[str | None, dict[str, Any]]] = []
        self.schema = CliSchema(
            binary_name="git",
            command=ParsedCommand(
                name="git",
                description="Git root command.",
                subcommands=[
                    ParsedSubcommand(
                        name="status",
                        aliases=[],
                        description="Show the working tree status.",
                        flags=[
                            ParsedFlag(
                                long_name="short",
                                short_name="s",
                                description="Give the output in the short format.",
                                takes_value=False,
                                value_name=None,
                                default_value=None,
                                is_negated=False,
                                is_required=False,
                                choices=None,
                                uses_equals=False,
                                is_global=False,
                            ),
                            ParsedFlag(
                                long_name="branch",
                                short_name="b",
                                description="Show branch information.",
                                takes_value=False,
                                value_name=None,
                                default_value=None,
                                is_negated=False,
                                is_required=False,
                                choices=None,
                                uses_equals=False,
                                is_global=False,
                            ),
                        ],
                    )
                ],
            ),
        )

    def _find_subcommand(self, name: str) -> ParsedSubcommand | None:
        return next(
            (
                sub
                for sub in self.schema.command.subcommands
                if sub.name == name
            ),
            None,
        )

    async def __call__(
        self,
        subcommand: str | None = None,
        **options: Any,
    ) -> CommandResult:
        self.calls.append((subcommand, options))
        return CommandResult(stdout="ok\n", stderr="", exit_code=0)


def test_register_cli_preserves_metadata_and_normalizes_result() -> None:
    runtime = Toolplane()
    api = FakeCliApi()

    capability = runtime.register_cli(
        "git_status",
        api,
        subcommand="status",
        tags={"git", "cli"},
    )

    assert capability.name == "git_status"
    assert capability.source == "cli-to-py"
    assert capability.tags == frozenset({"git", "cli"})
    assert capability.description == "Show the working tree status."
    assert capability.parameters["properties"]["short"]["type"] == "boolean"
    assert capability.returns is not None
    assert capability.returns["properties"]["exit_code"]["type"] == "integer"

    result = run(runtime.call_tool("git_status", {"short": True}))

    assert api.calls == [("status", {"short": True})]
    assert result == {
        "stdout": "ok\n",
        "stderr": "",
        "exit_code": 0,
        "ok": True,
    }


def test_register_cli_accepts_callable_wrapper_with_explicit_parameters() -> None:
    runtime = Toolplane()

    def generated_wrapper(**options: Any) -> CommandResult:
        """Run a generated CLI wrapper."""
        return CommandResult(stdout=str(options["message"]), stderr="", exit_code=0)

    capability = runtime.register_cli(
        "echo_message",
        generated_wrapper,
        parameters={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
            "additionalProperties": False,
        },
    )

    result = run(runtime.call_tool("echo_message", {"message": "hello"}))

    assert capability.description == "Run a generated CLI wrapper."
    assert capability.parameters["required"] == ["message"]
    assert result["stdout"] == "hello"
    assert result["ok"] is True


@pytest.mark.skipif(shutil.which("python3") is None, reason="python3 is not installed")
def test_cli_to_py_real_command_runs_through_local_execution() -> None:
    async def exercise() -> str:
        runtime = Toolplane()
        python = await convert("python3", subcommands=False)
        runtime.register_cli(
            "python_version",
            python,
            description="Return the Python interpreter version.",
            tags={"python", "cli"},
        )

        result = await runtime.execute(
            """
version = await call_tool("python_version", {"version": True})
return version["stdout"] + version["stderr"]
"""
        )
        assert result.ok, result.error
        return result.value

    output = run(exercise())

    assert "Python" in output


@pytest.mark.skipif(shutil.which("python3") is None, reason="python3 is not installed")
@pytest.mark.skipif(shutil.which("deno") is None, reason="Deno is not installed")
def test_cli_to_py_command_runs_through_pyodide_host_bridge() -> None:
    async def exercise() -> bool:
        runtime = Toolplane(backends=[PyodideDenoBackend(timeout_seconds=120)])
        python = await convert("python3", subcommands=False)
        runtime.register_cli(
            "python_version",
            python,
            description="Return the Python interpreter version.",
            tags={"python", "cli"},
        )

        result = await runtime.execute(
            """
version = await call_tool("python_version", {"version": True})
return "Python" in (version["stdout"] + version["stderr"])
""",
            backend="pyodide-deno",
        )
        assert result.ok, result.error
        return bool(result.value)

    assert run(exercise()) is True
