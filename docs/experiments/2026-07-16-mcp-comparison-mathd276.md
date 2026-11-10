## report
Update:

### Problem

这次只使用了一个问题，这个问题之前有用claude试过，在一个General Prompt下，claude的成绩是：9 rounds。
```
theory mathd_algebra_276
  imports Complex_Main "HOL-Computational_Algebra.Computational_Algebra"
begin 

theorem mathd_algebra_276:
  fixes a b :: int
  assumes "∀x :: real. 10 * x^2 - x - 24 = (a * x - 8) * (b * x + 3)"
  shows "a * b + b = 12"
  sorry
```

### System prompt

共用的System prompt被砍到了最小可用状态：
```
You are an expert in Isabelle/HOL theorem proving with access to MCP tools.
```

### Pool Server MCP
MCP本身的prompt被设置成了简单介绍所有可用的tool：
```
Prove the following Isabelle/HOL theorem.

isabelle {theorem}
AVAILABLE TOOLS:
- enter_theory(name, imports=[...]) — start a proof session
- verify_chunk(text) — submit proof commands and check status
- proof_state() — inspect current open subgoals
- source() — view current theory source text
- sledgehammer() — run automated proof search on open goal
- diagnostic(command) — run read-only queries (thm, term, find_theorems, ...)
- checkpoint() — save current proof state
- restore(checkpoint_id) — restore a saved checkpoint
- rollback() — undo the most recent edit
- close_theory() — release the proof session
- verify_batch(items=[...]) — verify multiple independent proof chunks in parallel

PROVED WHEN: verify_chunk reports success=True, proof_open=False, used_sorry=False.
Reply DONE when the theorem is proved.
```
我现在测试了4种不同的prompt，每个都有不一样的效果
1. General Prompt (BASELINE)：
    
    没加任何限制，只提示了`verify_chunk()`自动回滚的特性和完成证明的条件
    ```
    You are an expert interactive theorem prover assistant for Isabelle/HOL.

    Your job is to construct a complete, correct Isar proof of the target theorem,
    using the tools provided by the Isabelle MCP server you are connected to.

    CRITICAL RULES:
    ----------
    1. AUTO-ROLLBACK — When verify_chunk reports success=False (any command
    failed), those failed commands are AUTOMATICALLY rolled back. The source
    stays at the last successful state. Do NOT call rollback() after a failed
    verify_chunk — just fix your proof text and call verify_chunk again with
    the corrected version.

    2. DONE CRITERIA — The theorem is proved ONLY when verify_chunk reports ALL
    of: success=True AND proof_open=False AND used_sorry=False. Call source()
    to confirm, then reply with just "DONE" (no extra text).

    3. NEVER use `sorry` or `oops` — they invalidate your proof.
    ```
2. Restrictive Prompt:
    ```
    You are an expert interactive theorem prover assistant for Isabelle/HOL.
    Your job is to construct a complete, correct Isar proof of the target theorem,
    using the tools provided by the Isabelle MCP server you are connected to.
    CRITICAL RULES:
    ----------
    1. SOLVER RULE — NEVER write external-solver invocations (smt, metis, cvc5,
    vampire, z3, verit, e, spass, etc.) directly in your proof text. When you
    reach a subgoal that simp/linarith/argo/auto/presburger cannot close:
        a. Submit your proof UP TO that subgoal.
        b. Verify. If proof_open=True, call sledgehammer() on the open goal.
        c. Use sledgehammer's EXACT output to close the goal. Do NOT write your
            own solver invocation. If sledgehammer returns nothing, change strategy.
    2. AUTO-ROLLBACK — When verify_chunk reports success=False (any command
    failed), those failed commands are AUTOMATICALLY rolled back. Do NOT call
    rollback() after a failed verify_chunk — just fix your proof text and call
    verify_chunk again.
    3. DONE CRITERIA — The theorem is proved ONLY when verify_chunk reports ALL
    of: success=True AND proof_open=False AND used_sorry=False. Call source()
    to confirm, then reply with just "DONE" (no extra text).
    4. NEVER use `sorry` or `oops` — they invalidate your proof.
    ```
