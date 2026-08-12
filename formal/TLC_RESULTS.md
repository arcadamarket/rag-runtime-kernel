# TLC Model Checker Results

## S198 full re-run — 2026-08-12

Every spec in this directory re-checked against the kernel at runtime 0.4.58,
HEAD `62b8408`, alongside the 2,758-test Python gate. **21 configs: 11 proofs
PASSED, 10 counterfactuals VIOLATED as designed.**

A counterfactual that PASSES is a regression, not good news — it means the guard
it refutes has been weakened back to the naive form. Read the right-hand column
as "this is what the naive rule lets through".

| Spec | Proof | Counterfactual | Distinct states |
|---|---|---|---|
| `BootGuardFirstAction` | PASSED | `NaiveSound` violated | 63 |
| `BootmapRootPin` | PASSED | `NaivePinned` violated | 6 |
| `CloseSealEnforce` | PASSED | `NaivePhaseSound` violated | 6 |
| `ErrlogIdGuard` | PASSED | `NaiveFalsePos` violated | 1,555 |
| `GovernancePinProvenance` | PASSED | `NaiveClaimSound` violated | 256 |
| `IntentFidelityGate` | PASSED | `NaiveTextSound` violated | 4 |
| `SchemaMigrate` | PASSED | `NaiveReach` violated | 4 |
| `SecretsIngestGuard` | PASSED | `NaiveSound` violated | 121 |
| `RAGKernel` | PASSED | *(no counterfactual)* | 168,520 (389,522 generated) |
| `SessionIdShape` **(new, S198)** | PASSED | `NaiveNoEpoch` violated | 28 |
| `TransportProjectionGate` **(new, S198)** | PASSED | `NaiveCatchesDrift` violated | 16 |

### What the two new specs discharge

**`SessionIdShape`** — the AUTO-SID-DERIVE refusal. Proves the guard refuses
both epoch widths (10-digit seconds, 13-digit milliseconds) while admitting
every real counter and every clone prefix, and squeezes `MaxDigits` from both
sides (`MaxRealCounter =< MaxDigits < EpochSecDigits`) so the bound cannot be
widened later without breaking a proof. The counterfactual is the pre-S198 rule
— "ends in digits, so increment it" — refuted at `digits = 13`, which is exactly
`S1786488555313`.

**`TransportProjectionGate`** — the audit clause. Proves the gate errors on
exactly the divergent deployments and self-skips where no allowlist rule is
declared, stated as a biconditional so a new divergence mode cannot be added
without breaking the proof. Includes `P_MissingIsDrift`: a declared rule with no
rendered projection is drift, not a skip. The counterfactual is existence-only
checking, refuted by a projection that is present, parseable, and rendered from
a rule text that has since changed.

### Note on `RAGKernel` run time

`RAGKernel` looked hung for over twenty minutes before this run was diagnosed:
TLC spills its fingerprint set and state queue to disk, and the working
directory was on the `/mnt/c` 9p mount. Re-run from local disk it finished in
about two minutes. The spec was never the problem. See README.

---

# Historical — RAGKernel.tla
**Date:** 2026-05-16
**TLC Version:** 2026.05.12.170007
**MaxWALSeq:** 8

---

## Run Summary

| Metric | Value |
|---|---|
| States generated | 136,193 total |
| Distinct states | 84,261 |
| Search depth | 18 |
| Time | 6 seconds |
| Workers | 1 (8 cores available) |
| Fingerprint collision probability | 6.7E-10 (negligible) |

---

## Safety Invariants — ALL PASSED

| Invariant | Status | Description |
|---|---|---|
| TypeInvariant | PASSED | All variables hold declared types |
| TransitionSafety | PASSED | Every state reachable from BOOTING via legal graph |
| SingleWriter | PASSED | At most one proposal staged at a time |
| WALConsistency | PASSED | WAL is append-only, monotone, never lags behind state |
| TerminalSafety | PASSED | CLOSING is stable (no exit, no crash flag, no pending proposal) |
| NoDeadlock | PASSED | Non-terminal, non-crashed states always have enabled actions |
| CrashRecoveryConsistency | PASSED | crashed=TRUE implies state=RECOVERY |
| WALPrecedesStateChange | PASSED | WAL entry exists before state advances |

