# MCP servers

Two thin agent-facing layers over the HTTP API, for two agent shapes
(rationale: [docs/DESIGN_CHOICES.md](../docs/DESIGN_CHOICES.md) §2.9):

```
LLM agent  ⇄  MCP server (stdio or streamable-HTTP)  ⇄  Isabelle Pool Server  ⇄  Isabelle
```

| Server | Launch | Shape |
|---|---|---|
| `mcp_servers.stepwise` | `python -m mcp_servers.stepwise.app` | **Chunk-centric.** The agent submits Isar text; `verify_chunk` is the one execution tool, failed text rolls back. Env prefix `ISABELLE_MCP_`, HTTP port 8848. [stepwise/README.md](stepwise/README.md) |
| `mcp_servers.lsp` | `python -m mcp_servers.lsp.app` | **File-sync (LSP-style).** Tools take a `file_path`; the file is re-read from disk before every query and the broken state is kept for inspection; scratch execution, positional sledgehammer and the staged `fill_sorries` job. Env prefix `ISABELLE_MCP_LSP_`, HTTP port 8849. [lsp/README.md](lsp/README.md) |

Sessions and leases are managed per MCP connection; the agent never sees a
`session_id`. Each connection gets an isolated Isabelle session; a fresh
`enter_theory` always starts from a clean document. `common/` holds the shared
pieces (memory advice, JSON helpers).

## Prerequisites (where the agent runs)

```bash
cd Isabelle-Pool-Server
pip install -e ".[mcp]" -e ./client   # MCP SDK (mcp<2) + the async client
```

The MCP server needs `PYTHONPATH` at the repo root (so `client` and
`mcp_servers` import) and the Pool Server URL (`ISABELLE_MCP_SERVER_URL` /
`ISABELLE_MCP_LSP_SERVER_URL`, default `http://localhost:8000`; the older
`..._GYM_URL` name is still read). The Pool Server must be running.

## Option A: stdio (local agents: Claude Code, Claude Desktop, Cursor)

The client spawns the MCP server as a subprocess, one process per connection.

```bash
claude mcp add isabelle-pool-server \
  --env PYTHONPATH=/absolute/path/to/Isabelle-Pool-Server \
  --env ISABELLE_MCP_SERVER_URL=http://localhost:8000 \
  -- python -m mcp_servers.stepwise.app
```

or a `.mcp.json` in the project:

```json
{
  "mcpServers": {
    "isabelle-pool-server": {
      "command": "python",
      "args": ["-m", "mcp_servers.stepwise.app"],
      "env": {
        "PYTHONPATH": "/absolute/path/to/Isabelle-Pool-Server",
        "ISABELLE_MCP_SERVER_URL": "http://localhost:8000"
      }
    }
  }
}
```

Claude Desktop takes the same block under `mcpServers` in
`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or
`%APPDATA%\Claude\claude_desktop_config.json` (Windows); Cursor in
`.cursor/mcp.json` (project) or `~/.cursor/mcp.json` (global). For your own
agent loop, spawn `python -m mcp_servers.stepwise.app` (or `.lsp.app`) over
stdio with those two variables; `evaluation/MCP-comparison/common/mcp_client.py`
is a minimal working example with the `mcp` Python SDK.

## Option B: streamable-HTTP (remote server, several agents)

```bash
cd Isabelle-Pool-Server
PYTHONPATH=. ISABELLE_MCP_TRANSPORT=streamable-http \
  ISABELLE_MCP_HOST=0.0.0.0 ISABELLE_MCP_PORT=8848 \
  python -m mcp_servers.stepwise.app
```

Point HTTP-capable MCP clients at `http://<server>:8848/mcp`. Connections are
isolated from each other. There is no authentication: firewall the port or
tunnel (`ssh -N -L 8848:localhost:8848 user@server`).

## The stepwise tools

| Tool | Purpose |
|---|---|
| `enter_theory(name, imports, field)` | Start (or restart) a proof session; begins the theory header |
| `verify_chunk(text, timeout, detail)` | **The one execution tool.** One command or a whole proof; per-command status (`ok/failed/running/unprocessed`), the stuck line on timeout, automatic rollback of failures |
| `proof_state()` / `source()` | Open subgoals / the theory source as the prover sees it |
| `diagnostic(command)` | Read-only `thm`, `term`, `find_theorems`, `print_*`; never touches the proof |
| `sledgehammer(timeout_s)` | Proof search on the open goal; ready-to-paste methods |
| `checkpoint()` / `restore(id)` / `rollback()` | State management |
| `verify_batch(items, max_parallel)` | Many independent chunks concurrently across the pool |
| `close_theory()` | Dispose this connection's session |

