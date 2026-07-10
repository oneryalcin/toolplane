from __future__ import annotations

import asyncio
from typing import Any

import pytest

from toolplane import Toolplane
from toolplane.backends import MontyBackend
from toolplane.errors import BackendCapabilityError, NamespaceCollisionError


def run(coro):
    return asyncio.run(coro)


class _StubBridge:
    """Minimal bridge standing in for InProcessBridge."""

    def __init__(self, tools: dict[str, Any] | None = None) -> None:
        self.tools = tools or {}
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    async def call_tool(self, name: str, params: dict[str, Any] | None = None) -> Any:
        self.calls.append((name, params))
        handler = self.tools.get(name)
        if handler is None:
            raise RuntimeError(f"unknown tool: {name}")
        return await handler(**(params or {}))


async def _multiply(x: int, y: int) -> int:
    return x * y


def test_monty_runs_flat_alias_call_tool_inputs_and_stdout() -> None:
    backend = MontyBackend()
    bridge = _StubBridge({"mcp.math.multiply": _multiply})

    result = run(
        backend.run(
            """
print("composing")
a = await math_multiply(x=6, y=7)
b = await call_tool("mcp.math.multiply", {"x": 2, "y": 3})
return {"a": a, "b": b, "bias": bias}
""",
            bridge=bridge,
            namespace={"math_multiply": "mcp.math.multiply"},
            inputs={"bias": 100},
        )
    )

    assert result.error is None, result.error
    assert result.value == {"a": 42, "b": 6, "bias": 100}
    assert result.stdout == "composing\n"
    assert result.backend == "monty"


def test_monty_reports_snippet_error_with_original_type() -> None:
    backend = MontyBackend()

    result = run(
        backend.run('raise ValueError("boom")', bridge=_StubBridge())
    )

    assert result.error is not None
    assert result.error.type == "ValueError"
    assert result.error.message == "boom"
    # the frame must point at the snippet's own line, not a toolplane
    # internal; the REPL names snippet frames "<python-input-N>"
    # (0.0.19's feed_run ignores script_name for frame filenames)
    assert "line 1" in result.error.traceback
    assert "toolplane" not in result.error.traceback


def test_monty_tool_error_is_catchable_in_snippet() -> None:
    async def explode() -> None:
        raise RuntimeError("tool exploded")

    backend = MontyBackend()
    bridge = _StubBridge({"broken.tool": explode})

    result = run(
        backend.run(
            """
try:
    await broken()
except Exception as exc:
    return f"caught: {exc}"
""",
            bridge=bridge,
            namespace={"broken": "broken.tool"},
        )
    )

    assert result.error is None, result.error
    assert result.value == "caught: tool exploded"


def test_monty_times_out_hot_loop() -> None:
    backend = MontyBackend(timeout_seconds=0.5)

    result = run(
        backend.run("while True:\n    pass", bridge=_StubBridge())
    )

    assert result.error is not None
    assert result.error.type == "TimeoutError"


def test_monty_times_out_hanging_tool_call() -> None:
    async def hang() -> None:
        await asyncio.sleep(30)

    backend = MontyBackend(timeout_seconds=0.5)
    bridge = _StubBridge({"slow.tool": hang})

    result = run(
        backend.run(
            'return await call_tool("slow.tool", {})',
            bridge=bridge,
        )
    )

    assert result.error is not None
    assert result.error.type == "TimeoutError"
    assert result.duration_ms < 5000


def test_monty_rejects_packages() -> None:
    backend = MontyBackend()

    with pytest.raises(BackendCapabilityError, match="pyodide-deno"):
        run(backend.run("return 1", bridge=_StubBridge(), packages=["pandas"]))


def test_monty_binds_allowlisted_cli_binaries_as_flat_functions() -> None:
    async def fake_cli(binary: str, subcommand=None, options=None) -> dict:
        return {"ok": True, "binary": binary}

    backend = MontyBackend()
    bridge = _StubBridge({"toolplane:cli/run": fake_cli})

    result = run(
        backend.run(
            'return await git("status", short=True)',
            bridge=bridge,
            ambient_cli=True,
            ambient_cli_names=("git",),
        )
    )

    assert result.error is None, result.error
    assert result.value == {"ok": True, "binary": "git"}
    assert bridge.calls == [
        (
            "toolplane:cli/run",
            {"binary": "git", "subcommand": "status", "options": {"short": True}},
        )
    ]


