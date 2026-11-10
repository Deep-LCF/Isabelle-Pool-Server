(* REPL.ML — the Isabelle/ML side of Isabelle Pool Server's read-only state queries.

   Loaded into every session via the generated wrapper theories
   (server/repl/thys/, see thy_init.py). Every query is a Query_Operation
   (Pure/PIDE/query_operation.ML): the Scala side (Backend_Probes via
   Document_Utils.overlay_query) attaches the registered print function as a
   temporary document OVERLAY on a host command, the function runs against
   that command's result Toplevel.state, and every `writeln_result` becomes
   one instance-tagged RESULT in the host's command_results. No text edit is
   ever made, so the document, rollback chain and command history stay
   untouched. (Until 2026-09-30 the state queries were `ML_val` probe
   commands inserted at the document tip whose replies came back through
   Scala.Fun_Strings channels — a probe leaked into the script whenever its
   reply timed out; see docs/DESIGN_CHOICES.md 1.10 and docs/ISSUES.md
   tracker REPL-1.)

   Registered operations (Scala passes "<name>_query" as the print function):
     isabelle_pool_server_goals         []                  one line per open subgoal
     isabelle_pool_server_in_proof      []                  "1" | "0" (Toplevel.is_proof)
     isabelle_pool_server_local_facts   []                  one line per proof-context fact
     isabelle_pool_server_global_facts  [limit]             one line per global fact, bounded
     isabelle_pool_server_state         []                  rendered proof state, one line per
                                                   chunk; empty outside a proof
     isabelle_pool_server_sledgehammer  [timeout_s, subgoal] every line sledgehammer prints;
                                                   error "Unknown proof context"
                                                   outside a proof
   Serves BOTH MCP workflows: the chunk-centric one queries the document tip,
   the LSP-like one hosts sledgehammer on the command at a given line. *)

signature REPL =
sig
  val open_subgoals: Toplevel.state -> string list
  val local_facts: Toplevel.state -> string list
  val global_facts: Toplevel.state -> int -> string list
  val proof_state: Toplevel.state -> string list
  val sledgehammer_results: int -> int -> Toplevel.state -> string list
  val sledgehammer_fill: int -> int -> int -> string -> bool -> Toplevel.state -> string list
  val fill_probe: int -> int -> int -> Toplevel.state -> string list
  val mem_stats: unit -> string list
end;

structure Repl : REPL =
struct

(* YXML -> plain text: strip sendback/break/block markup so callers get plain
   strings (every query applies this before handing lines to Scala). *)
fun clean_string s = XML.content_of (YXML.parse_body s);

(* === Open subgoals === *)

fun open_subgoals state =
  if Toplevel.is_proof state then
    let
      val proof = Toplevel.proof_of state;
      val {context, goal, ...} = Proof.raw_goal proof;
      val num_goals = goal |> Thm.prop_of |> Logic.strip_horn |> #1 |> length;
      val pretty_goals = if num_goals > 0 then Goal_Display.pretty_goals context goal else [];
    in
      List.map (clean_string o Pretty.string_of) pretty_goals
    end
  else
    [];

(* === Local facts (facts visible in the proof context) === *)

fun pretty_local_facts verbose ctxt =
  let
    val facts = Proof_Context.facts_of ctxt;
    val props = map #1 (Facts.props facts);
    val local_facts =
      (if null props then [] else [("<unnamed>", props)]) @
      Facts.dest_static verbose [Global_Theory.facts_of (Proof_Context.theory_of ctxt)] facts;
  in
    if null local_facts then []
    else
      map #1 (sort_by (#1 o #2) (map (`(Proof_Context.pretty_fact ctxt)) local_facts))
      |> List.map (clean_string o Pretty.string_of)
  end;

fun local_facts state =
  if Toplevel.is_proof state then
    state |> Toplevel.proof_of |> Proof.context_of |> pretty_local_facts true
  else
    [];

(* === Global facts of the current theory, bounded by `limit` === *)

