# Open-Issue Engineering Triage

*Snapshot recorded 2026-07-10 after reviewing every open GitHub issue and relevant upstream dependency state. GitHub remains the source of truth for current status.*

There are **8 open issues**. The queue is small, but it mixes real work, parked strategy, and historical notes.

## Highest-value issues

### [#88 — AsyncMonty migration](https://github.com/oneryalcin/toolplane/issues/88)

This is becoming active: upstream released `0.0.19-beta.2` on July 9. Toolplane is protected by `<0.0.19`, but the migration is no longer hypothetical.

Why it matters:

- dependency upgrade eventually becomes mandatory;
- subprocess-per-session improves crash isolation;
- it may affect session performance and snapshot behavior;
- it may change the answer to #109’s concurrency problem;
- the pool architecture aligns with the parked multi-tenant design.

**Recommendation:** begin a compatibility branch against the beta and run the full empirical session suite, but retain the production pin until stable upstream release.

### [#109 — Parallel calls inside snippets](https://github.com/oneryalcin/toolplane/issues/109)

This is the clearest product-performance item and directly supported by benchmark evidence. I would prioritize it more highly than the issue currently suggests.

Important sequencing: test AsyncMonty first. If it still serializes external futures, build bounded `call_many`; if upstream now provides useful concurrency, avoid duplicating machinery.

### [#113 — Benchmark follow-ups](https://github.com/oneryalcin/toolplane/issues/113)

The interesting pieces are:

1. Codex cross-client run;
2. real CLI+MCP composition task;
3. genuinely code-resistant adaptive fixture.

The optional extra crossover repetitions are low value.

**Recommendation:** split this issue. Cross-client and composite workload are independently actionable and have different acceptance criteria.

## Security issue with a cheap valuable slice

### [#108 — Security patterns](https://github.com/oneryalcin/toolplane/issues/108)

This contains three projects of very different maturity:

- **Discovery receipts:** cheap, valuable now, and directly complementary to benchmark transcripts and audit logs.
- **Credential transport audit:** worthwhile bounded security review.
- **Validate/execute hash binding:** premature until Toolplane introduces code approval.

I would split it and implement discovery receipts first. Ideally each run records hashes/versions of the search results, schemas, and manifest the agent observed.

## Small user-facing bug worth fixing

### [#93 — `mcp status` unexpectedly opens a browser](https://github.com/oneryalcin/toolplane/issues/93)

This violates an explicit read-only/browser-safe promise and leaves an orphaned OAuth page pointing at a dead callback. Even though recent import logic rewrites many `mcp-remote` configurations to direct OAuth, existing configurations remain affected.

This looks like a contained, user-visible correctness fix. A PATH shim for `open`/`xdg-open`, plus detection of the browser-open marker, is likely more robust than `$BROWSER`.

## Strategically interesting, correctly parked

### [#96 — Remote/multi-tenant Toolplane](https://github.com/oneryalcin/toolplane/issues/96)

This is an excellent design note, not a work item. Its most important content is the foreclosure rules:

- keep sessions, stores, credentials, grants, and audit ownership caller-scoped conceptually;
- do not enable process-global state over multi-client transports;
- carry identity explicitly through the bridge, as with `run_id`;
- do not expose HTTP as though it were an authenticated enterprise service.

I agree strongly with leaving it parked until there is a real design partner.

## Issue-hygiene candidates

### [#21 — Pyodide/pandas CDN test failure](https://github.com/oneryalcin/toolplane/issues/21)

Pyodide is now documented as supported but feature-frozen. The current issue should probably be resolved minimally:

- mark the CDN test as network-dependent;
- exclude it from default `make test`;
- provide an explicit integration-test command.

Vendoring pandas wheels would conflict with the feature-frozen posture. This is a test-hygiene fix, not a backend investment.

### [#58 — Learnings](https://github.com/oneryalcin/toolplane/issues/58)

The remaining important insight is:

> Monty’s safety advantage and its dialect tax are the same object.

That remains strategically relevant. But the issue body is malformed historical prose and is not actionable. I would move any unique material into docs and close it as archived learning.

## Missing GitHub issues

The most consequential findings from the benchmark review are documented in [Benchmark and Optimization Opportunities](../benchmark-opportunities-review.md), but are not yet actionable GitHub items. Consider opening issues for:

1. **Hybrid facade experiment** — direct tools plus code-mode tools under deferred loading.
2. **Native ToolSearch double-discovery reduction** — current toolplane runs still use 1–2 more model requests.
3. **Immutable/counterbalanced benchmark harness** — source fingerprinting, randomized arm order, exact request counts.
4. **Payload size and API-granularity benchmark axes.**
5. **Many-server startup measurement** — `register_mcp_config()` currently initializes servers sequentially.
6. **Longitudinal session/snapshot performance.**
7. **0.4.1 release and benchmark-headline reconciliation.**

## Suggested order

1. Spike **#88** against Monty beta.
2. Resolve concurrency direction, then implement **#109**.
3. Run Codex and composite-workload slices from **#113**.
4. Benchmark the hybrid facade and discovery topology.
5. Add discovery receipts from **#108**.
6. Fix **#93** and clean up **#21/#58**.
7. Leave **#96** parked.

The strongest existing issues are #88 and #109; the strongest missing issue is the hybrid facade experiment.