def test_monty_cli_run_dispatches_non_identifier_binaries() -> None:
    async def fake_cli(binary: str, subcommand=None, options=None) -> dict:
        return {"ok": True}

    backend = MontyBackend()
    bridge = _StubBridge({"toolplane:cli/run": fake_cli})

    result = run(
        backend.run(
            'return await cli_run("docker-compose", "up", {"detach": True})',
            bridge=bridge,
            ambient_cli=True,
            ambient_cli_names=(),
        )
    )

    assert result.error is None, result.error
    assert bridge.calls == [
        (
            "toolplane:cli/run",
            {
                "binary": "docker-compose",
                "subcommand": "up",
                "options": {"detach": True},
            },
        )
    ]


def test_monty_capability_namespace_shadows_cli_binary_name() -> None:
    backend = MontyBackend()
    bridge = _StubBridge({"mcp.git.tool": _multiply})

    result = run(
        backend.run(
            "return await git(x=2, y=3)",
            bridge=bridge,
            namespace={"git": "mcp.git.tool"},
            ambient_cli=True,
            ambient_cli_names=("git",),
        )
    )

    assert result.error is None, result.error
    assert result.value == 6
    assert bridge.calls == [("mcp.git.tool", {"x": 2, "y": 3})]


def test_monty_cli_calls_blocked_by_host_side_policy() -> None:
    from toolplane import Toolplane

    async def exercise():
        runtime = await Toolplane.from_config(
            {"cli": {"mode": "allowlist", "allow": ["git"]}}
        )
        return await runtime.execute('return await cli_run("curl")')

    result = run(exercise())

    assert result.error is not None
    assert "CLI binary is not allowed by Toolplane policy: curl" in result.error.message
    # the rejection must teach the allowlist, not just say no
    assert "Allowed binaries: git" in result.error.message


def test_monty_does_not_bind_unlisted_cli_names() -> None:
    backend = MontyBackend()

    result = run(
        backend.run(
            "return await curl()",
            bridge=_StubBridge(),
            ambient_cli=True,
            ambient_cli_names=("git",),
        )
    )

    assert result.error is not None
    assert result.error.type == "NameError"


def test_monty_rejects_input_collisions() -> None:
    backend = MontyBackend()

    with pytest.raises(NamespaceCollisionError, match="call_tool"):
        run(
            backend.run(
                "return 1",
                bridge=_StubBridge(),
                inputs={"call_tool": 1},
            )
        )


def test_runtime_default_backends_include_monty() -> None:
    runtime = Toolplane(ambient_cli=False)

    assert "monty" in runtime.backends

    result = run(runtime.execute("return 40 + 2", backend="monty"))

    assert result.error is None, result.error
    assert result.value == 42


def test_monty_unawaited_call_fails_instead_of_returning_repr() -> None:
    runtime = Toolplane(ambient_cli=False)

    result = run(
        runtime.execute("h = save_result({'v': 1})\nreturn h", backend="monty")
    )

    assert result.error is not None
    assert result.error.type == "UnawaitedToolCallError"
    assert "await" in result.error.message


def test_monty_unawaited_call_detected_when_nested() -> None:
    runtime = Toolplane(ambient_cli=False)

    result = run(
        runtime.execute(
            "return {'handle': save_result({'v': 1}), 'n': 5}",
            backend="monty",
        )
    )

    assert result.error is not None
    assert result.error.type == "UnawaitedToolCallError"


def test_monty_awaited_call_still_succeeds() -> None:
    runtime = Toolplane(ambient_cli=False)

    result = run(
        runtime.execute(
            "h = await save_result({'v': 1})\nreturn h",
            backend="monty",
        )
    )

    assert result.error is None, result.error
    assert result.value.startswith("res_")


def test_monty_printed_unawaited_call_fails_instead_of_success() -> None:
    runtime = Toolplane(ambient_cli=False)

    result = run(
        runtime.execute(
            "print(save_result({'v': 1}))",
            backend="monty",
        )
    )

    assert result.error is not None
    assert result.error.type == "UnawaitedToolCallError"


