# RAG Runtime Kernel

> **LLM proposes. System decides. State persists.**

A filesystem-backed, event-sourced control system for LLM workflows that enforces structured memory, deterministic state transitions, and validated persistence — without requiring any external dependencies.

---

## What Problem This Solves

Every LLM session starts from zero. Close the tab, lose the state. The industry "solutions" are duct tape: chat history dumps, vector DBs that hallucinate retrieval, framework lock-in that breaks across platforms.

**RAG Runtime Kernel wraps around your project** — it doesn't replace your workflow, it adds a structured memory and orchestration layer on top. One markdown file. Zero dependencies. Drop it into any LLM session and you get: deterministic state persistence, crash recovery, conflict tracking, and cross-session memory that actually works — across Claude, GPT, and any LLM.

**Key benefits:**
- **Persistence** — your project state survives across sessions, tabs, and platforms
- **Reduced context loss** — HOT/COLD memory tiers keep only what's needed in context
- **Improved autonomy** — the LLM self-enforces all rules without external tooling
- **Audit trail** — every decision, conflict, and state change is logged and traceable

---

## Quick Start

> **Important:** The Init Prompt is a full specification (~16K tokens). It goes into a **project session**, not the Instructions/System Prompt field (which has size limitations on most platforms).

### Claude Desktop / Claude Projects

1. Create a new Project (or open an existing one)
2. Start a new session within that project
3. Drop [`INIT_UNIVERSAL_RUNTIME_KERNEL_v3.1.3.md`](INIT_UNIVERSAL_RUNTIME_KERNEL_v3.1.3.md) into the session as a file
4. Send your first message — the system bootstraps itself
5. Follow on-screen steps: provide root paths, optional project description, optional POV config
6. Copy the generated **pointer block** into your Project Instructions when prompted
7. All subsequent sessions auto-load the RAG and enforce all rules

### ChatGPT / GPT Web

1. Open a new conversation (or use Custom GPT if available)
2. Upload or paste the contents of [`INIT_UNIVERSAL_RUNTIME_KERNEL_v3.1.3.md`](INIT_UNIVERSAL_RUNTIME_KERNEL_v3.1.3.md)
3. Send your first message — the system bootstraps in autonomous mode
4. Follow on-screen steps (same as above)
5. At session end, download the generated RAG files and save to your project folder
6. Upload RAG files at the start of each new session to restore state

### Works for both new projects and existing ones being refined.

---

## Using with Cowork