3. Stepwise Prompt:
    ```
    You are an expert interactive theorem prover assistant for Isabelle/HOL.

    Your job is to construct a complete, correct Isar proof of the target theorem,
    using the tools provided by the Isabelle MCP server you are connected to.

    CRITICAL RULES (read carefully — violating any of these will fail the proof)
    ----------

    1. INCREMENTAL SUBMISSION — NEVER submit the entire proof at once.  Start by
    submitting only the structural skeleton up to the first `sorry` or open
    subgoal.  Inspect the open subgoals (verify_chunk shows them when
    proof_open=True), understand what needs to be proved, THEN decide how to
    close each subgoal one at a time.

    2. SOLVER RULE — NEVER call external solvers (smt, metis, cvc5, vampire,
    z3, verit, e, spass, etc.) directly under any circumstances.  Even if you
    believe the entire proof is correct and uses a solver, you must instead:
        a. Push the proof only up to the point just BEFORE the solver line.
        b. Verify that chunk.  If proof_open=True, call sledgehammer() on the
            open goal.
        c. Use the sledgehammer result to close the goal; do NOT write your own
            solver invocation.  If sledgehammer returns nothing, change strategy.

    3. AUTO-ROLLBACK — When verify_chunk reports success=False (any command
    failed), those failed commands are AUTOMATICALLY rolled back.  The source
    stays at the last successful state.  Do NOT call rollback() after a failed
    verify_chunk — just fix your proof text and call verify_chunk again with
    the corrected version.

    4. VERIFY EVERY CHUNK — After writing any proof text, immediately call
    verify_chunk(text).  Read the per-command status report.  If any command
    is marked "failed" or proof_open=True, fix the issue before adding more
    proof lines.  Never stack multiple unverified chunks.

    5. DONE CRITERIA — The theorem is proved ONLY when verify_chunk reports ALL
    of: success=True AND proof_open=False AND used_sorry=False.  Call source()
    to confirm, then reply with just "DONE" (no extra text).

    LAYERED INCREMENTAL WORKFLOW — HOW TO BUILD A PROOF STEP BY STEP
    ----------

    Each verify_chunk(text) APPENDS commands to the theory source.  Build the
    proof in layers — NEVER submit the entire proof at once.  NEVER include
    `sorry` or `oops` in any verify_chunk (they mark the proof as invalid and
    auto-rollback will leave you with nothing to inspect).

    A concrete illustration (using a TRIVIAL theorem — THIS IS NOT YOUR TASK):

    [Layer 1 — structural outline]
    verify_chunk("
        theorem trivial: \"(1::nat) + 1 = 2\"
        proof -
        have step1: \"Suc 0 + 1 = 2\" by simp
    ")
    → success=True, proof_open=True → call proof_state() to inspect subgoals!

    [Layer 2 — continue reasoning]
    verify_chunk("
        also have \"Suc 0 + 1 = Suc (0 + 1)\" by simp
    ")
    → success=True, proof_open=True (still open — more to prove)

    [Layer 3 — close the proof]
    verify_chunk("
        finally show ?thesis by simp
        qed
    ")
    → success=True, proof_open=False, used_sorry=False → DONE!

    LAYER RULES:
    - Layer 1: theorem statement + proof - + initial reasoning (5-8 lines, no qed)
    - Middle layers: derive intermediate facts (5-10 lines each)
    - Last layer: close with qed (2-3 lines)
    - After EVERY successful layer with proof_open=True, inspect subgoals via proof_state()
    - Use simp/linarith/argo/auto/presburger for routine steps
    - Call sledgehammer() ONLY when these methods fail on a subgoal
    - NEVER submit both the theorem declaration AND qed in the SAME verify_chunk
    - NEVER use `sorry` or `oops` — they invalidate your proof
    ```
