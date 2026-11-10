# Consolidated benchmark results

`benchmark_runs.json` (schema `isabellegym.benchmark_runs.v1`) holds every
benchmark run that used to live as raw per-run JSON under `evaluation/runs/`
plus the four legacy `mcp_bench_results*.json` files, consolidated on
2026-09-27 by `evaluation/scripts/consolidate_runs.py`. The raw files (22 MB)
were removed; this file (2.3 MB) keeps everything needed to recompute the
published tables.

| Family | Runs | Producer script (still runnable?) | Corpus |
|---|---|---|---|
| `bigstep_local_build` | 5 | `eval_bigstep_isabelle_build.py` (yes, needs `isabelle`) | HOL-Analysis/processed (93 files) |
| `bigstep_server_build` | 6 | `eval_bigstep_server_client_ver.py` (yes) | HOL-Analysis/processed; 1 run on Examples |
| `smallstep_server` | 21 | `eval_smallstep_server_client_with_reuse.py` / `_1_worker_no_reuse.py` (yes) | Examples/processed (16 files); 1 HOL-Analysis run |
| `smallstep_local_gym` | 5 | the in-process prototype baseline (its code was removed from the repository on 2026-11-10; the runs stay) | Examples/processed |
| `smallstep_local_qisabelle` | 5 | `eval_smallstep_qisabelle.py` (needs an external qIsabelle checkout; archived) | Examples/processed |
| `mcp_bench_legacy` | 4 | none — the producing script no longer exists | single miniF2F problems |

All runs date from 2026-07-24 (server at that time: before the memory
admission gate, heap pool, incremental document edits and the ML guard).
Re-run the server families and stamp new runs with the commit hash before
quoting numbers for the current server.

## Layout

```
runs[]                    one record per run
  family, config, run_id, source_file
  config_params           tool, corpus, field/parent_session, workers, reuse, timeouts…
  environment             host/python/isabelle info as the script recorded it
  recorded                the run script's own totals and means (verbatim)
  recorded_stats          the run script's own distribution blocks (mean/median/p95…)
  theories[]              one row per theory:
    theory_name, file, ok, wall_time_sec, startup_sec, api_execution_time_sec,
    client_overhead_sec, accepted_steps, total_steps, expected_steps,
    reused_session, worker_id, child_cpu_*_sec, return_code, http_status,
    error (failed theories only),
    step_elapsed_sec[]        per-step wall time (small-step families)
    step_api_execution_sec[]  per-step server-reported time (server family)
    step_accepted[]           per-step accept flag
```

What was dropped from the raw files: per-step command text, subgoal text,
previews, stdout/stderr tails and full bigstep responses. Nothing numeric was
dropped. `--check` in the consolidation script recomputed 157 recorded
run-level values from the theory rows with a worst relative drift of 7.6e-8.

`benchmark_runs.csv` is the flattened per-theory view (one line per theory,
with per-step mean and p95 pre-computed) for spreadsheet use.

`../runs_analysis.ipynb` reads this file and rebuilds the dissertation tables;
its CSV exports are written back into this directory.