fun global_facts state limit =
  if Toplevel.is_proof state then
    let
      val ctxt = state |> Toplevel.proof_of |> Proof.context_of;
      val thy = Proof_Context.theory_of ctxt;
      val global_facts = Global_Theory.facts_of thy;

      fun collect_facts (name, thms) (count, acc) =
        if count >= limit then (count, acc)
        else (count + 1, (name, thms) :: acc);

      val (_, limited_facts) =
        Facts.fold_static collect_facts global_facts (0, [])
        |> (fn (count, facts) => (count, sort_by #1 facts));
    in
      limited_facts
      |> map (Proof_Context.pretty_fact ctxt)
      |> List.map (clean_string o Pretty.string_of)
    end
  else
    [];

(* === Rendered proof state (what jEdit's Output panel shows) === *)

fun proof_state state =
  if Toplevel.is_proof state then
    Toplevel.pretty_state state |> List.map (clean_string o Pretty.string_of)
  else
    [];

(* === Sledgehammer on subgoal `i` with an Isabelle-level budget of `timeout_s`
   seconds. Collects every line sledgehammer would normally print (e.g. "metis
   found a proof...", "Try this: by (metis ...)", "No proof found") via the
   output callback — the return value of run_sledgehammer is "" on success.
   Proof.enter_backward normalizes Forward ("state")-mode proof states (the
   state after an initial `proof (method)`), which run_sledgehammer rejects
   (Proof.assert_backward in sledgehammer.ML) — safe here because the query is
   read-only. Runs SYNCHRONOUSLY in the print task; the Scala overlay bracket
   bounds the wait and removes the overlay (cancelling the task) on expiry.
   Raises "Unknown proof context" outside a proof. === *)

fun sledgehammer_results timeout_s i state =
  (case try Toplevel.proof_of state of
    NONE => error "Unknown proof context"
  | SOME proof_state =>
      let
        val bwd_state = Proof.enter_backward proof_state;
        val ctxt = Proof.context_of bwd_state;
        val thy = Proof_Context.theory_of ctxt;
        (* default_params takes a theory; "timeout" is the Isabelle-level budget
           in seconds. ("blocking", _) was removed in recent Isabelle. *)
        val params = Sledgehammer_Commands.default_params thy
          [("timeout", string_of_int timeout_s)];
        val collected = Unsynchronized.ref ([] : string list);
        fun on_output s = collected := ! collected @ [clean_string s];
        val (_, (_, message)) =
          Sledgehammer.run_sledgehammer params Sledgehammer_Prover.Normal
            (SOME on_output) i Sledgehammer_Fact.no_fact_override bwd_state;
        val extra = if message = "" then [] else [clean_string message];
      in ! collected @ extra end);

(* === Sledgehammer for the server-side `fill_sorries` job (plan 2026-10-18 §1.1):
   like sledgehammer_results, hosted on the command BEFORE a `sorry`, but with
   `max_proofs` (1 = stop as soon as one proof is in; provers run concurrently,
   so a second "Try this" line may still arrive — the server takes the first)
   and an optional space-separated
   `provers` list passed through to default_params, and a first line
   "subgoals=N" (N = premises of the goal the sorry would close) so the server
   knows how many subgoals to attack. Outside a proof, or for a subgoal index
   beyond N, it answers "subgoals=0"/"subgoals=N" plus an "error=..." line
   instead of raising, so the server reports `error` rather than a timeout. === *)

fun sledgehammer_fill timeout_s i max_proofs provers isar state =
  (case try Toplevel.proof_of state of
    NONE => ["subgoals=0", "error=Unknown proof context"]
  | SOME proof_state =>
      let
        val bwd_state = Proof.enter_backward proof_state;
        val n = Thm.nprems_of (#goal (Proof.goal bwd_state));
      in
        if i < 1 orelse i > n then
          ["subgoals=" ^ string_of_int n, "error=No subgoal " ^ string_of_int i]
        else
          let
            val ctxt = Proof.context_of bwd_state;
            val thy = Proof_Context.theory_of ctxt;
            (* isar: a second, structured attempt for the fill job (2026-11-10) --
               the default "smart" setting never produced an Isar proof in 921
               fills, so when the one-liner fails the job asks explicitly. *)
            val extra =
              [("timeout", string_of_int timeout_s), ("max_proofs", string_of_int max_proofs)] @
              (if provers = "" then [] else [("provers", provers)]) @
              (if isar then [("isar_proofs", "true"), ("compress", "10")] else []);
            val params = Sledgehammer_Commands.default_params thy extra;
            val collected = Unsynchronized.ref ([] : string list);
            fun on_output s = collected := ! collected @ [clean_string s];
            val (_, (_, message)) =
              Sledgehammer.run_sledgehammer params Sledgehammer_Prover.Normal
                (SOME on_output) i Sledgehammer_Fact.no_fact_override bwd_state;
            val extra_lines = if message = "" then [] else [clean_string message];
          in ("subgoals=" ^ string_of_int n) :: (! collected @ extra_lines) end
      end);

(* === Probe before the hammer, for the fill job (2026-11-10): one "key=value"
   line each -- "subgoals=N"; "goal=<subgoal i, one line>"; "shape=conj|all|imp|
   iff|ball|atom" (the top connective of the conclusion, for mechanical
   splitting with `intro conjI impI allI ballI iffI`) and "split=K" (the number
   of leaves that intro step leaves); "try0=<command>|none" (Isabelle's try0
   schedule -- simp, auto, blast, fastforce, force, linarith, presburger, ... --
   with try0_s per method; a found command closes every remaining goal, so it
   is a complete replacement for the sorry); and, only when try0 failed and
   nitpick_s > 0, "nitpick=genuine|quasi_genuine|potential|none|unknown[:text]"
   on subgoal i. Outside a proof: "subgoals=0" + "error=...". === *)

fun shape_of t =
  (case t of
    Const ("HOL.conj", _) $ _ $ _ => "conj"
  | Const ("HOL.All", _) $ _ => "all"
  | Const ("HOL.implies", _) $ _ $ _ => "imp"
  | Const ("HOL.eq", Type ("fun", [Type ("HOL.bool", []), _])) $ _ $ _ => "iff"
  | Const ("Set.Ball", _) $ _ $ _ => "ball"
  | _ => "atom");

fun split_leaves t =
  (case t of
    Const ("HOL.conj", _) $ a $ b => split_leaves a + split_leaves b
  | Const ("HOL.All", _) $ Abs (_, _, body) => split_leaves body
  | Const ("HOL.implies", _) $ _ $ b => split_leaves b
  | Const ("HOL.eq", Type ("fun", [Type ("HOL.bool", []), _])) $ a $ b => split_leaves b + split_leaves a
  | Const ("Set.Ball", _) $ _ $ Abs (_, _, body) => split_leaves body
  | _ => 1);

val one_line = String.translate (fn #"\n" => " " | c => str c);

(* try0 reports the fastest method that made progress; only a method whose
   resulting state has no goal left is a complete replacement for the sorry *)
fun try0_line timeout_s st =
  let
    fun closes {state, ...} = Thm.nprems_of (#goal (Proof.goal state)) = 0;
    val results = (case try (Try0.try0 (SOME (Time.fromSeconds timeout_s)) Try0.empty_facts) st of
      SOME rs => rs | NONE => []);
  in
    (case List.find closes results of
      SOME {command, ...} => "try0=" ^ one_line (clean_string command)
    | NONE => "try0=none")
  end;

fun nitpick_line timeout_s i bwd_state =
  let
    val thy = Proof_Context.theory_of (Proof.context_of bwd_state);
    val params = Nitpick_Commands.default_params thy
      [("timeout", string_of_int timeout_s), ("verbose", "false"), ("debug", "false"),
       ("show_all", "false"), ("max_threads", "1")];
    val cut = fn s => if size s > 400 then String.substring (s, 0, 400) ^ "..." else s;
  in
    (case try (Nitpick.pick_nits_in_subgoal bwd_state params Nitpick.Normal i) 0 of
      SOME (outcome, msgs) =>
        "nitpick=" ^ outcome ^
        (case msgs of [] => "" | m :: _ => ":" ^ cut (one_line (clean_string m)))
    | NONE => "nitpick=error")
  end;

fun fill_probe try0_s i nitpick_s state =
  (case try Toplevel.proof_of state of
    NONE => ["subgoals=0", "error=Unknown proof context"]
  | SOME proof_state =>
      let
        val bwd_state = Proof.enter_backward proof_state;
        val goal = #goal (Proof.goal bwd_state);
        val n = Thm.nprems_of goal;
      in
        if i < 1 orelse i > n then
          ["subgoals=" ^ string_of_int n, "error=No subgoal " ^ string_of_int i]
        else
          let
            val ctxt = Proof.context_of bwd_state;
            val prem = Logic.nth_prem (i, Thm.prop_of goal);
            val concl0 = Logic.strip_imp_concl prem;
            val concl = (case try HOLogic.dest_Trueprop concl0 of SOME c => c | NONE => concl0);
            val goal_s = one_line (clean_string (Pretty.string_of (Syntax.pretty_term ctxt prem)));
            val t0 = try0_line try0_s bwd_state;
            val nit =
              if nitpick_s > 0 andalso t0 = "try0=none" then [nitpick_line nitpick_s i bwd_state] else [];
          in
            ["subgoals=" ^ string_of_int n, "goal=" ^ goal_s, "shape=" ^ shape_of concl,
             "split=" ^ string_of_int (split_leaves concl), t0] @ nit
          end
      end);

(* === Memory facts of THIS ML process, one "key=value" line each: its OS pid
   and the Poly/ML runtime statistics (size_heap, size_heap_free_last_GC,
   size_heap_free_last_full_GC, ...; the same source Isabelle's own
   ML_Statistics monitor reads). The server maps the pid to the process's
   resident size in /proc; the heap figures come straight from the runtime.
   The pid is read from /proc/self: PolyML.ml_pid and Posix are hidden from
   user-level ML by the sanitized name space (only Pure's bootstrap sees
   them), while readLink on /proc/self is not. Where /proc is absent (a
   non-Linux host) the pid line is simply omitted. === *)

fun mem_stats () =
  let
    fun proc_stat_pid () =
      hd (String.tokens Char.isSpace (File.read (Path.explode "/proc/self/stat")));
    val pid_lines =
      (case try OS.FileSys.readLink "/proc/self" of
        SOME pid => ["pid=" ^ pid]
      | NONE =>
          (case try proc_stat_pid () of
            SOME pid => ["pid=" ^ pid]
          | NONE => []));
  in pid_lines @ map (fn (k, v) => k ^ "=" ^ v) (ML_Statistics.get ()) end;

end;

(* === Query_Operation registrations ===

   Registered when this file is loaded (the wrapper's ML_file), so every
   session — draft-wrapper and (Stage 3) heap-backed alike — has them. One
   `writeln_result` per line: each call is a separate instance-tagged RESULT,
   which the Scala side collects into a List[String] in order (a single
   `writelns_result` would glue every line into one result body). The print
   function receives the HOST command's result state, i.e. the current
   toplevel state when hosted on the document's last command. *)

val _ =
  Query_Operation.register {name = "isabelle_pool_server_goals", pri = 0}
    (fn {state = st, writeln_result, ...} =>
      List.app writeln_result (Repl.open_subgoals st));

val _ =
  Query_Operation.register {name = "isabelle_pool_server_in_proof", pri = 0}
    (fn {state = st, writeln_result, ...} =>
      writeln_result (if Toplevel.is_proof st then "1" else "0"));

val _ =
  Query_Operation.register {name = "isabelle_pool_server_local_facts", pri = 0}
    (fn {state = st, writeln_result, ...} =>
      List.app writeln_result (Repl.local_facts st));

val _ =
  Query_Operation.register {name = "isabelle_pool_server_global_facts", pri = 0}
    (fn {state = st, args, writeln_result, ...} =>
      let
        val limit =
          (case args of
            a :: _ => the_default 100 (Int.fromString a)
          | [] => 100);
      in List.app writeln_result (Repl.global_facts st limit) end);

val _ =
  Query_Operation.register {name = "isabelle_pool_server_state", pri = 0}
    (fn {state = st, writeln_result, ...} =>
      List.app writeln_result (Repl.proof_state st));

val _ =
  Query_Operation.register {name = "isabelle_pool_server_sledgehammer", pri = 0}
    (fn {state = st, args, writeln_result, ...} =>
      let
        val timeout_s = the_default 30 (Int.fromString (nth args 0));
        val i = the_default 1 (Int.fromString (nth args 1));
      in List.app writeln_result (Repl.sledgehammer_results timeout_s i st) end);

val _ =
  Query_Operation.register {name = "isabelle_pool_server_mem", pri = 0}
    (fn {writeln_result, ...} => List.app writeln_result (Repl.mem_stats ()));

val _ =
  Query_Operation.register {name = "isabelle_pool_server_sledgehammer_fill", pri = 0}
    (fn {state = st, args, writeln_result, ...} =>
      let
        val timeout_s = the_default 30 (Int.fromString (nth args 0));
        val i = the_default 1 (Int.fromString (nth args 1));
        val max_proofs = the_default 1 (Int.fromString (nth args 2));
        val provers = (case drop 3 args of [] => "" | p :: _ => p);
        val isar = (case drop 4 args of [] => false | f :: _ => f = "1");
      in List.app writeln_result (Repl.sledgehammer_fill timeout_s i max_proofs provers isar st) end);

val _ =
  Query_Operation.register {name = "isabelle_pool_server_fill_probe", pri = 0}
    (fn {state = st, args, writeln_result, ...} =>
      let
        val try0_s = the_default 5 (Int.fromString (nth args 0));
        val i = the_default 1 (Int.fromString (nth args 1));
        val nitpick_s = the_default 0 (Int.fromString (nth args 2));
      in List.app writeln_result (Repl.fill_probe try0_s i nitpick_s st) end);
