# The Codex Elicitation `title` Bug

A debugging narrative: how a one-line anomaly in a headless probe became an
upstream bug report ([openai/codex#31163](https://github.com/openai/codex/issues/31163)),
with both sides of the incompatibility verified from source. Recorded
because the method matters as much as the finding: probe first, root-cause
from primary sources, verify both ends before filing.

## The anomaly (2026-07-02)

While probing MCP client capabilities headlessly (see the
[capability matrix](mcp-client-capability-matrix.md)), Codex CLI 0.142.4
declared `elicitation: {form, url}` in its initialize handshake — but every
`elicitation/create` request from our FastMCP server failed with:

```
unknown field 'title', expected one of '$schema', 'type', 'properties', 'required'
```

Claude Code, sent the identical request, handled it (auto-cancel headless,
real form interactively). Same server, same schema, one client chokes on a
field name. That error text smells like Rust's serde with
`deny_unknown_fields` — which turned out to be exactly right.

## The root cause, from both sides

**Codex side** (verified against `openai/codex` main, 2026-07-05):
`McpElicitationSchema` in
`codex-rs/app-server-protocol/src/protocol/v2/mcp.rs` is declared

```rust
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct McpElicitationSchema {
    // $schema, type, properties, required — and nothing else
}
```

No top-level `title` field, and `deny_unknown_fields` makes any extra
member a hard parse failure. Inconsistently, the *property-level* schemas
in the same file (`McpElicitationStringSchema`, etc.) all accept `title`.

**FastMCP side** (verified empirically, fastmcp 3.4.2): the wire schema for
a plain `ctx.elicit(..., response_type=str)` is

```json
{
  "properties": {"value": {"title": "Value", "type": "string"}},
  "required": ["value"],
  "title": "ScalarElicitationType",
  "type": "object"
}
```

Pydantic auto-generates that top-level `"title"`, and FastMCP's schema
compression keeps it (`prune_titles=False` by default). Every
Pydantic-based MCP server in the wild emits this shape.

## Who owns the bug

The MCP spec adjudicates it. The normative `schema/2025-11-25/schema.json`
does **not** set `additionalProperties: false` on
`ElicitRequestFormParams.requestedSchema`, so a top-level `title`
*validates* against the spec. And the official Rust SDK agrees: rmcp's
`elicitation_schema.rs` models a top-level `title: Option<...>` and does
not use `deny_unknown_fields`.

So: primarily a Codex bug — its parser is stricter than the normative
schema and stricter than the reference SDK it could have reused. FastMCP
is secondarily sloppy (it emits a field outside the spec's *defined*
shape), but it is not in violation.

## Status and impact

- Filed as [openai/codex#31163](https://github.com/openai/codex/issues/31163)
  (2026-07-05) with a minimal repro; no prior report existed. The fix is
  one line: add `title: Option<String>` or drop `deny_unknown_fields`.
- Unfixed as of codex-cli 0.143.0-alpha.36 (2026-07-05).
- Impact: Codex has had a real elicitation form UI since April 2026
  (PR #17043), but no FastMCP-based server can reach it. Any elicitation
  feature must degrade gracefully on Codex — toolplane's CLI-escalation
  flow returns exactly the plain policy refusal there, by construction.
- Server-side workaround, if you need Codex elicitation before the fix:
  prune the top-level title from your `requestedSchema` before sending.

## The method, for reuse

1. **Instrument, don't infer**: a ~60-line probe server with a self-test
   mode, so every failure is attributable to the client, not the harness.
2. **Root-cause from primary sources**: the client's actual source file,
   the actual wire bytes — not documentation or vibes.
3. **Adjudicate with the spec** before assigning blame across projects.
4. **Verify both ends firsthand before filing.** The upstream report cites
   the exact struct, the exact wire schema, and the normative schema —
   nothing in it is secondhand.
