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


def _mcp_client(target, **client_kwargs):
    """Version-tolerant Client construction, pinned to the handshake era.

    Elicitation escalation exists only on initialize-era connections;
    fastmcp >=4 clients probe for the modern era by default and would
    silently degrade every prompt to a refusal (docs/fastmcp4-spike.md
    F3/F5). Legacy is what real clients speak today. Older Clients have
    no mode parameter.
    """
    import inspect

    from fastmcp import Client

    if (
        "mode" not in client_kwargs
        and "mode"
        in inspect.signature(Client.__init__).parameters
    ):
        client_kwargs["mode"] = "legacy"
    return Client(target, **client_kwargs)


def _facade_client(runtime, **client_kwargs):
    return _mcp_client(build_mcp_facade(runtime), **client_kwargs)


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
            # legacy era: on the modern era elicit is protocol-dead anyway,
            # and this test must exercise the transport gate, not ride that
            async with _mcp_client(
                app, elicitation_handler=allow
            ) as client:
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
        # legacy era, same reason as _mcp_client: exercise the stdio
        # escalation gate, not modern-era protocol death
        async with _mcp_client(app, elicitation_handler=deny) as client:
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


# --- 2026-07-28 era: escalation over MRTR (#139, #155) ----------------------
#
# Modern connections have no server-initiated requests, so ctx.elicit is dead
# and escalation rides Multi Round-Trip Requests. These clients are NOT pinned
# to the legacy era — that pin (#138) is exactly what hid #155.


def _modern_runtime(**backend_kwargs):
    import fastmcp

    if int(fastmcp.__version__.split(".")[0]) < 4:
        # the 2026-07-28 era (MRTR, sealed requestState) is fastmcp 4 /
        # mcp-sdk v2 only; mcp_types alone is importable on older lines
        pytest.skip("MRTR needs fastmcp>=4")
    from toolplane.backends import MontyBackend

    backends = [MontyBackend(**backend_kwargs)] if backend_kwargs else None
    runtime, spawned = _runtime_with_fake_cli(backends=backends)
    bumps: list[int] = []

    def bump() -> int:
        bumps.append(1)
        return len(bumps)

    runtime.register(bump, description="side effect counter")

    async def pause(seconds: float) -> None:
        await asyncio.sleep(seconds)

    runtime.register(pause, description="sleeps")
    return runtime, spawned, bumps


def _answer(value):
    async def handler(message, response_type, params, context):
        return {"value": value}

    return handler


def _modern_call(runtime, code, handler):
    from fastmcp import Client

    async def exercise():
        async with Client(build_mcp_facade(runtime), elicitation_handler=handler) as c:
            result = await c.call_tool(
                "execute_code", {"code": code, "backend": "monty"}
            )
            return result.data

    return run(exercise())


def test_modern_era_escalation_prompts_and_grants() -> None:
    runtime, spawned, _ = _modern_runtime()
    data = _modern_call(runtime, 'return await cli_run("curl")', _answer("allow"))

    assert data["error"] is None, data["error"]
    assert spawned == ["curl"]


def test_modern_era_denied_escalation_never_spawns() -> None:
    runtime, spawned, _ = _modern_runtime()
    data = _modern_call(runtime, 'return await cli_run("curl")', _answer("deny"))

    assert spawned == []
    assert data["error"]["type"] == "PermissionError"


def test_modern_era_side_effects_before_the_ask_run_exactly_once() -> None:
    # the reason runs are parked, not re-executed: re-running the snippet on
    # the re-issued call would repeat every capability call before the ask
    runtime, spawned, bumps = _modern_runtime()
    data = _modern_call(
        runtime,
        'await bump()\nreturn await cli_run("curl")',
        _answer("allow"),
    )

    assert data["error"] is None, data["error"]
    assert (len(bumps), spawned) == (1, ["curl"])


def _raw_leg(client, code, request_state=None, input_responses=None):
    return client.session.call_tool(
        name="execute_code",
        arguments={"code": code, "backend": "monty"},
        request_state=request_state,
        input_responses=input_responses,
        allow_input_required=True,
    )


def _grant_answer():
    import mcp_types

    return {"grant": mcp_types.ElicitResult(action="accept", content={"value": "allow"})}


def test_forged_request_state_executes_nothing() -> None:
    # mcp-sdk v2 seals requestState (AES-GCM, request-bound, TTL): a forged
    # handle is rejected at the protocol boundary and must never fall
    # through to a fresh execution of the supplied code
    runtime, spawned, bumps = _modern_runtime()
    from fastmcp import Client
    from mcp.shared.exceptions import MCPError


    async def exercise():
        async with Client(build_mcp_facade(runtime)) as c:
            with pytest.raises(MCPError):
                await _raw_leg(
                    c,
                    'await bump()\nreturn await cli_run("curl")',
                    request_state="forged",
                    input_responses=_grant_answer(),
                )

    run(exercise())

    assert (bumps, spawned) == ([], [])


def test_approval_token_cannot_be_replayed_with_different_code() -> None:
    # identity confusion (#114 lesson): the human approved the run that
    # asked; a genuine token re-issued with other code must not execute it
    runtime, spawned, bumps = _modern_runtime(timeout_seconds=1.0)
    from fastmcp import Client
    from mcp.shared.exceptions import MCPError


    async def exercise():
        async with Client(build_mcp_facade(runtime)) as c:
            first = await _raw_leg(c, 'return await cli_run("curl")')
            with pytest.raises(MCPError):
                await _raw_leg(
                    c,
                    'await bump()\nreturn await cli_run("wget")',
                    request_state=first.request_state,
                    input_responses=_grant_answer(),
                )

    run(exercise())

    assert (bumps, spawned) == ([], [])