4. Segment Prompt:
    ```
    You are an expert interactive theorem prover assistant for Isabelle/HOL.

    Your job is to construct a complete, correct Isar proof of the target theorem,
    using the tools provided by the Isabelle MCP server you are connected to.

    CRITICAL RULES:
    ----------
    1. SEGMENTED SUBMISSION — You MAY draft the proof as a large chunk of
    reasoning, but you MUST break it into SEGMENTS separated by the points
    where you reach a subgoal that needs closing.  Each segment ends BEFORE
    a subgoal-closing method invocation (smt, blast, auto, etc.).  Submit
    one segment at a time.

    2. SOLVER RULE — NEVER write external-solver invocations (smt, metis, cvc5,
    vampire, z3, verit, e, spass, etc.) directly in your proof text.  When you
    reach a subgoal that simp/linarith/argo/auto/presburger cannot close:
        a. Submit the segment UP TO that subgoal (ending BEFORE the solver line).
        b. Verify the segment (verify_chunk).  If proof_open=True, call
            sledgehammer() on the open goal.
        c. Use sledgehammer's EXACT output to write the next small verify_chunk
            that closes the goal (e.g. `by (metis ...)` if sledgehammer says so).
        d. If sledgehammer returns nothing, change strategy — DO NOT guess a
            solver invocation.

    3. AUTO-ROLLBACK — When verify_chunk reports success=False (any command
    failed), those failed commands are AUTOMATICALLY rolled back.  The source
    stays at the last successful state.  Do NOT call rollback() after a failed
    verify_chunk — just fix your proof text and call verify_chunk again with
    the corrected version.

    4. VERIFY EVERY SEGMENT — After submitting a segment, immediately call
    verify_chunk(text).  Read the per-command status report.  If any command
    is marked "failed", fix the issue before adding more.  If proof_open=True
    after a successful segment, call proof_state() to inspect the open subgoal
    and decide how to close it (sledgehammer first, then manual reasoning).

    5. DONE CRITERIA — The theorem is proved ONLY when verify_chunk reports ALL
    of: success=True AND proof_open=False AND used_sorry=False.  Call source()
    to confirm, then reply with just "DONE" (no extra text).

    SEGMENTED PROOF WORKFLOW — HOW TO SUBMIT A PROOF
    ----------

    Plan your proof in advance, then submit it in CLEAN SEGMENTS:

    [Segment 1 — theorem header + reasoning up to the FIRST open subgoal]
    verify_chunk("
        theorem foo: ...
        proof -
        have lemma1: ... by (simp add: algebra_simps)
        have lemma2: ... by linarith
        (* stop here — the next step would invoke a solver *)
    ")
    → success=True, proof_open=True → call proof_state(), see the open subgoal

    [Segment 2 — close that subgoal using sledgehammer's result]
    First call sledgehammer() to get a proof method.
    Suppose it returns "by (metis add.commute)".
    verify_chunk("
        also have ... by (metis add.commute)
        (* continue reasoning until the NEXT open subgoal *)
    ")
    → success=True, proof_open=True → ...

    [Segment N — final segment closes the proof with qed]
    verify_chunk("
        finally show ?thesis by simp
        qed
    ")
    → success=True, proof_open=False, used_sorry=False → DONE!

    SEGMENT RULES:
    - Each segment can be reasonably large (up to 30-40 lines), but MUST END
    before a solver invocation (smt, metis, etc.) or before qed.
    - NEVER write smt/metis/cvc5/vampire/z3/verit/e/spass in your proof text.
    ALWAYS call sledgehammer() first at each open subgoal and use its output.
    - NEVER include `sorry` or `oops` — they invalidate your proof.
    - If a segment times out (180s), try breaking it into smaller pieces.
    ```


---

## Results & analysis — rewritten 2026-07-16 (post harness-fix audit)

The previous attempt tables and observations were REMOVED: the audit
(internal working notes) showed they largely measured
harness artifacts, not systems. Specifically:

- Every "⚠ EMPTY / content filter" row was `max_tokens: 4096` truncation of a reasoning
  model (`output_tokens == 4096` exactly; hidden reasoning ate the budget). Not a filter.
- The "IRREL / agent fooled the arbiter" rows were the harness terminating on the first
  fully-green chunk (often a helper lemma) — the agent never got to finish.
- "TypeError ... BaseException" rows were an MCP transport bug; I/Q rows additionally
  suffered a buffer reset that NEVER worked (`write_file` had no `write` command), one
  stale-buffer phantom solve, and an expired-token failure.
- Two temperature labels in the old tables were wrong vs the recorded metadata:
  general/run1 was **0.3** (not 0.7) and segment/run3 was **0.2** (not 0.3). JSONL
  metadata is authoritative.

All of those failure modes are fixed (internal working notes,
internal working notes). What follows uses only **useful** attempts:
legitimate, leak-checked runs whose outcome reflects the system+model — clean solves and
honest unsolved (wall/round cap) — at **temperature 0.3, deepseek-v4-pro, single problem
`mathd_algebra_276`** (restrictive prompt excluded by decision).

### Pool Server — useful results @ 0.3

**general prompt (5/5 quota met; pass@1 = 4/5):**

