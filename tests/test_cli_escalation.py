"""Elicitation-based CLI allowlist escalation (issue #57).

Contract under test: a blocked binary asks the human once per (session,
binary); a grant is session-scoped and never persisted; every non-grant
outcome — decline, cancel, unsupported client, handler crash — produces
exactly the refusal that exists without escalation.
"""

from __future__ import annotations

import asyncio
import shutil

import pytest

from toolplane import Toolplane
from toolplane.adapters.ambient_cli import AMBIENT_CLI_CAPABILITY, AmbientCliPolicy
from toolplane.capabilities import Capability
from toolplane.errors import CliPolicyError
from toolplane.mcp_facade import build_mcp_facade
from toolplane.registry import CapabilityRegistry


def run(coro):
    return asyncio.run(coro)


REFUSAL = (
    "CLI binary is not allowed by Toolplane policy: curl. "
    "Allowed binaries: git."
)


def _runtime_with_fake_cli(allowlist=("git",), backends=None):
    """Runtime whose toolplane:cli/run never spawns a real binary."""
    registry = CapabilityRegistry()
    spawned: list[str] = []

    async def fake_cli(binary, subcommand=None, options=None):
        spawned.append(binary)
        return {"stdout": binary, "stderr": "", "exit_code": 0, "ok": True}

    registry.add(
        Capability(
            name=AMBIENT_CLI_CAPABILITY,
            callable=fake_cli,
            description="fake",
            parameters={"type": "object", "properties": {}},
            returns={"type": "object"},
            tags=frozenset({"toolplane", "cli"}),
            source="toolplane",
            hidden=True,
        )
    )
    runtime = Toolplane(
        registry=registry,
        ambient_cli=True,
        ambient_cli_allowlist=allowlist,
        backends=backends,
    )
    return runtime, spawned


# --- policy object contract -------------------------------------------------


def test_grant_is_session_scoped_and_visible_in_effective_allowlist() -> None:
    policy = AmbientCliPolicy(("git",))

    async def grant(binary: str) -> bool:
        return True

    policy.escalation_handler = grant
    run(policy.ensure_allowed("curl"))

    assert policy.effective_allowlist() == frozenset({"git", "curl"})
    # durable config is untouched: the grant dies with this object
    assert policy.configured == frozenset({"git"})


def test_decline_refuses_with_exactly_the_no_escalation_message() -> None:
    policy = AmbientCliPolicy(("git",))

    async def decline(binary: str) -> bool:
        return False

    policy.escalation_handler = decline

    with pytest.raises(CliPolicyError) as excinfo:
        run(policy.ensure_allowed("curl"))

    assert str(excinfo.value) == REFUSAL


def test_handler_crash_fails_closed_to_the_same_refusal() -> None:
    policy = AmbientCliPolicy(("git",))

    async def broken(binary: str) -> bool:
        raise RuntimeError("client exploded mid-elicitation")

    policy.escalation_handler = broken

    with pytest.raises(CliPolicyError) as excinfo:
        run(policy.ensure_allowed("curl"))

    assert str(excinfo.value) == REFUSAL


def test_asks_once_per_session_and_binary() -> None:
    policy = AmbientCliPolicy(("git",))
    asked: list[str] = []

    async def decline(binary: str) -> bool:
        asked.append(binary)
        return False

    policy.escalation_handler = decline

    async def exercise():
        for _ in range(3):
            with pytest.raises(CliPolicyError):
                await policy.ensure_allowed("curl")
        with pytest.raises(CliPolicyError):
            await policy.ensure_allowed("wget")

    run(exercise())

    assert asked == ["curl", "wget"]


def test_no_handler_means_todays_behavior() -> None:
    policy = AmbientCliPolicy(("git",))

    with pytest.raises(CliPolicyError) as excinfo:
        run(policy.ensure_allowed("curl"))

    assert str(excinfo.value) == REFUSAL


def test_unrestricted_policy_never_escalates() -> None:
    policy = AmbientCliPolicy(None)
    asked: list[str] = []

    async def handler(binary: str) -> bool:
        asked.append(binary)
        return True

    policy.escalation_handler = handler
    run(policy.ensure_allowed("anything"))

    assert asked == []


# --- in-sandbox behavior across backends -------------------------------------


@pytest.mark.parametrize("backend", ["monty", "local_unsafe"])
def test_granted_binary_runs_and_gains_a_flat_binding_next_run(
    backend: str,
) -> None:
    runtime, spawned = _runtime_with_fake_cli()

    async def grant(binary: str) -> bool:
        return True

    runtime.cli_policy.escalation_handler = grant

    async def exercise():
        first = await runtime.execute(
            'return await cli_run("curl")'
            if backend == "monty"
            else "return await cli.curl()",
            backend=backend,
        )
        # the grant persists: later runs bind curl as a flat function
        second = await runtime.execute("return await curl()", backend=backend)
        return first, second

    first, second = run(exercise())

    assert first.error is None, first.error
    assert second.error is None, second.error
    assert spawned == ["curl", "curl"]
    assert "curl" in runtime.describe_namespace()


