#!/usr/bin/env python3
"""Read MCP-comparison/runs/**/results.jsonl and print summary tables.

Results may live directly under runs/<system>/ or in per-experiment subfolders
(e.g. runs/isabelle_pool_server/segment_prompt/results.jsonl) — each such folder is
reported as a separate variant row.

Sledgehammer usage is counted per attempt from the session logs
(<variant>/logs/<problem>_rep<N>.log):
  - isabelle_pool_server  — `sledgehammer` tool calls
  - autocorrode  — `explore` calls with "query": "sledgehammer"
  - isabelle_mcp — standalone `sledgehammer` commands written into the file
                   (JSON-escaped \nsledgehammer\n in write_thy args)
"""
from __future__ import annotations

import argparse
import re
from collections import defaultdict
from pathlib import Path

sys = __import__("sys")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from common.metrics import load_results

SYSTEMS = ["isabelle_pool_server", "isabelle_mcp", "autocorrode", "isabelle_pool_server_lsp"]
# Pre-rename system keys (the project's name before 2026-10-05): runs/ folders and
# the `system` field of their JSONL rows still carry these; map them on read.
LEGACY_SYSTEMS = {"isabellegym": "isabelle_pool_server", "isabellegym_lsp": "isabelle_pool_server_lsp"}

# How a sledgehammer invocation is detected in each system's session log.
# NOTE: these count tool-level invocations, not rounds-with-sledgehammer —
# in this harness one sledgehammer call per round is the norm, so the numbers
# coincide in practice.
_SH_PATTERNS = {
    "isabelle_pool_server": re.compile(r"TOOL_CALL \S+: sledgehammer\b"),
    "autocorrode": re.compile(r'"query": "sledgehammer"'),
    "isabelle_mcp": re.compile(r"\\n\s*sledgehammer\s*\\n"),
    "isabelle_pool_server_lsp": re.compile(r"TOOL_CALL \S+: isabelle_sledgehammer\b"),
}


def _fmt(value: float | None, spec: str) -> str:
    return format(value, spec) if value is not None else "-"


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _sledgehammer_counts(variant_dir: Path, rows: list) -> dict[int, int]:
    """rep index -> number of sledgehammer invocations, from session logs.

    Missing logs (overwritten by later runs sharing a logs dir) are skipped.
    """
    pattern = None
    for r in rows:
        pattern = _SH_PATTERNS.get(r.system)
        break
    if pattern is None:
        return {}
    logs_dir = variant_dir / "logs"
    counts: dict[int, int] = {}
    for r in rows:
        log = logs_dir / f"{r.problem}_rep{r.repeat}.log"
        if not log.is_file():
            continue
        text = log.read_text(encoding="utf-8", errors="replace")
        counts[r.repeat] = len(pattern.findall(text))
    return counts


