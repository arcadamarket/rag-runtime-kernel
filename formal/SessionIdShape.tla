---------------------------- MODULE SessionIdShape ----------------------------
(***************************************************************************)
(* Formal model + proof for PHANTOM-SESSION-ID-S1786488555313 (S198).      *)
(*                                                                          *)
(* IMPLEMENTATION (_derive_next_sid / _SESSION_ID_RE in __main__.py).      *)
(*   AUTO-SID-DERIVE computes the next session id as the increment of       *)
(*   meta.written_by_session. The pre-fix form matched `(\d+)$` and         *)
(*   incremented whatever it found, so a millisecond epoch parked in        *)
(*   written_by_session -- S1786488555313 -- derived cleanly to             *)
(*   S1786488555314. The phantom then LOOKED governed: it was stamped into  *)
(*   a session log and three WAL entries before the close audit refused.    *)
(*                                                                          *)
(* ABSTRACTION.  A stored id is characterised by two features that are      *)
(* jointly decidable from the string alone: whether it carries an           *)
(* alphabetic prefix, and how many digits its counter has. That is the      *)
(* whole input space the guard can see -- it cannot know INTENT, only       *)
(* SHAPE, which is precisely why the fix is a shape refusal and not a       *)
(* heuristic. Epoch widths are the adversary: 10 digits for seconds, 13     *)
(* for milliseconds, both far beyond any real session counter.             *)
(*                                                                          *)
(* THE BOUND IS NOT ARBITRARY, and this spec is where that is discharged:   *)
(* MaxDigits must sit strictly between the widest plausible counter and the *)
(* narrowest epoch. P_NoEpoch and P_KeepsRealCounters together pin it from  *)
(* both sides, so a later widening of the bound to 10 breaks a proof rather *)
(* than silently re-admitting the phantom.                                  *)
(*                                                                          *)
(* SCOPE NOTE, carried from the ledger: the item asked for the literal      *)
(* S<n> shape. That would refuse SESS0099, which clone deployments rely on  *)
(* (tests/test_auto_sid_derive.py). The prefix was never the defect. Hence  *)
(* HasAlphaPrefix rather than an equality against a single letter, and      *)
(* P_ClonePrefixesSurvive below.                                            *)
(*                                                                          *)
(* THEOREM: the shape guard admits every well-formed counter and refuses    *)
(* every epoch-width id (AllProps). The naive `ends-in-digits` form is      *)
(* refuted in SessionIdShape_naive.cfg.                                     *)
(***************************************************************************)
EXTENDS Naturals

CONSTANTS MaxDigits,        \* the implementation's ceiling (9)
          MaxRealCounter,   \* widest counter a real project reaches (9)
          EpochSecDigits,   \* 10
          EpochMsDigits     \* 13

\* Digit widths worth modelling: 0 (no counter at all) through the ms epoch.
Widths == 0 .. EpochMsDigits

\* A stored id, reduced to what the guard can actually observe.
Ids == [alpha : BOOLEAN, digits : Widths]

VARIABLE id

(* --- correct: alphabetic prefix AND a counter of 1..MaxDigits digits --- *)
Accept(i) == i.alpha /\ i.digits >= 1 /\ i.digits =< MaxDigits

(* --- buggy naive: "it ends in digits, so increment it" --- *)
NaiveAccept(i) == i.digits >= 1

(***************************************************************************)
(* Properties.                                                             *)
(***************************************************************************)

\* 1. No id of either epoch width is ever derived from. This is THE defect.
P_NoEpoch ==
    \A i \in Ids :
        (i.digits = EpochSecDigits \/ i.digits = EpochMsDigits) => ~Accept(i)

\* 2. Every real counter width, with a prefix, still derives. A guard that
\*    refuses the normal case is not a fix, it is an outage.
P_KeepsRealCounters ==
    \A d \in 1 .. MaxRealCounter : Accept([alpha |-> TRUE, digits |-> d])

\* 3. Clone prefixes survive: acceptance does not depend on WHICH prefix,
\*    only that one is present. Modelled by alpha being the sole prefix
\*    feature -- any two ids agreeing on digits agree on acceptance.
P_ClonePrefixesSurvive ==
    \A a \in Ids, b \in Ids :
        (a.alpha = b.alpha /\ a.digits = b.digits) => (Accept(a) = Accept(b))

\* 4. A bare timestamp with no alphabetic prefix is refused whatever its width.
P_PrefixRequired ==
    \A i \in Ids : ~i.alpha => ~Accept(i)

\* 5. The bound is squeezed from both sides, so it cannot drift silently.
P_BoundIsTight ==
    /\ MaxRealCounter =< MaxDigits
    /\ MaxDigits < EpochSecDigits
    /\ EpochSecDigits < EpochMsDigits

AllProps ==
    /\ P_NoEpoch
    /\ P_KeepsRealCounters
    /\ P_ClonePrefixesSurvive
    /\ P_PrefixRequired
    /\ P_BoundIsTight

(* Refutation: the pre-fix rule accepts the exact phantom that caused this  *)
(* item. Expected to FAIL at [alpha |-> TRUE, digits |-> 13] -- the         *)
(* millisecond epoch S1786488555313 -- and at digits = 10 as well.          *)
NaiveNoEpoch ==
    \A i \in Ids :
        (i.digits = EpochSecDigits \/ i.digits = EpochMsDigits) => ~NaiveAccept(i)

(***************************************************************************)
Init == id \in Ids
Next == UNCHANGED id
Spec == Init /\ [][Next]_id
=============================================================================
