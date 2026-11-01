# Training metrics: IPS as a training and evaluation environment

The experiments behind the technical report's training-reader questions (report §4.3, the question table),
on the HOL-Analysis corpus, deterministic, no model in the loop:

| Question | Experiment | Script |
|---|---|---|
| How many verified rollouts per hour? | **T6** rollout rate: T6a whole-proof groups, T6b step-level paths (W 1 / 8 / 32) | `14_run_t6.sh` |
| (the step rate and its scaling; the comparison) | **T1** throughput (W 1 … 32) with the **PIDE MCP row** (W 1, 8) | `10_run_t1_t2.sh` |
| How long does one verify call take? | **T2** latency: a by-product of the T1 cells (D11) | — |
| Can I branch and backtrack cheaply? | **T3** branch cost: checkpoint / restore against a new node and a new session | `13_run_t3.sh` |
| Does "proved" in-session mean proved cold? | **T4** agreement: the corpus and a mutation suite against cold builds | `12_run_t4.sh` |
| Can one bad step stall a worker? | **T5** bounded step: seeds that loop, under budgets; one worker in a loop beside the others | `11_run_t5.sh` |

The design is `claude-work/2026-10-22-plan-training-metrics/EXPERIMENT_DESIGN.md` (v0.4), the plan
`IMPLEMENTATION_PLAN.md` (v1.7) beside it. `SERVER_GUIDE.md` is the same procedure as numbered steps for the
Ubuntu server; if the two differ, this README is right.

## 0. Prerequisites (the server)

- The memory-scaling setup (`../memory_scaling/SERVER_GUIDE.md` §3): IPS native, Isabelle2026-RC2 at
  `/opt/isabelle` (or `~/Isabelle2026-RC2`), the HOL-Analysis heap, PIDE MCP as an Isabelle component, the venv
  with `pip install -e ".[mcp,eval]" -e ./client`.
- Nothing else running Isabelle on the host. 128 GB, 32 cores (W = 32 sits on the server's default backend executor,
  `min(32, cpu_count + 4)`; every cell records it).
- Every IPS cell starts its own server with `config.yaml`'s `ips.env` (`ISABELLE_POOL_SIZE=40`,
  `ISABELLE_PARALLEL_PROOFS=2`, the bigstep build cap 4 for T4, …) and records it; port 8000 must be free.
- Run every script from `evaluation/training_metrics/`; `scripts/_py.sh` picks the Python (an active venv, the repo's
  `.venv`, else `python3`, or `PYTHON=…`) and sets the import path (`trainmetrics/__init__.py` adds the repo root and
  `../memory_scaling`).

## 1. The order (about 9 h of server time)

| # | Command | Time | Gives |
|---|---|---|---|
| 1 | `scripts/00_check_env.sh` | 1 min | PASS lines + the environment block (never builds) |
| 2 | `scripts/00a_prepare_corpus.sh` | seconds | `results/corpus/processed/` (94 theories from this Isabelle) + `MANIFEST.json` |
| 3 | `scripts/01_census.sh` | ~20 min | `results/census/census.json`: every block's command count, spans, verdict, time at W = 1 |
| 4 | `scripts/02_smoke.sh` | ~15 min | must pass: a 3-file census, T1 at W 1–2, the PIDE MCP row at W 1, the T6a and T6b calibration cells, the analysis |
| 5 | `python -m trainmetrics.calibrate_t6 --cell results/smoke/t6a/w1/rep0 --cell-t6b results/smoke/t6b/w1/rep0 --census results/census/census.json` | seconds | the T6 cell timeouts: set `t6.t6a.cell_timeout_s` and `t6.t6b.cell_timeout_s` in `config.yaml` |
| 6 | `scripts/10_run_t1_t2.sh` | ~1 h + 20 min | T1 cells `results/t1/w*/rep*`, then the PIDE MCP row `results/pide/w*/rep*` |
| 7 | `scripts/11_run_t5.sh` | ~1 h | `results/t5/w1/rep0`, `results/t5/w8/rep0` |
| 8 | `scripts/12_run_t4.sh` | ~1 h | `results/t4/rep0` (93 + 597 cold builds, 4 at a time) |
| 9 | `scripts/13_run_t3.sh` | ~1 h | `results/t3/rep0` (200 states) |
| 10 | `scripts/14_run_t6.sh` | ~3 h | `results/t6a/w*/rep*`, then `results/t6b/w*/rep*` |
| 11 | `scripts/20_analyze.sh` | 1 min | `results/tables/*.csv`, `results/plots/*.png`, `results/REPORT_SNIPPET.md` |
| 12 | `GH_TOKEN=… scripts/30_release_results.sh` | minutes | `results/` packed and attached to a GitHub Release (`TAG=trainmetrics-<date>`) |

