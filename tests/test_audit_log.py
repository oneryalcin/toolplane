"""Audit log contract (issue #74): JSONL events at the bridge choke point.

The privacy rule is the load-bearing contract: events carry names,
durations, and outcomes — never call arguments or results.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from toolplane import Toolplane
from toolplane.adapters.ambient_cli import AMBIENT_CLI_CAPABILITY
from toolplane.audit import AuditLog
from toolplane.capabilities import Capability
from toolplane.registry import CapabilityRegistry


def run(coro):
    return asyncio.run(coro)


def _events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _runtime(tmp_path: Path, allowlist=("git",)) -> tuple[Toolplane, Path]:
    log_path = tmp_path / "audit.jsonl"
    registry = CapabilityRegistry()

    async def fake_cli(binary, subcommand=None, options=None):
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
        audit_log=AuditLog(log_path, enabled=True),
    )

    @runtime.tool(tags={"demo"})
    def greet(secret_token: str) -> str:
        """Return a greeting."""
        return f"hello {secret_token}"

    return runtime, log_path


def test_disabled_by_default_writes_nothing(tmp_path: Path) -> None:
    runtime = Toolplane(ambient_cli=False)

    result = run(runtime.execute("return 1", backend="monty"))

    assert result.error is None
    assert not runtime.audit_log.enabled
    assert list(tmp_path.iterdir()) == []


def test_run_events_bracket_dispatches_with_a_shared_run_id(
    tmp_path: Path,
) -> None:
    runtime, log_path = _runtime(tmp_path)

    result = run(
        runtime.execute(
            'x = await greet(secret_token="s3cr3t")\n'
            'y = await git("status")\n'
            "return None",
            backend="monty",
        )
    )

    assert result.error is None, result.error
    events = _events(log_path)
    kinds = [e["event"] for e in events]
    assert kinds == ["run_start", "dispatch", "dispatch", "run_end"]
    run_ids = {e["run_id"] for e in events}
    assert len(run_ids) == 1
    start, greet_call, cli_call, end = events
    assert start["backend"] == "monty"
    assert len(start["code_sha256"]) == 12
    assert greet_call["ok"] is True and "duration_ms" in greet_call
    assert cli_call["capability"] == AMBIENT_CLI_CAPABILITY
    assert cli_call["binary"] == "git"
    assert end["ok"] is True and end["error_type"] is None


def test_arguments_and_results_never_reach_the_log(tmp_path: Path) -> None:
    runtime, log_path = _runtime(tmp_path)

    run(
        runtime.execute(
            'return await greet(secret_token="hunter2-api-key")',
            backend="monty",
        )
    )

    raw = log_path.read_text()
    assert "hunter2-api-key" not in raw
    assert "hello" not in raw  # results stay out too


def test_failed_dispatch_and_run_record_error_types(tmp_path: Path) -> None:
    runtime, log_path = _runtime(tmp_path)

    result = run(
        runtime.execute('return await cli_run("curl")', backend="monty")
    )

    assert result.error is not None
    events = _events(log_path)
    dispatch = next(e for e in events if e["event"] == "dispatch")
    assert dispatch["ok"] is False
    assert dispatch["error_type"] == "CliPolicyError"
    assert dispatch["binary"] == "curl"
    end = events[-1]
    assert end["event"] == "run_end" and end["ok"] is False


def test_escalation_outcomes_are_logged(tmp_path: Path) -> None:
    runtime, log_path = _runtime(tmp_path)
    policy = runtime.cli_policy
    decisions = iter([True, False])

    async def handler(binary: str) -> bool:
        return next(decisions)

    async def broken(binary: str) -> bool:
        raise RuntimeError("boom")

    async def never(binary: str) -> bool:
        await asyncio.Event().wait()
        return True

    async def exercise():
        policy.escalation_handler = handler
        await policy.ensure_allowed("curl")  # granted
        with pytest.raises(Exception):
            await policy.ensure_allowed("wget")  # declined
        policy.escalation_handler = broken
        with pytest.raises(Exception):
            await policy.ensure_allowed("jq")  # error -> fail closed
        policy.escalation_handler = never
        pending = asyncio.ensure_future(policy.ensure_allowed("ping"))
        await asyncio.sleep(0)
        policy.cancel_pending_escalations()  # abandoned
        with pytest.raises(Exception):
            await pending

    run(exercise())

    outcomes = [
        (e["binary"], e["outcome"])
        for e in _events(log_path)
        if e["event"] == "escalation"
    ]
    assert outcomes == [
        ("curl", "granted"),
        ("wget", "declined"),
        ("jq", "error"),
        ("ping", "abandoned"),
    ]


def test_from_config_wires_the_audit_log(tmp_path: Path) -> None:
    log_path = tmp_path / "from-config.jsonl"

    async def exercise():
        runtime = await Toolplane.from_config(
            {"audit": {"enabled": True, "path": str(log_path)}}
        )
        return await runtime.execute("return 1", backend="monty")

    result = run(exercise())

    assert result.error is None
    kinds = [e["event"] for e in _events(log_path)]
    assert kinds == ["run_start", "run_end"]


def test_write_failure_disables_instead_of_breaking_the_run(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("file, not dir")
    log = AuditLog(blocker / "audit.jsonl", enabled=True)
    runtime = Toolplane(ambient_cli=False, audit_log=log)

    result = run(runtime.execute("return 41 + 1", backend="monty"))

    assert result.error is None
    assert result.value == 42
    assert log.enabled is False
    assert "audit log disabled" in capsys.readouterr().err


# --- regressions from the #82 review round -----------------------------------


def test_concurrent_runs_keep_their_own_run_ids(tmp_path: Path) -> None:
    """All four reviewers: a shared current-run slot attributed run A's
    dispatches to run B and clobbered run_ids at completion. Correlation
    must hold by construction under overlapping executes."""
    log_path = tmp_path / "audit.jsonl"
    runtime = Toolplane(
        ambient_cli=False,
        default_backend="local_unsafe",
        audit_log=AuditLog(log_path, enabled=True),
    )
    gate = asyncio.Event()

    @runtime.tool()
    async def slow_wait() -> str:
        """Wait for the gate."""
        await gate.wait()
        return "slow"

    @runtime.tool()
    def fast_ping() -> str:
        """Return fast."""
        return "fast"

    async def exercise():
        slow_run = asyncio.create_task(
            runtime.execute("return await slow_wait()")
        )
        await asyncio.sleep(0.05)  # run A is mid-dispatch when B starts
        fast_result = await runtime.execute("return await fast_ping()")
        gate.set()
        return await slow_run, fast_result

    slow_result, fast_result = run(exercise())

    assert slow_result.error is None and fast_result.error is None
    events = _events(log_path)
    starts = [e for e in events if e["event"] == "run_start"]
    slow_id, fast_id = starts[0]["run_id"], starts[1]["run_id"]
    assert slow_id != fast_id
    by_capability = {
        e["capability"]: e["run_id"]
        for e in events
        if e["event"] == "dispatch"
    }
    assert by_capability["slow_wait"] == slow_id
    assert by_capability["fast_ping"] == fast_id
    ends = {e["run_id"] for e in events if e["event"] == "run_end"}
    assert ends == {slow_id, fast_id}


def test_abandoned_escalation_correlates_via_run_end(tmp_path: Path) -> None:
    """Reviewer-reproduced on #82: the abandoned event can fire after its
    run ended, so it carries NO run_id (never a wrong one); the join key
    is run_end.escalations_cancelled."""
    from toolplane.backends import MontyBackend

    runtime, log_path = _runtime(tmp_path)
    runtime.backends["monty"] = MontyBackend(timeout_seconds=0.3)

    async def human_still_reading(binary: str) -> bool:
        await asyncio.Event().wait()
        return True

    runtime.cli_policy.escalation_handler = human_still_reading

    async def exercise():
        result = await runtime.execute(
            'return await cli_run("curl")', backend="monty"
        )
        await asyncio.sleep(0)  # let the abandoned task unwind and emit
        return result

    result = run(exercise())

    assert result.error is not None and result.error.type == "TimeoutError"
    events = _events(log_path)
    end = next(e for e in events if e["event"] == "run_end")
    assert end["escalations_cancelled"] == ["curl"]
    assert end["run_id"] == events[0]["run_id"]
    abandoned = next(
        e
        for e in events
        if e["event"] == "escalation" and e["outcome"] == "abandoned"
    )
    assert abandoned["binary"] == "curl"
    assert "run_id" not in abandoned


def test_backend_raise_emits_the_same_run_end_shape(tmp_path: Path) -> None:
    import pytest

    from toolplane.errors import BackendCapabilityError

    runtime, log_path = _runtime(tmp_path)

    with pytest.raises(BackendCapabilityError):
        run(
            runtime.execute(
                "return 1", backend="monty", packages=["pandas"]
            )
        )

    end = next(e for e in _events(log_path) if e["event"] == "run_end")
    assert end["ok"] is False
    assert end["error_type"] == "BackendCapabilityError"
    # same shape as the success path: jq consumers key on these fields
    assert end["artifacts_saved"] == 0
    assert end["escalations_cancelled"] == []
    assert end["run_id"] == _events(log_path)[0]["run_id"]


def test_unserializable_field_disables_instead_of_raising(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class Boom:
        def __str__(self) -> str:
            raise RuntimeError("no repr for you")

    log = AuditLog(tmp_path / "audit.jsonl", enabled=True)

    log.emit("weird", payload=Boom())  # must not raise

    assert log.enabled is False
    assert "audit log disabled" in capsys.readouterr().err
