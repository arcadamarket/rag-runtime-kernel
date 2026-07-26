------------------------- MODULE BootGuardFirstAction -------------------------
(***************************************************************************)
(* Formal model + proof for BOOT-GUARD-FIRST-ACTION (KA-20, S172).         *)
(*                                                                          *)
(* IMPLEMENTATION (__main__.py session-start ritual + project memory).      *)
(*   The VERY FIRST action of every session MUST be the mechanized          *)
(*   `session-start` ritual (carry-forward gate -> gc dry-run -> attested    *)
(*   logger open, BOOT -> RULES_LOADED -> READY). No governed WORK action    *)
(*   (propose / checkpoint / edit / report-state) may occur before          *)
(*   session-start has driven the kernel to READY. This closes the eBay      *)
(*   S2/S4 "hand-scripted opening" drift where an agent skipped steps.       *)
(*                                                                          *)
(* ABSTRACTION.  A session is modeled as a finite trace of actions, each     *)
(*   Boot  the mechanized session-start ritual                              *)
(*   Work  any governed action that requires an open/attested session       *)
(* KA-20 == the first action is Boot (equivalently: no Work precedes the     *)
(*          first Boot).                                                     *)
(*                                                                          *)
(* MASTER THEOREM (P_Equiv): GuardOK(t) <=> Legit(t) for EVERY trace t,      *)
(* where Legit == every Work is preceded by a Boot. The naive "a Boot        *)
(* appears somewhere" guard is refuted in BootGuardFirstAction_naive.cfg     *)
(* (trace <<Work, Boot>>: a Work ran before the session was opened).         *)
(***************************************************************************)
EXTENDS Naturals, Sequences, FiniteSets

CONSTANTS Boot, Work, MaxLen

Actions  == {Boot, Work}
AllTraces == UNION { [1..n -> Actions] : n \in 0..MaxLen }

VARIABLE trace

(* --- ground truth: every Work at position i is preceded by some Boot --- *)
Legit(t) == \A i \in DOMAIN t :
                t[i] = Work => (\E j \in 1..(i-1) : t[j] = Boot)

(* --- the chosen guard: the first action must be Boot --- *)
GuardOK(t) == (Len(t) = 0) \/ t[1] = Boot

(* --- the discarded naive guard: a Boot appears anywhere in the trace --- *)
NaiveOK(t) == (Len(t) = 0) \/ (\E i \in DOMAIN t : t[i] = Boot)

(***************************************************************************)
(* Properties (checked as state invariants over the current trace).        *)
(***************************************************************************)
P_Sound      == ~Legit(trace) => ~GuardOK(trace)   \* work-before-boot always refused
P_NoFalsePos == Legit(trace)  => GuardOK(trace)     \* a properly-opened session accepted
P_Equiv      == GuardOK(trace) <=> Legit(trace)     \* MASTER characterization

AllProps == P_Sound /\ P_NoFalsePos /\ P_Equiv

(* Refutation: "Boot appears somewhere" is unsound -- a Work can precede it.  *)
(* Expected to FAIL with counterexample <<Work, Boot>>.                       *)
NaiveSound == ~Legit(trace) => ~NaiveOK(trace)

(***************************************************************************)
Init == trace \in AllTraces
Next == UNCHANGED trace
Spec == Init /\ [][Next]_trace
=============================================================================