- The census (3) must run **after** stage 3d (it records command spans; T6b needs them). `01_census.sh` exits 3 when
  it proves less than `census_min_proved_fraction` (0.9: stop and inspect), and 4 when a proved block's spans do not
  re-assemble it (the census is valid for T1–T5; only T6b's step split needs the spans).
- The census writes each finished file to `results/census/files.jsonl` at once. After a stop (Ctrl-C, a lost
  connection) the same command continues after the last finished file, if the corpus, the commit, `ips.env`, the
  field and the budget are unchanged. If only the commit changed and the commits between change nothing a replay
  runs (`census_journal.REPLAY_PATHS` / `REPLAY_DEFS`: the server, the client, the replay, the theory header
  parsing), it keeps every file whose blocks did not change and runs the others (D16). `--keep-unchanged` keeps
  them even when a commit changes such code, for a change the owner knows a replay does not run (D17; restore
  `files.jsonl.prev` first if a census already started again). Otherwise, or with
  `--fresh`, it starts again (the old journal becomes `files.jsonl.prev`). A block that times out or runs 120 s or longer prints a line (`[census n/93] <file> block
  i/k: …`).
- `02_smoke.sh` deletes `results/smoke/` first, so a stale cell can never pass a smoke of new code.
- Step 5: the slowest worker of each T6 matrix × `--contention` (3; once T1 has run, T1's slowdown at W = 32 is the
  better value) + the server start, never below 7200 s. Re-run it after a finished T6a W = 1 cell for real timeout
  shares.
- Long runs: under `nohup … &` or tmux, e.g. `nohup scripts/10_run_t1_t2.sh >> results/t1.log 2>&1 &` (`>>`: a
  restart keeps the log of the run that stopped). One runner per results tree (`results/RUNNER_LOCK`).
- The smoke passes when `02_smoke.sh` exits 0; its T6b calibration cell may be `empty` (the smoke file has no proof
  of 2–40 commands) and `calibrate_t6` then uses T6a's figures.
- Analysis at any time: `20_analyze.sh` reads every finished cell and recomputes every number from the raw records.

## 2. Resume and failures

- **Matrix scripts** (`10`, `14`; `run_matrix`): each cell is a child process with a wall timeout; a cell whose
  manifest is `ok` is skipped on the next start; `--rerun-partial` runs `error` / `timeout` / `terminated` cells
  again. A timed-out cell gets SIGTERM, then SIGKILL, and its server's process group is killed, so the next cell
  never meets an orphan. `--workers 1,8 --reps 1` runs a part.
- **Single-cell scripts**: `11_run_t5.sh` skips a finished cell (`--rerun` to repeat); `12_run_t4.sh` and
  `13_run_t3.sh` write their cell again when started again.
- Every cell writes its manifest at the start (`outcome: running`) and at the end (`ok`, `empty`, `error`,
  `terminated`, `timeout`); a failed cell is kept and reported, never silently re-run.
- Open leases: a killed cell's manifest lists `open_sessions`; the next fresh server starts empty.

## 3. Rules the code applies

- **Census first.** Every table refers to the census (`census_id` in every manifest): block budgets, size classes,
  T3 distances, T4 / T5 / T6 problems. Budget per block `min(600, max(30, 2 s × census commands))`; a seeded or
  mutated block keeps its original block's budget.
- **A session per file (D6)**: IPS fixes a session's context at acquisition, so every file is acquired with its own
  imports; uncovered imports are refused (409, stage S). A file enters under its own theory name (D13: 36 blocks name
  their own theory, `Abstract_Metric_Spaces.mtopology_capped_metric`); a second node of a file on the same session
  (T3's new-node alternative) gets a suffixed name, so no name is entered twice on a session.
- **Blocks** (`evaluation/scripts/theory_splitter.py`): cut only at a line outside comments, strings and cartouches;
  a comment-only block joins a neighbour; each block is the file's exact text (T4 splices mutants into the file).
  A cut is made outside every `proof` … `qed` (D16: `by` ends a goal, not a `proof`; `instance proof` and
  `proof qed auto` on one line count), and before a `private` / `qualified` command too.
- **ML commands (D14)**: every cell server runs with `ISABELLE_ALLOW_ML_COMMANDS=true` (`ips.env`); the default
  guard refuses a chunk with `attribute_setup`, `setup`, `method_setup`, a translation or `ML_file` (422), and the
  blocks after it fail. An IPS theory is `Draft.<name>` with no folder, so a relative load path is looked up in the
  server's working directory; `00a_prepare_corpus.sh` writes it from `$ISABELLE_HOME` (`~~/…`, MANIFEST
  `anchored_load_paths`), and the path of a `\<^file>` / `@{file}` antiquotation too (D15).
- **Import order (D15)**: a file's imports keep their header order (corpus and IPS session). The later import wins
  a short name that two imports declare; sorted imports made Cartesian_Space read `transpose` as
  `Transposition.transpose`.
- **Same work per W**: the T1 assignment is one seeded sequence of corpus permutations; a smaller run's files are a
  prefix of a larger run's; `seed + rep` per repeat.
- **Verified = proved** (`success ∧ ¬proof_open ∧ ¬used_sorry ∧ ¬timed_out`); every rate and p95 has a timeout
  column beside it.
- **Outcome classes**: `memory`, `prover_died`, `session_gone` are their own classes; a dead session stops its file.
- **Integrity**: T3, T6a and T6b check the node source at points where no restore's pending work can be absorbed (a
  source read waits for a stable snapshot); T5 hashes the source before a seed and after the attempt.