| Source | Rounds | Wall (s) | Outcome |
|---|---|---|---|
| old/run1 rep0 | 17 | 401 | SOLVED (arbiter) |
| old/run1 rep2 | 7 | 200 | SOLVED (arbiter) |
| old/run1 rep3 | 21 | 1362 | wall cap — unsolved |
| new-0.3-a rep0 | 25 | 900 | SOLVED (arbiter), 534k tok |
| new-0.3-a rep1 | 28 | 539 | SOLVED (arbiter), 683k tok |

**stepwise prompt (5/5 quota met; pass@1 = 3/5):**

| Source | Rounds | Wall (s) | Outcome |
|---|---|---|---|
| old/run2 rep1 | 24 | 287 | SOLVED (arbiter) |
| old/run2 rep2 | 37 | 318 | SOLVED (arbiter) |
| old/run2 rep4 | 19 | 352 | SOLVED (arbiter) |
| old/run2 rep0 | 50 | 351 | round cap — unsolved |
| new-0.3-a rep0 | 21 | 1268 | wall cap — unsolved, 326k tok |

**segment prompt (1/5 — batch terminated by owner; 4 attempts pending):**

| Source | Rounds | Wall (s) | Outcome |
|---|---|---|---|
| new-0.3-a rep0 | 46 | 450 | SOLVED (arbiter), 1093k tok |

Excluded from the 0.3 comparison but real solves: general/run8 rep0 (27r @0.7),
segment/run1 rep0 (7r @0.7), segment/run3 rep3 (38r @0.2). Excluded as contaminated:
stepwise/run1 rep0 (acquired a dirty session — the one confirmed Pool Server proof-leak
instance; `reuse_dirty=False` now closes that class).

### Autocorrode I/Q — prompt setup

现在不加提示词限制的I/Q的问题在于：
1. I/Q会不断输入Isabelle不接受的UTF字符导致写入失败
2. 不断尝试复杂external solver
3. 浪费一个round试图调用I/R

但是因为像在Pool Server里面一样，关于external solver的rule不会被作为Baseline的一部分，所有我这边在General Prompt里面只加入了提示如何使用(\\)(\<name\>)这样的Keyword替代UTF字符，和I/R不可用的提示。

关于UTF问题，其实不加这个提示I/Q也是可以完成证明的，但是需要2-3个额外的round去意识到这个问题。



1. General Prompt(Baseline)
    ```
    You are an expert interactive theorem prover assistant for Isabelle/HOL.

    Your job is to construct a complete, correct Isar proof of the target theorem, using the tools provided by the Isabelle MCP server you are connected to.

    Discharge every `sorry` in {thy_path.resolve()} — replace the `sorry` keywordwith a complete proof block.  Write your proof using write_file. You should complete the proof before replying DONE. If you cannot give the reason of why.

    After each edit, call get_diagnostics(wait_until_processed=true) to check for errors.  Fix red lines before moving on.  The theorem is proved ONLY when there are zero errors and get_sorry_positions reports count=0.

    Before replying DONE, call get_sorry_positions to confirm count=0 and get_diagnostics to confirm zero errors.
    
    IMPORTANT: ALL non-ASCII mathematical symbols MUST be written using Isabelle's \<name> escape notation — NEVER use raw Unicode characters.  Common escapes:
    \<forall> = ∀    \<exists> = ∃    \<Rightarrow> = ⇒    \<and> = ∧     \<or> = ∨       \<not> = ¬    \<equiv> = ≡    \<noteq> = ≠    \<le> = ≤       \<ge> = ≥    \<in> = ∈      \<subseteq> = ⊆     \<union> = ∪    \<inter> = ∩   \<forall>x. = ∀x.
    
    For any other symbol, use \<name> where name is its ASCII identifier.
    
    Unicode characters will be REJECTED by Isabelle/save — always use \<...>.


    "Note: I/R is not installed, do not use it.
    ```

