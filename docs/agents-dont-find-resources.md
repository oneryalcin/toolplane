# Agents Don't Find Resources

A design lesson from building toolplane's discovery surface, verified live
against real agents. It generalizes to any MCP server: **exposing a
capability is not the same as an agent discovering it.** If you ship a
resource, a template, or a clever affordance and don't put a plain-text
pointer to it in a channel the agent already reads, you shipped nothing.

## The experiment that settled it

While building toolplane's namespace manifest (a live MCP resource
describing every binding an `execute_code` snippet can use), we ran cold
sessions: a fresh Claude Code agent, the toolplane server connected, a task
that the manifest would make easy. The agent had the resource-listing tool
available from turn one.

It never called it. Asked directly afterward — *"if we hadn't told you the
server exposes resources, would you have discovered them?"* — the agent's
answer was no. It guessed capability names, misread error text, and
retried, but at no point did "enumerate the server's resources" enter its
loop. The capability was present; the behavior wasn't.

That single observation reshaped the whole design: **the resource is the
payload; the pointer is the feature.**

## The three channels agents actually read

Discovery works only through text that arrives in the agent's context in
the normal course of work:

1. **Tool descriptions and server instructions** — read once, at session
   start. This is where standing directives live: toolplane's
   `execute_code` description says *"Read the toolplane://namespace
   resource BEFORE writing code — guessing shapes fails."* Cold agents
   follow it.
2. **Tool response text on the happy path** — read at the moment of need.
   When a toolplane snippet saves an artifact, the response carries the
   handle *and* its resource URI; the agent never has to know that
   artifacts are enumerable, because the one it needs is already named.
3. **Error text on the failure path** — the strongest channel, because a
   failing agent is a searching agent. Every dead end must emit a
   signpost: toolplane's no-match search reports how many capabilities
   exist and how to list them; a policy refusal names the allowed
   binaries; a wrong call shape shows the right one, verbatim, ready to
   paste.

Listings, templates, and documentation pages are not channels. Claude Code
doesn't even show templated resource URIs in its listing (see the
[capability matrix](mcp-client-capability-matrix.md)) — but even where
listings work, agents don't browse; they act, fail, and read the error.

## The corollary we learned the hard way: signposts can dangle

The doctrine has a failure mode of its own, found when toolplane grew an
embedded mode (`runtime.as_tool()` — the same runtime as a single tool
inside pydantic-ai / OpenAI Agents SDK / LangGraph, no MCP anywhere).

A live agent run misremembered a capability name. The error helpfully
said: *search capabilities with an empty query, or read the
toolplane://namespace resource.* Neither exists in embedded mode — there
is no search tool and no resource protocol. The agent obediently tried to
"search" through the only tool it had, hit a second dead end, and gave up
on a task it was fully equipped to complete.

So the refined rule: **a signpost must only point at affordances that
exist in the reader's context — or carry the answer inline.** The fix was
to make the dead ends self-contained: the unknown-capability error now
lists the registered names itself; a miss on a CLI binary's name teaches
the correct call shape in the message; the tool description shows a real
canonical id instead of a `"canonical:name"` placeholder (which one model
read literally, inventing `canonical:git`). After the change, the same
agent completed the same task — the signposts walked it there.

## Design rules, compressed

- Every surface ships with its pointer, in a channel from the list above.
  A resource without a pointer is dark matter.
- Every dead end teaches the next move. If an error can be hit by a wrong
  guess, the message must contain the right guess.
- Signposts name only what exists *here* — same server, same mode, same
  policy — or include the answer inline.
- Deliver concrete URIs in results; never rely on the agent enumerating.
- Test discovery the only way that counts: a cold agent, a real task, no
  hints. If it needs a human to say "there's a resource for that," the
  pointer mesh has a hole.

Everything above was verified against live agents (Claude Code cold
sessions for the MCP mode; claude-sonnet-5, an OpenAI model, and a
LangGraph ReAct agent for embedded mode) — the failures described are
transcripts, not thought experiments.
