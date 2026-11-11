"""The staged fill (2026-11-10): what happens to ONE ``sorry`` of a fill job.

Pass 1 of the nearAI_Isabelle IMO 2026 experiment (six problems, 1 393 sorries
tried) showed what the one-stage filler could and could not do: 73 % closed
overall, above 90 % on well-stated leaves, and of the leaves it closed most
were ``simp``/``linarith``/``blast``-class, each bought with a 30 s
sledgehammer; a failed leaf reported nothing but ``none``; no structured Isar
proof came back in 921 fills; and the model could not tell a false leaf from
a hard one. The stages below answer each of these in turn, cheapest first:

  probe   ``fill_probe_at_command``: the goal text, its shape, and a try0 pass
          (Isabelle's cheap closers, ``try0_s`` each). A try0 command closes
          every remaining goal, so it is the whole replacement -- no hammer.
          When try0 fails and ``nitpick_s`` > 0, nitpick runs on the subgoal:
          a ``genuine`` counterexample makes the target ``false`` and no
          hammer time is spent on it.
  hammer  sledgehammer per subgoal, as before (``timeout_s`` each).
  isar    when the hammer found a proof the one-liner could not reconstruct
          (``needs_isar_retry``), one more run with ``isar_proofs = true``;
          a structured proof is accepted verbatim for a one-subgoal target.
  split   when everything failed and the goal's conclusion has a splittable
          shape (conjunction, universal, implication, equivalence, bounded
          universal), the sorry becomes ``apply (intro conjI impI allI ballI
          iffI)`` + K x ``subgoal sorry`` + ``done``; the job's second pass
          (``fill_sorries.FillJobMixin._run_split_pass``) attacks the K leaves.
          A split whose leaves all fail is reverted to the single sorry; one
          with some proved leaves is kept, so the model gets the finer
          skeleton and the verdict per leaf.

Everything here is pure or takes the manager/session as parameters, so it is
unit-testable with the fakes of tests/test_fill_routes.py. The module exists
because fill_sorries.py sits at the repository's 600-line cap.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from server.app.core.logging import get_logger
from server.app.services.memory_hooks import run_with_memory_hooks
from server.app.services.success_checker import SH_PROVED
from server.app.services.unicode_normaliser import ascii_output

logger = get_logger(__name__)

STAGE_TRY0 = "try0"
STAGE_HAMMER = "sledgehammer"
STAGE_ISAR = "isar"
STAGE_SPLIT = "split"
OUTCOME_FALSE = "false"           # nitpick found a genuine counterexample
OUTCOME_SPLIT = "split"           # replaced by a split skeleton with some leaves proved

SPLIT_SHAPES = ("conj", "all", "imp", "iff", "ball")
SPLIT_INTRO = "apply (intro conjI impI allI ballI iffI)"

_RECONSTRUCTION_FAILED_RE = re.compile(
    r"reconstruction failed|preplay|could not be reconstructed|found a proof", re.I
)
_TRY_THIS_RE = re.compile(r"Try this:")


# ------------------------------------------------------------ pure helpers


def needs_isar_retry(lines: List[str]) -> bool:
    """A prover found a proof but no ``Try this:`` one-liner survived preplay:
    the only case where asking for a structured Isar proof can add anything.
    (A plain "No proof found" / "Timed out" is not retried.)"""
    rows = [str(l) for l in lines or []]
    if any(_TRY_THIS_RE.search(r) for r in rows):
        return False
    return any(_RECONSTRUCTION_FAILED_RE.search(r) for r in rows)


def is_isar_proof(method: Optional[str]) -> bool:
    return bool(method) and str(method).lstrip().startswith("proof")


def nitpick_verdict(raw: Optional[str]) -> Tuple[str, str]:
    """``(outcome, text)`` from the probe's ``nitpick`` field: outcome is one
    of genuine / quasi_genuine / potential / none / unknown / error / '' ."""
    if not raw:
        return "", ""
    head, _, text = str(raw).partition(":")
    return head.strip(), text.strip()


def split_skeleton(k: int) -> str:
    """The replacement for a sorry whose conclusion splits into ``k`` leaves:
    the intro step, one ``subgoal sorry`` per leaf, ``done``. Each leaf is a
    new sorry the second pass attacks; its host command is the ``subgoal``."""
    k = max(1, int(k))
    return "\n".join([SPLIT_INTRO] + ["subgoal sorry"] * k + ["done"])


def split_with_leaves(k: int, methods: List[Optional[str]]) -> str:
    """The split skeleton with the proved leaves closed: ``subgoal by m`` for a
    proved leaf (``m`` is the leaf's ``by ...`` text), ``subgoal sorry`` for
    the rest. ``methods`` is in leaf order and has ``k`` entries."""
    rows = [SPLIT_INTRO]
    for i in range(max(1, int(k))):
        m = methods[i] if i < len(methods) else None
        if m and str(m).strip().startswith("by "):
            rows.append(f"subgoal {str(m).strip()}")
        elif m and str(m).strip().startswith("using "):
            rows.append(f"subgoal {str(m).strip()}")
        else:
            rows.append("subgoal sorry")
    rows.append("done")
    return "\n".join(rows)


def leaf_methods_by_split(
    split_results: List[Any], leaf_results: List[Any]
) -> Dict[int, List[Optional[str]]]:
    """Group the second pass's leaf results (document order) under their split
    targets (document order): the first split's K leaves come first, and so
    on. Returns ``{split target index: [method or None] * K}``."""
    out: Dict[int, List[Optional[str]]] = {}
    pos = 0
    for sr in split_results:
        k = max(1, int(getattr(sr, "split_k", 1) or 1))
        chunk = leaf_results[pos : pos + k]
        pos += k
        out[sr.target.index] = [
            (lr.replacement if lr.outcome == SH_PROVED else None) for lr in chunk
        ] + [None] * (k - len(chunk))
    return out


# ------------------------------------------------------------- the stages


async def probe_target(manager: Any, job: Any, result: Any, session: Any) -> Dict[str, Any]:
    """Stage 1 on one target: the backend probe in the session's worker thread.
    Records goal / shape / split / nitpick on the result and returns the raw
    probe dict (``{}`` when probing is off or the backend declined)."""
    if job.try0_s <= 0 and job.nitpick_s <= 0 and not job.split:
        return {}
    t0 = time.time()

    def call() -> Any:
        return session.fill_probe_at_command(
            result.target.host_index, 1, job.try0_s, job.nitpick_s
        )

    try:
        reply, override = await asyncio.get_running_loop().run_in_executor(
            None, lambda: run_with_memory_hooks(call, session, manager)
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("fill probe failed index=%s: %s", result.target.index, exc)
        return {}
    result.probe_ms = int((time.time() - t0) * 1000)
    if override or not isinstance(reply, dict) or not reply.get("ok"):
        return {}
    result.goal = str(ascii_output(str(reply.get("goal") or "")))[:400]
    result.shape = str(reply.get("shape") or "atom")
    result.split_k = int(reply.get("split") or 1)
    verdict, text = nitpick_verdict(reply.get("nitpick"))
    if verdict:
        result.nitpick = verdict
        result.nitpick_text = str(ascii_output(text))
    if result.subgoals < 0:
        result.subgoals = int(reply.get("subgoals", -1))
    return reply


def apply_try0(result: Any, reply: Dict[str, Any]) -> bool:
    """A try0 command closes every remaining goal of the host: it is the
    replacement. Returns True when the target is settled here."""
    cmd = str(ascii_output(str(reply.get("try0") or ""))).strip()
    if not cmd:
        return False
    result.outcome, result.replacement, result.stage = SH_PROVED, cmd, STAGE_TRY0
    return True


def apply_nitpick(result: Any) -> bool:
    """A genuine (or quasi-genuine) counterexample settles the target as
    ``false``: the statement, not the proof, is wrong, and no hammer time is
    spent on it. ``potential`` is recorded as a hint only."""
    if result.nitpick in ("genuine", "quasi_genuine"):
        result.outcome, result.stage = OUTCOME_FALSE, "nitpick"
        result.reason = f"nitpick: {result.nitpick} counterexample" + (
            f" -- {result.nitpick_text}" if result.nitpick_text else ""
        )
        return True
    return False


def mark_split(result: Any) -> bool:
    """After the hammer failed: turn a splittable conclusion into the split
    skeleton for the second pass. Returns True when the target was split."""
    if result.shape not in SPLIT_SHAPES or result.split_k < 2:
        return False
    if result.subgoals > 1:
        return False  # the intro step would act on several goals at once; keep it simple
    result.stage = STAGE_SPLIT
    result.outcome, result.replacement = OUTCOME_SPLIT, split_skeleton(result.split_k)
    result.reason = f"split into {result.split_k} leaves for a second pass"
    return True


def finish_split(result: Any, leaf_methods: List[Optional[str]], first_fail_reason: Optional[str]) -> None:
    """Close out a split target once its leaves were attacked: keep the
    skeleton when at least one leaf was proved (the model gets a finer
    skeleton and a verdict per leaf), revert to the single sorry otherwise."""
    proved = sum(1 for m in leaf_methods if m)
    result.split_proved = proved
    if proved == 0:
        result.outcome, result.replacement = "none", None
        result.reason = f"split into {result.split_k} leaves, none proved" + (
            f" ({first_fail_reason})" if first_fail_reason else ""
        )
        return
    result.replacement = split_with_leaves(result.split_k, leaf_methods)
    result.outcome = SH_PROVED if proved == result.split_k else OUTCOME_SPLIT
    result.reason = f"split into {result.split_k} leaves, {proved} proved"


# ------------------------------------------------------ the target runner


async def run_target(manager: Any, job: Any, result: Any, session: Any, slots: Any,
                     *, allow_split: bool = True) -> None:
    """All stages for one target, in order: probe (try0, nitpick) -> hammer
    per subgoal (-> isar retry) -> split marking. ``manager._run_step`` is the
    existing slot-bounded sledgehammer step."""
    reply = await probe_target(manager, job, result, session)
    if job.cancelled:
        return
    if reply and apply_try0(result, reply):
        return
    if reply and apply_nitpick(result):
        return
    subgoal, methods = 1, []
    while True:
        if job.cancelled:
            return
        step = await manager._run_step(job, result.target, subgoal, session, slots)  # noqa: SLF001
        if result.subgoals < 0:
            result.subgoals = step.subgoals
        if step.outcome != SH_PROVED and job.isar and needs_isar_retry(step.lines):
            # a prover found a proof the one-liner could not reconstruct: ask
            # once for a structured Isar proof (sledgehammer isar_proofs = true)
            retry = await manager._run_step(job, result.target, subgoal, session, slots, isar=True)  # noqa: SLF001
            if retry.outcome == SH_PROVED:
                step = retry
                result.stage = STAGE_ISAR
        result.per_subgoal.append(step)
        if step.outcome != SH_PROVED:
            from server.app.services.fill_sorries import OUTCOME_PARTIAL, OUTCOME_SKIPPED, StepResult  # noqa: PLC0415
            for i in range(subgoal + 1, max(result.subgoals, 0) + 1):
                result.per_subgoal.append(StepResult(i, OUTCOME_SKIPPED))
            result.outcome = step.outcome if subgoal == 1 else OUTCOME_PARTIAL
            if step.reason:
                result.reason = step.reason
            if result.outcome != OUTCOME_PARTIAL and allow_split and job.split and result.stage != STAGE_ISAR:
                mark_split(result)
            if result.stage is None:
                result.stage = STAGE_HAMMER
            return
        methods.append(step.method or "")
        if subgoal >= max(result.subgoals, 1):
            break
        subgoal += 1
    from server.app.services.fill_sorries import OUTCOME_PARTIAL, build_patch, replacement_for  # noqa: PLC0415
    rep = replacement_for(max(result.subgoals, 1), methods)
    if rep is not None:
        _text, bad = build_patch(job.text, [result.target], {result.target.index: rep})
        if bad:
            rep = None
    if rep is None:
        result.outcome, result.reason = OUTCOME_PARTIAL, "unpatchable"
    else:
        result.outcome, result.replacement = SH_PROVED, rep
    if result.stage is None:
        result.stage = STAGE_HAMMER


async def run_pass(manager: Any, job: Any, results: List[Any], sessions: List[Any], slots: Any,
                   *, allow_split: bool) -> None:
    """One pass over ``results`` with one worker per session (the primary and
    any siblings): targets are taken from a queue in document order, so a
    pass with one session is exactly the old sequential loop."""
    queue: "asyncio.Queue[Any]" = asyncio.Queue()
    for r in results:
        queue.put_nowait(r)

    async def worker(sess: Any) -> None:
        while not job.cancelled and time.time() - job.created_at <= job.max_total_s:
            try:
                r = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            job.current = max(job.current, results.index(r))
            await run_target(manager, job, r, sess, slots, allow_split=allow_split)

    await asyncio.gather(*(worker(s) for s in sessions))


async def acquire_siblings(manager: Any, job: Any, session: Any, n_commands: int) -> List[Tuple[Any, str]]:
    """Up to ``job.parallel - 1`` extra sessions with the same imports, each
    holding the job's text so command indices agree with the primary; a
    sibling whose load does not reproduce the command count is dropped.
    Bounded by the sledgehammer slots: a worker beyond the slot capacity
    would only wait. Returns ``[(session, lease_id)]``."""
    want = max(0, int(job.parallel) - 1)
    slots = getattr(manager, "slots", None)
    if slots is not None:
        want = min(want, max(0, slots.capacity_now() - 1))
    out: List[Tuple[Any, str]] = []
    for _ in range(want):
        try:
            sib, _reused, lease = await manager.acquire_session(
                theories=list(getattr(session, "theories", None) or []),
                field=getattr(session, "field", None), reuse_dirty=False)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.info("fill: no sibling session (%s); continuing with %d", exc, 1 + len(out))
            break

        def load(s: Any = sib) -> Any:
            return s.load_document(job.text, None, None, job.load_timeout, True)

        try:
            await asyncio.get_running_loop().run_in_executor(None, load)
            report = (getattr(sib, "last_chunk_report", None) or {}).get("report") or {}
            if len(report.get("commands") or []) != n_commands:
                raise RuntimeError("sibling command count differs")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.info("fill: sibling %s unusable (%s)", getattr(sib, "session_id", "?"), exc)
            manager.release_session(sib.session_id, lease)
            continue
        out.append((sib, lease))
    return out


def new_leaf_targets(commands: List[Dict[str, Any]]) -> List[Any]:
    """The second pass's targets: the sorries whose host is a ``subgoal``
    command, i.e. the leaves the split skeletons introduced."""
    from server.app.services.fill_sorries import extract_targets  # noqa: PLC0415
    return [t for t in extract_targets(commands) if t.host_kind == "subgoal"]


def stage_counts(results: List[Any]) -> Dict[str, int]:
    """How many targets each stage settled (proved by try0 / sledgehammer /
    isar, split with leaves proved, found false) and how many stay open."""
    c: Dict[str, int] = {}
    for r in results:
        settled = r.outcome in (SH_PROVED, OUTCOME_SPLIT, OUTCOME_FALSE)
        key = (r.stage or "open") if settled else "open"
        c[key] = c.get(key, 0) + 1
    return c


async def run_split_pass(manager: Any, job: Any, session: Any, slots: Any, result_cls: Any) -> None:
    """Load the pass-1 patch (proved replacements plus split skeletons), attack
    the skeletons' leaves with try0 and the hammer (no further split), then
    close out each split target: kept with its proved leaves, or reverted."""
    patched, _bad = job.patched_text()

    def load() -> Any:
        return session.load_document(patched, None, None, job.load_timeout, True)

    try:
        await asyncio.get_running_loop().run_in_executor(
            None, lambda: run_with_memory_hooks(load, session, manager))
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("fill split pass: reload failed (%s); splits reverted", exc)
        for r in job.results:
            if r.stage == STAGE_SPLIT:
                finish_split(r, [], "reload failed")
        return
    report = (getattr(session, "last_chunk_report", None) or {}).get("report") or {}
    leaves = new_leaf_targets(report.get("commands") or [])
    job.leaf_results = [result_cls(target=t) for t in leaves]
    saved_text, job.text = job.text, patched   # leaf patches are located in the new text
    try:
        await run_pass(manager, job, job.leaf_results, [session], slots, allow_split=False)
    finally:
        job.text = saved_text
    splits = [r for r in job.results if r.stage == STAGE_SPLIT]
    grouped = leaf_methods_by_split(splits, job.leaf_results)
    for r in splits:
        finish_split(r, grouped.get(r.target.index, []), None)
