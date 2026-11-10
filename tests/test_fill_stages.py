"""The staged fill (2026-11-10): try0 before the hammer, nitpick verdicts,
the isar retry, intro-splitting with a second pass, stage counts.

Fakes only: a session whose probe and sledgehammer answers are scripted, a
manager with the mixin's ``_run_step`` replaced by a recording stub.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from server.app.services import fill_sorries as fs
from server.app.services import fill_stages as st


def _target(index=3, host=2, line=2, host_kind="have"):
    return fs.SorryTarget(index=index, host_index=host, line=line, node_line=line, col=3, end_col=8, host_kind=host_kind)


def _job(**kw):
    text = kw.pop("text", 'lemma a: "P"\n  sorry\n')
    j = fs.FillJob(session_id="s", text=text, mode="document", timeout_s=30, max_total_s=600.0,
                   provers="", targets=[_target()], **kw)
    return j


class _Manager:
    """``_run_step`` scripted per (subgoal, isar): returns StepResults in order."""

    def __init__(self, steps):
        self.steps, self.calls = list(steps), []
        self.slots = None

    async def _run_step(self, job, target, subgoal, session, slots, isar=False):
        self.calls.append((target.index, subgoal, isar))
        s = self.steps.pop(0)
        s.subgoal = subgoal
        return s


class _Session:
    def __init__(self, probe):
        self.probe, self.probes = probe, []

    def fill_probe_at_command(self, host_index, subgoal, try0_s, nitpick_s):
        self.probes.append((host_index, subgoal, try0_s, nitpick_s))
        return self.probe


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


# ---- pure helpers ---------------------------------------------------------

def test_needs_isar_retry_only_when_a_proof_did_not_reconstruct():
    assert st.needs_isar_retry(["cvc4: One-line proof reconstruction failed: by (metis foo)"])
    assert st.needs_isar_retry(["vampire found a proof...", "Timed out"])
    assert not st.needs_isar_retry(["No proof found", "Timed out"])
    assert not st.needs_isar_retry(["e: Try this: by simp (1 ms)"])
    assert not st.needs_isar_retry([])


def test_split_skeleton_and_leaves():
    assert st.split_skeleton(2) == "apply (intro conjI impI allI ballI iffI)\nsubgoal sorry\nsubgoal sorry\ndone"
    out = st.split_with_leaves(3, ["by simp", None, "using assms by blast"])
    assert out.split("\n") == ["apply (intro conjI impI allI ballI iffI)", "subgoal by simp", "subgoal sorry",
                               "subgoal using assms by blast", "done"]


def test_replacement_accepts_an_isar_proof_for_one_subgoal():
    isar = "proof -\n  have 1 = 1 by simp\n  then show ?thesis by blast\nqed"
    assert fs.replacement_for(1, [isar]) == isar
    assert fs.replacement_for(2, [isar, "by simp"]) is None


def test_nitpick_verdict_parsing():
    assert st.nitpick_verdict("genuine:n = 2") == ("genuine", "n = 2")
    assert st.nitpick_verdict("none") == ("none", "")
    assert st.nitpick_verdict("") == ("", "")


# ---- stages on one target ----------------------------------------------------

def test_try0_settles_a_target_without_the_hammer():
    job = _job(); r = job.results[0]
    m = _Manager([])
    s = _Session({"ok": True, "subgoals": 1, "goal": "1 + 1 = 2", "shape": "atom", "split": 1, "try0": "by simp", "nitpick": ""})
    run(st.run_target(m, job, r, s, None))
    assert r.outcome == "proved" and r.replacement == "by simp" and r.stage == "try0"
    assert m.calls == [] and s.probes == [(2, 1, 5, 10)]
    assert job.patched_text()[0] == 'lemma a: "P"\n  by simp\n'
    assert job.stage_counts() == {"try0": 1}


def test_genuine_counterexample_marks_the_target_false_and_skips_the_hammer():
    job = _job(); r = job.results[0]
    m = _Manager([])
    s = _Session({"ok": True, "subgoals": 1, "goal": "n < 5", "shape": "all", "split": 1, "try0": "", "nitpick": "genuine:n = 5"})
    run(st.run_target(m, job, r, s, None))
    assert r.outcome == "false" and r.stage == "nitpick" and "n = 5" in r.reason and m.calls == []
    assert r.to_dict()["nitpick"] == "genuine" and job.stage_counts() == {"nitpick": 1}


def test_hammer_runs_after_try0_fails_and_isar_retry_is_asked_once():
    job = _job(); r = job.results[0]
    failed = fs.StepResult(1, "none", lines=["e: One-line proof reconstruction failed"], subgoals=1)
    isar_ok = fs.StepResult(1, "proved", method="proof -\n  show ?thesis by simp\nqed", subgoals=1)
    m = _Manager([failed, isar_ok])
    s = _Session({"ok": True, "subgoals": 1, "goal": "g", "shape": "atom", "split": 1, "try0": "", "nitpick": "none"})
    run(st.run_target(m, job, r, s, None))
    assert m.calls == [(3, 1, False), (3, 1, True)]
    assert r.outcome == "proved" and r.stage == "isar" and r.replacement.startswith("proof -")


def test_plain_failure_is_not_retried_and_a_conjunction_is_split():
    job = _job(); r = job.results[0]
    m = _Manager([fs.StepResult(1, "none", lines=["No proof found", "Timed out"], subgoals=1)])
    s = _Session({"ok": True, "subgoals": 1, "goal": "A ∧ B", "shape": "conj", "split": 2, "try0": "", "nitpick": "none"})
    run(st.run_target(m, job, r, s, None))
    assert m.calls == [(3, 1, False)]
    assert r.stage == "split" and r.outcome == "split" and r.replacement == st.split_skeleton(2)
    assert "sorry" in job.patched_text()[0] and "subgoal sorry" in job.patched_text()[0]
    # an atom is not split; the stage is the hammer and the outcome its verdict
    job2 = _job(); r2 = job2.results[0]
    m2 = _Manager([fs.StepResult(1, "timeout", lines=["Timed out"], subgoals=1)])
    s2 = _Session({"ok": True, "subgoals": 1, "goal": "g", "shape": "atom", "split": 1, "try0": "", "nitpick": "unknown"})
    run(st.run_target(m2, job2, r2, s2, None))
    assert r2.stage == "sledgehammer" and r2.outcome == "timeout" and r2.replacement is None


def test_split_is_off_when_disabled_or_multi_subgoal():
    job = _job(split=False); r = job.results[0]
    m = _Manager([fs.StepResult(1, "none", lines=["No proof found"], subgoals=1)])
    s = _Session({"ok": True, "subgoals": 1, "goal": "A ∧ B", "shape": "conj", "split": 2, "try0": "", "nitpick": ""})
    run(st.run_target(m, job, r, s, None))
    assert r.stage == "sledgehammer" and r.outcome == "none"


# ---- the second pass ----------------------------------------------------------

def test_finish_split_keeps_proved_leaves_and_reverts_an_empty_split():
    job = _job(); r = job.results[0]
    r.stage, r.outcome, r.shape, r.split_k, r.replacement = "split", "split", "conj", 2, st.split_skeleton(2)
    st.finish_split(r, ["by simp", None], None)
    assert r.outcome == "split" and r.split_proved == 1 and "subgoal by simp" in r.replacement and "subgoal sorry" in r.replacement
    assert r.to_dict()["split"] == {"k": 2, "proved": 1}
    st.finish_split(r, ["by simp", "by blast"], None)
    assert r.outcome == "proved" and r.split_proved == 2 and "sorry" not in r.replacement
    st.finish_split(r, [None, None], "all timed out")
    assert r.outcome == "none" and r.replacement is None and "none proved" in r.reason


def test_leaf_results_are_grouped_under_their_split_in_document_order():
    a = SimpleNamespace(target=_target(index=3), split_k=2)
    b = SimpleNamespace(target=_target(index=9), split_k=1)
    leaves = [SimpleNamespace(outcome="proved", replacement="by simp"), SimpleNamespace(outcome="none", replacement=None),
              SimpleNamespace(outcome="proved", replacement="by auto")]
    g = st.leaf_methods_by_split([a, b], leaves)
    assert g == {3: ["by simp", None], 9: ["by auto"]}


def test_new_leaf_targets_are_the_subgoal_hosted_sorries():
    cmds = [{"i": 0, "kind": "lemma", "line": 1}, {"i": 1, "kind": "apply", "line": 2},
            {"i": 2, "kind": "subgoal", "line": 3}, {"i": 3, "kind": "sorry", "line": 3, "range": {"start": {"col": 9}, "end": {"col": 14}}},
            {"i": 4, "kind": "have", "line": 6}, {"i": 5, "kind": "sorry", "line": 7, "range": {"start": {"col": 3}, "end": {"col": 8}}}]
    leaves = st.new_leaf_targets(cmds)
    assert [t.index for t in leaves] == [3] and leaves[0].host_kind == "subgoal"


def test_run_split_pass_reloads_and_closes_out(monkeypatch):
    job = _job(text='lemma a: "A ∧ B"\n  sorry\n'); r = job.results[0]
    r.stage, r.outcome, r.shape, r.split_k, r.replacement = "split", "split", "conj", 2, st.split_skeleton(2)
    patched = job.patched_text()[0]
    assert "subgoal sorry" in patched

    class S:
        last_chunk_report = None
        def load_document(self, text, thy, imports, timeout, report):
            # the reloaded document: two subgoal-hosted sorries on lines 3 and 4
            self.last_chunk_report = {"report": {"commands": [
                {"i": 0, "kind": "lemma", "line": 1}, {"i": 1, "kind": "apply", "line": 2},
                {"i": 2, "kind": "subgoal", "line": 3}, {"i": 3, "kind": "sorry", "line": 3, "range": {"start": {"col": 9}, "end": {"col": 14}}},
                {"i": 4, "kind": "subgoal", "line": 4}, {"i": 5, "kind": "sorry", "line": 4, "range": {"start": {"col": 9}, "end": {"col": 14}}}]}}
            return SimpleNamespace(success=True)
        def fill_probe_at_command(self, *a):
            return {"ok": True, "subgoals": 1, "goal": "g", "shape": "atom", "split": 1, "try0": "", "nitpick": ""}

    m = _Manager([fs.StepResult(1, "proved", method="by simp", subgoals=1),
                  fs.StepResult(1, "none", lines=["No proof found"], subgoals=1)])
    monkeypatch.setattr(st, "run_with_memory_hooks", lambda call, sess, mgr: (call(), None))
    run(st.run_split_pass(m, job, S(), None, fs.TargetResult))
    assert len(job.leaf_results) == 2 and job.text.startswith('lemma a: "A ∧ B"')
    assert r.outcome == "split" and r.split_proved == 1
    final = job.patched_text()[0]
    assert "subgoal by simp" in final and "subgoal sorry" in final and final.count("sorry") == 1
    assert job.stage_counts() == {"split": 1}
