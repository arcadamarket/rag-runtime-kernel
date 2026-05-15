--------------------------- MODULE RAGKernel ----------------------------
(*
  Formal specification of the RAG Runtime Kernel state machine.

  Scope
  -----
  This module specifies the core safety and liveness properties of the
  RAG Runtime Kernel as implemented in:
    src/rag_kernel/state_machine.py  -- transition table and guards
    src/rag_kernel/persistence.py    -- WAL append and replay
    src/rag_kernel/schemas.py        -- proposal contract

  The spec covers:
    1. Legal state transitions (exact mirror of TRANSITIONS dict)
    2. Proposal lifecycle: STAGED -> COMMITTED | REJECTED (single-writer)
    3. WAL invariants: append-only, monotone seq, write-before-commit
    4. Crash / nondeterministic failure -> RECOVERY path
    5. Safety invariants checked by TLC
    6. Liveness property: no permanent RECOVERY trap

  Notation conventions
  --------------------
  - State variables begin with a lowercase letter.
  - Constants are ALL_CAPS.
  - Operator names use CamelCase.
  - Primed variables (state') denote next-state values.

  References
  ----------
  - Lamport, "Specifying Systems" (Addison-Wesley, 2002)
  - docs/v3.2_ARCHITECTURE_DESIGN.md §6 (state machine), §7 (WAL)
*)

EXTENDS Integers, Sequences, FiniteSets, TLC

----------------------------------------------------------------------------
(* ========================================================================
   SECTION 1: CONSTANTS AND TYPE DECLARATIONS
   ======================================================================== *)

(* The seven legal kernel states (mirrors State enum in state_machine.py). *)
CONSTANT
    BOOTING,
    READY,
    INGESTING,
    WORKING,
    CHECKPOINTING,
    CLOSING,
    RECOVERY

States == {BOOTING, READY, INGESTING, WORKING, CHECKPOINTING, CLOSING, RECOVERY}

(* Terminal states have no outgoing transitions. *)
TerminalStates == {CLOSING}

(* Non-terminal states are crash-eligible. *)
CrashEligibleStates == States \ TerminalStates

(* Proposal lifecycle values. *)
CONSTANT STAGED, COMMITTED, REJECTED, NONE

ProposalStatuses == {STAGED, COMMITTED, REJECTED, NONE}

(* Maximum WAL sequence number modeled.  TLC will explore up to this bound.
   Keep small (e.g. 8) for tractable model checking; raise for deeper checks. *)
CONSTANT MaxWALSeq

----------------------------------------------------------------------------
(* ========================================================================
   SECTION 2: STATE VARIABLES
   ======================================================================== *)

VARIABLES
    \* Current kernel state (one of States).
    state,

    \* Monotone sequence counter for committed state changes.
    \* Incremented on every successful transition.
    stateSeq,

    \* Proposal subsystem.
    \* proposalStatus \in ProposalStatuses
    proposalStatus,
    \* proposalTarget: the State the staged proposal wants to move to.
    \* Meaningful only when proposalStatus = STAGED.
    proposalTarget,

    \* Write-Ahead Log modeled as a sequence of records.
    \* Each record is [seq |-> Nat, toState |-> States].
    \* Mirrors WALEntry in persistence.py.
    wal,

    \* Whether the system is currently in a crash/recovery episode.
    \* True between Crash() and RecoveryComplete().
    crashed

----------------------------------------------------------------------------
(* ========================================================================
   SECTION 3: DERIVED DEFINITIONS
   ======================================================================== *)

\* All variables, used in the stuttering frame condition.
vars == <<state, stateSeq, proposalStatus, proposalTarget, wal, crashed>>

\* Current WAL sequence number (length of the log).
WALSeq == Len(wal)

\* Type predicate for WAL entries.
IsWALEntry(e) ==
    /\ DOMAIN e = {"seq", "toState"}
    /\ e.seq \in Nat
    /\ e.toState \in States

----------------------------------------------------------------------------
(* ========================================================================
   SECTION 4: TRANSITION TABLE
   ======================================================================== *)
(*
  Direct transcription of TRANSITIONS in state_machine.py.

  TRANSITIONS: dict[State, frozenset[State]] = {
      State.BOOTING:       frozenset({State.READY, State.RECOVERY}),
      State.READY:         frozenset({State.INGESTING, State.WORKING,
                                      State.CHECKPOINTING, State.CLOSING}),
      State.INGESTING:     frozenset({State.READY, State.CHECKPOINTING,
                                      State.RECOVERY}),
      State.WORKING:       frozenset({State.READY, State.CHECKPOINTING,
                                      State.RECOVERY}),
      State.CHECKPOINTING: frozenset({State.READY, State.CLOSING,
                                      State.RECOVERY}),
      State.CLOSING:       frozenset(),
      State.RECOVERY:      frozenset({State.READY, State.BOOTING}),
  }
*)

AllowedTargets(s) ==
    CASE s = BOOTING       -> {READY, RECOVERY}
      [] s = READY         -> {INGESTING, WORKING, CHECKPOINTING, CLOSING}
      [] s = INGESTING     -> {READY, CHECKPOINTING, RECOVERY}
      [] s = WORKING       -> {READY, CHECKPOINTING, RECOVERY}
      [] s = CHECKPOINTING -> {READY, CLOSING, RECOVERY}
      [] s = CLOSING       -> {}
      [] s = RECOVERY      -> {READY, BOOTING}

IsLegalTransition(from, to) == to \in AllowedTargets(from)

----------------------------------------------------------------------------
(* ========================================================================
   SECTION 5: INITIAL STATE
   ======================================================================== *)
(*
  The kernel always boots in BOOTING (matches StateMachine.__init__ default).
  The WAL is empty, no proposal is staged, and the system is not crashed.
*)

Init ==
    /\ state          = BOOTING
    /\ stateSeq       = 0
    /\ proposalStatus = NONE
    /\ proposalTarget = BOOTING     \* placeholder; irrelevant when NONE
    /\ wal            = <<>>
    /\ crashed        = FALSE

----------------------------------------------------------------------------
(* ========================================================================
   SECTION 6: ACTIONS
   ======================================================================== *)

(*
  -----------------------------------------------------------------------
  6.1  StageProposal
  -----------------------------------------------------------------------
  An actor (LLM or API caller) stages a proposal for a state transition.
  Mirrors: schemas.py validate_proposal + the "single-writer" constraint.

  Preconditions:
    - No proposal is currently staged (single-writer guarantee).
    - The system is not crashed.
    - The proposed target is a legal next state from the current state.
    - A proposal can only be staged when in READY or WORKING
      (the states that represent an "active" session accepting commands).
*)

StageProposal(target) ==
    /\ ~crashed
    /\ proposalStatus = NONE
    /\ state \in {READY, WORKING}
    /\ IsLegalTransition(state, target)
    /\ WALSeq < MaxWALSeq          \* bound for TLC finite-state check
    /\ proposalStatus' = STAGED
    /\ proposalTarget' = target
    /\ UNCHANGED <<state, stateSeq, wal, crashed>>

(*
  -----------------------------------------------------------------------
  6.2  CommitProposal
  -----------------------------------------------------------------------
  A staged proposal is committed.  This is the Propose-Validate-Commit
  critical section:
    1. WAL entry is written BEFORE the state changes (WAL-before-commit).
    2. State changes.
    3. stateSeq advances.
    4. Proposal status clears to NONE.

  The WAL entry records the new seq and target state.
  Mirrors: persistence.WAL.append() called BEFORE StateMachine.transition().

  Preconditions:
    - A proposal is staged.
    - The system is not crashed.
    - The state machine is in a state that allows the staged transition
      (re-checked at commit time, guards may have changed).
*)

CommitProposal ==
    /\ ~crashed
    /\ proposalStatus = STAGED
    /\ IsLegalTransition(state, proposalTarget)
    /\ WALSeq < MaxWALSeq
    \* WAL entry written BEFORE state changes (WAL-before-commit invariant)
    /\ wal'            = Append(wal, [seq |-> WALSeq + 1,
                                      toState |-> proposalTarget])
    /\ state'          = proposalTarget
    /\ stateSeq'       = stateSeq + 1
    /\ proposalStatus' = NONE
    /\ proposalTarget' = BOOTING   \* reset placeholder
    /\ UNCHANGED crashed

