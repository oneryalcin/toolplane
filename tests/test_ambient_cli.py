from __future__ import annotations

import asyncio
import shutil

import pytest

from toolplane import CapabilityRegistry, Toolplane


def run(coro):
    return asyncio.run(coro)


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_ambient_cli_top_level_proxy_runs_git_without_registration() -> None:
    runtime = Toolplane()

    result = run(
        runtime.execute(
            """
version = await git.version()
return version["stdout"]
"""
        )
    )

    assert result.ok, result.error
    assert result.value.startswith("git version")


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_ambient_cli_root_runs_git_without_top_level_name() -> None:
    runtime = Toolplane()

    result = run(
        runtime.execute(
            """
version = await cli.git.version()
return version["stdout"]
"""
        )
    )

    assert result.ok, result.error
    assert result.value.startswith("git version")


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_ambient_cli_root_call_supports_non_identifier_binaries() -> None:
    runtime = Toolplane()

    result = run(
        runtime.execute(
            """
version = await cli("git").version()
return version["stdout"]
"""
        )
    )

    assert result.ok, result.error
    assert result.value.startswith("git version")


def test_ambient_cli_missing_binary_surfaces_cli_to_py_error() -> None:
    runtime = Toolplane()

    result = run(
        runtime.execute(
            """
return await cli("definitely_missing_toolplane_binary").version()
"""
        )
    )

    assert not result.ok
    assert result.error is not None
    assert "Binary not found" in result.error.message


def test_ambient_cli_runner_is_hidden_from_discovery() -> None:
    runtime = Toolplane()

    tools = run(runtime.list_tools(detail="full"))

    assert "toolplane:cli/run" not in tools


def test_ambient_cli_can_be_disabled() -> None:
    runtime = Toolplane(ambient_cli=False)

    result = run(runtime.execute("return cli"))

    assert not result.ok
    assert result.error is not None
    assert result.error.type == "NameError"


def test_ambient_cli_allowlist_blocks_root_call_before_cli_to_py() -> None:
    runtime = Toolplane(ambient_cli_allowlist=["git"])

    result = run(
        runtime.execute(
            """
return await cli("definitely_missing_toolplane_binary").version()
"""
        )
    )

    assert not result.ok
    assert result.error is not None
    assert result.error.type == "CliPolicyError"
    assert "definitely_missing_toolplane_binary" in result.error.message


def test_ambient_cli_allowlist_blocks_attribute_access() -> None:
    runtime = Toolplane(ambient_cli_allowlist=["git"])

    result = run(
        runtime.execute(
            """
return await cli.definitely_missing_toolplane_binary.version()
"""
        )
    )

    assert not result.ok
    assert result.error is not None
    assert result.error.type == "CliPolicyError"
    assert "definitely_missing_toolplane_binary" in result.error.message


def test_ambient_cli_allowlist_blocks_direct_hidden_runner_call() -> None:
    runtime = Toolplane(ambient_cli_allowlist=["git"])

    result = run(
        runtime.execute(
            """
return await call_tool(
    "toolplane:cli/run",
    {"binary": "curl", "options": {"version": True}},
)
"""
        )
    )

    assert not result.ok
    assert result.error is not None
    assert result.error.type == "CliPolicyError"
    assert "curl" in result.error.message


def test_ambient_cli_allowlist_is_runtime_scoped_with_shared_registry() -> None:
    registry = CapabilityRegistry()
    git_runtime = Toolplane(registry=registry, ambient_cli_allowlist=["git"])
    missing_runtime = Toolplane(
        registry=registry,
        ambient_cli_allowlist=["definitely_missing_toolplane_binary"],
    )

    blocked = run(
        git_runtime.execute(
            """
return await call_tool(
    "toolplane:cli/run",
    {"binary": "definitely_missing_toolplane_binary"},
)
"""
        )
    )
    allowed_then_missing = run(
        missing_runtime.execute(
            """
return await call_tool(
    "toolplane:cli/run",
    {"binary": "definitely_missing_toolplane_binary"},
)
"""
        )
    )

    assert blocked.error is not None
    assert blocked.error.type == "CliPolicyError"
    assert allowed_then_missing.error is not None
    assert allowed_then_missing.error.type != "CliPolicyError"
    assert "Binary not found" in allowed_then_missing.error.message
