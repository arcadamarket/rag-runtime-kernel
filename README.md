# RAG Runtime Kernel

> **LLM proposes. System decides. State persists.**

A filesystem-backed, event-sourced control system for LLM workflows that enforces structured memory, deterministic state transitions, and validated persistence — without requiring any external dependencies.

## What This Is

This is a **specification** — a complete protocol that turns any LLM (Claude, GPT, etc.) into a controlled, auditable agent with persistent project memory. No Python runtime required. No framework dependencies. Just paste the init prompt into your LLM's project instructions and it self-enforces.

The specification defines a 3-layer architecture:

```
LLM (reasoning engine)
  ↓ JSON proposals
Policy Layer (this specification)
  ↓ validated transitions
Runtime Kernel (state + persistence)
  ↓ atomic writes
Filesystem (source of truth)
```

## Core Capabilities

**Structured Memory (HOT/COLD)** — Active working state stays lean (~15KB). Archival data loads on-demand. No context bloat.

**Deterministic State Machine** — Every session follows: `BOOTING → READY → WORKING → CHECKPOINTING → CLOSING` with explicit failure paths to `RECOVERY`.

**Proposal → Validation → Commit** — The LLM cannot act directly. It proposes actions as JSON. The system validates against policy, then commits or rejects. No uncontrolled mutations.

**Atomic Persistence** — All writes are atomic and verified. Write-ahead log (WAL) enables crash recovery. Nothing is silently lost.

**Full Audit Trail** — Every state transition, every decision, every conflict is logged. Deterministic replay capability.

**Token Efficiency** — HOT-only boot, on-demand COLD loading, no full-file ingestion. Measured 70–95% token reduction vs. naive approaches.

## Two Execution Modes

| Mode | How It Works |
|---|---|
| **Autonomous** | The LLM self-enforces all rules from the specification alone. No external software needed. Works in Claude Projects, ChatGPT, or any LLM platform. |
| **Enforced** | A Python runtime kernel intercepts all mutations. The wrapper validates, commits, or rejects. The LLM emits proposals only. |

Autonomous mode is the default and is **not** degraded — all rules apply with full force.

## Quick Start

1. Create a project folder with a `RAG/` subfolder
2. Paste the contents of [`INIT_UNIVERSAL_RUNTIME_KERNEL_v3.1.1.md`](INIT_UNIVERSAL_RUNTIME_KERNEL_v3.1.1.md) into your LLM project instructions
3. Start a conversation — the system bootstraps itself

## Repository Structure

```
rag-runtime-kernel/
├── INIT_UNIVERSAL_RUNTIME_KERNEL_v3.1.1.md   # The specification (this is the product)
├── docs/
│   ├── architecture.md                        # System architecture overview
│   └── design_principles.md                   # Core design philosophy
├── LICENSE
├── README.md
└── .gitignore
```

> **Note:** The `core/` directory with Python runtime kernel, graph orchestrator, and CLI tools is on the roadmap. The specification works standalone today — the runtime wrapper is an optional enforcement layer, not a prerequisite.

## Design Philosophy

The fundamental insight: LLMs are powerful reasoning engines but terrible execution controllers. They forget context, hallucinate state, and cannot guarantee persistence. This system externalizes all state management to the filesystem and constrains the LLM to a proposal-only role.

Key principles:
- The LLM is not the system controller
- All state must be externalized and persisted
- No implicit memory — everything is explicit
- Deterministic transitions only
- Fail loudly, never silently
- Filesystem is the source of truth

## Specification Highlights

The full specification (v3.1.1) contains 38 sections covering:

- §0–§1: Operating principle and core architecture
- §2–§3: State machine and tool verification
- §4–§6: Proposal/validation/commit contract, tool contract, filesystem boundary
- §7–§10: Files Tab rule, HOT/COLD memory model, source hierarchy, ingestion pipeline
- §11–§14: Conflict ledger, event log/WAL, atomic write protocol, drift detection
- §15–§17: Token economy, multi-POV validation, session-close audit
- §18–§20: Boot sequence, recovery protocol, halt conditions
- §21–§28: Operational discipline rules (decisional integrity, response discipline, no guesswork, self-sufficiency, filesystem discipline, concurrency guard, runtime directive, self-export)
- §29–§37: Runtime wrapper contract, bootstrap, schemas, pointer block, completion standard, prerequisites, version history

## Current Status

- **Specification:** v3.1.1 — complete and operational
- **Runtime Kernel:** Roadmap — Python wrapper for enforced mode
- **Graph Orchestrator:** Roadmap — LangGraph-class DAG execution
- **CLI Tools:** Roadmap — Command-line interface for kernel operations

## Version History

See [§37 in the specification](INIT_UNIVERSAL_RUNTIME_KERNEL_v3.1.1.md#37--version-history) for full version history from v1.4 through v3.1.1.

## License

MIT License — see [LICENSE](LICENSE).