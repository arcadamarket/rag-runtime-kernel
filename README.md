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

## Core Features

**Structured Memory (HOT/COLD)** — Active working state stays lean (~15KB). Archival data loads on-demand with automatic partitioning when data grows. No context bloat.

**Deterministic State Machine** — Every session follows: `BOOTING → READY → WORKING → CHECKPOINTING → CLOSING` with explicit failure paths to `RECOVERY`.

**Proposal → Validation → Commit** — The LLM cannot act directly. It proposes actions as JSON. The system validates against policy, then commits or rejects. No uncontrolled mutations.

**Atomic Persistence** — All writes are atomic and verified. Write-ahead log (WAL) enables crash recovery. Nothing is silently lost.

**COLD Partitioning & Scaling** — When archival data exceeds context limits, automatic partitioning splits COLD into domain-specific chunks (sessions, inventory, conflicts, evidence) with sub-partitioning support and integrity-preserving chopping protocol.

**Tool Fallback Chain** — Ordered fallback for file operations. If primary tool fails, system automatically switches to next available tool. No single-tool dependency.

**Cross-Platform Interoperability** — Works across Claude Projects, ChatGPT, and any LLM with or without filesystem tools. The spec is the invariant; the tool layer is the variable.

**Multi-Account Safety** — Session identity tagging, write collision detection, and anti-corruption guards for RAGs shared across multiple LLM accounts.

**Full Audit Trail** — Every state transition, decision, and conflict is logged. Deterministic replay capability.

**Token Efficiency** — HOT-only boot, on-demand COLD loading, mandatory load triggers for analytical work, no full-file ingestion. Measured 70–95% token reduction vs. naive approaches.

## Two Execution Modes

| Mode | How It Works |
|---|---|
| **Autonomous** | The LLM self-enforces all rules from the specification alone. No external software needed. Works in Claude Projects, ChatGPT, or any LLM platform. |
| **Enforced** | A Python runtime kernel intercepts all mutations. The wrapper validates, commits, or rejects. The LLM emits proposals only. |

Autonomous mode is the default and is **not** degraded — all rules apply with full force.

## Prerequisites

**Minimum (any platform):**
- An LLM that supports project instructions or system prompts (Claude Projects, ChatGPT custom instructions, etc.)
- A folder on your local machine for project files

