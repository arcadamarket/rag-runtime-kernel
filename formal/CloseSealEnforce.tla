--------------------------- MODULE CloseSealEnforce ---------------------------
(***************************************************************************)
(* Formal model + proof for CLOSE-SEAL-ENFORCE (KA-21, S172).              *)
(*                                                                          *)
(* IMPLEMENTATION (__main__.py session-start carry-forward gate +           *)
(* session-resume).                                                         *)
(*   A new session may only OPEN on an inherited RAG whose prior            *)
(*   session_close is fully SEALED: phase = COMPLETE AND transfer_ready =    *)
(*   TRUE. An interrupted close (transfer_ready = FALSE) must be repaired by *)
(*   `session-resume` first; a COMPLETE phase with transfer_ready still      *)
(*   FALSE is a mid-seal crash and is NOT safe to inherit. The carry-forward *)
(*   gate is fail-loud (no silent proceed) unless --force is given.         *)
(*                                                                          *)
(* ABSTRACTION.  A handoff is the prior close record                        *)
(*   [phase \in {COMPLETE, PARTIAL, NONE}, ready \in BOOLEAN]                *)
(* Sealed(h) == phase = COMPLETE /\ ready. A handoff is SAFE to inherit iff  *)
(* it is Sealed (ground truth). GUARD permits start iff Sealed.             *)
(*                                                                          *)
(* MASTER THEOREM (P_Equiv): StartOK(h) <=> Sealed(h) for EVERY handoff h.   *)
(* Two naive guards are refuted (CloseSealEnforce_naive.cfg):               *)
(*   NaiveReady : checks only ready  -> accepts [PARTIAL, TRUE] (unsealed)   *)
(*   NaivePhase : checks only phase  -> accepts [COMPLETE, FALSE] (mid-seal) *)
(***************************************************************************)
EXTENDS Naturals, FiniteSets

CONSTANTS COMPLETE, PARTIAL, NONE

Phases   == {COMPLETE, PARTIAL, NONE}
Handoffs == [phase : Phases, ready : BOOLEAN]

VARIABLE handoff

(* --- ground truth: safe to inherit iff fully sealed --- *)
Sealed(h)  == h.phase = COMPLETE /\ h.ready
SafeToInherit(h) == Sealed(h)

(* --- the chosen guard: open only on a sealed prior close --- *)
StartOK(h) == h.phase = COMPLETE /\ h.ready

(* --- discarded naive guards --- *)
NaiveReadyOK(h) == h.ready                 \* ignores phase
NaivePhaseOK(h) == h.phase = COMPLETE      \* ignores transfer_ready

(***************************************************************************)
(* Properties (state invariants over the current handoff).                 *)
(***************************************************************************)
P_Sound      == ~SafeToInherit(handoff) => ~StartOK(handoff)  \* never open on unsealed
P_NoFalsePos == SafeToInherit(handoff)  => StartOK(handoff)    \* sealed prior accepted
P_Equiv      == StartOK(handoff) <=> SafeToInherit(handoff)    \* MASTER characterization

AllProps == P_Sound /\ P_NoFalsePos /\ P_Equiv

(* Refutation targets (each expected to FAIL with a counterexample):        *)
(*   NaiveReadySound : [PARTIAL, TRUE]  is unsealed but NaiveReadyOK accepts *)
(*   NaivePhaseSound : [COMPLETE,FALSE] is unsealed but NaivePhaseOK accepts *)
NaiveReadySound == ~SafeToInherit(handoff) => ~NaiveReadyOK(handoff)
NaivePhaseSound == ~SafeToInherit(handoff) => ~NaivePhaseOK(handoff)

(***************************************************************************)
Init == handoff \in Handoffs
Next == UNCHANGED handoff
Spec == Init /\ [][Next]_handoff
=============================================================================