---

## Liveness Properties — Phase 1: DEFERRED (bounded model limitation)

Liveness checking found counterexamples caused by the finite `MaxWALSeq` bound:
when the WAL fills up (reaches MaxWALSeq entries), all actions that append to
the WAL become disabled, causing the system to stutter in whatever state it's in.
If that state is RECOVERY with crashed=TRUE, TLC flags a liveness violation.

This is an artifact of the bounded model, not a real system bug.

**Initial finding (pre-fix):** TLC found a genuine BOOTING↔RECOVERY infinite loop
where `RecoveryComplete` nondeterministically chose BOOTING over READY forever.

**Fix applied:** Strengthened fairness from `WF` to `SF` on `RecoveryComplete(READY)`,
and added `WF_vars(DirectTransition(READY))`. This ensures recovery eventually reaches
READY, matching the Python implementation behavior.

---

## Phase 2: WAL Compaction + Liveness Verification — VERIFIED

**Date:** 2026-05-19 (S16–S17)
**Status:** ALL PASSED — 8 safety invariants + 3 liveness properties verified

### Changes Made

1. **WALCompaction action added** (Section 6.7 in RAGKernel.tla):
   - Fires when: `state ∉ TerminalStates`, `Len(wal) >= 2`, `proposalStatus = NONE`
   - Allowed during `crashed=TRUE` — compaction is a storage-layer op that doesn't alter kernel state, and is essential for recovering from crash-at-full-WAL
   - Effect: Replaces entire WAL with a single entry `<<[seq=1, toState=wal[Len(wal)].toState]>>`, resets `stateSeq` to 1
   - Models real-world WAL checkpoint rotation in `persistence.py`
   - Resolves the finite-bound issue: WAL no longer fills up permanently

2. **Next-state relation updated**: `WALCompaction` added to the disjunction

3. **Fairness extended** (4 conditions total):
   - `SF_vars(RecoveryComplete(READY))` — prevents RECOVERY livelock
   - `SF_vars(DirectTransition(READY))` — prevents BOOTING↔RECOVERY direct-transition loop (SF required because nondeterministic target choice interrupts continuous enablement)
   - `WF_vars(CommitProposal)` + `WF_vars(ClearRejection)` — proposal lifecycle
   - `WF_vars(WALCompaction)` — prevents WAL exhaustion

4. **RAGKernel.cfg updated**: All 3 PROPERTY lines uncommented:
   - `PROPERTY EventualProgress`
   - `PROPERTY EventualTermination`
   - `PROPERTY ProposalEventuallyResolved`

### Liveness Bugs Found and Fixed During TLC

Two genuine liveness violations were caught and fixed before the final passing run:

**Bug 1 — BOOTING↔RECOVERY direct-transition loop:**
TLC counterexample showed `DirectTransition` cycling between BOOTING and RECOVERY indefinitely, never reaching READY. Fix: added `SF_vars(DirectTransition(READY))` — strong fairness ensures READY is eventually chosen even when nondeterministic alternatives keep interrupting.

**Bug 2 — Crash at full WAL deadlock:**
TLC counterexample showed crash occurring when WAL has 8 entries (MaxWALSeq). With `~crashed` precondition on WALCompaction, compaction was disabled during recovery; RecoveryComplete was also disabled (WAL full). System permanently stuttered. Fix: removed `~crashed` precondition from WALCompaction. Compaction is a storage-layer operation — safe during crash recovery and essential for preventing deadlock.

### TLC Run Summary

| Metric | Value |
|---|---|
| States generated | 389,522 total |
| Distinct states | 168,520 |
| Search depth | 19 |
| Time | 52 minutes 43 seconds |
| Workers | 4 |
| Fingerprint collision probability | 9.6E-10 (negligible) |

### Liveness Properties — ALL PASSED

| Property | Status | Description |
|---|---|---|
| EventualProgress | PASSED | Crash eventually leads back to READY |
| EventualTermination | PASSED | CLOSING is stable (stays forever) |
| ProposalEventuallyResolved | PASSED | STAGED proposal eventually reaches COMMITTED, REJECTED, or NONE |

