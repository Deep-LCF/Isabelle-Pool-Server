"""The server-side ``fill_sorries`` job (plan 2026-10-18 §1.2, §1.3; owner decision D-A).

An agent submits a proof that still contains ``sorry``s; the server locates every
``sorry`` command in the chunk report, and a per-session asyncio task runs
sledgehammer on the goal BEFORE each one — hosted by command index (stage 1a),
sequentially, one subgoal at a time, under the tier-aware sledgehammer slots
(parked, not refused, at ``critical``). The job state lives on the manager keyed
by session id (one job per session), is polled through the routes, and is
cancelled whenever the session is closed, released or discarded (review B2).
The result carries the submitted text with every fully solved ``sorry`` replaced
by its method (D-C; patch rule per review B5/B6).

Pure helpers (``extract_targets``, ``build_patch``, ``eta``) are kept free of
asyncio so they can be unit-tested directly.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from server.app.core import metrics
from server.app.core.logging import get_logger
from server.app.services.atp_attribution import AtpAttributionMixin
from server.app.services import fill_stages
from server.app.services.memory_hooks import run_with_memory_hooks
from server.app.services.sledgehammer_slots import SlotWaitExpired
from server.app.services.success_checker import (
    SH_ERROR,
    SH_PROVED,
    classify_sledgehammer,
)

logger = get_logger(__name__)

STEP_OVERHEAD_S = 30.0  # the Scala overlay budget beyond timeout_s: the true wall bound of a step (review S1)
TYPICAL_STEP_S = 6.0  # measured: proved steps answer in 2–6 s (research §2)

OUTCOME_SKIPPED = "skipped"
OUTCOME_PARTIAL = "partial"


# --------------------------------------------------------------- targets


@dataclass
class SorryTarget:
    """One `sorry` of the submitted text and the command whose state is its goal."""

    index: int  # the sorry command's report index
    host_index: int  # the command before it (its result state = the open goal)
    line: int  # 1-based line in the SUBMITTED text (chunk-relative)
    node_line: int
    col: int  # 1-based start column of the sorry (Line.Document units)
    end_col: int
    host_kind: str


def extract_targets(commands: List[Dict[str, Any]]) -> List[SorryTarget]:
    """Every ``sorry`` command of a chunk report with its host (the previous
    reported command). A ``sorry`` without a predecessor in the report (first
    command) has no goal to attack and is skipped with a log line."""
    out: List[SorryTarget] = []
    prev: Optional[Dict[str, Any]] = None
    for cmd in commands or []:
        if cmd.get("kind") == "sorry":
            if prev is None:
                logger.warning(
                    "sorry at report index %s has no host command; skipped",
                    cmd.get("i"),
                )
            else:
                rng = cmd.get("range") or {}
                start = rng.get("start") or {}
                end = rng.get("end") or {}
                out.append(
                    SorryTarget(
                        index=int(cmd.get("i", -1)),
                        host_index=int(prev.get("i", -1)),
                        line=int(cmd.get("line", 0) or 0),
                        node_line=int(cmd.get("node_line", 0) or 0),
                        col=int(start.get("col", 0) or 0),
                        end_col=int(end.get("col", 0) or 0),
                        host_kind=str(prev.get("kind", "")),
                    )
                )
        prev = cmd
    return out


# ----------------------------------------------------------------- patch

_SINGLE_METHOD_RE = re.compile(r"^(?:(using\s+.+?)\s+)?(by|apply)\s+(.+)$", re.S)
_TOP_LEVEL_GROUPS_RE = re.compile(r"\)\s*\(")


def _split_method(text: str) -> Optional[Tuple[Optional[str], str, str]]:
    """``(using-clause | None, keyword, method)`` for a single-method proof text
    (``by m``, ``using f by m``, ``apply m``); None for anything else — e.g. a
    two-method ``by (m1) (m2)`` — which is left unpatched (review B5)."""
    m = _SINGLE_METHOD_RE.match(text.strip())
    if not m:
        return None
    using, kw, method = m.group(1), m.group(2), m.group(3).strip()
    if _TOP_LEVEL_GROUPS_RE.search(method):
        return None
    return using, kw, method


def _locate_sorry(line_text: str, col: int) -> Optional[Tuple[int, int]]:
    """Start/end offsets of the ``sorry`` token on a line: at the reported column
    when the slice is literally ``sorry`` (review B6: the column unit may differ
    for symbol-bearing lines), else the first ``sorry`` token at or after the
    column, else the first on the line."""
    tok = re.compile(r"(?<![A-Za-z0-9_'])sorry(?![A-Za-z0-9_'])")
    if col >= 1 and line_text[col - 1 : col - 1 + 5] == "sorry":
        return col - 1, col + 4
    for m in tok.finditer(line_text):
        if m.start() >= max(0, col - 1):
            return m.start(), m.end()
    first = tok.search(line_text)
    return (first.start(), first.end()) if first else None


def replacement_for(subgoals: int, methods: List[str]) -> Optional[str]:
    """The text that replaces a ``sorry`` once all its subgoals are proved, or
    None when the methods are not patchable (review B5).

    - one subgoal and a ``by``/``using … by`` form: the method text verbatim;
    - otherwise an ``apply`` chain, one per subgoal, then ``done``: ``by m`` →
      ``apply m``, ``using f by m`` → ``using f apply m``, ``apply m`` verbatim;
      any multi-method form → None.
    """
    if subgoals < 1 or len(methods) != subgoals:
        return None
    if subgoals == 1 and fill_stages.is_isar_proof(methods[0]):
        return methods[0].strip()  # sledgehammer's structured proof (isar stage), verbatim
    parts = [_split_method(m) for m in methods]
    if any(p is None for p in parts):
        return None
    if subgoals == 1 and parts[0] is not None and parts[0][1] == "by":
        return methods[0].strip()
    steps = []
    for using, _kw, method in parts:  # type: ignore[misc]
        steps.append(f"{using} apply {method}" if using else f"apply {method}")
    return "\n".join(steps + ["done"])


def build_patch(
    text: str, targets: List[SorryTarget], replacements: Dict[int, str]
) -> Tuple[str, List[int]]:
    """``(patched_text, unpatchable target indices)``: each target with a
    replacement has its ``sorry`` token replaced in place (continuation lines of
    a multi-line replacement get the sorry line's indentation)."""
    lines = text.split("\n")
    unpatchable: List[int] = []
    # patch bottom-up / right-to-left so earlier offsets stay valid
    for t in sorted(targets, key=lambda x: (x.line, x.col), reverse=True):
        rep = replacements.get(t.index)
        if rep is None:
            continue
        if not 1 <= t.line <= len(lines):
            unpatchable.append(t.index)
            continue
        span = _locate_sorry(lines[t.line - 1], t.col)
        if span is None:
            unpatchable.append(t.index)
            continue
        s, e = span
        indent_m = re.match(r"[ \t]*", lines[t.line - 1])
        indent = indent_m.group(0) if indent_m else ""
        rep_lines = rep.split("\n")
        rep_text = "\n".join([rep_lines[0]] + [indent + l for l in rep_lines[1:]])
        lines[t.line - 1] = lines[t.line - 1][:s] + rep_text + lines[t.line - 1][e:]
    return "\n".join(lines), sorted(unpatchable)


# ------------------------------------------------------------------- job


@dataclass
class StepResult:
    """One sledgehammer run on one subgoal."""

    subgoal: int
    outcome: str
    method: Optional[str] = None
    ms: int = 0
    lines: List[str] = field(default_factory=list)
    subgoals: int = -1  # the host goal's subgoal count as the backend reported it
    reason: Optional[str] = (
        None  # for a skipped step: why (e.g. max_total_s while parked)
    )


@dataclass
class TargetResult:
    """The outcome for one `sorry`: its subgoal steps and the replacement text."""

    target: SorryTarget
    subgoals: int = -1
    outcome: str = OUTCOME_SKIPPED
    per_subgoal: List[StepResult] = field(default_factory=list)
    replacement: Optional[str] = None
    reason: Optional[str] = None
    # staged fill (2026-11-10): which stage settled it and what the probe saw
    stage: Optional[str] = None       # try0 | sledgehammer | isar | split | nitpick
    goal: str = ""                    # the subgoal, one line, for the model's verdict
    shape: str = "atom"
    split_k: int = 1
    split_proved: int = 0
    nitpick: str = ""                 # genuine | quasi_genuine | potential | none | unknown | error
    nitpick_text: str = ""
    probe_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """The §3.2 row for this target."""
        return {
            "index": self.target.index,
            "node_line": self.target.node_line,
            "line": self.target.line,
            "host_kind": self.target.host_kind,
            "subgoals": self.subgoals,
            "outcome": self.outcome,
            "method": self.replacement,
            "reason": self.reason,
            "stage": self.stage,
            "goal": self.goal,
            "shape": self.shape,
            "split": {"k": self.split_k, "proved": self.split_proved} if self.stage == fill_stages.STAGE_SPLIT else None,
            "nitpick": self.nitpick or None,
            "probe_ms": self.probe_ms,
            "per_subgoal": [
                {"i": s.subgoal, "outcome": s.outcome, "method": s.method, "ms": s.ms}
                for s in self.per_subgoal
            ],
        }


@dataclass
class FillJob:  # pylint: disable=too-many-instance-attributes
    """The per-session job: inputs, targets, progress and results (plan §1.2)."""

    session_id: str
    text: str
    mode: str
    timeout_s: int
    max_total_s: float
    provers: str
    targets: List[SorryTarget]
    try0_s: int = 5            # staged fill (2026-11-10): cheap closers before the hammer; 0 = off
    nitpick_s: int = 10        # counterexample search when try0 fails; 0 = off
    isar: bool = True          # structured retry when a found proof did not reconstruct
    split: bool = True         # intro-split a failed splittable goal; second pass on the leaves
    parallel: int = 1          # sessions working the targets (bounded by the sledgehammer slots)
    load_timeout: float = 120.0
    leaf_results: List["TargetResult"] = field(default_factory=list)   # the split pass's targets
    results: List[TargetResult] = field(default_factory=list)
    status: str = "queued"  # queued | running | done | cancelled | error
    current: int = 0  # index into targets of the step in progress
    cancelled: bool = False
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    parked_since: Optional[float] = None
    parked_reason: Optional[str] = None
    queue_position: int = -1
    error: Optional[str] = None
    peak_rss_bytes: int = 0
    task: Optional["asyncio.Task[None]"] = None
    inflight: Optional["asyncio.Future[Any]"] = None  # the step running in the executor

    def __post_init__(self) -> None:
        self.results = [TargetResult(target=t) for t in self.targets]

    # --- derived -----------------------------------------------------------
    def remaining_steps(self) -> int:
        """Sledgehammer steps still to run (one per unresolved subgoal)."""
        n = 0
        for i, r in enumerate(self.results):
            if i < self.current and r.outcome != OUTCOME_SKIPPED:
                continue
            n += max(1, r.subgoals if r.subgoals > 0 else 1)
        return n

    def elapsed_s(self) -> float:
        """Wall time since submission, parking included (plan review S7)."""
        end = self.finished_at or time.time()
        return round(end - self.created_at, 1)

    def eta(self, queue_wait_s: float) -> Optional[Dict[str, float]]:
        """Deterministic estimate (plan §1.2): worst = remaining × (timeout + 5)
        + queue wait; typical = remaining × 6 + queue wait. None while parked."""
        if self.status in ("done", "cancelled", "error"):
            return {"worst": 0.0, "typical": 0.0}
        if self.parked_reason:
            return None
        rem = self.remaining_steps()
        return {
            "worst": round(rem * (self.timeout_s + STEP_OVERHEAD_S) + queue_wait_s, 1),
            "typical": round(rem * TYPICAL_STEP_S + queue_wait_s, 1),
        }

    def stage_counts(self) -> Dict[str, int]:
        return fill_stages.stage_counts(self.results)

    def patched_text(self) -> Tuple[str, List[int]]:
        """The submitted text with every fully solved `sorry` replaced."""
        reps = {r.target.index: r.replacement for r in self.results if r.replacement}
        return build_patch(self.text, self.targets, reps)

    def to_dict(
        self, queue_wait_s: float = 0.0, extra: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """The §3.2 status body."""
        patched, _unpatchable = self.patched_text()
        body: Dict[str, Any] = {
            "status": self.status,
            "n": len(self.targets),
            "done": sum(
                1
                for r in self.results
                if r.outcome != OUTCOME_SKIPPED
                or self.status in ("done", "cancelled", "error")
            ),
            "filled": sum(
                1 for r in self.results if r.outcome == SH_PROVED and r.replacement
            ),
            "current": self.current,
            "queue_position": self.queue_position,
            "parked": bool(self.parked_reason),
            "parked_reason": self.parked_reason,
            "parked_since": self.parked_since,
            "elapsed_s": self.elapsed_s(),
            "eta_s": self.eta(queue_wait_s),
            "results": [r.to_dict() for r in self.results],
            "patched_text": patched,
            "error": self.error,
            "sledgehammer_peak_mb": round(self.peak_rss_bytes / (1024.0 * 1024.0), 1),
            "stages": self.stage_counts(),
            "parallel": self.parallel,
        }
        if extra:
            body.update(extra)
        return body


def _classify_step(reply: Dict[str, Any]) -> StepResult:
    """One ``sledgehammer_at_command`` reply → outcome/method (classifier of 1a)."""
    lines = [str(l) for l in (reply.get("results") or [])]
    error = str(reply.get("error")) if reply.get("error") else None
    outcome, method = classify_sledgehammer(lines, error=error)
    return StepResult(0, outcome, method, lines=lines or ([error] if error else []))


class FillJobMixin(AtpAttributionMixin):
    """Manager-side job registry: one ``FillJob`` per session id (the ATP
    attribution tracker comes from ``AtpAttributionMixin``, §1.6)."""

    _fill_jobs: Dict[str, FillJob]

    def _jobs(self) -> Dict[str, FillJob]:
        """The registry (created lazily: the mixin has no __init__)."""
        if not hasattr(self, "_fill_jobs"):
            self._fill_jobs = {}
        return self._fill_jobs

    def fill_job(self, session_id: Any) -> Optional[FillJob]:
        """The session's job (any status) or None."""
        return self._jobs().get(str(session_id))

    def start_fill(  # pylint: disable=too-many-arguments
        self,
        session: Any,
        *,
        text: str,
        mode: str,
        commands: List[Dict[str, Any]],
        timeout_s: int,
        max_total_s: float,
        provers: str,
        try0_s: int = 5,
        nitpick_s: int = 10,
        isar: bool = True,
        split: bool = True,
        parallel: int = 1,
        load_timeout: float = 120.0,
    ) -> FillJob:
        """Register and start the job (raises ValueError if one is in flight)."""
        sid = str(session.session_id)
        old = self._jobs().get(sid)
        if old is not None and old.status in ("queued", "running"):
            raise ValueError("fill_in_flight")
        job = FillJob(
            session_id=sid,
            text=text,
            mode=mode,
            timeout_s=int(timeout_s),
            max_total_s=float(max_total_s),
            provers=provers,
            targets=extract_targets(commands),
            try0_s=int(try0_s), nitpick_s=int(nitpick_s), isar=bool(isar), split=bool(split),
            parallel=max(1, int(parallel)), load_timeout=float(load_timeout),
        )
        job.n_commands = len(commands or [])
        self._jobs()[sid] = job
        job.task = asyncio.get_running_loop().create_task(self._run_fill(job, session))
        return job

    async def cancel_job(self, session_id: Any) -> bool:
        """Cancel (and await) the session's job, if any (review B2: before the
        backend is stopped). Returns True when a job was cancelled."""
        job = self._jobs().get(str(session_id))
        if job is None or job.status not in ("queued", "running"):
            return False
        job.cancelled = True
        if job.task is not None and not job.task.done():
            job.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await job.task
        # The step itself runs in a worker thread and cannot be interrupted:
        # wait for it (bounded by the worker timeout) so the session is no
        # longer busy when this returns and the caller may close it (review B2).
        if job.inflight is not None and not job.inflight.done():
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait({job.inflight}, timeout=job.timeout_s + 45.0)
        job.inflight = None
        await asyncio.sleep(0)  # let the slot's done-callback run (review S2)
        job.status, job.finished_at = "cancelled", time.time()
        metrics.fill_jobs_total.labels("cancelled").inc()
        return True

    def forget_job(self, session_id: Any) -> None:
        """Drop the session's registry entry (the session is gone — review S4).
        A still-running task keeps its own reference and finishes on its own."""
        self._jobs().pop(str(session_id), None)

    def cancel_job_nowait(self, session_id: Any) -> None:
        """Thread-safe flag + task cancel for sync close paths (discard_dead)."""
        job = self._jobs().get(str(session_id))
        if job is None or job.status not in ("queued", "running"):
            return
        job.cancelled = True
        if job.task is not None and not job.task.done():
            loop = getattr(job.task, "get_loop", lambda: None)()
            if loop is not None:
                loop.call_soon_threadsafe(job.task.cancel)

    def queue_wait_s(self, job: FillJob) -> float:
        """Estimated wait behind the waiters ahead of the job's step."""
        slots = getattr(self, "slots", None)
        if slots is None:
            return 0.0
        cap = max(1, slots.capacity_now())
        ahead = job.queue_position if job.queue_position > 0 else 0
        return float(round(ahead * (job.timeout_s + STEP_OVERHEAD_S) / cap, 1))

    def fill_status(self, session_id: Any) -> Optional[Dict[str, Any]]:
        """The §3.2 body for the session's job, or None without one."""
        job = self.fill_job(session_id)
        if job is None:
            return None
        extra = getattr(self, "slots", None)
        return job.to_dict(
            self.queue_wait_s(job), extra.snapshot() if extra is not None else None
        )

    # --------------------------------------------------------------- runner
    async def _run_fill(self, job: FillJob, session: Any) -> None:
        job.status, job.started_at = "running", time.time()
        slots = getattr(self, "slots", None)
        siblings: List[Tuple[Any, str]] = []
        try:
            if job.parallel > 1:
                siblings = await fill_stages.acquire_siblings(
                    self, job, session, getattr(job, "n_commands", -1))
            sessions = [session] + [s for s, _ in siblings]
            await fill_stages.run_pass(self, job, job.results, sessions, slots, allow_split=True)
            if not job.cancelled and any(r.stage == fill_stages.STAGE_SPLIT for r in job.results):
                await self._run_split_pass(job, session, slots)
            if job.cancelled:
                job.status = "cancelled"
            else:
                job.status = "done"
                for r in job.results:
                    if r.outcome == OUTCOME_SKIPPED and r.subgoals < 0:
                        r.reason = r.reason or "max_total_s"
        except asyncio.CancelledError:
            job.status = "cancelled"
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception("fill job failed session=%s", job.session_id)
            job.status, job.error = "error", f"{type(exc).__name__}: {exc}"
        finally:
            for sib, lease in siblings:
                with contextlib.suppress(Exception):
                    self.release_session(sib.session_id, lease)
            job.finished_at = time.time()
            job.parked_reason, job.queue_position = None, -1
            metrics.fill_jobs_total.labels(job.status).inc()

    async def _run_split_pass(self, job: FillJob, session: Any, slots: Any) -> None:
        """Second pass (2026-11-10): the split skeletons' leaves (fill_stages)."""
        await fill_stages.run_split_pass(self, job, session, slots, TargetResult)

    async def _run_step(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
        self,
        job: FillJob,
        target: SorryTarget,
        subgoal: int,
        session: Any,
        slots: Any,
        isar: bool = False,
    ) -> StepResult:
        def on_wait(position: int, reason: Optional[str]) -> None:
            job.queue_position = position
            if reason and not job.parked_reason:
                job.parked_reason, job.parked_since = reason, time.time()
            elif not reason:
                job.parked_reason = None

        if slots is not None:
            try:
                await slots.acquire(
                    parkable=True,
                    on_wait=on_wait,
                    deadline=job.created_at + job.max_total_s,
                )
            except SlotWaitExpired as exc:
                # the wall budget ran out while queued/parked (review ed2d751 B1):
                # no slot was taken; the step is skipped and _run_fill ends the job
                job.parked_reason, job.queue_position = None, -1
                where = (
                    f"parked ({exc.parked_reason})" if exc.parked_reason else "queued"
                )
                return StepResult(
                    subgoal, OUTCOME_SKIPPED, reason=f"max_total_s while {where}"
                )
        job.parked_reason, job.queue_position = None, -1
        t0 = time.time()
        fut: Optional["asyncio.Future[Any]"] = None
        try:

            def call() -> Any:
                if isar:
                    return session.sledgehammer_at_command(
                        target.host_index, subgoal, job.timeout_s, 1, job.provers, isar=True
                    )
                return session.sledgehammer_at_command(
                    target.host_index, subgoal, job.timeout_s, 1, job.provers
                )

            fut = asyncio.get_running_loop().run_in_executor(
                None, lambda: run_with_memory_hooks(call, session, self)
            )
            job.inflight = fut
            async with self.track_sledgehammer(session):
                # shield: cancelling the job task must not cancel the executor
                # future (the thread keeps running) — cancel_job waits on it and
                # the slot is released only when it completes (reviews B2, S2)
                await asyncio.shield(fut)
            reply, override = fut.result()
            job.peak_rss_bytes = max(
                job.peak_rss_bytes,
                int(getattr(session, "sledgehammer_peak_bytes", 0) or 0),
            )
        finally:
            if fut is not None and not fut.done():
                # cancelled while the worker still runs the overlay (reviews
                # ebffafe S2, 260d78d S3): the slot stays taken and the ATP
                # attribution stays on the session until that process ends
                def _done_later(_f: Any, s: Any = slots, sess: Any = session) -> None:
                    clear = getattr(sess, "clear_sledgehammer_rss", None)
                    if clear is not None:
                        clear()
                    if s is not None:
                        asyncio.get_running_loop().create_task(s.release())

                fut.add_done_callback(_done_later)
            elif slots is not None:
                await slots.release()
        job.inflight = (
            None  # cleared only after a normal completion: cancel_job waits on it
        )
        ms = int((time.time() - t0) * 1000)
        if override:
            job.cancelled, job.error = True, "prover_died"
            return StepResult(
                subgoal, SH_ERROR, lines=[f"prover died ({override})"], ms=ms
            )
        step = _classify_step(reply or {})
        step.subgoal, step.ms = subgoal, ms
        step.subgoals = int((reply or {}).get("subgoals", -1))
        metrics.fill_steps_total.labels(step.outcome).inc()
        return step
