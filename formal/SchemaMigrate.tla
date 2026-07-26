----------------------------- MODULE SchemaMigrate -----------------------------
(***************************************************************************)
(* Formal model + proof for SCHEMA-MIGRATE (migrate verb, boot-time).      *)
(*                                                                          *)
(* IMPLEMENTATION (migrate verb + session-start).                          *)
(*   At boot, a RAG at an older schema version must be brought forward to    *)
(*   the CURRENT schema before the session goes READY. Migration is:         *)
(*     monotone      never lowers the schema version (no downgrade)          *)
(*     total         brings ANY supported prior version up to CURRENT in     *)
(*                   one governed migrate (not just single-step)             *)
(*     idempotent    a RAG already at CURRENT is left unchanged             *)
(*                                                                          *)
(* ABSTRACTION.  A schema version is a Nat in 0..CURRENT. Migrate(v) is the  *)
(* boot-time bring-forward. The naive "increment-by-one" migrator fails to   *)
(* reach CURRENT in one pass when the RAG is >= 2 versions behind.           *)
(*                                                                          *)
(* THEOREM: Migrate is monotone, total-to-CURRENT, idempotent (AllProps).   *)
(* The step-by-one naive migrator is refuted in SchemaMigrate_naive.cfg.    *)
(***************************************************************************)
EXTENDS Naturals

CONSTANTS CURRENT

Versions == 0..CURRENT

VARIABLE ver

(* --- correct: bring any supported version forward to CURRENT --- *)
Migrate(v) == CURRENT

(* --- buggy naive: single-step upgrade only --- *)
NaiveMigrate(v) == IF v < CURRENT THEN v + 1 ELSE v

(***************************************************************************)
(* Properties (state invariants over the current schema version).          *)
(***************************************************************************)
P_Monotone     == Migrate(ver) >= ver         \* no downgrade
P_ReachCurrent == Migrate(ver) = CURRENT       \* total: boot ends at CURRENT
P_Idempotent   == Migrate(CURRENT) = CURRENT   \* already-current unchanged

AllProps == P_Monotone /\ P_ReachCurrent /\ P_Idempotent

(* Refutation: single-step migrator does NOT reach CURRENT in one pass when   *)
(* the RAG is >= 2 versions behind. Expected to FAIL, e.g. ver = 0 -> 1.      *)
NaiveReach == NaiveMigrate(ver) = CURRENT

(***************************************************************************)
Init == ver \in Versions
Next == UNCHANGED ver
Spec == Init /\ [][Next]_ver
=============================================================================
