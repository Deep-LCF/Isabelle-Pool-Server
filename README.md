# Isabelle Pool Server

**Isabelle Pool Server** turns the Isabelle theorem prover into a managed, concurrent
service: a pool of warm proof sessions behind a REST API, with lease-based ownership,
memory-aware admission control and Prometheus observability. It is the Isabelle
counterpart of [Kimina Lean Server](https://github.com/project-numina/kimina-lean-server),
extended with checkpoints and rollback, chunk and whole-theory verification, a heap pool,
server-side sledgehammer on every `sorry` of a proof, and two MCP servers for LLM agents.

It serves three purposes on one core:

- a **training and evaluation environment** for LLM provers: small-step execution (one
  Isar command at a time, with checkpoints and rollback), chunk verification
  (`verify_chunk`, a whole proof in one PIDE edit with per-command status) and big-step
  verification (whole `.thy` files through `isabelle build`);
- a **verification service** for single theories or batch workloads: result caching,
  pre-built heaps, pool-level parallelism (the batch loop lives in the client or the MCP
  layer; there is no server-side job queue);
- a **platform for many concurrent agents**: the MCP servers give each agent an isolated
  session over a shared prover pool, inside a trusted network (no authentication; agents
  are isolated from each other, not coordinated).

It is implemented by Xuanwei Ren (University of Edinburgh) and grew out of two earlier,
unpublished REPL prototypes by Tom Milan (University of Cambridge) and Zijing Li
(University of Edinburgh). The design rationale is in
[docs/DESIGN_CHOICES.md](docs/DESIGN_CHOICES.md); the bug log and dated work log in
[docs/ISSUES.md](docs/ISSUES.md).

## Repository map

| Path | What it is |
|---|---|
| `server/` | FastAPI service: session pool, leases, memory admission, metrics; endpoints under `server/app/api/v1/routes/` |
| `server/repl/` | Scala/ML Isabelle backend (PIDE sessions, one shared gateway JVM), launched only by the server; [server/repl/README.md](server/repl/README.md) |
| `client/` | Async Python HTTP client (`PoolAsyncClient`), its own package, httpx only |
| `mcp_servers/` | The two MCP servers for LLM agents and their shared code; [mcp_servers/README.md](mcp_servers/README.md) |
| `deploy/` | Dockerfile, setup scripts, systemd unit, monitoring configs, operations and troubleshooting; [deploy/README.md](deploy/README.md) |
| `evaluation/` | Benchmark scripts and results, the MCP-comparison harness, memory-scaling and training-metrics studies; READMEs per folder |
| `examples/` | `demo.ipynb` (HTTP client and both MCP servers end to end), demo theories, a heap demo project |
| `tests/` | Unit tests, no Isabelle needed (`pytest` from the repo root) |
| `docs/` | Design choices, issues and work log, installation by hand, feature inventory, experiment write-ups |
| `archive/` | Frozen material (the RC0 image notes) |

## Setup

Two paths, the same HTTP API on port 8000 at the end. The scripts are described step by
step in [docs/INSTALL_MANUAL.md](docs/INSTALL_MANUAL.md); sizing, operation and
troubleshooting are in [deploy/README.md](deploy/README.md). The project targets
**Isabelle2026** (the 2026 release candidates also build; Isabelle2025-2 and earlier do
not, the PIDE header API changed in 2026-RC1).

**Docker** (recommended; Docker Engine + Compose v2, 10 to 30 min for the first build):

```bash
git clone https://github.com/Deep-LCF/Isabelle-Pool-Server.git && cd Isabelle-Pool-Server
./deploy/setup.sh --verify            # --install-docker on a bare Ubuntu/Debian box
```

**Native Linux** (Ubuntu 22.04/24.04 or similar, `sudo`):

```bash
git clone https://github.com/Deep-LCF/Isabelle-Pool-Server.git ~/Isabelle-Pool-Server
cd ~/Isabelle-Pool-Server && ./deploy/native_setup.sh --system --systemd --verify
```

Both create `.env`, start the server, wait for `/healthz`, and with `--verify` prove
`lemma True by simp` through the API. `--build-heaps "HOL-Library HOL-Analysis"` prebuilds
heaps for heavy imports; without them the first session on such imports builds the heap
on demand, which takes minutes.

## Usage

The server listens on `0.0.0.0:8000` with **no authentication**: firewall the port or
tunnel over SSH. `/healthz` is liveness, `/readyz` readiness (200 once the gateway JVM is
up), `/` the version and pool summary, `/docs` the interactive API, `/metrics` Prometheus,
`/admin` the console, `GET /api/v1/memory` the memory tier and every session's attributed
memory.

### The HTTP API in four calls

Sessions are **leased**: `acquire` returns a `session_id` and a `lease_id`; every mutating
call carries the lease in `X-Lease-Id`; `release` hands the session back to the warm pool.

```bash
# 1. acquire a warm session (or create one) on HOL with Main imported
RESP=$(curl -s -X POST localhost:8000/api/v1/sessions/acquire \
  -H 'Content-Type: application/json' -d '{"theories": ["Main"], "field": "HOL"}')
SID=$(echo "$RESP" | python3 -c 'import json,sys; print(json.load(sys.stdin)["session_id"])')
LEASE=$(echo "$RESP" | python3 -c 'import json,sys; print(json.load(sys.stdin)["lease_id"])')

# 2. begin a theory (the server writes the `theory Scratch imports Main begin` header)
curl -s -X POST "localhost:8000/api/v1/sessions/$SID/enter_theory/Scratch" \
  -H 'Content-Type: application/json' -H "X-Lease-Id: $LEASE" -d '{"imports": ["Main"]}'

# 3. verify a proof chunk under one wall budget (seconds)
curl -s -X POST "localhost:8000/api/v1/sessions/$SID/verify_chunk" \
  -H 'Content-Type: application/json' -H "X-Lease-Id: $LEASE" \
  -d '{"chunk": "theorem t: \"rev (rev xs) = xs\" by (induct xs) auto", "timeout": 60}'
# → {"success": true, "proof_open": false, "used_sorry": false, "timed_out": false,
#    "stuck_line": null, "commands": [{"index": 0, "line": 1, "kind": "theorem", "status": "ok", ...}]}

# 4. release the lease (the session stays warm for the next caller)
curl -s -X POST "localhost:8000/api/v1/sessions/$SID/release" -H "X-Lease-Id: $LEASE"
```

**The one rule for judging a result:** a theorem is proved only when `success=true` **and**
`proof_open=false` **and** `used_sorry=false`. `success` alone means no command errored.
A `timeout` is a hard wall budget: the expired command is rolled back and reported with
`stuck_line`; nothing keeps running in the background.

Other endpoints, all under `/api/v1/sessions/{id}` with a lease: `POST commands` (one
Isar command), `GET state` / `subgoals` / `goals` / `source` / `facts/local` /
`facts/global`, `POST checkpoints` / `checkpoints/{id}/restore` / `rollback`,
`POST diagnostic` (read-only `thm`, `term`, `find_theorems`), `POST sledgehammer`,
`PUT document` (replace the whole theory text, incremental sync), positional queries
(`command_at_line`, `hover`, `definition`, `sledgehammer_at`), and the fill job
(`POST`/`GET`/`DELETE fill_sorries`). Lease-free: `POST /api/v1/sessions/bigstep`
(whole-theory `isabelle build`), `POST /api/v1/parse_theory_header`,
`GET /api/v1/heaps/available`.

### Sledgehammer and the fill job

`POST …/sledgehammer` and `…/sledgehammer_at` report `success` only when a proof was found
and expose `outcome` (`proved` | `none` | `timeout` | `error`) and `method`. A run that
outlives its budget (the ATP timeout plus a grace of half the timeout, 30 s at least)
answers 504 with the budget named.

`POST …/fill_sorries` runs a staged job on every `sorry` of a submitted proof and returns
the patched text. Per sorry, cheapest first: `try0` (Isabelle's cheap closers, 5 s per
method); `nitpick` on what try0 cannot close, so a false statement comes back as outcome
`false` with its counterexample; sledgehammer; a structured-proof retry when a prover
found a proof the one-liner could not reconstruct; and a split of a failed conjunction,
universal, implication or equivalence into `intro` leaves attacked in a second pass.
`parallel` sibling sessions share the work under the memory-tier slots. Each row says
which stage settled it and carries the goal text; the status carries the stage counts.

### The Python client

```bash
pip install -e ./client            # httpx only; or copy client/ into your project
```

```python
import asyncio
from client import PoolAsyncClient

async def main():
    async with PoolAsyncClient("http://localhost:8000") as ips:
        s = await ips.acquire_session(theories=["Main"], field="HOL")
        sid, lease = s["session_id"], s["lease_id"]
        try:
            r = await ips.verify_chunk(sid, 'theorem t: "rev (rev xs) = xs" by (induct xs) auto',
                                       timeout=60, lease_id=lease)
            print("proved:", r["success"] and not r["proof_open"] and not r["used_sorry"])
        finally:
            await ips.release_session(sid, lease_id=lease)

asyncio.run(main())
```

The client mirrors the HTTP API (`execute_command`, `get_proof_state`, checkpoints,
`diagnostic`, `load_document`, `sledgehammer`, `fill_sorries`, `verify_bigstep_text` /
`verify_bigstep_file`). [examples/demo.ipynb](examples/demo.ipynb) walks through all of it.

### Connecting an LLM agent (MCP)

Two MCP servers sit on the HTTP API: `mcp_servers.stepwise` (chunk-centric; the agent
submits Isar text and `verify_chunk` is the one execution tool) and `mcp_servers.lsp`
(file-sync; tools take a file path, with scratch execution, positional sledgehammer and
the staged `fill_sorries`). Sessions and leases are handled per connection; the agent
never sees a session id. The quickest start, for Claude Code:

```bash
pip install -e ".[mcp]" -e ./client
claude mcp add isabelle-pool-server \
  --env PYTHONPATH=/absolute/path/to/Isabelle-Pool-Server \
  --env ISABELLE_MCP_SERVER_URL=http://localhost:8000 \
  -- python -m mcp_servers.stepwise.app
```

Configuration for other clients, the streamable-HTTP transport, the tool tables, the
memory protocol and a smoke test without an agent are in
[mcp_servers/README.md](mcp_servers/README.md); the lsp server's tools in
[mcp_servers/lsp/README.md](mcp_servers/lsp/README.md).

## Evaluation and development

- Benchmarks and results: [evaluation/results/README.md](evaluation/results/README.md);
  the MCP comparison harness: [evaluation/MCP-comparison/README.md](evaluation/MCP-comparison/README.md);
  memory scaling and training metrics have their own READMEs under `evaluation/`;
  experiment write-ups under [docs/experiments/](docs/experiments/).
- Developer reference: [CLAUDE.md](CLAUDE.md) / [AGENTS.md](AGENTS.md) (architecture,
  conventions, environment variables), [docs/FEATURE_INVENTORY.md](docs/FEATURE_INVENTORY.md).
- Tests: `pytest` from the repo root (about 870 cases, no Isabelle needed).

## How this work relates to other Isabelle agent tooling

Several groups connected LLM agents to Isabelle in 2025 and 2026. They differ in where
the prover session lives and who owns it; the Pool Server's place is the shared pool.

- **[Isabelle-MCP](https://github.com/xqyww123/Isabelle-MCP)** (Qiyuan Xu; LGPL-2.1; on
  PyPI as `isabelle-mcp`). A Python MCP server over an Isabelle/Scala `isabelle mcp_server`
  component that speaks the LSP/PIDE protocol: one headless session per server process,
  tools that evaluate a theory file up to a position and read goals, hover, definitions,
  `find_theorems` and command output, plus an Isabelle/ML debugger; by design one agent per
  process and no human in the loop. The Pool Server's `lsp` MCP server has the same shape of
  tools (a file path in every call, PIDE markup read back per command) but puts them over a
  shared pool with leases, scratch sessions, a sledgehammer tool and the `fill_sorries` job.
- **Qiyuan Xu's other pipelines.** [Isa-REPL](https://github.com/xqyww123/Isa-REPL) (a socket
  REPL with a Python client, rollback, concurrent files and sledgehammer through
  [`auto_sledgehammer`](https://github.com/xqyww123/auto_sledgehammer)) is the lineage the
  Pool Server's small-step backend continues; [Isa-Mini / Minilang](https://github.com/xqyww123/Isa-Mini)
  (OOPSLA 2026, [arXiv:2507.18885](https://arxiv.org/abs/2507.18885)) replaces Isar with a
  ten-operation proof language whose `END`/`NEXT` steps are discharged by `auto` and an
  improved sledgehammer, and its agent-over-Minilang ([arXiv:2607.16372](https://arxiv.org/abs/2607.16372))
  tries sledgehammer before the model; [Theory-Agent-over-Trees](https://github.com/xqyww123/Theory-Agent-over-Trees)
  is an MCP server for editing whole theory trees. The server-side `fill_sorries` job here
  is the pool-level counterpart of "sledgehammer first, model second".
- **[Isabelle/Q (I/Q)](https://github.com/awslabs/AutoCorrode/blob/main/iq/README.md)**
  (AWS Labs, inside the AutoCorrode repository; MIT). An Isabelle/jEdit plugin that exposes
  the editor's live PIDE session as an MCP server on a local TCP port, with token
  authentication, document tools (`read_file`, `write_file`, `get_sorry_positions`, an
  `explore` overlay for proof steps, sledgehammer and `find_theorems`) and, through the
  sibling I/R component, a REPL with fork, merge and `repl_sledgehammer`. It is the system
  behind "Just Type It in Isabelle!" ([arXiv:2604.15713](https://arxiv.org/abs/2604.15713)).
  I/Q is interactive and editor-attached: one human's session, one agent beside them. The
  Pool Server is headless and multi-tenant; its `evaluation/MCP-comparison/` harness ran the
  two side by side.
- **[Isabelle PIDE AI](https://github.com/kappelmann/isabelle-pide-ai)** (Kevin Kappelmann;
  LGPL-3.0; formerly *PIDE MCP*, the name its forks such as `danmatichuk/isabelle-pide-mcp`
  still carry; preprint at the CICM 2026 AI4Math workshop, [Zenodo](https://doi.org/10.5281/zenodo.21519365)).
  A headless, editor-agnostic PIDE frontend as an Isabelle/Scala component, serving MCP over
  stdio or TCP and a command protocol, with `read`/`edit`/`get_state`/`find_entities`
  tools, several sessions per server, a plugin system for tools and agent skills, and no
  sledgehammer tool by design. Its paper's comparison frame (headless PIDE, editor-attached,
  LSP, REPL, batch build) places the Pool Server as all of REPL, headless PIDE and build in
  one service, which none of the above combine, plus pooling, leases and admission control,
  which none of them have.

What the Pool Server adds to this landscape is the operational layer: a pool of warm sessions
shared by many agents under lease-based ownership and a memory-aware admission gate;
small-step, chunk and whole-theory verification behind one API; and server-side automation
(`fill_sorries`) that runs sledgehammer, cheap closers and counterexample search on every
`sorry` of a submitted proof. It is the Isabelle counterpart of the Kimina Lean Server.

Two harnesses are built on it and drive its development:

- **[nearAI_Isabelle](https://github.com/FullBackward/nearAI_Isabelle)**: an Isabelle port
  of NEAR AI's minimal PutnamBench harness (one model, a file, a verifier), used to measure
  what automation at the proof-closing step buys a model on the IMO 2026 problems. Its two
  arms, per-goal sledgehammer against a `sorry` skeleton filled by this server's
  `fill_sorries`, are where the staged filler, the sledgehammer budget fix and the
  `FIELD_FROM_IMPORTS` feature of the `lsp` MCP server came from.
- **[isabelle-humanize](https://github.com/FullBackward/isabelle-humanize)**: a port of the
  humanize build-and-review loop (one agent builds, an independent agent reviews, a drift
  state machine decides) to Isabelle/HOL, with this server's bigstep `isabelle build` as the
  ground-truth arbiter and the `lsp` MCP server as the builder's tools.
