# Architecture

## System Layers

The RAG Runtime Kernel operates as a layered control system where each layer has a single, well-defined responsibility:

```
┌─────────────────────────────┐
│  LLM (Claude / GPT / etc.)  │  Reasoning engine — proposes actions
├─────────────────────────────┤
│  Policy Layer               │  This specification — defines all rules
│  (INIT prompt)              │  
├─────────────────────────────┤
│  Runtime Kernel             │  State enforcement + persistence
│  (self-enforced or wrapper) │  Validates, commits, or rejects proposals
├─────────────────────────────┤
│  Filesystem                 │  Source of truth — all state persisted here
└─────────────────────────────┘
```

The LLM never writes directly. Every mutation follows: **Propose → Validate → Commit**.

## State Machine

Every session is a deterministic state machine:

```
BOOTING → READY → { INGESTING | WORKING } → CHECKPOINTING → CLOSING
                                                    ↓
                                                RECOVERY → READY
```

Transitions are explicit and logged. Invalid transitions are rejected. Any write failure triggers RECOVERY.

## Memory Architecture (HOT / COLD)

**HOT (RAG_MASTER.json)** — Loaded every boot. Kept under ~15KB. Contains active state: project context, current status, open tasks, recent sessions, configuration.

**COLD (RAG_COLD.json)** — Loaded on-demand only. Contains archival data: full document inventory, extracted findings, conflict ledger, complete session history.

This split ensures fast boot (~4000 tokens for HOT) while maintaining full project history in COLD.

## Persistence Stack

All persistence is filesystem-backed with crash safety:

1. **Write-Ahead Log (WAL)** — Append-only JSONL event log. Every critical operation is logged before execution. Enables crash recovery and replay.
2. **Atomic Writes** — HOT writes use temp-file → verify → rotate-backup → rename sequence. Failures trigger retry, then RECOVERY.
3. **Backup Rotation** — HOT is backed up to `.bak` at every session end and at critical checkpoints. Full verbatim content, never a stub.
4. **Consistency Detection** — Monotonic sequence counters and timestamps detect drift between sessions. Unexpected changes trigger RECOVERY.

## Proposal → Validation → Commit

The core control mechanism:

```json
{
  "proposal_id": "S1-3",
  "action": "update_status",
  "state_before": "WORKING",
  "state_after": "CHECKPOINTING",
  "payload": { "current_status": { "phase": "review" } },
  "risk": "low",
  "reasoning": "Task complete, saving state"
}
```

Validation checks: schema validity, transition legality, policy compliance, consistency, filesystem boundary. Only validated proposals commit.

## Three-Root Path System

All paths resolve from exactly three anchors:

- `root_project` — source material
- `root_deliverables` — model outputs
- `root_rag` — RAG system files

No absolute paths appear anywhere except in these three root values. Everything else is `join(root_*, relative_offset)`. To relocate: update one root.