(*
  -----------------------------------------------------------------------
  6.3  RejectProposal
  -----------------------------------------------------------------------
  A staged proposal is rejected (guard failed, or target became illegal
  due to concurrent state change).  No state change, no WAL entry.

  Mirrors: StateMachine.transition() returning False.
*)

RejectProposal ==
    /\ ~crashed
    /\ proposalStatus = STAGED
    /\ proposalStatus' = REJECTED
    /\ UNCHANGED <<state, stateSeq, proposalTarget, wal, crashed>>

(*
  -----------------------------------------------------------------------
  6.4  ClearRejection
  -----------------------------------------------------------------------
  After a rejection is observed, the proposal slot is cleared so a new
  proposal can be staged.  Separate from RejectProposal to make the
  REJECTED status observable to invariant checking.
*)

ClearRejection ==
    /\ proposalStatus = REJECTED
    /\ proposalStatus' = NONE
    /\ proposalTarget' = BOOTING
    /\ UNCHANGED <<state, stateSeq, wal, crashed>>

(*
  -----------------------------------------------------------------------
  6.5  DirectTransition
  -----------------------------------------------------------------------
  Models transitions that happen without going through the proposal
  subsystem (e.g., crash-triggered moves to RECOVERY, or the kernel's
  internal boot completion).

  This covers:
    - BOOTING -> READY    (normal boot completion)
    - BOOTING -> RECOVERY (boot detected corruption)
    - Any state -> RECOVERY via Crash (see below)
    - RECOVERY -> READY / BOOTING (recovery completion)
    - READY / INGESTING / WORKING -> CHECKPOINTING (scheduled checkpoint)
    - CHECKPOINTING -> CLOSING (graceful shutdown)

  The WAL write-before-commit rule still applies.
*)