def summarize(runs_dir: Path) -> None:
    # (system, variant) -> (results_path, rows)
    groups: dict[tuple[str, str], tuple[Path, list]] = {}
    for system in SYSTEMS:
        legacy = [k for k, v in LEGACY_SYSTEMS.items() if v == system]
        for folder in [system, *legacy]:
            system_dir = runs_dir / folder
            if not system_dir.is_dir():
                continue
            for path in sorted(system_dir.rglob("results.jsonl")):
                variant = str(path.parent.relative_to(system_dir)) or "."
                rows = load_results(path)
                for r in rows:
                    r.system = LEGACY_SYSTEMS.get(r.system, r.system)
                groups.setdefault((system, variant), (path, []))[1].extend(rows)

    if not groups:
        print(f"No results found under {runs_dir}")
        return

    def variant_key(item: tuple[tuple[str, str], tuple[Path, list]]) -> tuple:
        (system, variant), _ = item
        return (SYSTEMS.index(system), "old" in variant, variant)

    print("| System | Variant | attempts | pass@1 | solved | mean rounds (solved) | mean productive rounds (solved) | mean wall_s (solved) | mean setup_s | mean first_tool_s | mean total tok (solved) | mean sh calls | attempts w/ sh | trunc | nudges |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    per_variant_sh: dict[tuple[str, str], dict[int, int]] = {}
    for (system, variant), (path, rs) in sorted(groups.items(), key=variant_key):
        attempts = len(rs)
        solved = [r for r in rs if r.arbiter_solved]
        pass_at_1 = len(solved) / attempts if attempts else 0.0
        mean_rounds = sum(r.rounds for r in solved) / len(solved) if solved else None
        # productive rounds = rounds minus harness nudges (fairness: nudges
        # measure model chattiness/infra, not the prover interface)
        mean_prod = (sum(r.rounds - getattr(r, "n_nudge_rounds", 0) for r in solved) / len(solved)
                     if solved else None)
        mean_wall = sum(r.wall_s for r in solved) / len(solved) if solved else None
        # setup_s / first_tool_s expose the warm-vs-cold protocol asymmetry:
        # I/Q's persistent jEdit amortises warmth across attempts while
        # Isabelle Pool Server starts a fresh session per attempt.
        mean_setup = _mean([r.setup_s for r in rs if getattr(r, "setup_s", None) is not None])
        mean_first = _mean([r.first_tool_s for r in rs if getattr(r, "first_tool_s", None) is not None])
        mean_tok = sum(r.total_tokens for r in solved) / len(solved) if solved else None
        truncated = sum(getattr(r, "n_truncated_rounds", 0) for r in rs)
        nudges = sum(getattr(r, "n_nudge_rounds", 0) for r in rs)
        sh_counts = _sledgehammer_counts(path.parent, rs)
        per_variant_sh[(system, variant)] = sh_counts
        mean_sh = _mean([float(c) for c in sh_counts.values()]) if sh_counts else None
        attempts_sh = sum(1 for c in sh_counts.values() if c > 0)
        print(f"| {system} | {variant} | {attempts} | {pass_at_1:.2f} | {len(solved)} | "
              f"{_fmt(mean_rounds, '.1f')} | {_fmt(mean_prod, '.1f')} | {_fmt(mean_wall, '.1f')} | "
              f"{_fmt(mean_setup, '.1f')} | {_fmt(mean_first, '.1f')} | "
              f"{_fmt(mean_tok, '.0f')} | {_fmt(mean_sh, '.1f')} | {attempts_sh}/{len(sh_counts) or 0} | "
              f"{truncated} | {nudges} |")

    # Per-attempt sledgehammer detail.
    print("\nSledgehammer invocations per attempt (rep -> count):")
    for (system, variant), counts in sorted(per_variant_sh.items(), key=lambda kv: (SYSTEMS.index(kv[0][0]), kv[0][1])):
        if not counts:
            print(f"  {system}/{variant}: (no matching logs found)")
            continue
        parts = ", ".join(f"rep{k}:{v}" for k, v in sorted(counts.items()))
        print(f"  {system}/{variant}: {parts}")

    # Repeat-index control: warmth drift (e.g. I/Q's persistent jEdit getting
    # faster across repeats) shows up here as a rep-index trend.
    all_rows = [r for _, rows in groups.values() for r in rows]
    repeats = sorted({r.repeat for r in all_rows})
    print("\nMean wall_s (solved) by repeat index:")
    print("| Variant | " + " | ".join(f"rep{i}" for i in repeats) + " |")
    print("|---|" + "---|" * len(repeats))
    for (system, variant), (_, rs) in sorted(groups.items(), key=variant_key):
        cells = []
        for i in repeats:
            walls = [r.wall_s for r in rs if r.repeat == i and r.arbiter_solved]
            cells.append(_fmt(_mean(walls), ".1f"))
        print(f"| {system}/{variant} | " + " | ".join(cells) + " |")

    print("\nPer-problem pass@1:")
    problems = sorted({r.problem for r in all_rows})
    for problem in problems:
        parts = []
        for (system, variant), (_, rs) in sorted(groups.items(), key=variant_key):
            prs = [r for r in rs if r.problem == problem]
            solved = sum(1 for r in prs if r.arbiter_solved)
            parts.append(f"{system}/{variant}={solved}/{len(prs)}")
        print(f"  {problem}: {', '.join(parts)}")

    print("\nError classes (unsolved attempts):")
    classes: dict[str, int] = defaultdict(int)
    for r in all_rows:
        if r.arbiter_solved:
            continue
        err = (r.error or "none recorded").splitlines()[0]
        if "truncated at max_tokens" in err:
            key = "truncated at max_tokens"
        elif "empty response" in err:
            key = "empty response"
        elif "wall cap" in err:
            key = "wall cap exceeded"
        elif "not found" in err and "theorem" in err:
            key = "target theorem missing"
        elif "sorry/oops" in err:
            key = "sorry/oops left in file"
        else:
            key = err[:60]
        classes[key] += 1
    for key, count in sorted(classes.items(), key=lambda kv: -kv[1]):
        print(f"  {count:3d}  {key}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-dir", type=Path,
                        default=Path(__file__).resolve().parent / "runs")
    args = parser.parse_args()
    summarize(args.runs_dir)


if __name__ == "__main__":
    main()
