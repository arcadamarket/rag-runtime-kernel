------------------------ MODULE TransportProjectionGate ------------------------
(***************************************************************************)
(* Formal model + proof for PROJECTION-DRIFT-UNGATED (S198).              *)
(*                                                                          *)
(* IMPLEMENTATION (drift_audit.check_transport_projection over             *)
(* rag_kernel.transport_projection.drift_reasons).                         *)
(*   operating_protocol.transport_allowlist is the AUTHORITY for which      *)
(*   agent-facing transports may run. .claude/transport_allowlist.json is a *)
(*   PROJECTION of it, read by the PreToolUse hook on every tool call       *)
(*   because the hook cannot afford to parse the RAG or take its lock.      *)
(*   A performance boundary may cache an authority ONLY while the cache is  *)
(*   verifiable; the projection therefore carries the sha256 of the rule    *)
(*   text it was rendered from.                                             *)
(*                                                                          *)
(*   S197 shipped that hash and a --check that compared it, then called the *)
(*   check from nowhere. Drift was DETECTABLE and NOT GATED -- and the rule *)
(*   text asserted "audit fails on drift", which was false when written.    *)
(*                                                                          *)
(* ABSTRACTION.  A deployment is (declared, rendered, hash, patterns):      *)
(*   declared  -- does the RAG declare the allowlist rule at all?           *)
(*   rendered  -- does a projection file exist on disk?                     *)
(*   hashOK    -- was it rendered from the CURRENT rule text?               *)
(*   patsOK    -- does its pattern list equal the declared list?            *)
(* hashOK/patsOK are modelled as independent because they fail             *)
(* independently in practice: editing the rule breaks hashOK, hand-editing  *)
(* the projection breaks patsOK, and the DEC-0009 failure mode is exactly   *)
(* the second one.                                                          *)
(*                                                                          *)
(* THE INTERESTING CASE IS declared /\ ~rendered. That is NOT a skip: it    *)
(* means the RAG says a policy is in force while the hook layer has nothing *)
(* to enforce -- the "declared but not running" shape that                  *)
(* ACTIVATION-GAP-S197 exists to name. P_MissingIsDrift pins it.            *)
(*                                                                          *)
(* THEOREM: the gate errors on exactly the divergent states and self-skips  *)
(* on undeclared ones (AllProps). The naive existence-only check is refuted *)
(* in TransportProjectionGate_naive.cfg.                                    *)
(***************************************************************************)
EXTENDS Naturals

VARIABLE dep

Deployments == [declared : BOOLEAN,
                rendered : BOOLEAN,
                hashOK   : BOOLEAN,
                patsOK   : BOOLEAN]

(* The projection genuinely agrees with its authority. *)
Agrees(d) == d.rendered /\ d.hashOK /\ d.patsOK

(* --- correct: what check_transport_projection reports as ERROR --- *)
GateErrors(d) == d.declared /\ ~Agrees(d)

(* --- buggy naive: "a projection file is present, so we are fine" --- *)
NaiveErrors(d) == d.declared /\ ~d.rendered

(***************************************************************************)
(* Properties.                                                             *)
(***************************************************************************)

\* 1. No rule declared -> silence. Most clones wire no allowlist; that is
\*    not a defect and must never be reported as one.
P_SelfSkips == ~dep.declared => ~GateErrors(dep)

\* 2. A declared rule with no projection rendered is DRIFT, not a skip.
P_MissingIsDrift ==
    \A d \in Deployments : (d.declared /\ ~d.rendered) => GateErrors(d)

\* 3. Rule text moved under the projection -> caught.
P_StaleHashCaught ==
    \A d \in Deployments : (d.declared /\ d.rendered /\ ~d.hashOK) => GateErrors(d)

\* 4. Projection hand-edited (the DEC-0009 second-source-of-truth move) -> caught.
P_HandEditCaught ==
    \A d \in Deployments : (d.declared /\ d.rendered /\ ~d.patsOK) => GateErrors(d)

\* 5. No false positives: a projection that truly agrees is never reported.
P_NoFalsePositive ==
    \A d \in Deployments : (d.declared /\ Agrees(d)) => ~GateErrors(d)

\* 6. Completeness, stated as one biconditional so no divergent state can be
\*    added later without breaking this proof.
P_ExactlyDivergence ==
    \A d \in Deployments : GateErrors(d) <=> (d.declared /\ ~Agrees(d))

AllProps ==
    /\ P_SelfSkips
    /\ P_MissingIsDrift
    /\ P_StaleHashCaught
    /\ P_HandEditCaught
    /\ P_NoFalsePositive
    /\ P_ExactlyDivergence

(* Refutation: existence-only checking misses BOTH real drift modes.        *)
(* Expected to FAIL at [declared|->TRUE, rendered|->TRUE, hashOK|->FALSE,   *)
(* patsOK|->TRUE] -- a projection that is present, parseable, and wrong.    *)
NaiveCatchesDrift ==
    \A d \in Deployments : (d.declared /\ ~Agrees(d)) => NaiveErrors(d)

(***************************************************************************)
Init == dep \in Deployments
Next == UNCHANGED dep
Spec == Init /\ [][Next]_dep
=============================================================================