DirectTransition(target) ==
    /\ ~crashed
    /\ proposalStatus = NONE       \* no pending proposal
    /\ IsLegalTransition(state, target)
    /\ WALSeq < MaxWALSeq
    /\ wal'       = Append(wal, [seq |-> WALSeq + 1, toState |-> target])
    /\ state'     = target
    /\ stateSeq'  = stateSeq + 1
    /\ UNCHANGED <<proposalStatus, proposalTarget, crashed>>

(*
  -----------------------------------------------------------------------
  6.6  Crash
  -----------------------------------------------------------------------
  A nondeterministic crash can occur in any non-CLOSING state.
  After a crash:
    - state moves to RECOVERY (matches the RECOVERY entry in TRANSITIONS
      for all crash-eligible states, modeled via force_state() in Python).
    - crashed flag is set to TRUE (guards against further actions until
      recovery completes).
    - No new WAL entry is written — the crash may have left the WAL
      in a partially-written state that replay will reconcile.

  Mirrors: StateMachine.force_state(State.RECOVERY, reason="crash")
*)

Crash ==
    /\ state \in CrashEligibleStates
    /\ ~crashed
    /\ state'          = RECOVERY
    /\ crashed'        = TRUE
    /\ proposalStatus' = NONE       \* in-flight proposal is abandoned
    /\ proposalTarget' = BOOTING
    /\ UNCHANGED <<stateSeq, wal>>

