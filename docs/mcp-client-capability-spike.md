# MCP Client Capability Spike

Status: findings recorded 2026-07-02. Instrument: `examples/mcp_client_probe.py`.

!!! note "This is the dated spike record"

    The maintained, current view — including the later interactive
    elicitation probes and behavioral findings — lives in the
    [MCP Client Capability Matrix](mcp-client-capability-matrix.md).

Toolplane's roadmap keeps bumping into MCP protocol features beyond tools:
resources for the result store and a future artifacts lane, elicitation for
allowlist escalation, sampling for server-side generation. Client support for
these features is uneven and mostly undocumented, so before designing on any
of them we measured what the two target clients actually do.

## Method

A ~60-line FastMCP server exposes one probe per feature: a static resource, a
templated resource, a binary resource, and tools that attempt sampling,
elicitation, and progress/logging against the connected client. One extra
tool, `client_capabilities`, returns whatever the client declared in its
`initialize` handshake — the client reports on itself.

The server is first verified against a full-featured `fastmcp.Client` (with
sampling, elicitation, log, and progress handlers installed) so that every
subsequent failure is attributable to the client under test, not the
instrument. Real clients are then driven headlessly with isolated configs:

```bash
# ground truth — every feature should pass
uv run python examples/mcp_client_probe.py --self-test

# Claude Code (headless, only the probe server loaded)
claude -p "Call client_capabilities on the probe server; print raw JSON." \
    --mcp-config probe-mcp.json --strict-mcp-config \
    --allowedTools "mcp__probe__client_capabilities"

# Codex (headless, config injected per-invocation)
codex exec \
    -c 'mcp_servers.probe.command="python"' \
    -c 'mcp_servers.probe.args=["examples/mcp_client_probe.py"]' \
    --skip-git-repo-check \
    "Call client_capabilities on the probe server; print raw JSON."
```

## Findings

Probed 2026-07-02 against claude-code 2.1.198 and codex-cli 0.142.4, both on
stdio transport.

| Feature | Claude Code 2.1.198 | Codex 0.142.4 |
| --- | --- | --- |
| Resources: list + read | yes | yes |
| Resource templates in listing | **no** — readable if the concrete URI is known, invisible in `ListMcpResourcesTool` | yes — template URIs enumerated |
| Binary resources | yes — bytes written to a local file, path returned to the agent | yes — inline base64, bytes verified |
| Sampling | **no** — `capabilities.sampling = null` | **no** — same |
| Elicitation | declared (`form` + `url`); headless auto-cancels (`ELICIT_cancel`) | declared; headless auto-cancels, and the parser **rejects FastMCP's elicitation schema** (`unknown field 'title'`) |
| Progress / log notifications | accepted by the client, never surfaced to the headless agent | not observed |
| Protocol version | 2025-11-25 | 2025-06-18 |

## What this means for Toolplane

**Resources are safe to build on, and better than expected.** Both clients
list and read them today. Claude Code's binary handling is the standout: a
blob resource arrives as a real file on the client's disk with the path handed
to the agent — for an artifacts lane that is the ideal read-back UX, with no
base64 in the agent's context. This shapes the design of the artifacts lane
(#46) and the namespace-manifest fix for discoverability (#51).

One caveat travels with every resource design: Claude Code does not enumerate
resource templates, so a templated URI like `toolplane://results/{handle}`
cannot be discovered from a listing there. Concrete URIs must be delivered in
tool results (which handle-in-result already does) or documented in a static
resource.

**Sampling is dead on arrival.** Neither client supports it. Do not design
features that require the server to borrow the client's LLM.

**Elicitation is declared but unproven.** Both clients advertise it in the
handshake; both auto-cancel headlessly, which is expected without a user.
Interactive behavior is untested, and the Codex schema rejection suggests
FastMCP↔Codex elicitation may be broken regardless. Re-run the probe from an
interactive session before building anything on elicitation (the candidate
feature: escalating a blocked CLI binary to a human yes/no instead of a hard
refusal).

**Progress and logging are cosmetic for now.** Neither headless client
surfaces them to the agent, so they help interactive users at best.

## Re-running

Client capabilities drift with every release. When claude-code or codex-cli
versions jump, or a new client becomes a target, re-run the probe and update
the table — the instrument is `examples/mcp_client_probe.py` and a run takes
minutes.
