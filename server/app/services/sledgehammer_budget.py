"""Wall-clock budgets for sledgehammer runs.

Isabelle's sledgehammer `timeout` bounds each prover, not the run: preplay
and minimisation follow, and on a hard goal they take about as long again as
the provers. The plain-arm P2 run of 2026-11-09 saw 148 s for timeout_s=120
and then two runs past the old fixed 150 s wall, which surfaced as HTTP 500
"Internal server error" and made the client retry with a *longer* ATP budget
against the same 30 s grace. The grace is now half the ATP budget, 30 s at
least, on both sides: here (the Python future waits in Session) and in
Backend_Probes.sledgehammer_grace_ms (the Scala overlay budget), with the
Python wait a margin above the Scala budget so the backend's own answer
arrives first.
"""
from __future__ import annotations

GRACE_MIN_S = 30.0
PY_MARGIN_TIP_S = 15.0    # Session.sledgehammer waits this much past the Scala budget
PY_MARGIN_AT_S = 25.0     # Session.sledgehammer_at (one more hop: line -> command)


def grace_s(timeout_s: float) -> float:
    """Grace beyond the ATP budget before a run is abandoned."""
    return max(GRACE_MIN_S, float(timeout_s) / 2.0)


def http_wait_tip_s(timeout_s: float) -> float:
    return timeout_s + grace_s(timeout_s) + PY_MARGIN_TIP_S


def http_wait_at_s(timeout_s: float) -> float:
    return timeout_s + grace_s(timeout_s) + PY_MARGIN_AT_S
