# Code Mode in the Wild: A Survey

*2026-07-09 · compiled from three parallel research sweeps (vendor/platform,
academic, community/OSS) with primary-source verification. Confidence labels:
**[V]** = primary source read during the sweep; **[S]** = search-level
evidence, not fully verified. Corrections welcome via issues — this page is
maintained the same way as the [capability matrix](mcp-client-capability-matrix.md).*

Code mode — agents writing programs against tool APIs instead of emitting
one tool call per model turn — went from blog-post idea to industry
consensus in roughly 14 months. This survey maps who built what, what they
measured (and, mostly, didn't), and where [toolplane's own
measurements](code-mode-benchmark.md) sit relative to prior work.

## Platform and vendor work

**Anthropic.** "Code execution with MCP" (Nov 2025) [V] presented MCP
servers as a filesystem of TypeScript files the agent explores and imports;
headline number 150K→2K tokens (98.7%) on one contrasted example, no
methodology. "Advanced tool use" (Nov 2025) [V] shipped three API betas:
the Tool Search Tool (deferred tool loading + server-side search; ~72K→8.7K
tokens, and *accuracy* gains — Opus 4 49%→74% on their MCP eval — the
strongest published evidence that context bloat hurts selection quality,
not just cost), Programmatic Tool Calling (Claude writes Python that calls
opted-in tools inside Anthropic's sandbox; 43.6K→27.3K tokens (37%),
accuracy flat; a 2026 75-tool benchmark reported ~38% billed-input
reduction [S]), and Tool Use Examples (72%→90% parameter accuracy).
Anthropic's guidance gates tool search behind ">10K tokens of definitions /
10+ tools" — an implicit admission that discovery overhead loses on small
surfaces. Claude Code ships deferred tool loading (ToolSearch) and
instructs servers to batch loads "because each separate call wastes a full
round-trip" [V, firsthand].

**Cloudflare.** "Code Mode" (Sep 2025) [V] converts MCP schemas into typed
TypeScript APIs executed in V8 isolates; no numbers, no discovery mechanism
(the whole generated API loads upfront). "Code Mode MCP" (Feb 2026) [V] is
the interesting one: the 2,500-endpoint Cloudflare API behind exactly two
tools, `search()` + `execute()` — and `search()` is not keyword lookup; the
agent writes code that filters a fully-resolved OpenAPI spec object *inside
the sandbox*, returning only matching summaries to context. Discovery
itself became a programmable, single-turn operation. Claim: 1.17M→~1K
tokens — a static context-size comparison, tiktoken-counted.

**OpenAI.** Tool search / `defer_loading` in the Responses API (2026,
gpt-5.4+) [V] — a direct analog of Anthropic's tool search, with a hosted
mode that injects matched tools in the *same* response (zero-turn
discovery) and a design detail that matters: loaded tools are appended at
the end of context specifically to preserve the prompt cache. No code-mode
product; no published numbers ("may help reduce").

