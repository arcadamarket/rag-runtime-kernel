----------------------- MODULE GovernancePinProvenance -----------------------
(***************************************************************************)
(* Formal model + proof for RULE19-PIN-REFRESH (F3, S175) —                 *)
(* drift_audit.check_governance_pin_provenance.                             *)
(*                                                                          *)
(* IMPLEMENTATION.                                                          *)
(*   Rule 19 (governance_runtime) declares that governance runs on the      *)
(*   deployment's OWN pinned runtime, naming it as a version + release      *)
(*   commit. The guard binds that DECLARED pin to the live authority        *)
(*   rag_kernel.__version__:                                                *)
(*     - every DECLARED pin version that differs from live  -> ERROR        *)
(*     - a declared pin with no release provenance (commit) -> WARNING      *)
(*     - a pin CLAIM with no parseable version              -> WARNING      *)
(*     - no governance_runtime rule at all                  -> no findings  *)
(*   Declared pins are the two anchored phrases ("pinned vX.Y.Z runtime",   *)
(*   "byte-identical copy of the runtime-vX.Y.Z"). HISTORY tokens in the    *)
(*   same string ("pin refreshed S173->v0.4.46@8af6ed6") are deliberately   *)
(*   NOT anchored: they are SUPPOSED to name older versions.                *)
(*                                                                          *)
(* ABSTRACTION.  A rule record is                                           *)
(*   [declared \in SUBSET Versions,   \* anchored pin versions              *)
(*    history  \in SUBSET Versions,   \* history tokens (must be ignored)   *)
(*    prov     \in BOOLEAN,           \* release commit present             *)
(*    claims   \in BOOLEAN]           \* text claims a pinned backbone      *)
(*   against a fixed live authority LIVE \in Versions.                      *)
(*                                                                          *)
(*   GROUND TRUTH (the defect the guard exists to catch): the rule MISSTATES *)
(*   which runtime governs — i.e. some ANCHORED pin disagrees with LIVE.     *)
(*   History disagreement is NOT a defect; it is the audit trail.            *)
(*                                                                          *)
(* MASTER THEOREM (P_Equiv): HasError(r) <=> Misstates(r) for EVERY rule     *)
(* record r. Supporting properties pin the severity split and the self-skip. *)
(*                                                                          *)
(* Two naive guards are refuted (GovernancePinProvenance_naive.cfg):         *)
(*   NaiveAllTokensSound : flags ANY version token, history included ->      *)
(*       false ERROR on a correctly-refreshed pin that records its own past  *)
(*       (this is the E-043/KA-CS-PROSE-DRIFT trap: a guard that cries wolf  *)
(*       on the audit trail gets muted, and then misses the real drift).     *)
(*   NaiveClaimSound     : treats any unverifiable pin CLAIM as an ERROR ->  *)
(*       false ERROR on a deployment that words its rule differently, i.e.   *)
(*       a WARNING-severity condition escalated to a release blocker.        *)
(***************************************************************************)
EXTENDS Naturals, FiniteSets

CONSTANTS LIVE, OLD, STRAY

Versions == {LIVE, OLD, STRAY}

Rules == [declared : SUBSET Versions,
          history  : SUBSET Versions,
          prov     : BOOLEAN,
          claims   : BOOLEAN]

VARIABLE rule

(* --- ground truth --------------------------------------------------- *)
(* The rule misstates the governing runtime iff an ANCHORED pin != LIVE. *)
Misstates(r) == \E v \in r.declared : v # LIVE

(* --- the chosen guard ------------------------------------------------ *)
(* ERROR exactly on an anchored pin that disagrees with the authority.   *)
GuardErrors(r)   == {v \in r.declared : v # LIVE}
HasError(r)      == GuardErrors(r) # {}

(* WARNINGs: a declared pin with no release provenance, or a pin claim   *)
(* the guard cannot resolve to any version.                             *)
HasWarning(r)    == \/ (r.declared # {} /\ ~r.prov)
                    \/ (r.declared  = {} /\ r.claims)

(* Self-skip: no declared pin and no claim -> the deployment does not    *)
(* self-host; the guard returns nothing at all.                         *)
Silent(r)        == ~HasError(r) /\ ~HasWarning(r)

(* --- discarded naive guards ----------------------------------------- *)
NaiveAllTokensError(r) == \E v \in (r.declared \cup r.history) : v # LIVE
NaiveClaimError(r)     == HasError(r) \/ (r.declared = {} /\ r.claims)

(***************************************************************************)
(* Properties (state invariants over the current rule record).             *)
(***************************************************************************)

\* MASTER: the ERROR verdict is exactly the ground-truth defect.
P_Equiv      == HasError(rule) <=> Misstates(rule)

\* Sound: a rule that does NOT misstate is never an ERROR.
P_Sound      == ~Misstates(rule) => ~HasError(rule)

\* Complete: a rule that DOES misstate is always an ERROR (never a warning).
P_Complete   == Misstates(rule)  => HasError(rule)

\* The load-bearing exclusion: history tokens cannot influence the verdict.
\* Two records differing ONLY in `history` get the same ERROR verdict — stated
\* here in the form TLC can check over a single state: the verdict is a
\* function of `declared` alone.
P_HistoryIrrelevant ==
    HasError(rule) <=> (\E v \in rule.declared : v # LIVE)

\* A correctly-refreshed pin stays clean no matter what its audit trail says.
P_RefreshedIsClean ==
    (rule.declared \subseteq {LIVE}) => ~HasError(rule)

\* Severity discipline: an unresolvable/unprovenanced pin WARNS, never ERRORs.
P_UnverifiableWarnsOnly ==
    (~Misstates(rule) /\ HasWarning(rule)) => ~HasError(rule)

\* A deployment that declares no pin and makes no claim is out of scope.
P_SelfSkip ==
    (rule.declared = {} /\ ~rule.claims) => Silent(rule)

AllProps == /\ P_Equiv
            /\ P_Sound
            /\ P_Complete
            /\ P_HistoryIrrelevant
            /\ P_RefreshedIsClean
            /\ P_UnverifiableWarnsOnly
            /\ P_SelfSkip

(***************************************************************************)
(* Refutation targets (each expected to FAIL with a counterexample):        *)
(*   NaiveAllTokensSound : declared = {LIVE}, history = {OLD}  — a properly *)
(*       refreshed pin that records its own history; ground truth is clean, *)
(*       the naive all-tokens guard reports an ERROR.                       *)
(*   NaiveClaimSound     : declared = {}, claims = TRUE — an unverifiable   *)
(*       pin claim; ground truth is clean (nothing is misstated), the naive *)
(*       guard escalates it to a release-blocking ERROR.                    *)
(***************************************************************************)
NaiveAllTokensSound == ~Misstates(rule) => ~NaiveAllTokensError(rule)
NaiveClaimSound     == ~Misstates(rule) => ~NaiveClaimError(rule)

(***************************************************************************)
Init == rule \in Rules
Next == UNCHANGED rule
Spec == Init /\ [][Next]_rule
=============================================================================