[Cowork](https://docs.claude.com) is Anthropic's desktop tool for non-developers to automate file and task management.

**New project:** Create a project folder with a `RAG/` subfolder, open Cowork, start a session, and drop the Init Prompt file in. The system bootstraps, scans your project folder, and builds the RAG.

**Existing project:** Point the system to your existing project folder during bootstrap. The boot scan inventories all existing files, classifies them by tier, and extracts knowledge into COLD storage. Your existing work becomes queryable, trackable, and persistent.

**Benefits:** Cowork's file access lets the kernel read/write RAG files directly — no manual copy-paste. Task automation pairs naturally with the kernel's checkpoint and audit system.

---

## Using with Claude Code

[Claude Code](https://docs.claude.com) is Anthropic's CLI tool for agentic coding tasks.

**New project:** Initialize your project directory, reference the Init Prompt in a Claude Code session, and the system creates RAG files in your `RAG/` directory via direct filesystem access.

**Existing project:** Add a `RAG/` directory to your existing codebase, bootstrap the kernel — it scans your project, builds inventory, and starts tracking state.

**How it enhances Claude Code:** Context persistence across stateless sessions. Deterministic state machine structures long-running development. Zero-token file ops via direct filesystem access. Conflict ledger preserves both sides when code changes contradict prior decisions.

---

## How It Compares

Full benchmark: [`docs/benchmark_comparison.md`](docs/benchmark_comparison.md)

| Capability | RAG Runtime Kernel | Claude Code | lean-ctx | LLM Wiki |
|---|---|---|---|---|
| **Cross-session memory** | Full: HOT/COLD + WAL + crash recovery | Partial: CLAUDE.md, no crash recovery | None | Pattern only |
| **Deterministic state machine** | BOOTING→READY→WORKING→CHECKPOINTING→CLOSING + RECOVERY | None | None | None |
| **Token efficiency** | 60–90% reduction (HOT-only boot ~4K tokens) | Unbounded growth without curation | 60–99% raw compression (best-in-class I/O) | Depends on wiki quality |
| **Cross-platform** | Claude + GPT + any LLM, same spec | Claude Code only | Editor-focused | Platform-agnostic pattern |
| **Dependencies** | Zero. Single markdown file | Node.js + CLI | Rust binary | Varies |
| **Crash recovery** | WAL replay + .bak rotation + RECOVERY state | File-history checkpoints | N/A | None |
| **Conflict tracking** | Explicit ledger — both sources preserved | None | N/A | None |

### Key Differentiators

1. **Only system with a formal state machine on LLM workflows** — deterministic transition guards, not ad-hoc
2. **Only system that works identically across Claude and GPT** — the spec is the invariant
3. **Only system with atomic write protocol + WAL + backup rotation** — enterprise-grade persistence
4. **Zero install, zero dependencies** — the specification IS the product
5. **Conflict ledger is unique** — no other system tracks disagreements between sources

---

## What This Is

A **specification** — a complete protocol that turns any LLM into a controlled, auditable agent with persistent project memory. 3-layer architecture:

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

**Structured Memory (HOT/COLD)** — Active state stays lean (~15KB). Archival data loads on-demand with automatic partitioning.

**Deterministic State Machine** — `BOOTING → READY → WORKING → CHECKPOINTING → CLOSING` with `RECOVERY` path.

**Proposal → Validation → Commit** — LLM proposes JSON actions. System validates against policy, then commits or rejects.

**Atomic Persistence** — All writes atomic and verified. WAL enables crash recovery.

**COLD Partitioning** — Auto-splits into sessions/inventory/conflicts/evidence with sub-partitioning and integrity-preserving chopping.

**Tool Fallback Chain** — Ordered fallback for file operations across platform tools.

**Cross-Platform** — Claude Projects, ChatGPT, Cowork, Claude Code, any LLM.

**Multi-Account Safety** — Session identity tagging, write collision detection, anti-corruption guards.

**Full Audit Trail** — Every state transition, decision, and conflict logged.

**Token Efficiency** — 70–95% reduction vs. naive approaches.

## Two Execution Modes

| Mode | How It Works |
|---|---|
| **Autonomous** | LLM self-enforces all rules. No external software needed. Default mode. |
| **Enforced** | Python runtime kernel intercepts all mutations. (Roadmap) |

## Prerequisites

**Minimum:** An LLM that supports file uploads or long-form input + a project folder.

**Recommended:** [Filesystem MCP](https://github.com/modelcontextprotocol/servers) for direct file read/write.

**Optional:** Shell/PowerShell MCP, Python 3.10+ (ENFORCED mode roadmap), Claude Code or Cowork.

## Repository Structure

```
rag-runtime-kernel/
├── INIT_UNIVERSAL_RUNTIME_KERNEL_v3.1.3.md   # The specification (the product)
├── CONTRIBUTING.md                            # How to report issues
├── COMPREHENSIVE_TODO_PLAN_v3_1_3.md          # Full development plan
├── docs/
│   ├── architecture.md                        # System architecture
│   ├── benchmark_comparison.md                # Head-to-head vs alternatives
│   ├── design_principles.md                   # Core design philosophy
│   ├── test_analysis_gpt_web.md               # GPT Web test findings
│   └── ROADMAP.md                             # Development roadmap
├── tests/
│   ├── UNIT_TEST_CLAUDE_DESKTOP.md            # Claude Desktop test suite (32 tests)
│   └── UNIT_TEST_GPT_WEB.md                   # GPT Web test suite (33 tests)
├── .github/ISSUE_TEMPLATE/                    # Bug report + feature request templates
├── LICENSE                                    # AGPL-3.0
└── README.md
```

## Session Lifecycle

1. **BOOTING** — Load HOT, verify consistency, check WAL, probe tools
2. **READY** — Accept tasks
3. **WORKING / INGESTING** — Execute tasks, ingest files, extract knowledge
4. **CHECKPOINTING** — Save atomically with backup rotation
5. **CLOSING** — Audit findings, final save

## Disclaimer

- **Autonomous mode is self-enforced** — the LLM follows the spec by instruction, not by hard runtime constraints
- **Persistence depends on platform** — full atomic writes with MCP; manual file management on GPT Web
- **Context window ceiling** — spec consumes ~16K tokens; large projects may hit limits
- **Not a database** — structured file-based memory, not a production database replacement

See [`docs/test_analysis_gpt_web.md`](docs/test_analysis_gpt_web.md) for detailed platform-specific findings.

## Known Limitations

1. **Context window bound** — spec ~16K tokens; large projects may hit limits
2. **No cross-filesystem bridge yet** — relies on platform tools; user-assisted I/O without them
3. **No runtime kernel yet** — ENFORCED mode is roadmap
4. **Single-writer** — concurrent writes detected-and-halted, not auto-merged
5. **GPT Web** — no atomic writes, no real token counter, manual persistence

## Future Development

See [`docs/ROADMAP.md`](docs/ROADMAP.md) for complete roadmap.

| Release | Focus |
|---|---|
| **v3.1.4** | Defect fixes: optional POV, session-zero boot scan, archive extraction |
| **v3.2** | OS-Level Runtime: filesystem bridge, real WAL, COLD partition manager |
| **v3.3** | UX: graduated POV, conflict auto-categorization, delta checkpoints |
| **v4.0** | Graph Orchestrator: DAG execution, parallel tasks, dependency tracking |

## Reporting Issues

Found a bug? Please [open an issue](../../issues/new/choose) using the provided templates. See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Support the Developer

**Developer:** Artem Pakhol
**LinkedIn:** [linkedin.com/in/pakhol](https://www.linkedin.com/in/pakhol)

## License

This project is licensed under the [GNU Affero General Public License v3.0](https://www.gnu.org/licenses/agpl-3.0.html) — see [LICENSE](LICENSE).

**What this means:** You may use, modify, and distribute this software, but any modified version you deploy (including as a network service) must also be released under AGPL-3.0 with attribution to the original project.
