"""A running fill job reports the sorries it has not reached as `pending`;
`skipped` is final and carries its reason (nearAI_Isabelle rep 1, 2026-11-11:
a client that stopped waiting read `skipped` as a verdict). No Isabelle."""
from __future__ import annotations

from server.app.services import fill_sorries as fs


def _target(index, line):
    return fs.SorryTarget(index=index, host_index=index - 1, line=line, node_line=line, col=3, end_col=8, host_kind="have")


def _job(status):
    job = fs.FillJob(session_id="s", text='lemma a: "P"\n  sorry\nlemma b: "Q"\n  sorry\n', mode="document",
                     timeout_s=30, max_total_s=600.0, provers="", targets=[_target(3, 2), _target(6, 4)])
    job.status = status
    job.results[0].outcome, job.results[0].replacement, job.results[0].stage = "proved", "by simp", "try0"
    return job


def test_untried_targets_are_pending_while_running_and_skipped_when_over():
    rows = _job("running").to_dict()["results"]
    assert [r["outcome"] for r in rows] == ["proved", "pending"]
    rows = _job("done").to_dict()["results"]
    assert [r["outcome"] for r in rows] == ["proved", "skipped"]


def test_a_skipped_target_with_a_reason_is_never_pending():
    job = _job("running")
    job.results[1].reason = "max_total_s while parked"
    assert job.to_dict()["results"][1]["outcome"] == "skipped"
