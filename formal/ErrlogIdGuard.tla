---------------------------- MODULE ErrlogIdGuard ----------------------------
(***************************************************************************)
(* Formal model + proof for ERRLOG-ID-GUARD (P1/G1, S140).                  *)
(*                                                                          *)
(* PROBLEM. ERROR_LOG.md is a semi-structured log. Each markdown heading    *)
(* that mentions an error id `E-NNN` is one of:                             *)
(*   Dfn  a definition heading  (grammar: id immediately followed by `:`)   *)
(*   Rcr  a recurrence/reference heading (id + a recurrence marker such as  *)
(*        `(S123)`, `recurrence`, `repeat`, `follow-up`, `regression`)      *)
(*   Mfd  malformed: mentions E-NNN but matches NEITHER grammar             *)
(*                                                                          *)
(* The pre-S140 auditor (check_record_coverage) de-duped ALL headings before*)
(* checking coverage, so a REUSED id (two DEFs) was invisible (E-055/E-056  *)
(* refiled S137). A naive "each id heads exactly once" fix over-corrects:   *)
(* it FALSE-POSITIVES on the legitimate Dfn+Rcr pattern (E-043 recurrence). *)
(*                                                                          *)
(* CHOSEN DESIGN (dual-POV synthesis).                                      *)
(*   * ML/agent lens: do not INFER structure from prose (brittle, drifts);  *)
(*     ENFORCE it. A malformed heading fails loud (I0), so the convention   *)
(*     cannot silently drift -- the guard is self-stabilizing.              *)
(*   * CS/systems lens: uniqueness is on DEFINITIONS only (I1); every       *)
(*     mentioned id must be definition-backed (I2). This is a total,        *)
(*     deterministic classification with a provable characterization.       *)
(*                                                                          *)
(* GUARD  == I0 /\ I1 /\ I2                                                  *)
(*   I0 (well-formed)     : no Mfd heading                                   *)
(*   I1 (unique def)      : <= 1 Dfn heading per id                          *)
(*   I2 (definition-backed): every mentioned id has >= 1 Dfn heading         *)
(*                                                                          *)
(* MASTER THEOREM (P_Equiv): GUARD(l) <=> Legit(l) for EVERY log l, where   *)
(* Legit == no Mfd /\ no reuse /\ no dangling. TLC checks this exhaustively  *)
(* over all logs up to MaxLen. The naive guard is refuted by counterexample *)
(* in ErrlogIdGuard_naive.cfg.                                              *)
(***************************************************************************)
EXTENDS Naturals, Sequences, FiniteSets

CONSTANTS Ids, Dfn, Rcr, Mfd, MaxLen

Kinds    == {Dfn, Rcr, Mfd}
Mentions == [id: Ids, kind: Kinds]
AllLogs  == UNION { [1..n -> Mentions] : n \in 0..MaxLen }

VARIABLE log

(* --- derived sets over a concrete log l --- *)
DefsOf(l, id)     == { i \in DOMAIN l : l[i].id = id /\ l[i].kind = Dfn }
MentionedIds(l)   == { l[i].id : i \in DOMAIN l }
HasMal(l)         == \E i \in DOMAIN l : l[i].kind = Mfd
HasReuse(l)       == \E id \in Ids : Cardinality(DefsOf(l, id)) >= 2
HasDangling(l)    == \E id \in MentionedIds(l) : DefsOf(l, id) = {}

(* --- the chosen guard --- *)
I0(l)     == ~HasMal(l)
I1(l)     == \A id \in Ids : Cardinality(DefsOf(l, id)) <= 1
I2(l)     == \A id \in MentionedIds(l) : DefsOf(l, id) # {}
GuardOK(l) == I0(l) /\ I1(l) /\ I2(l)

(* --- ground-truth semantics --- *)
Legit(l)  == ~HasMal(l) /\ ~HasReuse(l) /\ ~HasDangling(l)

(* --- the discarded naive guard: uniqueness over ALL headings --- *)
NaiveGuardOK(l) == \A id \in Ids :
                     Cardinality({ i \in DOMAIN l : l[i].id = id }) <= 1

(***************************************************************************)
(* Properties (checked as state invariants over the current log).          *)
(***************************************************************************)
P_Sound      == HasReuse(log)    => ~GuardOK(log)   \* no false negative on reuse
P_Dangling   == HasDangling(log) => ~GuardOK(log)   \* dangling ref caught
P_DriftProof == HasMal(log)      => ~GuardOK(log)   \* malformed caught (self-stabilizing)
P_NoFalsePos == Legit(log)       => GuardOK(log)    \* legit Dfn+Rcr accepted
P_Equiv      == GuardOK(log) <=> Legit(log)         \* MASTER: exact characterization

AllProps == P_Sound /\ P_Dangling /\ P_DriftProof /\ P_NoFalsePos /\ P_Equiv

(* Refutation target: the naive guard is NOT sound-and-complete. Expected to *)
(* FAIL with a counterexample (a legit Dfn+Rcr log the naive guard rejects). *)
NaiveFalsePos == Legit(log) => NaiveGuardOK(log)

(***************************************************************************)
Init == log \in AllLogs
Next == UNCHANGED log
Spec == Init /\ [][Next]_log
=============================================================================
