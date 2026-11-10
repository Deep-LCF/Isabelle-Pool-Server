# Isabelle Pool Server MCP server

Exposes the running Isabelle Pool Server to LLM agents via the **Model Context Protocol**.
It is a thin layer over `client.async_client.PoolAsyncClient` (no core edits beyond the
client wrappers added in Phase 0); it does **not** modify or patch Isabelle.

## What it offers

- **Auto-managed, per-connection sessions** — the agent never handles `session_id`/leases;
  each MCP connection gets its own isolated Isabelle session.
- **Parallelism, both levels**:
  - intra-proof — `verify_chunk` checks a whole chunk with `parallel_proofs=2` and returns
    per-command status (`ok/failed/running/unprocessed`) under a **single** wall budget,
    naming the `stuck_line` on timeout (no opaque timeouts);
  - inter-session — `verify_batch` fans out many independent chunks **concurrently** across
    the server's session pool in one call (bounded by `max_parallel`).

### Tools
`enter_theory`, `verify_chunk` (the one execution tool — one command or a whole proof),
`diagnostic` (read-only queries — `thm`/`term`/`find_theorems`/`print_*`, output that
`verify_chunk` discards), `proof_state`, `source`, `sledgehammer`, `checkpoint`, `restore`,
`rollback`, `close_theory`, `verify_batch`.
`list_my_sessions` (the sessions this connection holds: idle time, expiry, memory),
`memory_status` (server memory state + who holds it + advice).
### Resources
`isabelle-pool-server://health`, `isabelle-pool-server://sessions`.

### Memory protocol
The server never evicts a leased session (it is the client's) and never words advice; it
reports **facts**: every 503 body has an `error` key (`memory_pressure` with the numbers and
the sessions involved, `pool_full`, `gateway_unavailable`), every response carries
`X-Memory-State` (ok / warning / critical) and `GET /api/v1/memory` lists every session's
expiry and attributed memory. This server keeps a **registry** of what it holds (session id
and lease stay private; the agent sees a display-only `ref`), turns a memory 503 into
`{"error": "memory_pressure", "your_sessions": [...], "others": {...}, "advice": [...]}`, and
attaches a `memory_reminder` to results while the server is at warning/critical. Advice, in
order: release what you hold but do not use (`close_theory()`), retry after `retry_after_s`,
and — when the memory is held by other clients, builds, or external pressure — stop and tell
the user. Knob: `ISABELLE_MCP_ADVICE_IDLE_S` (default 120 s) — idle threshold for "not using".
### Prompts
`prove_theorem`.

## Run

Prereq: a running Isabelle Pool Server (default `http://localhost:8000`).

```bash
pip install -e ".[mcp]" -e ./client           # MCP SDK (mcp<2) + the async client
export PYTHONPATH=$PWD                        # so `client` imports

# local (stdio) — for Claude Desktop/Code, Cursor:
python -m mcp_servers.stepwise.app

# remote (Streamable HTTP):
ISABELLE_MCP_TRANSPORT=streamable-http ISABELLE_MCP_PORT=8848 python -m mcp_servers.stepwise.app
```

### Register (stdio, e.g. Claude Code / Desktop `mcp` config)
```json
{
  "mcpServers": {
    "isabelle-pool-server": {
      "command": "python",
      "args": ["-m", "mcp_servers.stepwise.app"],
      "env": { "PYTHONPATH": "/path/to/Isabelle-Pool-Server", "ISABELLE_MCP_SERVER_URL": "http://localhost:8000" }
    }
  }
}
```

## Config (env)
`ISABELLE_MCP_SERVER_URL` (default `http://localhost:8000`), `ISABELLE_MCP_FIELD` (`HOL`),
`ISABELLE_MCP_MAX_PARALLEL` (`4`), `ISABELLE_MCP_CHUNK_TIMEOUT` (`180`),
`ISABELLE_MCP_HTTP_TIMEOUT` (`600`), `ISABELLE_MCP_TRANSPORT` (`stdio`|`streamable-http`),
`ISABELLE_MCP_HOST`, `ISABELLE_MCP_PORT` (`8848`).

## Run an agent proof (Claude in the loop)

During development an agent benchmark harness drove Claude through these MCP tools to prove
theorems end-to-end and recorded **success rate, token usage, and latency** per theorem: it
spawned the MCP server over stdio, listed its tools, and ran an agentic loop against the
Anthropic API (Claude calls the tools; the harness executes them via MCP and feeds the results
back), with options for single inline statements, repeats, model choice and miniF2F problem
sets. That harness lives in the internal working notes and is not part of the repository; the
tracked equivalent for comparing MCP servers with a model in the loop is the cross-MCP
harness in `evaluation/MCP-comparison/`.

**Prerequisites for any such driver**
- A running Isabelle Pool Server (default `http://localhost:8000`) — the MCP layer wraps it.
- The model API key exported in the environment (the model must be in the loop).
- Host deps: the model SDK, `mcp`, plus the repo's client deps (`httpx`).
- `PYTHONPATH` set to the repo root so `mcp_servers` / `client` import (forward it to the spawned
  MCP server as the subprocess's `PYTHONPATH`).

