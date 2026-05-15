# Formal Verification — RAG Runtime Kernel

This directory contains a **TLA+ specification** for the RAG Runtime Kernel
state machine, along with a TLC model-checker configuration.

Formal verification complements the Python test suite by exhaustively
exploring *all reachable states*, including adversarial crash/recovery
interleavings that are difficult to cover with unit tests.

---

## Files

| File | Purpose |
|---|---|
| `RAGKernel.tla` | TLA+ specification (state machine, WAL, crash/recovery, invariants) |
| `RAGKernel.cfg` | TLC model configuration (constants, invariants, properties) |
| `README.md` | This file |

---

## What Is Verified

### State Machine Transitions

The spec encodes the **exact** transition table from `state_machine.py`:

| From | Allowed targets |
|---|---|
| `BOOTING` | `READY`, `RECOVERY` |
| `READY` | `INGESTING`, `WORKING`, `CHECKPOINTING`, `CLOSING` |
| `INGESTING` | `READY`, `CHECKPOINTING`, `RECOVERY` |
| `WORKING` | `READY`, `CHECKPOINTING`, `RECOVERY` |
| `CHECKPOINTING` | `READY`, `CLOSING`, `RECOVERY` |
| `CLOSING` | *(terminal — no exits)* |
| `RECOVERY` | `READY`, `BOOTING` |

TLC checks that no execution ever reaches a state via an illegal edge.

### Safety Invariants

| Invariant | What it checks |
|---|---|
| `TypeInvariant` | Every variable holds a value of its declared type |
| `TransitionSafety` | Current state is always reachable from `BOOTING` via legal edges |
| `SingleWriter` | At most one proposal staged at a time |
| `WALConsistency` | WAL is append-only, monotonically sequenced, never lags state |
| `TerminalSafety` | Once in `CLOSING`, state never changes |
| `NoDeadlock` | Every non-terminal state has at least one enabled action |
| `CrashRecoveryConsistency` | `crashed = TRUE` implies `state = RECOVERY` |
| `WALPrecedesStateChange` | WAL entry for new state is written before `stateSeq` advances |

### Liveness Properties (Temporal)

| Property | What it checks |
|---|---|
| `EventualProgress` | After any crash, the system eventually reaches `READY` again |
| `EventualTermination` | Once `CLOSING` is entered, it is maintained forever |
| `ProposalEventuallyResolved` | A staged proposal is never left pending forever |

All liveness properties rely on **weak fairness** over recovery and commit
actions (declared in the `Fairness` conjunction within `Spec`).

---

## How to Run TLC

### Option 1 — TLA+ Toolbox (GUI)

1. Download the [TLA+ Toolbox](https://github.com/tlaplus/tlaplus/releases)
   (choose the installer for your OS).
2. Open Toolbox → *File* → *Open Spec* → select `RAGKernel.tla`.
3. In the *TLC Model Checker* panel, create a new model.
4. Set **Init formula** to `Init`, **Next formula** to `Next`,
   **Temporal formula** to `Spec`.
5. Add all invariants and properties listed above.
6. Click **Run TLC**.

### Option 2 — Command Line (tla2tools.jar)

```bash
# Download tla2tools.jar (one-time)
curl -L -o tla2tools.jar \
  https://github.com/tlaplus/tlaplus/releases/latest/download/tla2tools.jar

# Run TLC with the provided config
java -jar tla2tools.jar \
     -config formal/RAGKernel.cfg \
     formal/RAGKernel.tla

# For a specific number of worker threads (use nproc/2 recommended)
java -jar tla2tools.jar \
     -workers 4 \
     -config formal/RAGKernel.cfg \
     formal/RAGKernel.tla
```

### Option 3 — VS Code Extension

Install the
[TLA+ extension for VS Code](https://marketplace.visualstudio.com/items?itemName=alygin.vscode-tlaplus),
open `RAGKernel.tla`, and use the *TLA+: Check model* command.

---

## Installing TLA+ Tools

### macOS (Homebrew)

```bash
brew install tlaplus
```

### Linux

```bash
# Download tla2tools.jar directly — Java 11+ required
curl -L -o ~/bin/tla2tools.jar \
  https://github.com/tlaplus/tlaplus/releases/latest/download/tla2tools.jar

# Convenience wrapper
echo '#!/bin/sh\nexec java -jar ~/bin/tla2tools.jar "$@"' > ~/bin/tlc
chmod +x ~/bin/tlc
```

### Windows

Download `TLAToolbox-*.exe` from the
[releases page](https://github.com/tlaplus/tlaplus/releases) and run
the installer.  The Toolbox bundles its own JRE.

---

## Tuning the Model

The constant `MaxWALSeq` in `RAGKernel.cfg` bounds how many WAL entries
TLC will explore.  Larger values increase coverage at the cost of runtime:

| `MaxWALSeq` | Approx. states | Approx. time |
|---|---|---|
| 6 | ~50 k | seconds |
| 8 | ~500 k | minutes |
| 12 | ~10 M | tens of minutes |
| 16 | ~200 M | hours |

Start with the default (8) to confirm no violations, then raise for
deeper verification runs in CI.

---

## Correspondence to Python Source

| TLA+ construct | Python source |
|---|---|
| `AllowedTargets(s)` | `TRANSITIONS` dict in `state_machine.py` |
| `StageProposal` / `CommitProposal` | `validate_proposal()` in `schemas.py` + `StateMachine.transition()` |
| `RejectProposal` | `StateMachine.transition()` returning `False` |
| `WAL` variable (sequence) | `WAL` class in `persistence.py` |
| `Crash` action | `StateMachine.force_state(RECOVERY, "crash")` |
| `RecoveryComplete` | `WAL.replay()` + `StateMachine.transition(READY)` |
| `SingleWriter` invariant | `ProjectLock` + single-staged-proposal constraint |
| `WALPrecedesStateChange` | `WAL.append()` called before `StateMachine.transition()` |

---

## Extending the Spec

To model additional subsystems:

- **Split-brain detection** (`concurrency.py`): add a `sessionId` variable
  and a `DetectSplitBrain` action that nondeterministically sets a
  `splitBrainDetected` flag; verify that this always triggers `RECOVERY`.
- **Checkpoint rotation**: add a `checkpointSeq` variable and verify it
  never exceeds `WALSeq`.
- **Concurrent sessions**: replicate the `state`/`wal` variables as a
  function over a session set; verify mutual exclusion via the lock model.

---

## References

- L. Lamport, *Specifying Systems* (Addison-Wesley, 2002)
- [TLA+ Home Page](https://lamport.azurewebsites.net/tla/tla.html)
- [Learn TLA+](https://learntla.com) — free online textbook
- `docs/v3.2_ARCHITECTURE_DESIGN.md` §6 (state machine), §7 (WAL)
- `src/rag_kernel/state_machine.py`
- `src/rag_kernel/persistence.py`