def test_abandoned_park_times_out_and_a_late_answer_executes_nothing() -> None:
    # a client that never re-issues must not wedge the run forever, and its
    # late answer must not grant anything (the #71 late-answer hazard)
    import mcp_types
    from fastmcp import Client

    runtime, spawned, _ = _modern_runtime(timeout_seconds=1.0)

    async def exercise():
        async with Client(build_mcp_facade(runtime)) as c:
            first = await _raw_leg(c, 'return await cli_run("curl")')
            await asyncio.sleep(1.5)  # backend timeout fires while parked
            late = await _raw_leg(
                c,
                'return await cli_run("curl")',
                request_state=first.request_state,
                input_responses=_grant_answer(),
            )
            return first, late

    first, late = run(exercise())

    assert isinstance(first, mcp_types.InputRequiredResult)
    assert spawned == []
    assert late.structured_content["error"]["type"] == "EscalationExpiredError"
    assert runtime.cli_policy.escalation_handler is None


def _prompt(first) -> str:
    return first.input_requests["grant"].params.message


def test_overlapping_runs_each_ask_through_their_own_call() -> None:
    # the grant handler is runtime-wide; without serialization run B's
    # handler replaced run A's, so A's ask surfaced on B's call and B's
    # approval let A spawn (Codex adversarial, #156)
    runtime, spawned, _ = _modern_runtime(timeout_seconds=10.0)
    from fastmcp import Client

    code_a = 'await pause(seconds=0.3)\nreturn await cli_run("curl")'
    code_b = 'return await cli_run("wget")'

    async def exercise():
        async with Client(build_mcp_facade(runtime)) as c:
            leg_a = asyncio.ensure_future(_raw_leg(c, code_a))
            await asyncio.sleep(0.05)
            leg_b = asyncio.ensure_future(_raw_leg(c, code_b))
            first_a = await leg_a
            b_waited = not leg_b.done()
            done_a = await _raw_leg(
                c, code_a, first_a.request_state, _grant_answer()
            )
            first_b = await leg_b
            return first_a, b_waited, done_a, first_b

    first_a, b_waited, done_a, first_b = run(exercise())

    assert "'curl'" in _prompt(first_a)
    assert b_waited
    assert done_a.structured_content["error"] is None
    assert "'wget'" in _prompt(first_b)
    assert spawned == ["curl"]


@pytest.mark.parametrize("session", [False, True])
def test_client_cancelling_a_leg_stops_the_run(session: bool) -> None:
    # a disconnected client can never answer or read the result; the run
    # must not keep executing toward an ask (Codex adversarial, #156), and
    # in session mode — the stdio default — the cancel must not wedge the
    # session for every later run (Fable review, #156)
    runtime, spawned, bumps = _modern_runtime(timeout_seconds=10.0, session=session)
    from fastmcp import Client

    code = 'await pause(seconds=0.5)\nawait bump()\nreturn await cli_run("curl")'

    async def exercise():
        async with Client(build_mcp_facade(runtime)) as c:
            leg = asyncio.ensure_future(_raw_leg(c, code))
            await asyncio.sleep(0.1)
            leg.cancel()
            await asyncio.sleep(1.0)  # past the point the run would bump
            # the escalation slot is free again: a new run is not blocked
            after = await asyncio.wait_for(
                _raw_leg(c, "return 7"), timeout=5
            )
            return after

    after = run(exercise())

    assert (bumps, spawned) == ([], [])
    assert after.structured_content["value"] == 7


def test_unanswered_park_expires_with_the_request_state_ttl(monkeypatch) -> None:
    # past the sealed-token TTL no answer can verify; a parked run on a
    # backend without its own run timeout (local_unsafe) held the
    # escalation slot forever (Fable review, #156)
    runtime, spawned, _ = _modern_runtime(timeout_seconds=30.0)
    import mcp_types
    from fastmcp import Client

    import toolplane.mcp_facade as facade

    monkeypatch.setattr(facade, "_PARK_TTL_SECONDS", 0.5)

    async def exercise():
        async with Client(build_mcp_facade(runtime)) as c:
            first = await _raw_leg(c, 'return await cli_run("curl")')
            await asyncio.sleep(1.0)  # TTL lapses; backend timeout has not
            after = await asyncio.wait_for(_raw_leg(c, "return 7"), timeout=5)
            return first, after

    first, after = run(exercise())

    assert isinstance(first, mcp_types.InputRequiredResult)
    assert spawned == []
    assert after.structured_content["value"] == 7


def test_answer_after_the_run_deadline_never_grants(monkeypatch) -> None:
    # an accept landing as the backend times out (during its rollback,
    # before pending escalations are cancelled) still granted, and the
    # binary spawned after the client was told the run timed out (#159).
    # The facade's deadline is pinned below the backend's real timeout so
    # the "after the deadline, run still parked" window is deterministic.
    runtime, spawned, _ = _modern_runtime(timeout_seconds=10.0)
    from fastmcp import Client

    import toolplane.mcp_facade as facade

    monkeypatch.setattr(facade, "_run_timeout_seconds", lambda *_: 0.3)

    async def exercise():
        async with Client(build_mcp_facade(runtime)) as c:
            first = await _raw_leg(c, 'return await cli_run("curl")')
            await asyncio.sleep(0.5)
            return await _raw_leg(
                c,
                'return await cli_run("curl")',
                request_state=first.request_state,
                input_responses=_grant_answer(),
            )

    late = run(exercise())

    assert spawned == []
    assert late.structured_content["error"]["type"] == "EscalationExpiredError"
