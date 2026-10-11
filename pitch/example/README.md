# Demo: which enterprise customers does this release fix something for?

One program (`program.py`, 12 lines) on toolplane's default sandbox backend (monty) composes `git` (allowlisted CLI), two stdio MCP servers (`tickets`, `customers`) and one registered Python function (`policy_sla_breach`).
    cd pitch/example && PY="env -u ANTHROPIC_API_KEY UV_NO_CONFIG=1 ../../.venv/bin/python"
    $PY make_repo.py   # build/release-repo: 15 commits, tag v1.4, 100 commits (HEAD fa64e9b)
    $PY run.py         # build/trace.json, result.json, counts.json
    $PY refusal.py     # build/refusals.json

Verified on 2026-10-11 (no model or API calls; all data fictional and seeded in `data.py`):
- 181 capability calls in one execution: 1 `git`, 100 `tickets_get_ticket`, 40 `customers_get_customer`, 40 `policy_sla_breach`.
- The `if` saves 60 of 100 customers-server calls; each server's own call log (100 and 40 lines) and toolplane's audit log (181 dispatch events) agree with the trace.
- `result.json` and `counts.json` are byte-identical across runs (`trace.json` timings vary). Result: 100 commits, 7 hits across 4 customers. Returned value 419 bytes; the tool results it was computed from total 24,990 bytes.
- Wall time of `execute()` 1.1-1.8 s with 20 ms simulated latency per MCP call (`DEMO_LATENCY_MS`), 0.25 s with none. The 100 ticket fetches are started first, then awaited.
- Refusals: `rm` has no binding (`NameError`), `cli_run("rm", ...)` is refused by toolplane policy (`PermissionError`), `open("/etc/hosts")` and `import socket` are refused by the monty sandbox. The target file survived.
- `git` runs as a host subprocess, outside the sandbox; the trace wrapper in `run.py` exists because the audit log records no payloads.