- **Resolution**: `verify_chunk` re-reads the node every 0.1 s (`session.output_delay`); every latency steps by it;
  the PIDE MCP row polls at the same 0.1 s. After a chunk that succeeds, `verify_chunk` also asks whether a proof is
  still open (one more 0.1 s poll); the PIDE row does not (D18).
- **Resources**: the memscale sampler (PSS, CPU per process, host) once per second around every cell; the memory
  tier polled during T4 / T5 / T6.

## 4. Decisions (owner)

| | Decision |
|---|---|
| D1 | the HOL-Analysis corpus (94 theories, prepared from the Isabelle under test); smoke on three of its files |
| D2 | T1: 8 files per worker, a seeded nested assignment |
| D3 | the PIDE MCP row: topology A, W ∈ {1, 8}, edit-and-settle, a throughput row |
| D4 | T4 cold builds 4 at a time (`ISABELLE_MAX_CONCURRENT_BIGSTEP_BUILDS=4`) |
| D5 | T5 seeds: `subst`-loops and `metis` on a false goal |
| D6 | a session per file, acquired with the file's imports |
| D7 | the server refuses uncovered imports (409) |
| D8–D10 | T6: both styles; one fixed candidate mix per group of 8 (T6a); W ∈ {1, 8, 32} × 2 repeats |
| D11 | T2 latency only as a by-product of T1 |
| D12 | the PIDE MCP sessions run `parallel_proofs=2` as IPS's (both interactive level 3: they check alike) |
| D13 | IPS enters each file under its real theory name (self-qualified facts and constants resolve); PIDE MCP keeps its scratch names |
| D14 | the cell servers accept ML commands (`ISABELLE_ALLOW_ML_COMMANDS=true`: the corpus is Isabelle's own source, 11 blocks in 8 files have ML); corpus preparation anchors relative load paths (`ML_file ‹measurable.ML›` → `~~/src/HOL/Analysis/measurable.ML`) |
| D15 | IPS keeps a session's import order (it sorted them: the later import wins a short name, so Cartesian_Space read `transpose` as `Transposition.transpose`; docs/ISSUES.md Bug 26); corpus preparation also anchors relative `\<^file>` / `@{file}` paths (2 text blocks) |
| D16 | the splitter cuts outside every `proof` … `qed` and before `private` / `qualified` commands (4 blocks of 3 files ended inside a proof, 10 blocks of 5 files held a `private` lemma; 11 files split anew); the span check accepts the modifier (Isabelle counts it as part of the command); a census after a new commit reruns only the files whose blocks changed |
| D17 | `census --keep-unchanged`: the owner keeps the unchanged files across a commit that the D16 check refuses (14daab8 removed dead server code, and the server census started again); the log line names the change it overrode |
| D18 | the PIDE MCP row runs each file on a fresh session, started inside the window and stopped after the file, as IPS acquires one per file (D6): both rows pay the same setup; the per-call difference stays and is stated (IPS's `verify_chunk` undoes a failed chunk and asks whether a proof is still open, about one 0.1 s poll a call) |
| D19 | T6 reports no speedup across W: a cell at W runs more files than the W = 1 cell and each repeat its own seeded files, so a ratio of rates mixes the files' proof times (40.8 at W = 32 on the server); the per-rollout time on the W = 1 files is the like-for-like comparison |
| D20 | the per-call comparison pairs the PIDE MCP row's blocks with IPS's by block hash at W = 1 (a block's time the median over its repeats; only blocks proved under both rows; the sums and means are the comparison, the 0.1 s poll steps both medians): `tables/t1_pide_latency.csv` (its quantiles, the report's distribution figure, in `t1_pide_latency_quantiles.csv`) and the latency paragraph of the report's PIDE MCP section (owner, 2026-10-29); the blocks proved under one row only are the self-naming blocks (D13), the `instantiation` openers that the loop's closing `end` fails before their instance proof, and blocks after them in the same theory, under 1 % together |

## 5. The local container (development only)

The Mac's IPS container has no HOL-Analysis heap (owner, 2026-10-22: never build it locally). Every script takes
`--local-smoke` (three Main-only theories on the HOL heap, `config.yaml` `local_smoke`) and `--ips-external` (the
container's running server), e.g. inside the container:
`cd /app/evaluation/training_metrics && PYTHON=python scripts/02_smoke.sh --local`. Take `LIVE_LOCK` in the impl
folder around live runs on the shared container.

## 6. Outputs

```
results/
├── corpus/processed/, corpus/MANIFEST.json      # 00a
├── census/census.json, replays.jsonl, files.jsonl   # 01 (files.jsonl: the journal a rerun continues from)
├── smoke/                                       # 02 (deleted and rewritten by every smoke)
├── t1/w<W>/rep<r>/  pide/w<W>/rep<r>/           # 10: manifest.json, replays.jsonl, samples.jsonl
├── t5/w1/rep0/  t5/w8/rep0/                     # 11
├── t4/rep0/                                     # 12: part_a.jsonl, part_b.jsonl, tiers.jsonl
├── t3/rep0/                                     # 13: states.jsonl
├── t6a/w<W>/rep<r>/  t6b/w<W>/rep<r>/           # 14: events.jsonl, files.jsonl, tiers.jsonl
├── runner-logs/, RUNNER_LOCK
├── tables/*.csv  plots/*.png                    # 20
└── REPORT_SNIPPET.md                            # 20: the report's paragraphs, in the order of its questions
```

Hand-over: the release asset (step 12; it goes to `origin`'s repository, `REPO=Deep-LCF/Isabelle-Pool-Server`
overrides), and the raw tree copied into `FullBackward/Isabelle-Pool-Server-results`
under `training-metrics/` (as the memory-scaling data). The report's source is kept outside this repository
(Overleaf; the last in-tree copy is commit f6cae10); its figures come from `plots/` of that tree.

## 7. Layout

`trainmetrics/` — `corpus.py`, `prepare_corpus.py`, `census.py`, `census_journal.py`, `spans.py`, `replay.py` (verify, budgets,
sessions); `harness.py` (server / tool driver, sampler, manifest, CLI), `run_matrix.py`, `procctl.py`, `serve.py`;
`run_t1.py`, `metrics_t12.py`, `drive_pide_chunks.py`, `analyze_pide.py` (T1, T2, the PIDE row); `seeds.py`,
`t5_core.py`, `run_t5.py`, `run_t5w8.py`, `contention.py`, `metrics_t5.py`, `analyze_t5.py` (T5); `mutate.py`,
`run_t4.py`, `metrics_t4.py`, `analyze_t4.py` (T4); `run_t3.py`, `metrics_t3.py`, `analyze_t3.py` (T3); `t6_core.py`,
`t6b.py`, `run_t6.py`, `metrics_t6.py`, `calibrate_t6.py`, `analyze_t6.py` (T6); `analyze.py`, `csvout.py`,
`samples.py`. Tests: `tests/test_trainmetrics_*.py` (no Isabelle needed).