### Safety Invariants — ALL PASSED (re-verified with WALCompaction)

All 8 safety invariants from Phase 1 continue to hold across the expanded 168,520-state space that includes WALCompaction transitions.

---

## Conclusion

**Phase 1 (Safety):** The RAG Runtime Kernel state machine is **safety-correct**: all 8 invariants hold across 136,193 explored states (84,261 distinct) with zero violations.

**Phase 2 (Liveness):** WALCompaction action models real-world WAL truncation. Two genuine liveness bugs were caught and fixed: (1) BOOTING↔RECOVERY direct-transition loop (fixed with SF fairness), (2) crash-at-full-WAL deadlock (fixed by allowing compaction during recovery). After fixes, **all 8 safety invariants and all 3 liveness properties pass** across 168,520 distinct states with zero violations. The kernel is **formally verified for both safety and liveness**.

## S173 (2026-07-25) runtime-v0.4.46
Gate for AUTO-SID-DERIVE + doctor --recover. pytest: 2032 passed. TLC RAGKernel.cfg: full reachable state space explored (505,560 distinct states, 0 left on queue), no invariant/property violation reported. Changes do not touch state_machine.py (modeled surface unchanged). NOTE: new guards (secrets/intent-fidelity/boot-guard/close-seal) are NOT yet modeled — tracked as FV-GATE-RETROVERIFY (F2) for S174.

## S174 (2026-07-26) FV-GATE-RETROVERIFY (F1/F2) — hardening characterization proofs
Closes the S173 gap: the S144–S172 hardenings previously landed WITHOUT TLA/TLC are
now each backed by a focused characterization-proof module (same pattern as
ErrlogIdGuard): a GUARD predicate proven sound + complete against ground truth over
ALL inputs up to a bound, plus a REFUTED naive alternative (counterexample-backed).
All six proofs PASS and all six naive refutations FAIL as designed:

| Hardening (origin) | Module | Proof (INVARIANT AllProps) | Naive refutation (counterexample) |
|---|---|---|---|
| SECRETS-INGEST-GUARD (P1/G2, S144) | SecretsIngestGuard.tla | PASSED — 121 states | `NaiveSound` FAILS: `<<Plain, SecretVal>>` (secret in non-first field) |
| BOOT-GUARD-FIRST-ACTION (KA-20, S172) | BootGuardFirstAction.tla | PASSED — 63 states | `NaiveSound` FAILS: `<<Work, Boot>>` (work before session-start) |
| CLOSE-SEAL-ENFORCE (KA-21, S172) | CloseSealEnforce.tla | PASSED — 6 states | `NaivePhaseSound` FAILS: `[phase↦COMPLETE, ready↦FALSE]` (mid-seal crash) |
| KA-INTENT-FIDELITY (inc1/inc2, S147) | IntentFidelityGate.tla | PASSED — 4 states | `NaiveTextSound` FAILS: `[idBound↦FALSE, restatementExact↦TRUE]` |
| BOOTMAP-BOOTROOT-FIX (E-074, v0.4.44) | BootmapRootPin.tla | PASSED — 6 states | `NaivePinned` FAILS: `[cwd↦ProjectRoot, gc↦OtherDir]` (cwd/gc-keyed boot_root) |
| SCHEMA-MIGRATE (migrate verb) | SchemaMigrate.tla | PASSED — 4 states | `NaiveReach` FAILS: `ver=0` (single-step migrator not total) |