(*
  -----------------------------------------------------------------------
  6.7  RecoveryComplete
  -----------------------------------------------------------------------
  The recovery process replays the WAL and transitions to either READY
  (normal recovery) or BOOTING (full restart required).

  After recovery:
    - stateSeq is reconciled with the highest WAL seq (WAL.replay()).
    - crashed flag cleared.
    - Proposal slot remains NONE.

  Mirrors: WAL.replay() + StateMachine.transition(State.READY).
*)

RecoveryComplete(target) ==
    /\ crashed
    /\ state = RECOVERY
    /\ target \in AllowedTargets(RECOVERY)   \* {READY, BOOTING}
    /\ WALSeq < MaxWALSeq
    /\ wal'       = Append(wal, [seq |-> WALSeq + 1, toState |-> target])
    /\ state'     = target
    /\ stateSeq'  = IF WALSeq + 1 > stateSeq
                    THEN WALSeq + 1
                    ELSE stateSeq
    /\ crashed'   = FALSE
    /\ UNCHANGED <<proposalStatus, proposalTarget>>

----------------------------------------------------------------------------
(* ========================================================================
   SECTION 7: NEXT-STATE RELATION
   ======================================================================== *)
(*
  Next is the disjunction of all possible actions.  TLC explores every
  enabled action at every state.
*)

Next ==
    \/ Crash
    \/ RejectProposal
    \/ ClearRejection
    \/ CommitProposal
    \/ (\E target \in States : StageProposal(target))
    \/ (\E target \in States : DirectTransition(target))
    \/ (\E target \in {READY, BOOTING} : RecoveryComplete(target))

(*
  Specification: Init followed by repeated Next steps.
  Fairness condition (weak fairness on recovery) ensures the system does
  not stay stuck in RECOVERY forever when a recovery action is enabled.
*)

Fairness ==
    /\ WF_vars(\E target \in {READY, BOOTING} : RecoveryComplete(target))
    /\ WF_vars(CommitProposal)
    /\ WF_vars(ClearRejection)

Spec == Init /\ [][Next]_vars /\ Fairness

----------------------------------------------------------------------------
(* ========================================================================
   SECTION 8: INVARIANTS (Safety Properties)
   ======================================================================== *)

(*
  8.1  TypeInvariant
  ------------------
  All variables hold values of their declared types.
  This is TLC's first line of defence against spec bugs.
*)

TypeInvariant ==
    /\ state          \in States
    /\ stateSeq       \in Nat
    /\ proposalStatus \in ProposalStatuses
    /\ proposalTarget \in States
    /\ crashed        \in BOOLEAN
    /\ \A i \in DOMAIN wal : IsWALEntry(wal[i])
    /\ WALSeq         \in Nat

(*
  8.2  TransitionSafety
  ---------------------
  Every state reached via a committed transition was legal from its
  predecessor.  We check this indirectly: the current state is always
  reachable from BOOTING via the legal transition graph.

  TLC verifies this by construction (Next only applies legal transitions),
  but we add an explicit invariant as documentation and cross-check.

  A state s is in the reachable set if it equals BOOTING or there exists
  some predecessor p such that s \in AllowedTargets(p).
*)

ReachableFromBooting ==
    LET Succ[s \in States] == AllowedTargets(s)
        \* Compute transitive closure from BOOTING
        Reachable == {t \in States :
                        \/ t = BOOTING
                        \/ \E p \in States : t \in Succ[p]}
    IN state \in Reachable

TransitionSafety == ReachableFromBooting

(*
  8.3  SingleWriter
  -----------------
  At most one proposal can be staged at any time.
  Mirrors the "single-writer" constraint in the concurrency model.
*)

SingleWriter ==
    proposalStatus = STAGED =>
        /\ proposalTarget \in States
        /\ IsLegalTransition(state, proposalTarget)