A typical flow:

```
enter_theory(name="Scratch", imports=["Main"])
verify_chunk("theorem foo: \"rev (rev xs) = xs\"\n  by (induct xs) auto")
  → success=True proof_open=False used_sorry=False   ✓ proved
verify_chunk("theorem bar: ...\nproof -\n  have step1: ... by simp")
  → success=True proof_open=True                     (open goal kept)
sledgehammer()                                        → "by (metis ...)"
verify_chunk("  show ?thesis by (metis ...)\nqed")
```

The lsp server's tools (file sync, positional queries, `isabelle_run_code`,
`isabelle_multi_attempt`, `isabelle_sledgehammer`, the staged
`isabelle_fill_sorries`) are tabulated in [lsp/README.md](lsp/README.md).

## Memory protocol

The server reports facts, the MCP words the advice. Each MCP server keeps a
registration list of what it holds (theory / file / scratch sessions, idle
time, expiry, attributed memory; `list_my_sessions` / `isabelle_list_sessions`).
A call refused for memory comes back as `{"error": "memory_pressure",
"your_sessions": [...], "others": {...}, "advice": [...]}`: release what you
hold but do not use (`close_theory()`, `isabelle_release_idle()`,
`isabelle_close(file, destroy=true)`), retry after `retry_after_s`, and when
other clients, builds or external pressure hold the memory, stop and tell the
user. At `warning` / `critical` every result carries a `memory_reminder`;
`memory_status` / `isabelle_memory_status` show who holds what. Rationale:
[docs/DESIGN_CHOICES.md](../docs/DESIGN_CHOICES.md) §1.6 and §2.10.

## Configuration (stepwise; the lsp server uses the `ISABELLE_MCP_LSP_` prefix)

| Variable | Default | Meaning |
|---|---|---|
| `ISABELLE_MCP_SERVER_URL` | `http://localhost:8000` | The Pool Server (`ISABELLE_MCP_GYM_URL` still read) |
| `ISABELLE_MCP_FIELD` | `HOL` | Default Isabelle session for new theories |
| `ISABELLE_MCP_CHUNK_TIMEOUT` | `180` | Default wall budget (s) per `verify_chunk` |
| `ISABELLE_MCP_HTTP_TIMEOUT` | `600` | httpx timeout (must exceed the chunk timeout) |
| `ISABELLE_MCP_MAX_PARALLEL` | `4` | Cap for `verify_batch` fan-out |
| `ISABELLE_MCP_TRANSPORT` | `stdio` | `stdio` or `streamable-http` |
| `ISABELLE_MCP_HOST` / `ISABELLE_MCP_PORT` | `127.0.0.1` / `8848` | HTTP transport bind |

The lsp server adds `LOAD_TIMEOUT`, `ATTEMPT_TIMEOUT`, `SCRATCH_POOL_SIZE`,
`TASK_GROUP`, `CLOSE_DESTROYS`, `FIELD_FROM_IMPORTS` and the `FILL_*` budgets
(see [lsp/README.md](lsp/README.md)).

## Smoke test (no agent)

```bash
cd Isabelle-Pool-Server
PYTHONPATH=. python - <<'EOF'
import asyncio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

async def main():
    params = StdioServerParameters(command="python", args=["-m", "mcp_servers.stepwise.app"],
                                   env={"PYTHONPATH": ".", "ISABELLE_MCP_SERVER_URL": "http://localhost:8000"})
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            print("tools:", [t.name for t in (await s.list_tools()).tools])
            await s.call_tool("enter_theory", {"name": "Smoke", "imports": ["Main"]})
            out = await s.call_tool("verify_chunk",
                {"text": 'theorem t: "rev (rev xs) = xs" by (induct xs) auto'})
            print(out.content[0].text)
            await s.call_tool("close_theory", {})

asyncio.run(main())
EOF
```

Expected: the tool list, then `success=True proof_open=False used_sorry=False ...`.
If the subprocess dies at once (`McpError: Connection closed`), `PYTHONPATH` or
a pip dependency is missing; run `PYTHONPATH=. python -m mcp_servers.stepwise.app`
by hand to see the traceback. If `enter_theory` hangs and then errors, the Pool
Server is not running, the URL is wrong, or a heap is being built.