Each MASTER theorem is `GuardOK <=> GroundTruth` (P_Equiv), exhaustively checked by TLC.
SecretsIngestGuard additionally proves `P_BoundaryAgree` (proactive ingest == reactive
audit boundary, the KA-SECRETS-BOUNDARY single-source-of-truth claim) and `P_Redacted`
(the guard's diagnostic never carries the secret value). Core RAGKernel.tla state
machine is unchanged; its S173 full-state-space-clean result (505,560 distinct states)
remains authoritative.

## S175 (2026-07-26) runtime-v0.4.47 / spec v3.2.8
Gate for SPEC-PROMOTION-DRIFT (KA-20/KA-21 promoted into INIT spec v3.2.8) + RULE19-PIN-REFRESH
(`drift_audit.check_governance_pin_provenance`). pytest: **2,050 passed** (+18). Health 21/21.
`guardgen --check`: `generated_guards.py` matches `RAGKernel.tla` (sha `268149294421`, unchanged).

- **RAGKernel.cfg** — re-verified from scratch, full reachable state space: `Model checking
  completed. No error has been found.` 389,522 states generated, **168,520 distinct**, 0 left on
  queue; liveness checked over the complete **505,560**-state graph; search depth 19. Matches the
  S173 baseline exactly (the modeled surface is untouched — this release changes `drift_audit`,
  `__init__` version constants, the INIT spec, docs and tests only).
- **All 7 characterization proofs re-run and PASSED**: SecretsIngestGuard (121 states),
  BootGuardFirstAction (63), CloseSealEnforce (6), IntentFidelityGate (4), BootmapRootPin (6),
  SchemaMigrate (4), ErrlogIdGuard (1,555).
- **All 7 naive refutations FAIL as designed** (1 invariant violation each, counterexample-backed).

### S175 addendum — GovernancePinProvenance (closes FV-PIN-PROVENANCE-PROOF)

The v0.4.47 gate above ran the SEVEN pre-existing proofs, but the guard this release
SHIPPED — `drift_audit.check_governance_pin_provenance` — went out with unit tests and no
formal module, re-opening in miniature the exact gap FV-GATE-RETROVERIFY closed (a hardening
landing without a characterization proof). Closed here, same session:

| Hardening (origin) | Module | Proof (INVARIANT AllProps) | Naive refutations (counterexamples) |
|---|---|---|---|
| RULE19-PIN-REFRESH (F3, S175) | GovernancePinProvenance.tla | **PASSED — 512 states generated, 256 distinct, 0 left on queue** | `NaiveClaimSound` FAILS: `[declared↦{}, claims↦TRUE]` (unverifiable claim escalated to a release blocker) · `NaiveAllTokensSound` FAILS: `[declared↦{}, history↦{OLD}]` (history token drives a false ERROR) |

MASTER theorem `P_Equiv`: `HasError(r) <=> Misstates(r)` — the ERROR verdict is exactly
"an ANCHORED pin disagrees with `rag_kernel.__version__`", over all 256 rule records
(`declared`/`history` ∈ SUBSET {LIVE, OLD, STRAY}, `prov`/`claims` ∈ BOOLEAN). Six supporting
invariants are checked with it: `P_Sound`, `P_Complete`, `P_HistoryIrrelevant` (the verdict is a
function of `declared` alone), `P_RefreshedIsClean`, `P_UnverifiableWarnsOnly` (severity
discipline — an unresolvable pin WARNS, never ERRORs) and `P_SelfSkip` (a deployment declaring no
pin is out of scope).

The refutations pin the two design decisions that a "more thorough" guard would get wrong:
anchoring on ALL version tokens would cry wolf on the rule's own audit trail, and escalating an
unverifiable claim to ERROR would block releases on wording. A guard that cries wolf gets muted,
and a muted guard is how the pin drifted for twenty-two releases in the first place.

Full-suite re-run at this addendum: **8/8 proofs PASS, 8/8 naive refutations FAIL as designed.**

RUN NOTE (E-080, environment not model): the first full-space run was killed mid-liveness with no
error text and no completion line. TLC's scratch state-graph was being written to the repo under
`/mnt/c` (a Windows drive mounted into WSL), which is slow enough to stall the liveness phase. Re-run
with `-metadir` pointed at the Linux filesystem and an explicit `-Xmx4g`, the identical model
completed in **42 s**. Standing guidance for this host: always run `RAGKernel.cfg` as
`java -Xmx4g -jar ~/tla2tools.jar -metadir /tmp/tlcmeta -workers 2 -config RAGKernel.cfg RAGKernel.tla`
with output redirected to a file, and treat an ABSENT "Model checking completed" line as a FAILED
gate — never as a pass by omission.