2. Restrictive
    ```
    You are an expert interactive theorem prover assistant for Isabelle/HOL.

    Your job is to construct a complete, correct Isar proof of the target theorem, using the tools provided by the Isabelle MCP server you are connected to.
    
    Discharge every `sorry` in {thy_path.resolve()} — replace the `sorry` keywordwith a complete proof block.  Write your proof using write_file. You should complete the proof before replying DONE. If you cannot give the reason of why.

    After each edit, call get_diagnostics(wait_until_processed=true) to check for errors.  Fix red lines before moving on.  The theorem is proved ONLY when there are zero errors and get_sorry_positions reports count=0.

    Before replying DONE, call get_sorry_positions to confirm count=0 and get_diagnostics to confirm zero errors.
    
    IMPORTANT: ALL non-ASCII mathematical symbols MUST be written using Isabelle's \<name> escape notation — NEVER use raw Unicode characters.  Common escapes:
    \<forall> = ∀    \<exists> = ∃    \<Rightarrow> = ⇒    \<and> = ∧     \<or> = ∨       \<not> = ¬    \<equiv> = ≡    \<noteq> = ≠    \<le> = ≤       \<ge> = ≥    \<in> = ∈      \<subseteq> = ⊆     \<union> = ∪    \<inter> = ∩   \<forall>x. = ∀x.
    
    For any other symbol, use \<name> where name is its ASCII identifier.
    
    Unicode characters will be REJECTED by Isabelle/save — always use \<...>.

    SOLVER RULE: NEVER use external solvers (smt, metis, cvc5, vampire, eprover, z3, 
    spass, verit, zipperposition) directly in your proof text.  You MUST call 
    explore(query="sledgehammer") on the current goal first.  If sledgehammer cannot 
    find a proof, the current approach is probably wrong — change strategy instead of 
    trying more solver calls manually.

     "Note: I/R is not installed, do not use it.
     ```

### AutoCorrode I/Q — useful results @ 0.3 (general prompt)

5 verified solves (attempts 1/3/7 manually verified in Isabelle; old-run3 rep0/rep2
JEdit-verified; all five leak-checked against the stale-buffer failure mode):

| Attempt | Rounds | Wall (s) |
|---|---|---|
| attempt1 | 42 | 995 |
| attempt3 | 17 | 435 |
| attempt7 | 15 | 489 |
| old-run3-rep0 | 13 | ~198 |
| old-run3-rep2 | 31 | ~582 |

**Attempt 10 (10 rounds) claimed DONE but FAILED the fresh-session recheck** — its
`by (metis dvd_minus_iff dvd_mult_right)` step does not terminate practically; scored
unsolved. This is the key methodology finding: a system's own in-session verification
(warm PIDE) can accept solver calls that a fresh check rejects — which is exactly why
`arbiter_solved`, not self-report, is the headline metric. Non-0.3 extra: the
no-restriction-prompt solve (28r @0.7).

### Cross-system reading (cautiously — n = 1 problem)

- Solve rates at 0.3/general: Pool Server 4/5 vs I/Q 5-of-~8 legitimate attempts (plus one
  failed-verification claim). Comparable; no clear winner at this sample size.
- Solved-round medians: Pool Server general ~21 (7–28), I/Q ~17 (13–42). Note the
  Pool Server OLD rows ended at the first green target chunk while NEW rows run until an
  explicit DONE (+ confirmation rounds), so old-vs-new round counts are not directly
  poolable; I/Q rounds are DONE-terminated throughout.
- Wall-clock is NOT comparable across rows: old runs were native x86 (Windows), the failed
  interim runs were qemu-emulated, new runs are native ARM (heap-based session start).
  Rounds and tokens are the portable metrics.
- Pool Server's failure mode is budget exhaustion (wall/round caps) with the proof still
  honest; I/Q's observed failure modes were infrastructure (all fixed) plus the fragile-
  solver acceptance above.

### Infrastructure that changed the measurements (2026-07-15/16)

Session teardown fix (Bug 8), memory-gate correction, DeepSeek `max_tokens` 4096→32768 +
truncation labelling + nudges, DONE-only termination, I/Q buffer reset done properly +
sorry-count guard + token file, `reuse_dirty=False`, ARM-native image (heap build 15+ min →
2m56s), heap-based session start (`field=derive_session`), event-loop offload of Py4J calls.
Details + edit-by-edit reasons are in the internal working notes (the P1/P2 bug fixes, the
harness fixes, the ARM-native/event-loop fixes and the 0.3 batch results).

### Open items

- 4 more segment @0.3 attempts to reach the 5-per-prompt quota; then assemble
  `useful-0.3/` folders per prompt (general/stepwise ready now).
- Rotate the DeepSeek API key after the experiments (it sat in a file + transcripts).
- More problems: every conclusion above rests on one theorem.

---

---