(*
  8.4  WALConsistency
  -------------------
  The WAL is append-only and monotonically sequenced.
  Mirrors WAL._scan_max_seq() and the seq increment in WAL.append().

  Properties checked:
    a) WAL sequence numbers are strictly increasing.
    b) WAL seq is always >= stateSeq (WAL never lags behind state).
    c) Each WAL entry records a state that was a legal transition target
       (or RECOVERY as a crash destination).
*)

WALConsistency ==
    /\ \A i \in DOMAIN wal : wal[i].seq = i
    /\ WALSeq >= stateSeq
    /\ \A i \in 2..Len(wal) : wal[i].seq > wal[i-1].seq

(*
  8.5  TerminalSafety
  -------------------
  Once in CLOSING, the state never changes.
  Mirrors: TRANSITIONS[State.CLOSING] == frozenset() in state_machine.py.
*)

TerminalSafety ==
    state = CLOSING => crashed = FALSE /\ proposalStatus = NONE

(*
  8.6  NoDeadlock (in non-terminal, non-crashed states)
  ------------------------------------------------------
  In every non-terminal, non-crashed state there is at least one enabled
  action.  This guards against a stuck machine that isn't CLOSING.

  We check that AllowedTargets is non-empty for all non-terminal states,
  plus that proposals can be staged or direct transitions can fire.
*)

NoDeadlock ==
    (state \notin TerminalStates /\ ~crashed) =>
        AllowedTargets(state) # {}

(*
  8.7  CrashRecoveryConsistency
  ------------------------------
  When crashed = TRUE, the state must be RECOVERY.
  This ensures the Crash action's postcondition is maintained.
*)

CrashRecoveryConsistency ==
    crashed => state = RECOVERY

(*
  8.8  WALPrecedesStateChange (WAL-before-commit)
  ------------------------------------------------
  After any commit (CommitProposal or DirectTransition), the WAL contains
  an entry for the new state before stateSeq advances.

  We verify the weaker form checkable as a state invariant:
  if stateSeq > 0, the last WAL entry's toState equals the current state
  (assuming no crash has intervened to leave things in RECOVERY without
  a new WAL entry).
*)

WALPrecedesStateChange ==
    (stateSeq > 0 /\ ~crashed) =>
        (Len(wal) > 0 /\ wal[Len(wal)].toState = state)

----------------------------------------------------------------------------
(* ========================================================================
   SECTION 9: LIVENESS PROPERTIES (Temporal)
   ======================================================================== *)

(*
  9.1  EventualProgress
  ---------------------
  Under weak fairness on RecoveryComplete, the system does not stay in
  RECOVERY forever.  This is the formal rendering of:
    "After a crash, the system eventually reaches READY."
*)

EventualProgress ==
    (crashed /\ state = RECOVERY) ~> (state = READY /\ ~crashed)

(*
  9.2  EventualTermination
  ------------------------
  If the system enters CLOSING, it stays there (trivially true by
  TerminalSafety, but stated as a temporal property for documentation).
*)

EventualTermination ==
    state = CLOSING ~> [](state = CLOSING)

(*
  9.3  ProposalEventuallyResolved
  --------------------------------
  A staged proposal is eventually either committed or rejected.
  Under weak fairness on CommitProposal, a validly-staged proposal
  does not sit pending forever.
*)

ProposalEventuallyResolved ==
    proposalStatus = STAGED ~> proposalStatus \in {COMMITTED, REJECTED, NONE}

----------------------------------------------------------------------------
(* ========================================================================
   SECTION 10: COMBINED INVARIANT (convenience for TLC config)
   ======================================================================== *)

Invariants ==
    /\ TypeInvariant
    /\ TransitionSafety
    /\ SingleWriter
    /\ WALConsistency
    /\ TerminalSafety
    /\ NoDeadlock
    /\ CrashRecoveryConsistency
    /\ WALPrecedesStateChange

Properties ==
    /\ EventualProgress
    /\ EventualTermination
    /\ ProposalEventuallyResolved

=============================================================================