**fastmcp / Prefect.** FastMCP 3.1's experimental CodeMode transform (Mar
2026) [V] collapses a server's tools into Search/GetSchemas/execute
meta-tools, sandboxed in **Pydantic Monty** — the same interpreter family
toolplane uses. Discovery depth is explicitly tunable ("collapse to two
stages or skip discovery entirely"). Numbers are user anecdotes (50K→2-3K)
with no methodology.

**Google, Microsoft, AWS.** No code-over-tools product found. Gemini has
compositional function calling and code execution as separate features [S];
Microsoft Agent Framework's "code-first" means developer DX, not
agent-written code [S]; AWS Bedrock AgentCore has a code-interpreter
primitive and an API→MCP gateway — adjacent infrastructure [S].

**MCP spec.** Dynamic tool discovery (SEP-1821) is still a draft seeking a
sponsor [V]; the 2026 roadmap targets transport/caching of discovery
(`ttlMs` on list results, `server/discover`), not model-facing search [V];
skills-over-MCP working groups are discussing the many-tools problem [S].
The community's sharpest concern about code mode is *auditability* — code
execution makes it harder to inspect what data became model-visible [S].

## Academic lineage

**The anchor**: CodeAct (Wang et al., ICML 2024, arXiv:2402.01030) [V] —
code actions beat JSON tool-calling on 12 of 17 models, up to 20% higher
success, up to ~30% fewer turns. It is the number everyone still quotes
(HuggingFace smolagents' "30% fewer steps" is inherited from it, not
independently measured [V]). Its baselines are strictly
one-call-per-turn with **no prompt caching and no parallel tool calling** —
both now standard in production clients, which is precisely the gap
toolplane's benchmark measured into.

**Closest to a crossover**: "From Tool Orchestration to Code Execution: A
Study of MCP Design Choices" (arXiv:2602.15945, Feb 2026) [V] — code
execution's token savings *grow with task complexity*, and on some
complex multi-server tasks code execution scores **lower** task
fulfillment (bad global orchestration decisions); iterative/adaptive tasks
sometimes favor classic multi-turn MCP. Qualitative envelope, no numeric
crossover, no caching/parallel accounting.

**The missing join**: "Don't Break the Cache" (arXiv:2601.06007, Jan 2026)
[V] measured prompt caching on agent workloads — 41–80% cost reduction,
13–31% TTFT improvement across providers. No paper joins this with a
code-vs-tool-calling comparison.

**Tool retrieval/discovery**: ToolLLM (arXiv:2307.16789), Gorilla
(arXiv:2305.15334), AnyTool (arXiv:2402.04253, +35.4% over ToolLLM via
hierarchical retrieval), MCP-Zero (arXiv:2506.01056, model-initiated tool
requests, 98% schema-token reduction), SING (arXiv:2606.16591, retrieval
that follows evolving task state), MemTool (arXiv:2507.21428, dynamic tool
add/remove over 100-turn conversations — weak models fail at pruning).
All measure retrieval *accuracy* or schema-token *savings*; none measure
the model-turn cost of the discovery phase itself. CodeNav (AI2,
arXiv:2406.12276) is the nearest spirit — the agent finds its own tools by
searching a codebase, with a measured source-code-vs-descriptions ablation
— but reports success rates, not discovery cost.

**Interface design for models**: SWE-agent's ACI work (arXiv:2405.15793)
[V] is the canonical "design the interface for the LM" citation. There is
**no controlled study of flat vs scoped namespaces or docstrings vs JSON
schemas** as presented to models — an open gap.

**Benchmarks that price things**: BFCL reports per-model cost/latency
columns; TPS-Bench (arXiv:2511.01527) reports token usage, wall time,
turns, and cost-of-pass (expected $ per successful completion) — the
academically respectable cousin of what [toolplane's
harness](https://github.com/oneryalcin/toolplane/tree/main/bench)
measures per task.

## Community and OSS

Sandboxes cluster by substrate: **V8/Deno/TypeScript** (Cloudflare, goose's
pctx backend, StackOne, codeforge-mcp), **Monty/Python** (fastmcp CodeMode,
pydantic-ai-harness Code Mode, toolplane), **Node VM contexts**
(universal-tool-calling-protocol/code-mode — a known-escapable isolation
primitive), **Pyodide/WASM** (pydantic mcp-run-python, langchain-sandbox),
**containers** (elusznik's rootless-container implementation).

Notable projects [V unless noted]:

- **goose (Block)** shipped Code Mode in v1.17 (Dec 2025) backed by
  **pctx** (Rust, Deno sandbox, typed TS over aggregated MCP servers).
  Goose's own experiment: 30% fewer tokens on a real task — and an explicit
  concession that code mode is *slower for single-tool tasks* due to
  discovery and code-writing overhead. The only vendor-adjacent
  acknowledgment of the crossover.
- **AAIF / Port of Context production case study** (Jun 2026): the only
  production A/B found anywhere — code mode ON vs OFF on a GTM agent:
  100% vs 56% delivery rate, $0.20 vs $0.41/run, 31.8K vs 93.6K input
  tokens. One workload; mechanism was Promise.all batching plus large
  payloads staying in-sandbox.
- **pydantic-ai-harness Code Mode** wraps agent tools into one `run_code`
  tool executed in Monty; no published benchmarks; converging on the same
  substrate as toolplane from the framework side.
- **mcp-use** (10K+ stars) added code mode as a feature (Nov 2025) [S].
- **StackOne** (Feb 2026): search_tools + execute_code at the integration-
  platform tier; one workflow measured 96% context reduction.
- **Composio** offers intent-based dynamic tool loading and is adding
  sandboxed code execution [S] — and disclosed a May 2026 incident where
  malicious tool definitions escalated to arbitrary code execution in
  their sandbox environment: the standing cautionary citation for
  aggregator-side code execution.

Ideas from the field worth adopting (tracked in toolplane issues):
network-layer credential injection so secrets never enter the sandbox at
all (codeforge-mcp); "discovery receipts" — an auditable record of what
discovery surface the agent actually saw (mcp-gateway-registry); a
validate/execute split with hash-binding of validated code (the strongest
security critique of the pattern, Apr 2026); discovery-inside-the-sandbox
(Cloudflare, above).

## Discovery mechanisms: four clusters

1. **Upfront typed stubs** — the whole generated API in context
   (smolagents, Cloudflare Agents SDK). Zero discovery turns, maximal
   context cost; only viable for small surfaces.
2. **Meta-tool search** — search/schemas/execute tools (goose, StackOne,
   fastmcp CodeMode, **toolplane**). Pay-per-use context, costs
   sequential model turns — the measured 3–5-turn discovery tax.
3. **Filesystem-as-API** — tools as importable files the agent explores
   (Anthropic's blog pattern). Turns spent in `ls`/`cat` instead of
   search calls; same tax, different currency.
4. **Platform-native deferred loading** — the API provider searches and
   injects tools server-side, same response (Anthropic Tool Search,
   OpenAI hosted tool search). Zero-turn discovery, but provider-locked
   and tool-calling-shaped rather than code-shaped.

Cloudflare's Feb 2026 design is a fifth point emerging: **discovery as
sandboxed code over a spec object** — one turn, arbitrarily expressive,
and the strongest existing answer to the discovery tax.

## Novelty assessment: what is and isn't new here

Claims we believe are **novel** to [toolplane's benchmark](code-mode-benchmark.md),
after three sweeps (standing offer: tell us what we missed):

1. **A numeric cost/latency crossover** (~30–100 tool interactions/task in
   a production client) — every other published number is a static
   context-size percentage; the closest prior art (arXiv:2602.15945) is
   qualitative; the closest practitioner data (goose, AAIF) is one-sided
   or single-workload.
2. **The mechanism decomposition under modern client economics** — with
   prompt caching and parallel tool batching accounted for, code mode's
   advantage is output-token scaling + context growth, *not* round-trip
   latency (the code-mode arm made 2–3x more API round-trips and won
   anyway). CodeAct-lineage baselines predate both features.
3. **The discovery tax quantified in model turns** (3–5 turns + retry
   before the first snippet, fixed in N) — vendors engineer around it and
   the retrieval literature measures around it, but nobody prices it.

What prior work does **better** than us, honestly: accuracy evaluation at
scale (Anthropic's MCP evals), production A/B evidence (AAIF), retrieval
quality at 1K–16K-tool scale (AnyTool, MCP-Zero, SING), security analysis
(the 16-attack-class catalog in arXiv:2602.15945), and peer review (we
have none). Our n=3-per-cell, one-client, one-model envelope is a first
measurement, not a final one.

## Implications for toolplane

- The discovery tax is now a product KPI with a measurable target
  ([#106](https://github.com/oneryalcin/toolplane/issues/106)); the
  Cloudflare discovery-in-sandbox design is the strongest input.
- Benchmark rigor roadmap — localize the crossover, add the task shapes
  where prior work says code mode *loses* (iterative/adaptive
  orchestration per arXiv:2602.15945), more reps with dispersion,
  cost-of-pass metric — tracked in
  [#107](https://github.com/oneryalcin/toolplane/issues/107).
- Security patterns from the field (network-layer credential injection,
  validate/execute split, discovery receipts) — tracked in
  [#108](https://github.com/oneryalcin/toolplane/issues/108).
