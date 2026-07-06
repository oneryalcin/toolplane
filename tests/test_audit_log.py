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