@pytest.mark.parametrize("backend", ["monty", "local_unsafe"])
def test_declined_binary_is_refused_and_catchable_as_permissionerror(
    backend: str,
) -> None:
    runtime, spawned = _runtime_with_fake_cli()

    async def decline(binary: str) -> bool:
        return False

    runtime.cli_policy.escalation_handler = decline
    call = (
        'await cli_run("curl")' if backend == "monty" else "await cli.curl()"
    )
    code = "\n".join(
        [
            "try:",
            f"    {call}",
            "except PermissionError as exc:",
            '    return {"caught": True, "msg": str(exc)}',
        ]
    )

    result = run(runtime.execute(code, backend=backend))

    assert result.error is None, result.error
    assert result.value["caught"] is True
    assert REFUSAL in result.value["msg"]
    assert spawned == []


@pytest.mark.skipif(shutil.which("deno") is None, reason="Deno is not installed")
def test_pyodide_escalation_grant_and_decline() -> None:
    runtime, spawned = _runtime_with_fake_cli()
    decisions = iter([True, False])

    async def handler(binary: str) -> bool:
        return next(decisions)

    runtime.cli_policy.escalation_handler = handler
    code = "\n".join(
        [
            "granted = await cli.curl()",
            "try:",
            "    await cli.wget()",
            "except PermissionError as exc:",
            '    return {"granted_ok": granted["ok"], "refused": str(exc)}',
        ]
    )

    result = run(runtime.execute(code, backend="pyodide-deno"))

    assert result.error is None, result.error
    assert result.value["granted_ok"] is True
    assert "not allowed by Toolplane policy: wget" in result.value["refused"]
    assert spawned == ["curl"]


# --- escalations are run-scoped (driver findings on #71) ---------------------


def test_run_timeout_abandons_escalation_and_teaches_retry() -> None:
    """Driver-found on #71: monty's timeout kills the run but the detached
    dispatch keeps the human prompt alive, and a late answer silently
    mutated session policy. Now the run's end cancels pending escalations,
    the late answer is discarded, and the timeout error says what to do."""
    from toolplane.backends import MontyBackend

    runtime, spawned = _runtime_with_fake_cli(
        backends=[MontyBackend(timeout_seconds=0.3)]
    )
    gate = asyncio.Event()
    outcomes: list[str] = []

    async def human_still_reading(binary: str) -> bool:
        try:
            await gate.wait()
        except asyncio.CancelledError:
            outcomes.append("cancelled")
            raise
        outcomes.append("answered")
        return True

    runtime.cli_policy.escalation_handler = human_still_reading

    async def exercise():
        result = await runtime.execute(
            'return await cli_run("curl")', backend="monty"
        )
        await asyncio.sleep(0)  # let the abandoned task unwind
        gate.set()  # the human answers "allow" on the now-stale form
        await asyncio.sleep(0)
        return result

    result = run(exercise())

    assert result.error is not None
    assert result.error.type == "TimeoutError"
    assert "waiting for a human decision on: curl" in result.error.message
    assert "execute again to re-prompt" in result.error.message
    # the stale answer must not have granted anything
    assert runtime.cli_policy.effective_allowlist() == frozenset({"git"})
    assert outcomes == ["cancelled"]
    assert spawned == []


def test_retry_after_abandoned_escalation_reprompts() -> None:
    from toolplane.backends import MontyBackend

    runtime, spawned = _runtime_with_fake_cli(
        backends=[MontyBackend(timeout_seconds=0.3)]
    )
    asked: list[str] = []

    async def too_slow(binary: str) -> bool:
        asked.append(binary)
        await asyncio.Event().wait()
        return True

    async def prompt_answered(binary: str) -> bool:
        asked.append(binary)
        return True

    async def exercise():
        runtime.cli_policy.escalation_handler = too_slow
        first = await runtime.execute(
            'return await cli_run("curl")', backend="monty"
        )
        # the abandoned question was forgotten, so the retry asks again
        runtime.cli_policy.escalation_handler = prompt_answered
        second = await runtime.execute(
            'return await cli_run("curl")', backend="monty"
        )
        return first, second

    first, second = run(exercise())

    assert first.error is not None and first.error.type == "TimeoutError"
    assert second.error is None, second.error
    assert asked == ["curl", "curl"]
    assert spawned == ["curl"]


# --- facade wiring: real MCP elicitation round-trip ---------------------------


def _facade_client(runtime, **client_kwargs):
    from fastmcp import Client

    return Client(build_mcp_facade(runtime), **client_kwargs)


# fastmcp >=3.2 client contract: an elicitation handler must answer with a
# dict (or None) matching the requested schema. The facade's
# response_type=["allow", "deny"] wraps the scalar as {"value": ...}, so a
# bare "allow" string now raises client-side and reaches the server as
# INTERNAL_ERROR — indistinguishable from a refusal (#133).


