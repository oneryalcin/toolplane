# Example: which enterprise customers does this release fix something for?

One 12-line program answers the question by joining three kinds of tool:

- `git`, a command-line program, for the commits since the last release
- two MCP servers, `tickets` and `customers`, for who reported each ticket
- `policy_sla_breach`, a plain Python function, for our own rule

```python
import re
log = await git("log", format="%s", _=["v1.4..HEAD"])
ids = re.findall(r"TCK-\d+", log["stdout"])
pending = [tickets_get_ticket(id=i) for i in ids]
hits = []
for p in pending:
    ticket = await p
    if ticket["reported_by_customer"]:
        customer = await customers_get_customer(id=ticket["customer_id"])
        if await policy_sla_breach(ticket=ticket, customer=customer):
            hits.append({"customer": customer["name"], "ticket": ticket["id"]})
return {"commits": len(ids), "fixed_for": hits}
```

It runs on toolplane's default sandbox (Monty). Nothing here calls a model or
any outside service, and all the data is made up and seeded in `data.py`.

## Run it

From this directory:

```bash
uv run --no-project --with-editable ../.. python make_repo.py   # builds a small git repo to query
uv run --no-project --with-editable ../.. python run.py         # runs the program and records it
uv run --no-project --with-editable ../.. python refusal.py     # shows what the sandbox and policy refuse
```

Output goes to `build/`, which is not committed.

## What the run shows

| | |
| --- | --- |
| Calls made by the one program | 181 |
| `git` | 1 |
| `tickets_get_ticket` | 100 |
| `customers_get_customer` | 40 |
| `policy_sla_breach` | 40 |
| Calls to `customers` the `if` skipped | 60 of 100 |
| Size of the answer returned | 419 bytes |
| Size of the tool results behind it | 24,990 bytes |
| Answer | 7 fixes across 4 enterprise customers, from 100 commits |

- **The counts are checked three ways.** `run.py` asserts that its own trace,
  each server's call log (100 and 40 lines) and toolplane's audit log (181
  events) agree.
- **The run is repeatable.** `result.json` and `counts.json` are
  byte-identical from run to run; only the timings in `trace.json` vary.
- **The lookups overlap.** The 100 ticket fetches are started first and then
  awaited. With 20 ms of simulated latency per MCP call (`DEMO_LATENCY_MS`)
  the whole program takes 1.1 to 1.8 seconds; with none, about 0.25.

## What is refused

`refusal.py` tries four things the program should not be able to do:

| Attempt | Result |
| --- | --- |
| `rm(...)` | `NameError`: no such function exists, because `rm` is not allowed |
| `cli_run("rm", ...)` | `PermissionError` from toolplane's command policy |
| `open("/etc/hosts")` | refused by the sandbox |
| `import socket` | refused by the sandbox |

The file `rm` was aimed at is still there afterwards.

One thing to be exact about: `git` runs on your machine, outside the sandbox.
That is why commands go through an allowlist.

## Files

| File | What it is |
| --- | --- |
| `program.py` | the program above |
| `run.py` | registers the tools, runs the program, records and checks the trace |
| `refusal.py` | the four refused attempts |
| `make_repo.py` | builds the git repo the program reads |
| `data.py` | the made-up tickets and customers, and the rule |
| `tickets_server.py`, `customers_server.py` | the two MCP servers |
| `toolplane.toml` | `git` and the two servers as a config file, for `toolplane serve mcp` |

`run.py` wraps the call path to record arguments and result sizes, because
toolplane's audit log deliberately stores neither.
