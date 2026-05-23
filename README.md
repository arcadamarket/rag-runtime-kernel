<p align="center">
  <img src="assets/logo.png" alt="RAG Runtime Kernel" width="280"/>
</p>

# RAG Runtime Kernel

> **LLM proposes. System decides. State persists.**

Enterprise-grade memory and state management for any LLM — crash recovery, conflict tracking, audit trails, and deterministic lifecycle control. Single file. Zero dependencies. Zero lock-in. Outperforms multi-tool stacks while fitting inside a chat window.

---

## What Problem This Solves

Every LLM session starts from zero. Close the tab, lose the state. The industry "solutions" are duct tape: chat history dumps, vector DBs that hallucinate retrieval, framework lock-in that breaks across platforms.

**RAG Runtime Kernel wraps around your project** — it doesn't replace your workflow, it adds a structured memory and orchestration layer on top. One markdown file. Zero dependencies. Drop it into any LLM session and you get: deterministic state persistence, crash recovery, conflict tracking, and cross-session memory that actually works — across Claude, GPT, and any LLM.

In [head-to-head benchmarks](#how-it-compares), this single-file specification matches or exceeds multi-tool stacks (Claude Code, lean-ctx, LLM Wiki) on state management, crash recovery, and cross-platform interoperability — while requiring zero installation.

**Key benefits:**
- **Persistence** — your project state survives across sessions, tabs, and platforms
- **Reduced context loss** — HOT/COLD memory tiers keep only what's needed in context
- **Improved autonomy** — the LLM self-enforces all rules without external tooling
- **Audit trail** — every decision, conflict, and state change is logged and traceable

---

## Quick Start

### Cowork (Fastest Path)

1. Create a project folder with a `RAG/` subfolder
2. Copy `rag_kernel/` into your project (see below)
3. Open Cowork, select the folder, start a session
4. Run: `python -m rag_kernel init --spec RAG/INIT_UNIVERSAL_RUNTIME_KERNEL_v3.1.8.md --output RAG/` — deterministic bootstrap, zero tokens, zero LLM
5. Optionally merge project-specific context: `python -m rag_kernel configure --rag RAG/RAG_MASTER.json --context your_context.json`

**Copy `rag_kernel/` into your project:**

PowerShell:
```powershell
git clone https://github.com/arcadamarket/rag-runtime-kernel.git temp-clone
Copy-Item -Recurse temp-clone\rag_kernel YOUR_PROJECT\rag_kernel
Remove-Item -Recurse -Force temp-clone
```

CMD:
```cmd
git clone https://github.com/arcadamarket/rag-runtime-kernel.git temp-clone
xcopy temp-clone\rag_kernel YOUR_PROJECT\rag_kernel\ /E /I
rmdir /s /q temp-clone
```

Bash:
```bash
git clone https://github.com/arcadamarket/rag-runtime-kernel.git temp-clone
cp -r temp-clone/rag_kernel YOUR_PROJECT/rag_kernel
rm -rf temp-clone
```

Then run as MCP server or HTTP server:
```bash
python -m rag_kernel mcp --project /path/to/your/RAG    # MCP mode (Claude Desktop)
python -m rag_kernel serve --project /path/to/your/RAG   # HTTP mode (GPT / any LLM)
```

Full platform-specific setup: [`docs/LAUNCH_MANUAL.md`](docs/LAUNCH_MANUAL.md)

### Claude Projects / ChatGPT

> **Note:** The Init Prompt is a full specification (~16K tokens). It goes into a **project session**, not the Instructions/System Prompt field (which has size limitations on most platforms).

### Claude Desktop / Claude Projects

1. Create a new Project (or open an existing one)
2. Copy `rag_kernel/` into your project folder and place [`INIT_UNIVERSAL_RUNTIME_KERNEL_v3.1.8.md`](INIT_UNIVERSAL_RUNTIME_KERNEL_v3.1.8.md) in `RAG/`
3. Start a new session, run: `python -m rag_kernel init --spec RAG/INIT_UNIVERSAL_RUNTIME_KERNEL_v3.1.8.md --output RAG/`
4. The kernel parses the spec deterministically and produces RAG_MASTER.json — zero LLM tokens
5. Copy the generated **pointer block** into your Project Instructions when prompted
6. All subsequent sessions auto-load the RAG and enforce all rules

**Without rag_kernel (Autonomous mode):** Drop [`INIT_UNIVERSAL_RUNTIME_KERNEL_v3.1.8.md`](INIT_UNIVERSAL_RUNTIME_KERNEL_v3.1.8.md) into a session as a file and send "Initialize the project." — the LLM self-bootstraps.

### ChatGPT / GPT Web

1. Open a new conversation (or use Custom GPT if available)
2. Upload or paste the contents of [`INIT_UNIVERSAL_RUNTIME_KERNEL_v3.1.8.md`](INIT_UNIVERSAL_RUNTIME_KERNEL_v3.1.8.md)
3. Send your first message — the system bootstraps in autonomous mode
4. Follow on-screen steps (same as above)
5. At session end, download the generated RAG files and save to your project folder
6. Upload RAG files at the start of each new session to restore state

### Works for both new projects and existing ones being refined.

### ENFORCED Mode (v0.2.0 Runtime Kernel)

For hard runtime validation of every state transition, use the Python runtime:

```bash
# HTTP mode (for GPT Chat Custom Actions or any HTTP client)
python -m rag_kernel serve --project /path/to/your/RAG --port 7437

# MCP mode (for Claude Desktop)
python -m rag_kernel mcp --project /path/to/your/RAG
```

Full setup instructions for all platforms and modes: [`docs/LAUNCH_MANUAL.md`](docs/LAUNCH_MANUAL.md)

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
| **Deterministic state machine** | BOOTING > READY > WORKING > CHECKPOINTING > CLOSING + RECOVERY | None | None | None |
| **Token efficiency** | 60-90% reduction (HOT-only boot ~4K tokens) | Unbounded growth without curation | 60-99% raw compression (best-in-class I/O) | Depends on wiki quality |
| **Cross-platform** | Claude + GPT + any LLM, same spec | Claude Code only | Editor-focused | Platform-agnostic pattern |
| **Dependencies** | Zero. Single markdown file | Node.js + CLI | Rust binary | Varies |
| **Crash recovery** | WAL replay + .bak rotation + RECOVERY state | File-history checkpoints | N/A | None |
| **Conflict tracking** | Explicit ledger — both sources preserved | None | N/A | None |

### Key Differentiators

1. **Only system with a formal state machine on LLM workflows** — deterministic transition guards, not ad-hoc
2. **Only system that works identically across Claude and GPT** — the spec is the invariant
3. **Only system with atomic write protocol + WAL + backup rotation** — enterprise-grade persistence
4. **Formally verified with TLA+** — the same technique Amazon uses for AWS infrastructure (see below)
5. **Zero install, zero dependencies** — the specification IS the product
6. **Conflict ledger is unique** — no other system tracks disagreements between sources

---

## What This Is

A **specification** — a complete protocol that turns any LLM into a controlled, auditable agent with persistent project memory. 3-layer architecture:

```
LLM (reasoning engine)
  | JSON proposals
Policy Layer (this specification)
  | validated transitions
Runtime Kernel (state + persistence)
  | atomic writes
Filesystem (source of truth)
```

## Formally Verified with TLA+

The state machine is verified using [TLA+](https://lamport.azurewebsites.net/tla/tla.html) and the TLC model checker — the same formal methods technique [used by Amazon to verify AWS infrastructure](https://lamport.azurewebsites.net/tla/amazon-excerpt.html).

TLC exhaustively explored **389,522 states** (168,520 distinct) at depth 19 and verified all 8 safety invariants + 3 liveness properties with zero violations:

| Safety Invariant | What It Proves |
|---|---|
| TypeInvariant | All state variables hold valid types at all times |
| TransitionSafety | Every reachable state is legal per the transition graph |
| SingleWriter | At most one proposal staged at any time (no concurrent mutations) |
| WALConsistency | Write-ahead log is append-only, monotone, and never lags behind state |
| TerminalSafety | CLOSING is irreversible — no exit, no crash, no pending proposals |
| NoDeadlock | Every non-terminal state has at least one enabled action |
| CrashRecoveryConsistency | Crash flag is only true when state is RECOVERY |
| WALPrecedesStateChange | WAL entry exists before any state transition commits |

| Liveness Property | What It Proves |
|---|---|
| EventualReady | The system always eventually reaches READY from any reachable state |
| EventualCheckpoint | Once in WORKING, the system always eventually checkpoints |
| EventualClose | The system always eventually reaches CLOSING (no infinite loops) |

Phase 2 verification found and fixed two genuine liveness bugs: a BOOTING/RECOVERY direct-transition loop, and a crash-at-full-WAL deadlock.

The TLA+ specification (`formal/RAGKernel.tla`) is a direct transcription of the Python state machine — every transition, guard, and invariant maps 1:1 to the runtime code. Full results in [`formal/TLC_RESULTS.md`](formal/TLC_RESULTS.md).

Unit tests prove "these 401 scenarios work." TLA+ proves "no scenario can ever violate the invariants — and the system always makes progress." That is a fundamentally stronger guarantee.

---

## Core Features

**Structured Memory (HOT/COLD)** — Active state stays lean (~15KB). Archival data loads on-demand with automatic partitioning.

**Deterministic State Machine** — `BOOTING > READY > WORKING > CHECKPOINTING > CLOSING` with `RECOVERY` path.

**Proposal > Validation > Commit** — LLM proposes JSON actions. System validates against policy, then commits or rejects.

**Atomic Persistence** — All writes atomic and verified. WAL enables crash recovery.

**COLD Partitioning** — Auto-splits into sessions/inventory/conflicts/evidence with sub-partitioning and integrity-preserving chopping.

**Tool Fallback Chain** — Ordered fallback for file operations across platform tools.

**Cross-Platform** — Claude Projects, ChatGPT, Cowork, Claude Code, any LLM.

**Multi-Account Safety** — Session identity tagging, write collision detection, anti-corruption guards.

**Full Audit Trail** — Every state transition, decision, and conflict logged.

**Token Efficiency** — 70-95% reduction vs. naive approaches.

## Two Execution Modes

| Mode | How It Works |
|---|---|
| **Autonomous** | LLM self-enforces all rules. No external software needed. Default mode. |
| **Enforced** | Python runtime (v0.2.0) intercepts all mutations. 9 modules, 401 tests. Zero-touch bootstrap: `rag_kernel init` parses spec deterministically — no LLM needed. |

## Prerequisites

**Minimum:** An LLM that supports file uploads or long-form input + a project folder.

**Recommended:** [Filesystem MCP](https://github.com/modelcontextprotocol/servers) for direct file read/write.

**Optional:** Shell/PowerShell MCP, Python 3.10+ (ENFORCED mode), Claude Code or Cowork.

## Repository Structure

```
rag-runtime-kernel/
├── INIT_UNIVERSAL_RUNTIME_KERNEL_v3.1.8.md   # The specification (current version)
├── INIT_UNIVERSAL_RUNTIME_KERNEL_v3.1.7.md   # Previous version (archived)
├── CONTRIBUTING.md                            # How to report issues
├── CHANGELOG.md                              # Version history
├── docs/
│   ├── architecture.md                        # System architecture
│   ├── benchmark_comparison.md                # Head-to-head vs alternatives
│   ├── design_principles.md                   # Core design philosophy
│   ├── test_analysis_gpt_web.md               # GPT Web test findings
│   ├── LAUNCH_MANUAL.md                       # Full setup guide (all platforms + modes)
│   ├── LOCAL_TESTING_GUIDE.md                 # Local dev testing & GPT Custom Actions
│   ├── v3.2_ARCHITECTURE_DESIGN.md            # Runtime architecture (v0.1.0 design doc)
│   └── ROADMAP.md                             # Development roadmap
├── rag_kernel/                                # v0.2.0 Runtime Kernel (ENFORCED mode)
│   ├── __init__.py                            # Package entry, discover() capability registry
│   ├── __main__.py                            # CLI entry point (init / configure / health / serve / mcp)
│   ├── api.py                                 # HTTP API (FastAPI)
│   ├── state_machine.py                       # Deterministic state engine
│   ├── persistence.py                         # Atomic writes, WAL, hash verification
│   ├── cold_manager.py                        # COLD partition manager
│   ├── concurrency.py                         # Lock manager, write collision guard
│   ├── mcp_transport.py                       # MCP tool interface
│   ├── schemas.py                             # Pydantic models for proposals/state
│   └── spec_parser.py                         # Deterministic MD→RAG parser (zero LLM)
├── tests/                                     # Test suites
│   ├── test_state_machine.py                  # State machine unit tests
│   ├── test_persistence.py                    # Persistence + WAL tests
│   ├── test_cold_manager.py                   # COLD partition tests
│   ├── test_concurrency.py                    # Lock + collision tests
│   ├── test_api.py                            # HTTP API tests
│   ├── test_mcp_transport.py                  # MCP transport tests
│   ├── test_schemas.py                        # Schema validation tests
│   ├── test_main.py                           # CLI entry point tests
│   ├── test_spec_parser.py                    # Spec parser + init/configure tests (64)
│   ├── UNIT_TEST_CLAUDE_DESKTOP.md            # Claude Desktop spec-level tests (42)
│   └── UNIT_TEST_GPT_WEB.md                   # GPT Web spec-level tests (43)
├── .github/
│   ├── FUNDING.yml                            # GitHub Sponsors
│   └── ISSUE_TEMPLATE/                        # Bug report + feature request templates
├── formal/
│   ├── RAGKernel.tla                          # TLA+ state machine specification
│   ├── RAGKernel.cfg                          # TLC model checker configuration
│   └── TLC_RESULTS.md                         # Verification results (389K states, 8 safety + 3 liveness)
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
3. **Single-writer** — concurrent writes detected-and-halted, not auto-merged
4. **GPT Web** — no atomic writes, no real token counter, manual persistence

## Future Development

See [`docs/ROADMAP.md`](docs/ROADMAP.md) for complete roadmap.

| Release | Status | Focus |
|---|---|---|
| **v3.1.8** | Released | Machine-parseable spec with `rag-config` fenced blocks for deterministic parsing. Zero-touch bootstrap target. |
| **v0.2.1** | Released | Graduated POV enforcement (STRICT/ADVISORY/SILENT), 427 tests. Version scheme cleanup. |
| **v0.2.0** | Released | 9 modules, 401 tests. Zero-touch bootstrap (`rag_kernel init`), capability self-discovery (`discover()`), project configuration (`rag_kernel configure`). Paradigm shift: fully autonomous OS-level Python backbone — LLM role reduced to task assignor, results checker, orchestrator. |
| **v0.3.0** | In Progress | Delta checkpoints (ENH-006), conflict auto-categorization (ENH-005) |
| **v0.4.0+** | Planned | Graph Orchestrator: DAG execution, parallel tasks, dependency tracking |

## Reporting Issues

Found a bug? Please [open an issue](../../issues/new/choose) using the provided templates. See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Support

**Developer:** Artem Pakhol
**LinkedIn:** [linkedin.com/in/pakhol](https://www.linkedin.com/in/pakhol)

## License

This project is licensed under the [GNU Affero General Public License v3.0](https://www.gnu.org/licenses/agpl-3.0.html) — see [LICENSE](LICENSE).

**What this means:** You may use, modify, and distribute this software, but any modified version you deploy (including as a network service) must also be released under AGPL-3.0 with attribution to the original project.