**Recommended (for full autonomy):**
- [Filesystem MCP](https://github.com/modelcontextprotocol/servers) — enables direct file read/write from the LLM
- Node.js (for MCP server via npx)

**Optional:**
- Shell/PowerShell MCP — for git operations, file copy/move
- Python 3.10+ — for ENFORCED mode runtime kernel (roadmap)
- Claude Code — for zero-token file operations

## Quick Start

1. Create a project folder with a `RAG/` subfolder
2. Paste the contents of [`INIT_UNIVERSAL_RUNTIME_KERNEL_v3.1.3.md`](INIT_UNIVERSAL_RUNTIME_KERNEL_v3.1.3.md) into your LLM project instructions
3. Start a conversation — the system bootstraps itself, asks for root paths and POV configuration, then creates the RAG
4. Copy the generated pointer block into your Project Instructions when prompted
5. All subsequent sessions auto-load the RAG and enforce all rules

**Without Filesystem MCP:** The system works in user-assisted mode — you copy-paste RAG content into the chat. All rules still apply; only the I/O method differs.

## Repository Structure

```
rag-runtime-kernel/
├── INIT_UNIVERSAL_RUNTIME_KERNEL_v3.1.3.md   # The specification (this is the product)
├── docs/
│   ├── architecture.md                        # System architecture overview
│   └── design_principles.md                   # Core design philosophy
├── LICENSE
├── README.md
└── .gitignore
```

> **Note:** The `core/` directory with Python runtime kernel, graph orchestrator, and CLI tools is on the roadmap. The specification works standalone today — the runtime wrapper is an optional enforcement layer.

## How It Works

### Session Lifecycle

Every session follows a deterministic lifecycle:

1. **BOOTING** — Load HOT (active state), verify consistency, check WAL for unsaved data, probe available tools
2. **READY** — Accept tasks, inspect state
3. **WORKING / INGESTING** — Execute tasks, ingest new source files, extract knowledge
4. **CHECKPOINTING** — Save state atomically with backup rotation
5. **CLOSING** — Audit all session findings, ensure nothing is lost, final save

### Memory Architecture

The system uses a two-tier memory model:

- **HOT** (`RAG_MASTER.json`, ~15KB) — Loaded every boot. Contains current status, active tasks, recent sessions, project context. Kept lean for token efficiency.
- **COLD** (`RAG_COLD*.json`, on-demand) — Archival vault. Loaded only when needed. Contains full document inventory, extracted findings, conflict ledger, session history, evidence. Auto-partitions when data grows.

### Safety Guarantees

- **Atomic writes** with `.bak` rotation and WAL protection
- **Crash recovery** via WAL replay at next boot
- **Conflict ledger** — disagreements between sources are preserved, never silently merged
- **Filesystem boundary enforcement** — the model cannot access files outside designated roots
- **Concurrency guard** — detects and prevents silent last-write-wins corruption

## Specification Overview (v3.1.3)

The full specification contains 40 sections (§0–§38 + §3a):

| Sections | Coverage |
|---|---|
| §0–§1 | Operating principle, core architecture, three-root path system |
| §2–§3a | State machine, tool verification with filesystem mapping, tool fallback chain |
| §4–§6 | Proposal/validation/commit contract, tool contract, filesystem boundary with upload and search rules |
| §7–§8 | Files Tab rule, HOT/COLD memory model with partitioning, chopping protocol, mandatory COLD triggers |
| §9–§10 | Source hierarchy, ingestion pipeline with conflict cross-validation |
| §11–§14 | Conflict ledger, event log/WAL, atomic write protocol, drift detection |
| §15–§17 | Token economy, multi-POV validation, session-close audit with self-initiated close |
| §18 | Audit protocol (8-dimension integrity checks, bounded repair loops) |
| §19–§21 | Boot sequence, recovery protocol, halt conditions with fallback-aware loop detection |
| §22–§27 | Operational discipline, concurrency guard with multi-account protocol |
| §28–§37 | Runtime directive, self-export, wrapper contract, bootstrap, schemas, pointer block, environment prerequisites with cross-platform interoperability |
| §38 | Version history |

## Known Limitations

1. **Context window bound** — In autonomous mode, the spec itself consumes ~16K tokens of context. Very large projects may hit context limits during complex operations.
2. **No cross-filesystem bridge** — Currently relies on platform-specific tools (MCP, plugins) for file access. Platforms without any filesystem tool require user-assisted I/O (copy-paste).
3. **No runtime kernel yet** — ENFORCED mode (Python wrapper) is on the roadmap. Currently all enforcement is self-enforced by the LLM.
4. **Single-writer assumption** — While multi-account safeguards exist, true concurrent writes from multiple sessions are detected-and-halted, not merged automatically.
5. **Tool availability varies** — Different LLM platforms provide different tool sets. The fallback chain mitigates this but cannot create tools that don't exist.

## Future Development

### v3.2 — OS-Level Runtime (planned)
- Background process providing filesystem access to any LLM regardless of platform
- COLD partition management in system RAM with on-demand serving
- Eliminates need for MCP-specific tooling and user-assisted I/O
- Cross-platform bridge: one runtime, any LLM

### v4.0 — Graph Orchestrator (roadmap)
- LangGraph-class DAG execution engine
- Multi-step workflow orchestration with dependency tracking
- Parallel task execution where safe

### Runtime Kernel (roadmap)
- Python wrapper for ENFORCED mode
- Hash-based integrity verification
- CLI tools for kernel operations

## Version History

See [§38 in the specification](INIT_UNIVERSAL_RUNTIME_KERNEL_v3.1.3.md#38--version-history) for full version history from v1.4 through v3.1.3.

Current version: **v3.1.3** (2026-05-04)

## Support the Developer

If you find this project useful, you're welcome to support its development.

**Developer:** Artem Pakhol  
**LinkedIn:** [linkedin.com/in/pakhol](https://www.linkedin.com/in/pakhol)

## License

MIT License — see [LICENSE](LICENSE).
