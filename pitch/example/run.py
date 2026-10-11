"""Run PROGRAM once on the monty backend and record what it actually did.

Writes build/trace.json, build/result.json and build/counts.json.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

from toolplane import Toolplane
from toolplane.audit import AuditLog

from data import sla_breach
from program import PROGRAM

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"
REPO = BUILD / "release-repo"
LATENCY_MS = os.environ.get("DEMO_LATENCY_MS", "20")  # simulated backend latency per MCP call


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


async def main() -> None:
    assert (REPO / ".git").is_dir(), "run make_repo.py first"
    logs = {name: BUILD / f"{name}_calls.jsonl" for name in ("tickets", "customers")}
    audit_path = BUILD / "audit.jsonl"
    for path in (*logs.values(), audit_path):
        path.unlink(missing_ok=True)
        path.touch()

    # `git` runs as a host subprocess in this process's cwd: point it at the demo repo
    os.chdir(REPO)

    runtime = Toolplane(
        default_backend="monty",
        ambient_cli_allowlist=["git"],
        audit_log=AuditLog(audit_path, enabled=True),
    )
    runtime.register_python_namespace("policy", {"sla_breach": sla_breach})
    await runtime.register_mcp_config(
        {
            "mcpServers": {
                name: {
                    "command": sys.executable,
                    "args": [str(HERE / f"{name}_server.py")],
                    "env": {"DEMO_CALL_LOG": str(log), "DEMO_LATENCY_MS": LATENCY_MS},
                }
                for name, log in logs.items()
            }
        }
    )

    # Toolplane's audit log is metadata only by design (no arguments, no
    # results), and its timestamps are completion times at 1 ms resolution.
    # The trace needs start times and result sizes, so wrap the one choke
    # point every capability call goes through; the audit log cross-checks it.
    flat_names = {canon: flat for flat, canon in runtime.registry.callable_namespace().items()}
    trace: list[dict] = []
    result_bytes: list[int] = []
    inner_call = runtime.bridge.call_tool
    started = time.perf_counter()

    async def recording_call(name, params=None):
        entry: dict = {"t": round(time.perf_counter() - started, 4)}
        if name == "toolplane:cli/run":
            entry |= {"kind": "cli", "name": params["binary"]}
        elif name.startswith("mcp:"):
            entry |= {"kind": "mcp", "name": flat_names[name], "server": name[4:].split("/")[0]}
        else:
            entry |= {"kind": "python", "name": flat_names[name]}
        trace.append(entry)
        value = await inner_call(name, params)
        result_bytes.append(len(json.dumps(value).encode()))
        return value

    runtime.bridge.call_tool = recording_call
    result = await runtime.execute(PROGRAM)
    wall = time.perf_counter() - started
    assert result.ok, result.error
    assert len(result_bytes) == len(trace)

    by_kind = Counter(e["kind"] for e in trace)
    by_name = Counter(e["name"] for e in trace)
    dispatches = [e for e in read_jsonl(audit_path) if e["event"] == "dispatch"]
    server_calls = {name: len(read_jsonl(log)) for name, log in logs.items()}
    assert len(dispatches) == len(trace) and all(e["ok"] for e in dispatches)
    assert server_calls["tickets"] == by_name["tickets_get_ticket"]
    assert server_calls["customers"] == by_name["customers_get_customer"]
    assert by_kind["mcp"] == sum(server_calls.values())

    returned = len(json.dumps(result.value).encode())
    counts = {
        "calls_total": len(trace),
        "by_kind": dict(sorted(by_kind.items())),
        "by_name": dict(sorted(by_name.items())),
        "server_call_logs": server_calls,
        "audit_dispatch_events": len(dispatches),
        "customers_calls_saved_by_branch": by_name["tickets_get_ticket"] - server_calls["customers"],
        "returned_value_bytes": returned,
        "all_tool_results_bytes": sum(result_bytes),
    }
    (BUILD / "trace.json").write_text(json.dumps(trace, indent=1) + "\n")
    (BUILD / "result.json").write_text(json.dumps(result.value, indent=2) + "\n")
    (BUILD / "counts.json").write_text(json.dumps(counts, indent=2) + "\n")
    print(json.dumps(result.value, indent=2))
    print(json.dumps(counts, indent=2))
    print(f"wall: {wall:.2f}s (execute), backend: {result.backend}, latency/call: {LATENCY_MS}ms")


if __name__ == "__main__":
    asyncio.run(main())
