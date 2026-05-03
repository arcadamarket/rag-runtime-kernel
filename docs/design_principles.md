# Design Principles

## Core Axiom

**LLM proposes. System decides. State persists.**

This is not a slogan — it is the architectural invariant that every design decision derives from.

## Principles

### 1. The LLM Is Not the Controller

LLMs are powerful reasoning engines but unreliable execution controllers. They forget context across sessions, hallucinate state they never had, and cannot guarantee that a write actually persisted. This system strips the LLM of execution authority and constrains it to a proposal-only role. The system — whether self-enforced or wrapper-enforced — decides what commits.

### 2. All State Must Be Externalized

Nothing important lives only in chat. Every decision, finding, conflict, and action item must be encoded in HOT, COLD, the event log, or the deliverables index. The test: if the conversation disappears, can the next session reconstruct full project state from the filesystem alone?

### 3. No Implicit Memory

LLMs create an illusion of memory through context windows. This system replaces that illusion with explicit, structured, versioned memory. HOT is the active working set. COLD is the archive. The WAL is the audit trail. There is no hidden state.

### 4. Deterministic Transitions Only

The state machine defines exactly which transitions are legal. BOOTING can go to READY or RECOVERY. WORKING can go to CHECKPOINTING. There is no "it depends" — every transition is enumerable and auditable.

### 5. Fail Loudly, Never Silently

A write that can't be verified triggers RECOVERY, not a silent retry. A missing source file triggers HALT, not a guess. A conflict between sources is ledgered, not averaged. The system prioritizes correctness over convenience.

### 6. Filesystem Is the Source of Truth

Not the LLM's memory. Not the chat history. Not a cloud database. The filesystem. RAG files are JSON on disk. Deliverables are files on disk. The WAL is a file on disk. This makes the system portable, inspectable, and independent of any specific LLM platform.

### 7. Token Economy Is a First-Class Concern

Context windows are finite and expensive. HOT stays under ~15KB (~4000 tokens). COLD loads on-demand, minimum relevant slice only. Files are never re-read in the same session. Batch operations estimate cost before execution. The system is designed to minimize token waste at every layer.

### 8. Autonomous Mode Is Not Degraded Mode

The specification works without any external software. When the LLM self-enforces (autonomous mode), all rules apply with full force. The difference between autonomous and enforced mode is enforcement authority (self vs. wrapper), not enforcement strictness.