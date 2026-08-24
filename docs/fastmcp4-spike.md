# FastMCP 4 Spike: the Facade Under MCP 2026-07-28

Empirical record of running the current facade code against fastmcp
4.0.0b3 / MCP Python SDK 2.0.0, focused on the CLI-escalation elicitation
path (#132 finding 1). Probed 2026-08-24. Everything below was executed,
not inferred; scripts were throwaway.

## Setup

Scratch venv, `--no-deps` editable install of toolplane over
`fastmcp==4.0.0b3` + `pydantic-monty==0.0.19b4` (the `<4` pin as it stood
at spike time refuses to co-resolve with the beta; reopened after the
gated contextvar landed).

## Findings

### F1 — imports and facade build pass untouched

`toolplane.mcp_facade` imports and builds cleanly on fastmcp 4 / SDK v2.
All breakage lives at runtime, inside the escalation registration.

### F2 — `request_ctx` is gone, and there is a supported replacement

`from mcp.server.lowlevel.server import request_ctx`
(`src/toolplane/mcp_facade.py`) raises `ImportError` at first escalation.
fastmcp 4 exposes its own documented ContextVar,
`fastmcp_request_ctx`, holding the request/session pair
(`fastmcp/server/dependencies.py`). A one-line alias —

```python
import mcp.server.lowlevel.server as lls
from fastmcp.server.dependencies import fastmcp_request_ctx
lls.request_ctx = fastmcp_request_ctx
```

— makes the capture/re-seat hack run verbatim. The mechanism survives;
only the import target died.

### F3 — held-open elicitation is protocol-dead on modern connections

With a default fastmcp 4 client (negotiates the 2026-07-28 era),
`ctx.elicit(...)` raises:

> `ToolError: elicitation via server-initiated requests is unavailable on
> 2026-07-28 connections.`

This is SEP-2260/SEP-2322 doing exactly what they say: no server-initiated
requests outside an active exchange, mid-call asks become Multi Round-Trip
Requests. Fail-closed (plain refusal), never a crash into policy.

### F4 — legacy-era connections keep everything working

Same build, client forced to `mode="legacy"` (initialize handshake, i.e.
what today's Claude Code @ 2025-11-25 and Codex @ 2025-06-18 negotiate):
full elicitation round-trip succeeds — prompt renders, `{"value":
"allow"}` returns, grant applies, binary spawns. Dual-era serving means
current clients are unaffected by an upgrade *provided* F2's import is
fixed.

### F5 — `Client(mode=...)` decides the era

`Client.__init__` gained `mode`: `"auto"` (default — probe
`server/discover`, fall back), `"legacy"`, or a literal version string.
Implication: once a real client ships modern-era support, its toolplane
escalation prompts silently degrade to refusals until the MRTR path
exists. No error surfaces at the server; this is the trap to document.

## Recommendation

1. ~~Now (small): replace the raw-SDK import with a gated lookup~~
   **Done** — `_request_context_var()` gates per line, test clients pin
   the handshake era, `<4` reopened; full suite green on 3.2.0 and
   4.0.0b3 (2026-08-24).
2. Before any client ships modern era (track in the capability matrix):
   implement escalation as an MRTR guard-mode tool for 2026-07-28
   connections, keeping `ctx.elicit` for handshake-era sessions. fastmcp
   4's guard-mode tools (PrefectHQ/fastmcp#4544) are the candidate shape.
3. ~~Do not upgrade the pin until 1 lands~~ — superseded by (1).

## Scorecard vs #132 predictions

| Prediction | Outcome |
| --- | --- |
| `request_ctx` breaks on upgrade | confirmed (F2) |
| guard-mode MRTR replaces the pattern | directionally confirmed; API not yet exercised end-to-end |
| legacy clients keep working via dual-era | confirmed (F4) |
| workaround gets deleted, not ported | revised: ported (one-line), deletion only when MRTR lands |
