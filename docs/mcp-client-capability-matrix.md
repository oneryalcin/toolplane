# MCP Client Capability Matrix

What MCP clients *actually* support, measured — not what they declare in the
handshake, and not what their docs imply. Maintained as findings accumulate;
each row states when and against which version it was probed. Corrections
and probe results from other clients are welcome as issues or PRs.

Instrument: [`examples/mcp_client_probe.py`](https://github.com/oneryalcin/toolplane/blob/main/examples/mcp_client_probe.py)
(~60-line FastMCP server, one probe per feature, self-test mode as ground
truth). Method described in the
[original spike record](mcp-client-capability-spike.md); a run takes
minutes. When client versions jump, re-run before trusting this table.

## The matrix

Headless probes 2026-07-02 (claude-code 2.1.198, codex-cli 0.142.4);
interactive elicitation probes 2026-07-05 (claude-code 2.1.201). Stdio
transport throughout.

| Feature | Claude Code | Codex CLI |
| --- | --- | --- |
| Resources: list + read | yes | yes |
| Resource **templates** in listing | **no** — readable if the concrete URI is known, invisible in the listing | yes — template URIs enumerated |
| Binary resources | yes — bytes materialize as a **local file**, path returned to the agent | yes — inline base64 |
| Sampling | **no** (`capabilities.sampling = null`) | **no** |
| Elicitation, headless | declared (`form` + `url`); auto-cancels (`{"action":"cancel"}` synthesized) | declared; auto-cancels — and the parser **rejects FastMCP's schema** before any UI ([details](codex-elicitation-title-bug.md)) |
| Elicitation, interactive | **yes** — full battery passed (2.1.201): inline bordered form, Accept/Decline buttons, Esc = cancel; accept/decline/cancel all map to the spec actions; repeat requests surface fresh forms | form UI exists in the TUI (since openai/codex PR #17043, 2026-04) but unreachable from FastMCP servers due to the schema bug |
| Elicitation: string-enum rendering | yes — `["allow", "deny"]` renders as a selectable option list, not free text (2.1.201) | untested (blocked by the schema bug) |
| Skills over MCP (fastmcp `SkillsDirectoryProvider`) | skill's static resources (`skill://<name>/SKILL.md`) appear in resource listing; `download_skill` works; the `list_skills`/`sync_skills` tool layer is **not** surfaced (2026-07-03) | untested |
| Progress / log notifications | accepted, never surfaced to the headless agent | not observed |
| Protocol version | 2025-11-25 | 2025-06-18 |

## Behavioral findings the table can't hold

These matter more than the feature rows.

**Capability ≠ discovery.** A cold agent with the resource-listing tool
available from turn one never called it unaided — and said so explicitly
when asked afterward. Resources are only found when a channel the agent
already reads names them in plain text. Full argument:
[Agents Don't Find Resources](agents-dont-find-resources.md).

**Elicitation outlives the tool call that triggered it.** In Claude Code,
the client-side tool call can time out (or the server-side run can die)
while the form stays open on screen; the human's late answer still resolves
the protocol request. Verified live 2026-07-05: a form answered ~2 minutes
after the requesting call had already returned `TimeoutError`. If your
server mutates state on elicitation answers, tie the request's lifetime to
the work that asked — a late answer with no witness is a silent policy
change. (This forced run-scoped escalation cancellation in toolplane.)

**Synthetic cancel is indistinguishable from a real user cancel.** Headless
surfaces answer `{"action":"cancel"}` immediately; a human pressing Esc
sends the same thing. Servers cannot detect "this surface can't prompt" —
design elicitation as an enhancement over a path that already works, never
as a new failure mode.

**Headless Claude Code can still answer elicitations** via the `Elicitation`
hook (matcher = MCP server name), so degraded mode is scriptable, not dead.

## What this means if you're building an MCP server

- Resources are safe to build on; deliver concrete URIs in tool results
  (Claude Code can't discover templated URIs from listings).
- Binary resources are a genuinely good artifact lane on Claude Code — the
  agent gets a real file path, no base64 in context.
- Don't design on sampling.
- Elicitation works interactively on Claude Code (≥2.1.76; battery-verified
  on 2.1.201). Keep response schemas to flat strings/enums, fail closed on
  cancel/decline/error, and scope requests to the work that issued them.
- For Codex, elicitation is blocked upstream:
  [openai/codex#31163](https://github.com/openai/codex/issues/31163).