def test_monty_discarded_unawaited_call_fails_at_preflight() -> None:
    runtime = Toolplane(ambient_cli=False)

    result = run(
        runtime.execute(
            "save_result({'v': 1})\nreturn 1",
            backend="monty",
        )
    )

    assert result.error is not None
    assert result.error.type == "UnawaitedToolCallError"
    assert "save_result" in result.error.message
    assert "line 1" in result.error.message


def test_monty_fstring_unawaited_call_fails_at_preflight() -> None:
    runtime = Toolplane(ambient_cli=False)

    result = run(
        runtime.execute(
            "return f\"handle={save_result({'v': 1})}\"",
            backend="monty",
        )
    )

    assert result.error is not None
    assert result.error.type == "UnawaitedToolCallError"
    assert "f-string" in result.error.message


def test_monty_deferred_await_is_not_a_false_positive() -> None:
    runtime = Toolplane(ambient_cli=False)

    result = run(
        runtime.execute(
            "pending = save_result({'v': 1})\nreturn await pending",
            backend="monty",
        )
    )

    assert result.error is None, result.error
    assert result.value.startswith("res_")


def test_monty_flat_cli_binding_teaches_shape_on_extra_positionals() -> None:
    backend = MontyBackend()
    bridge = _StubBridge()

    result = run(
        backend.run(
            "return await git('log', '--oneline', '-3')",
            bridge=bridge,
            ambient_cli=True,
            ambient_cli_names=("git",),
        )
    )

    # previously: "TypeError: _make_cli_binary.<locals>.run_binary() takes
    # from 0 to 1 positional arguments but 3 were given"
    assert result.error is not None
    assert "takes at most one positional argument" in result.error.message
    assert "await git('log', oneline=True, max_count=3)" in result.error.message
    assert "<locals>" not in result.error.message


def test_monty_cli_run_accepts_flag_kwargs() -> None:
    async def fake_cli(**params: Any) -> dict[str, Any]:
        return {"stdout": "", "stderr": "", "exit_code": 0, "ok": True}

    backend = MontyBackend()
    bridge = _StubBridge({"toolplane:cli/run": fake_cli})

    result = run(
        backend.run(
            "return await cli_run('git', 'log', oneline=True, max_count=3)",
            bridge=bridge,
            ambient_cli=True,
            ambient_cli_names=("git",),
        )
    )

    # one convention with the flat bindings: kwargs are flags
    assert result.error is None, result.error
    assert bridge.calls == [
        (
            "toolplane:cli/run",
            {
                "binary": "git",
                "subcommand": "log",
                "options": {"oneline": True, "max_count": 3},
            },
        )
    ]


def test_monty_cli_run_rejects_conflicting_flag_sources() -> None:
    backend = MontyBackend()
    bridge = _StubBridge()

    result = run(
        backend.run(
            "return await cli_run('git', 'log', {'oneline': True}, oneline=False)",
            bridge=bridge,
            ambient_cli=True,
            ambient_cli_names=("git",),
        )
    )

    assert result.error is not None
    assert "both in the options dict and as keyword arguments" in result.error.message
    assert "oneline" in result.error.message


def test_pool_is_created_once_under_concurrent_first_runs() -> None:
    # concurrent first runs raced _ensure_pool's check-create-enter-assign
    # and leaked all pools but the last (found by both port reviewers)
    import toolplane.backends.monty as monty_module

    created = []
    original = monty_module.AsyncMonty

    def counting_async_monty(*args: Any, **kwargs: Any) -> Any:
        pool = original(*args, **kwargs)
        created.append(pool)
        return pool

    monty_module.AsyncMonty = counting_async_monty  # type: ignore[assignment]
    try:
        backend = MontyBackend()
        bridge = _StubBridge()

        async def three_first_runs() -> Any:
            return await asyncio.gather(
                backend.run("1 + 1", bridge=bridge),
                backend.run("2 + 2", bridge=bridge),
                backend.run("3 + 3", bridge=bridge),
            )

        results = run(three_first_runs())
    finally:
        monty_module.AsyncMonty = original

    assert [r.error for r in results] == [None, None, None]
    assert [r.value for r in results] == [2, 4, 6]
    assert len(created) == 1
