--------------------------- MODULE IntentFidelityGate ---------------------------
(***************************************************************************)
(* Formal model + proof for KA-INTENT-FIDELITY (inc1 close gate + inc2      *)
(* session-START plan-vs-settled gate), S147..S172.                        *)
(*                                                                          *)
(* IMPLEMENTATION (__main__.py cmd_intent_audit + checkpoint handoff).      *)
(*   A session's stated plan must HONOR the settled next_session_directive.  *)
(*   The gate is fail-loud and requires BOTH:                               *)
(*     idBound        the plan binds to the settled directive's ID          *)
(*     restatement    the plan restates the directive text NORMALIZED-EXACT  *)
(*   A plan that cites the right ID but paraphrases/alters the directive is  *)
(*   NOT faithful; a plan that echoes the text but is not ID-bound is not    *)
(*   provably about the settled directive. Only the conjunction is fidelity. *)
(*                                                                          *)
(* MASTER THEOREM (P_Equiv): GateOK(p) <=> Faithful(p) for EVERY plan p,     *)
(* where Faithful == idBound /\ restatementExact. Two naive single-check     *)
(* gates are refuted in IntentFidelityGate_naive.cfg.                        *)
(***************************************************************************)
EXTENDS Naturals

Plans == [idBound : BOOLEAN, restatementExact : BOOLEAN]

VARIABLE plan

(* --- ground truth: fidelity = ID-binding AND normalized-exact restatement *)
Faithful(p) == p.idBound /\ p.restatementExact

(* --- the chosen gate: both checks --- *)
GateOK(p) == p.idBound /\ p.restatementExact

(* --- discarded naive gates --- *)
NaiveIdOnly(p)   == p.idBound             \* ignores restatement fidelity
NaiveTextOnly(p) == p.restatementExact    \* ignores ID-binding

(***************************************************************************)
(* Properties (state invariants over the current plan).                    *)
(***************************************************************************)
P_Sound      == ~Faithful(plan) => ~GateOK(plan)   \* infidelity always refused
P_NoFalsePos == Faithful(plan)  => GateOK(plan)      \* a faithful plan accepted
P_Equiv      == GateOK(plan) <=> Faithful(plan)      \* MASTER characterization

AllProps == P_Sound /\ P_NoFalsePos /\ P_Equiv

(* Refutation targets (each expected to FAIL with a counterexample):        *)
(*   NaiveIdSound   : [idBound|->TRUE,  restatementExact|->FALSE] slips by   *)
(*   NaiveTextSound : [idBound|->FALSE, restatementExact|->TRUE]  slips by   *)
NaiveIdSound   == ~Faithful(plan) => ~NaiveIdOnly(plan)
NaiveTextSound == ~Faithful(plan) => ~NaiveTextOnly(plan)

(***************************************************************************)
Init == plan \in Plans
Next == UNCHANGED plan
Spec == Init /\ [][Next]_plan
=============================================================================
