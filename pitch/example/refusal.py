"""Show the boundary: run programs that step outside it and record what comes back.

No escalation handler is installed, so nothing prompts; each run just returns
its structured error. Writes build/refusals.json.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from toolplane import Toolplane
from toolplane.audit import AuditLog

BUILD = Path(__file__).resolve().parent / "build"
SENTINEL = BUILD / "sentinel.txt"  # a throwaway file: harmless even if rm did run

PROGRAMS = {
    "binary_not_on_allowlist": f'return await rm(_=["{SENTINEL}"])',
    "binary_not_on_allowlist_via_cli_run": f'return await cli_run("rm", None, {{"_": ["{SENTINEL}"]}})',
    "read_host_file": 'return open("/etc/hosts").read()',
    "network": 'import socket\nreturn socket.create_connection(("example.com", 80))',
}


async def main() -> None:
    SENTINEL.write_text("still here\n")
    audit_path = BUILD / "refusal_audit.jsonl"
    audit_path.unlink(missing_ok=True)
    runtime = Toolplane(
        default_backend="monty",
        ambient_cli_allowlist=["git"],
        audit_log=AuditLog(audit_path, enabled=True),
    )
    refusals = {}
    for label, code in PROGRAMS.items():
        result = await runtime.execute(code)
        assert not result.ok, f"{label} was not refused"
        refusals[label] = {
            "program": code.replace(str(SENTINEL), "build/sentinel.txt"),
            "ok": result.ok,
            "value": result.value,
            "backend": result.backend,
            "error": {"type": result.error.type, "message": result.error.message},
        }
    assert SENTINEL.read_text() == "still here\n"
    events = [json.loads(line) for line in audit_path.read_text().splitlines()]
    out = {
        "refusals": refusals,
        "sentinel_survived": True,
        # metadata only; timestamps, run ids and durations dropped to keep this file stable
        "audit_dispatch_events": [
            {k: e[k] for k in ("event", "capability", "binary", "ok", "error_type")}
            for e in events
            if e["event"] == "dispatch"
        ],
    }
    (BUILD / "refusals.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
