"""Contracts for persistent monty sessions (#84).

Everything here runs the real MontyRepl path — the failure modes that
matter (timeout poison, memory caps, rollback) live in the native layer
and cannot be faked.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from toolplane import Toolplane
from toolplane.backends import MontyBackend
from toolplane.config import load_toolplane_config
from toolplane.mcp_facade import resolve_serve_config


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


def _session_runtime(**backend_kwargs: Any) -> Toolplane:
    return Toolplane(
        backends=[MontyBackend(session=True, **backend_kwargs)],
        default_backend="monty",
        ambient_cli=False,
    )


def _register_slow(runtime: Toolplane, seconds: float) -> None:
    async def slow_op() -> str:
        await asyncio.sleep(seconds)
        return "late"

    runtime.register(slow_op, description="sleeps for the test")


def test_variables_and_functions_persist_across_runs() -> None:
    async def case() -> None:
        runtime = _session_runtime()

        first = await runtime.execute(
            "data = {'rows': [1, 2, 3]}\n"
            "def total():\n"
            "    return sum(data['rows'])"
        )
        assert first.error is None

        second = await runtime.execute("return total()")
        assert second.error is None
        assert second.value == 6

    _run(case())


def test_top_level_return_keeps_its_contract() -> None:
    # the one-shot snippet convention (`return` a value) must carry over
    # unchanged: monty's REPL accepts top-level return and keeps state
    async def case() -> None:
        runtime = _session_runtime()

        result = await runtime.execute("x = 7\nreturn {'x': x}")
        assert result.error is None
        assert result.value == {"x": 7}

        persisted = await runtime.execute("return x")
        assert persisted.value == 7

    _run(case())


def test_failed_run_keeps_the_session() -> None:
    async def case() -> None:
        runtime = _session_runtime()
        await runtime.execute("kept = 'still-here'")

        failed = await runtime.execute("1 / 0")
        assert failed.error is not None
        assert failed.error.type == "ZeroDivisionError"

        after = await runtime.execute("return kept")
        assert after.value == "still-here"

    _run(case())


def test_timeout_rolls_back_the_namespace() -> None:
    # the run mutates state with a NEW interned string before blocking —
    # the exact shape that permanently poisons an unprotected session
    # (pydantic/monty#533); rollback must leave the pre-run state and a
    # usable session
    async def case() -> None:
        runtime = _session_runtime(timeout_seconds=0.4)
        _register_slow(runtime, seconds=5)
        await runtime.execute("n = 1\nitems = ['keep']")

        timed_out = await runtime.execute(
            "n = 99\nitems.append('side-effect')\nawait slow_op()"
        )
        assert timed_out.error is not None
        assert timed_out.error.type == "TimeoutError"

        after = await runtime.execute("return (n, items)")
        assert after.error is None
        assert after.value == (1, ["keep"])

    _run(case())


def test_timeout_message_scopes_the_rollback_honestly() -> None:
    # rollback is VM-state-only: host-side effects stand, and the error
    # text must say both halves rather than promise a transaction
    async def case() -> None:
        runtime = _session_runtime(timeout_seconds=0.4)
        _register_slow(runtime, seconds=5)

        timed_out = await runtime.execute("x = 1\nawait slow_op()")
        message = timed_out.error.message
        assert "rolled back" in message
        assert "stand" in message

    _run(case())


def test_host_effects_survive_a_rolled_back_run() -> None:
    async def case() -> None:
        runtime = _session_runtime(timeout_seconds=0.4)
        _register_slow(runtime, seconds=5)

        timed_out = await runtime.execute(
            "h = await save_result({'k': 'v'})\nprint(h)\nawait slow_op()"
        )
        assert timed_out.error is not None
        # the sandbox variable `h` was rolled back, but the store write
        # stands — and stdout printed before the timeout is delivered
        handle = timed_out.stdout.strip()
        assert handle
        assert runtime.result_store.load(handle) == {"k": "v"}

    _run(case())


def test_reset_session_clears_after_the_run_completes() -> None:
    async def case() -> None:
        runtime = _session_runtime()
        await runtime.execute("x = 1")

        reset = await runtime.execute(
            "marker = await reset_session()\nreturn x"
        )
        # the reset is pending, not immediate: the rest of the run saw x
        assert reset.value == 1

        after = await runtime.execute("return x")
        assert after.error is not None
        assert after.error.type == "NameError"

    _run(case())


def test_memory_cap_fires_and_the_session_survives() -> None:
    # empirical guard: ResourceLimits silently ignores unknown keys
    # (pydantic/monty#534), so the cap must be proven to fire, not
    # trusted from construction
    async def case() -> None:
        runtime = _session_runtime(session_max_memory_bytes=10_000_000)
        await runtime.execute("a = list(range(100))")

        capped = await runtime.execute("big = list(range(5_000_000))")
        assert capped.error is not None
        assert capped.error.type == "MemoryError"
        assert "reset_session" in capped.error.message

        after = await runtime.execute("return len(a)")
        assert after.value == 100

    _run(case())


def test_overlapping_runs_serialize_instead_of_erroring() -> None:
    # MontyRepl rejects concurrent feeds; the backend lock must queue
    # them so both callers succeed
    async def case() -> None:
        runtime = _session_runtime(timeout_seconds=10)
        _register_slow(runtime, seconds=0.3)

        first, second = await asyncio.gather(
            runtime.execute("a = await slow_op()\nreturn a"),
            runtime.execute("return 'quick'"),
        )
        assert first.error is None and first.value == "late"
        assert second.error is None and second.value == "quick"

    _run(case())


def test_inputs_arrive_and_persist() -> None:
    async def case() -> None:
        runtime = _session_runtime()

        seeded = await runtime.execute("return rows * 2", inputs={"rows": 21})
        assert seeded.value == 42

        later = await runtime.execute("return rows")
        assert later.value == 21

    _run(case())


def test_result_store_still_works_inside_a_session() -> None:
    async def case() -> None:
        runtime = _session_runtime()

        result = await runtime.execute(
            "h = await save_result([1, 2])\nreturn await load_result(h)"
        )
        assert result.error is None
        assert result.value == [1, 2]

    _run(case())


def test_sessions_off_keeps_runs_isolated() -> None:
    async def case() -> None:
        runtime = Toolplane(
            default_backend="monty", ambient_cli=False, sessions=False
        )
        await runtime.execute("x = 1")

        second = await runtime.execute("return x")
        assert second.error is not None
        assert second.error.type == "NameError"

    _run(case())


def test_multi_client_transports_disable_sessions() -> None:
    config = load_toolplane_config({})
    assert config.session.enabled

    resolved = resolve_serve_config(config, "http")
    assert not resolved.session.enabled

    stdio = resolve_serve_config(config, "stdio")
    assert stdio.session.enabled


def test_manifest_documents_sessions_only_when_live() -> None:
    with_sessions = _session_runtime().describe_namespace()
    assert "## Session" in with_sessions
    assert "reset_session" in with_sessions

    without = Toolplane(
        default_backend="monty", ambient_cli=False, sessions=False
    ).describe_namespace()
    assert "## Session" not in without