def test_facade_elicits_and_grants_over_mcp() -> None:
    runtime, spawned = _runtime_with_fake_cli()
    prompts: list[str] = []

    async def allow(message, response_type, params, context):
        prompts.append(message)
        return {"value": "allow"}

    async def exercise():
        async with _facade_client(runtime, elicitation_handler=allow) as client:
            result = await client.call_tool(
                "execute_code",
                {"code": 'return await cli_run("curl")', "backend": "monty"},
            )
            return result.data

    data = run(exercise())

    assert data["error"] is None, data["error"]
    assert spawned == ["curl"]
    assert len(prompts) == 1
    # the prompt must name the binary and the standing policy
    assert "curl" in prompts[0]
    assert "git" in prompts[0]
    # the handler is per-request: nothing lingers after the call
    assert runtime.cli_policy.escalation_handler is None


@pytest.mark.skipif(shutil.which("deno") is None, reason="Deno is not installed")
def test_facade_elicitation_reaches_pyodide_dispatch() -> None:
    """Pyodide dispatches from the RPC callback thread, outside the MCP
    request's contextvars — ctx.elicit fails closed there unless the facade
    re-seats the captured request context (found empirically; this is the
    regression test for that wiring)."""
    runtime, spawned = _runtime_with_fake_cli()

    async def allow(message, response_type, params, context):
        return {"value": "allow"}

    async def exercise():
        async with _facade_client(runtime, elicitation_handler=allow) as client:
            result = await client.call_tool(
                "execute_code",
                {"code": "return await cli.curl()", "backend": "pyodide-deno"},
            )
            return result.data

    data = run(exercise())

    assert data["error"] is None, data["error"]
    assert spawned == ["curl"]


def test_facade_answer_other_than_allow_refuses() -> None:
    runtime, spawned = _runtime_with_fake_cli()

    async def deny(message, response_type, params, context):
        return {"value": "deny"}

    async def exercise():
        async with _facade_client(runtime, elicitation_handler=deny) as client:
            result = await client.call_tool(
                "execute_code",
                {"code": 'return await cli_run("curl")', "backend": "monty"},
            )
            return result.data

    data = run(exercise())

    assert data["error"] is not None
    assert REFUSAL in data["error"]["message"]
    assert spawned == []


def test_facade_client_without_elicitation_gets_todays_refusal() -> None:
    runtime, spawned = _runtime_with_fake_cli()

    async def exercise():
        async with _facade_client(runtime) as client:
            result = await client.call_tool(
                "execute_code",
                {"code": 'return await cli_run("curl")', "backend": "monty"},
            )
            return result.data

    data = run(exercise())

    assert data["error"] is not None
    assert REFUSAL in data["error"]["message"]
    assert spawned == []


def test_multi_client_transport_never_elicits() -> None:
    """Adversarial finding on PR #71: grants live on the shared runtime
    policy, so on a multi-client transport client A's approval would let
    client B run the binary without ever seeing a prompt. Off stdio the
    facade must not elicit at all — both clients get the plain refusal."""
    from fastmcp import Client

    from toolplane.mcp_facade import build_mcp_facade_from_config

    prompts: list[str] = []

    async def allow(message, response_type, params, context):
        prompts.append(message)
        return {"value": "allow"}

    async def exercise():
        app = await build_mcp_facade_from_config(
            {"cli": {"mode": "allowlist", "allow": ["git"]}},
            transport="http",
        )
        results = []
        for _ in range(2):  # two clients sharing one facade
            async with Client(app, elicitation_handler=allow) as client:
                result = await client.call_tool(
                    "execute_code",
                    {
                        "code": 'return await cli_run("curl")',
                        "backend": "monty",
                    },
                )
                results.append(result.data)
        return results

    first, second = run(exercise())

    for data in (first, second):
        assert data["error"] is not None
        assert REFUSAL in data["error"]["message"]
    assert prompts == []


def test_stdio_transport_keeps_escalation() -> None:
    from fastmcp import Client

    from toolplane.mcp_facade import build_mcp_facade_from_config

    prompts: list[str] = []

    async def deny(message, response_type, params, context):
        prompts.append(message)
        return {"value": "deny"}

    async def exercise():
        app = await build_mcp_facade_from_config(
            {"cli": {"mode": "allowlist", "allow": ["git"]}},
            transport="stdio",
        )
        async with Client(app, elicitation_handler=deny) as client:
            result = await client.call_tool(
                "execute_code",
                {"code": 'return await cli_run("curl")', "backend": "monty"},
            )
            return result.data

    data = run(exercise())

    assert len(prompts) == 1
    assert data["error"] is not None
    assert REFUSAL in data["error"]["message"]


def test_facade_advertises_escalation_in_the_manifest() -> None:
    runtime, _ = _runtime_with_fake_cli()

    build_mcp_facade(runtime)

    manifest = runtime.describe_namespace()
    assert "asks the human operator" in manifest


def test_unrestricted_runtime_facade_does_not_advertise_escalation() -> None:
    runtime, _ = _runtime_with_fake_cli(allowlist=None)

    build_mcp_facade(runtime)

    assert runtime.cli_policy.escalation_available is False
