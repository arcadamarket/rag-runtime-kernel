<p align="center">
  <img src="assets/logo.png" alt="RAG Runtime Kernel" width="280"/>
</p>

# RAG Runtime Kernel

> **LLM proposes. System decides. State persists.**

Persistent memory and deterministic state control for any LLM. The kernel keeps state management **out of the language model** — bootstrap, validation, persistence, and crash recovery run as deterministic code, so the model spends its tokens on reasoning, not on bookkeeping.

It ships in **two tiers** so it fits both a non-technical user pasting one file into a chat, and a developer running a serious, long-lived, token-critical project on a hardened Python backbone.

---

## Choose Your Path

| | **Tier 1 — Simple** | **Tier 2 — Enforced** |
|---|---|---|
| **Who it's for** | Anyone. No Python, no Node, no install. | Builders of large, multi-session, token-critical projects who want hard guarantees. |
| **What you run** | One markdown specification, dropped into a chat session. | The `rag_kernel` Python runtime (MCP or HTTP server) alongside the spec. |
| **How rules are applied** | The LLM **self-enforces** the spec by instruction (autonomous). | The Python kernel **intercepts and validates** every state change. The LLM cannot bypass it. |
| **Determinism** | As reliable as the model following instructions. | Deterministic state machine — formally verified (TLA+) and covered by 1,524 passing tests. |
| **Token cost of state ops** | The model reads and reasons over the spec (~100 KB). | **Zero LLM tokens** for bootstrap, validation, persistence, and recovery — they run in Python. |
| **Version** | Specification **v3.2.5** | Runtime kernel **v0.4.21** |
| **Setup effort** | Seconds. Paste a file. | Minutes. Copy `rag_kernel/`, run one command. |

> **Same project, same RAG files.** Start in Tier 1 and graduate to Tier 2 without rewriting anything — the enforced runtime reads and writes the exact same `RAG/` state. Tier 2 is a strict superset of Tier 1.

> **On the two version numbers.** This repo tracks two things on separate version lines: the **specification** (the protocol the LLM follows — currently `v3.2.5`) and the **runtime kernel** (the Python engine that enforces it — currently `v0.4.21`). Tier 1 uses the spec alone; Tier 2 uses the runtime to enforce that spec.

---

## What Problem This Solves

Every LLM session starts from zero. Close the tab, lose the state. The common workarounds are fragile: chat-history dumps, vector stores that retrieve the wrong thing, and framework lock-in that breaks when you switch platforms. Underneath all of them sits a deeper problem — **the language model is doing its own bookkeeping.** Tracking what's done, what's pending, which decision superseded which, whether a write actually landed: every one of those is reasoning the model has to redo each session, and every one of them costs tokens and invites drift.

RAG Runtime Kernel moves that bookkeeping out of the model. State lives in plain files on disk. The lifecycle is a fixed state machine. And in **Enforced mode** the transitions, validation, and persistence are executed by deterministic Python — not proposed by the model and hoped for.

**The shift this represents (Tier 2):**

- **State management leaves the LLM entirely.** The model proposes a JSON action; the kernel validates it against policy and either commits or rejects it. The model never directly mutates state.
- **Bootstrap costs zero LLM tokens.** `rag_kernel init` parses the ~100 KB specification and produces `RAG_MASTER.json` deterministically — no model call. The work that used to mean "feed the model a 20K-token spec and ask it to build the RAG" is now a function call.
- **Determinism is proven, not asserted.** The state machine is verified with TLA+ (the same class of formal method Amazon uses for AWS) and exercised by 1,279 unit tests — all passing.

**What you get in both tiers:**

- **Persistence** — project state survives across sessions, tabs, and platforms.
- **Lean context** — HOT/COLD memory tiers keep only active state in the window; archival data loads on demand.
- **Audit trail** — every state transition, decision, and conflict is logged and traceable.
- **Conflict ledger** — when a new fact contradicts a stored one, both are preserved, never silently overwritten.

---

## Quick Start

### Tier 1 — Simple (no install)

Best for Claude Projects, ChatGPT, or any chat interface.

1. Open a new project or conversation.
2. Add [`INIT_UNIVERSAL_RUNTIME_KERNEL_v3.2.5.md`](INIT_UNIVERSAL_RUNTIME_KERNEL_v3.2.5.md) to the session as a file (it's a full specification, ~100 KB — it goes into a **project/session**, not the short system-prompt field).
3. Send: **"Initialize the project."** The LLM self-bootstraps, scans your folder if it has file access, and builds the `RAG/` state.
4. On ChatGPT / GPT Web without file tools: download the generated RAG files at session end and re-upload them at the start of each session to restore state.

That's it — no Python, no dependencies.

### Tier 2 — Enforced (Python runtime)

Best for long-lived, multi-session, token-critical projects where you want hard guarantees.

**1. Copy the runtime into your project:**

```bash
git clone https://github.com/arcadamarket/rag-runtime-kernel.git temp-clone
cp -r temp-clone/rag_kernel YOUR_PROJECT/rag_kernel
rm -rf temp-clone
```

<details>
<summary>PowerShell / CMD equivalents</summary>

```powershell
# PowerShell
git clone https://github.com/arcadamarket/rag-runtime-kernel.git temp-clone
Copy-Item -Recurse temp-clone\rag_kernel YOUR_PROJECT\rag_kernel
Remove-Item -Recurse -Force temp-clone
```

```cmd
:: CMD
git clone https://github.com/arcadamarket/rag-runtime-kernel.git temp-clone
xcopy temp-clone\rag_kernel YOUR_PROJECT\rag_kernel\ /E /I
rmdir /s /q temp-clone
```
</details>

**2. Bootstrap the RAG deterministically (zero LLM tokens):**

```bash
python -m rag_kernel init --spec RAG/INIT_UNIVERSAL_RUNTIME_KERNEL_v3.2.5.md --output RAG/
# optional: merge project-specific context
python -m rag_kernel configure --rag RAG/RAG_MASTER.json --context your_context.json
```

**3. Run the kernel as a server:**

```bash
python -m rag_kernel mcp   --project /path/to/your/RAG               # MCP mode (Claude Desktop)
python -m rag_kernel serve --project /path/to/your/RAG --port 7437   # HTTP mode (GPT Custom Actions / any client)
```

Every state mutation now flows through the kernel's proposal → validation → commit pipeline. Full platform-specific setup: [`docs/LAUNCH_MANUAL.md`](docs/LAUNCH_MANUAL.md).

> Works for **both new projects and existing ones**. On an existing project, the boot scan inventories your files, classifies them by tier, and extracts knowledge into COLD storage — your prior work becomes queryable, trackable, and persistent.

---

## What's Actually Proven

This section states only what is measured or formally verified — no marketing percentages.

**Determinism (Tier 2):**

- **1,142 / 1,142 unit tests passing** (runtime v0.4.2) across 19 runtime modules (state machine with TLA+-enforced transition guards, persistence/WAL, COLD manager, concurrency, conflict engine, schemas, HTTP API, MCP transport, spec parser, session logger, generated guards, guard generator, context-truncation policy, graph orchestrator, agent/session supervisor, and the DRIFT-ELIM project-state layer — item-lifecycle core, atomic mutation store, deterministic renders, and the fail-loud session auditor).
- **TLA+ formal verification:** the TLC model checker exhaustively explored **389,522 states (168,520 distinct)** to depth 19 and confirmed **8 safety invariants + 3 liveness properties with zero violations**. The TLA+ spec is a 1:1 transcription of the Python state machine. Two genuine liveness bugs were found and fixed during verification.
- **The verified model is now mechanically enforced at runtime (FV-PHASE4):** the state machine's transition table is *generated* from the TLA+ model and legality is checked through the generated predicate — the runtime can no longer drift from what TLC proved. A `guardgen --check` gate detects any model/code divergence.
- Unit tests prove "these 1,279 scenarios work." TLA+ proves "no reachable state can violate the invariants, and the system always makes progress." The second is a strictly stronger guarantee.

**Token economy (Tier 2):**

- **Bootstrap: 0 LLM tokens.** `rag_kernel init` parses the ~100 KB / ~20K-token specification in Python. No model is involved.
- **State operations: 0 LLM reasoning tokens.** Validation, atomic writes, WAL, checkpointing, COLD partitioning, and crash recovery all execute as code. The model's only job is to *propose*; it never spends tokens managing or re-deriving state.
- **Lean active context.** HOT memory holds only live state (on the order of ~15 KB); archival data is loaded on demand rather than carried in every prompt.

We deliberately do **not** publish a single headline "X% token savings" number — the honest claim is structural: the entire state-management layer is removed from the model's token budget. Your actual savings depend on your project size and platform.

---

## How It Compares

A positioning comparison, not a controlled benchmark. Full notes: [`docs/benchmark_comparison.md`](docs/benchmark_comparison.md).

| Capability | RAG Runtime Kernel | Claude Code | lean-ctx | LLM Wiki |
|---|---|---|---|---|
| **Cross-session memory** | Full: HOT/COLD + WAL + crash recovery | Partial: CLAUDE.md + auto-memory, no crash recovery | None (compresses I/O, doesn't persist state) | Pattern only |
| **Deterministic state machine** | Yes — formally verified (TLA+), 1,279 tests | No | No | No |
| **Where state work runs** | Off the LLM, in Python (Tier 2) | In-session, model-mediated | N/A — I/O compression layer | In the LLM / external tooling |
| **Token approach** | State ops cost **0 LLM tokens**; lean HOT boot | Grows without curation | **60–99% raw I/O compression (best in class)** | Depends on wiki quality |
| **Cross-platform** | Claude + GPT + any LLM, one spec | Claude Code CLI only | Editor-focused | Platform-agnostic pattern |
| **Dependencies** | Tier 1: none. Tier 2: Python only | Node.js + CLI | Rust binary | Varies |
| **Crash recovery** | WAL replay + .bak rotation + RECOVERY state | File-history checkpoints | N/A | None |
| **Conflict tracking** | Explicit ledger — both sources preserved | None | N/A | None |

**Honest take:** if raw token compression is your only goal, **lean-ctx wins** — it's purpose-built for that and pairs cleanly with this kernel (lean-ctx compresses the I/O layer; the kernel manages the state layer). Where this project is genuinely distinct is the combination of a **formally-verified deterministic state machine, atomic persistence with crash recovery, a conflict ledger, and one spec that runs across Claude and GPT** — no other system in this list offers that set.

---

## What This Is

A **specification** plus an optional **runtime that enforces it** — together they turn any LLM into a controlled, auditable agent with persistent project memory. Three layers:

```
LLM (reasoning engine)
  | JSON proposals
Policy Layer (the specification)
  | validated transitions
Runtime Kernel (state + persistence)   <- enforced by Python in Tier 2
  | atomic writes
Filesystem (source of truth)
```

In Tier 1 the LLM plays the role of the runtime by following the spec. In Tier 2 the Python kernel *is* the runtime, and the LLM can only propose.

---

## Formally Verified with TLA+

The state machine is verified using [TLA+](https://lamport.azurewebsites.net/tla/tla.html) and the TLC model checker — the same formal-methods technique [Amazon uses to verify AWS infrastructure](https://lamport.azurewebsites.net/tla/amazon-excerpt.html).

TLC exhaustively explored **389,522 states** (168,520 distinct) at depth 19 and verified all 8 safety invariants + 3 liveness properties with zero violations:

| Safety Invariant | What It Proves |
|---|---|
| TypeInvariant | All state variables hold valid types at all times |
| TransitionSafety | Every reachable state is legal per the transition graph |
| SingleWriter | At most one proposal staged at any time (no concurrent mutations) |
| WALConsistency | Write-ahead log is append-only, monotone, never lags behind state |
| TerminalSafety | CLOSING is irreversible — no exit, no crash, no pending proposals |
| NoDeadlock | Every non-terminal state has at least one enabled action |
| CrashRecoveryConsistency | Crash flag is only true when state is RECOVERY |
| WALPrecedesStateChange | WAL entry exists before any state transition commits |

| Liveness Property | What It Proves |
|---|---|
| EventualProgress | The system always eventually returns to READY from any reachable state |
| EventualTermination | CLOSING is stable — once reached, it stays (no infinite loops) |
| ProposalEventuallyResolved | A staged proposal always reaches COMMITTED, REJECTED, or NONE |

Phase 2 verification found and fixed two genuine liveness bugs: a BOOTING↔RECOVERY direct-transition loop, and a crash-at-full-WAL deadlock. The TLA+ specification (`formal/RAGKernel.tla`) maps 1:1 to the runtime code. Full results in [`formal/TLC_RESULTS.md`](formal/TLC_RESULTS.md).

---

## Core Features

**Structured Memory (HOT/COLD)** — Active state stays lean; archival data loads on demand with automatic partitioning.

**Deterministic State Machine** — `BOOTING → READY → WORKING → CHECKPOINTING → CLOSING` with a `RECOVERY` path.

**Proposal → Validation → Commit** — The LLM proposes JSON actions; the system validates against policy, then commits or rejects.

**Atomic Persistence** — All writes are atomic and hash-verified. A write-ahead log enables crash recovery.

**COLD Partitioning** — Auto-splits into sessions / inventory / conflicts / evidence with sub-partitioning and integrity-preserving chopping.

**Conflict Engine** — Auto-categorizes conflicts into 7 types, scores confidence, and auto-resolves low-risk cases; preserves both sides otherwise.

**Tool Fallback Chain** — Ordered fallback for file operations across platform tools.

**Cross-Platform** — Claude Projects, ChatGPT, Cowork, Claude Code, any LLM.

**Multi-Account Safety** — Session identity tagging, write-collision detection, anti-corruption guards.

**Full Audit Trail** — Every state transition, decision, and conflict logged.

---

## Using with Cowork

[Cowork](https://docs.claude.com) is Anthropic's desktop tool for non-developers to automate file and task management. Its direct file access lets the kernel read and write `RAG/` files with no manual copy-paste, and its task automation pairs naturally with the kernel's checkpoint and audit system. For a new project, drop the Init Prompt in and the system bootstraps and scans your folder; for an existing one, point it at the folder during bootstrap and your work becomes tracked state.

## Using with Claude Code

[Claude Code](https://docs.claude.com) is Anthropic's CLI for agentic coding. The kernel adds context persistence across its stateless sessions, a deterministic state machine to structure long-running development, zero-token file ops via direct filesystem access, and a conflict ledger that preserves both sides when new code contradicts a prior decision. Add a `RAG/` directory, bootstrap, and it starts tracking state.

---

## Prerequisites

**Tier 1 minimum:** an LLM that supports file uploads or long-form input, plus a project folder.

**Tier 2:** Python 3.10+. [Filesystem MCP](https://github.com/modelcontextprotocol/servers) recommended for direct file read/write; a shell/PowerShell MCP is optional.

## Repository Structure

```
rag-runtime-kernel/
├── INIT_UNIVERSAL_RUNTIME_KERNEL_v3.2.5.md   # The specification (Tier 1 + Tier 2)
├── INIT_UNIVERSAL_RUNTIME_KERNEL_v3.2.4.md   # Previous spec version (archived)
├── CONTRIBUTING.md                            # How to report issues
├── CHANGELOG.md                               # Version history
├── docs/
│   ├── architecture.md                        # System architecture
│   ├── benchmark_comparison.md                # Positioning vs alternatives
│   ├── design_principles.md                   # Core design philosophy
│   ├── test_analysis_gpt_web.md               # GPT Web platform findings
│   ├── LAUNCH_MANUAL.md                       # Full setup guide (all platforms + tiers)
│   ├── LOCAL_TESTING_GUIDE.md                 # Local dev testing & GPT Custom Actions
│   ├── v3.2_ARCHITECTURE_DESIGN.md            # Runtime architecture design doc
│   └── ROADMAP.md                             # Development roadmap
├── rag_kernel/                                # Tier 2 runtime kernel (v0.4.10)
│   ├── __init__.py                            # Package entry, discover() capability registry
│   ├── __main__.py                            # CLI (init / configure / verify / health / serve / mcp / session / checkpoint / gc / graph / resolve / defer / render / note)
│   ├── api.py                                 # HTTP API (FastAPI)
│   ├── state_machine.py                       # Deterministic state engine
│   ├── persistence.py                         # Atomic writes, WAL, hash verification; live pre-write side-store guard (FIX-7, v0.4.10)
│   ├── cold_manager.py                        # COLD partition manager
│   ├── concurrency.py                         # Lock manager, write-collision guard
│   ├── conflict_engine.py                     # Conflict auto-categorization (7 categories, auto-resolve)
│   ├── mcp_transport.py                       # MCP tool interface
│   ├── schemas.py                             # Pydantic models for proposals/state
│   ├── session_logger.py                      # Universal JSONL session observability
│   ├── spec_parser.py                         # Deterministic MD→RAG parser (zero LLM); single <SPEC_VERSION> self-version stamp across HOT+COLD, fail-loud on survivor (FIX-2); <ISO> substitution + _-template-key strip so a fresh deploy is born clean (FIX-3, v0.4.6)
│   ├── guardgen.py                            # Deterministic TLA+ → Python guard generator (build-time)
│   ├── generated_guards.py                    # Generated, runtime-enforced transition table + guards
│   ├── context_policy.py                      # Kernel-enforced context-truncation policy (M-009)
│   ├── graph_orchestrator.py                  # Graph Orchestrator: DAG core + execution engine (v0.4.0)
│   ├── agent_supervisor.py                    # Graph Orchestrator: observable off-process worker supervisor / AgentView (v0.4.0)
│   ├── drift_control.py                       # DRIFT-ELIM: item-lifecycle core — ItemStatus enum + LIFECYCLE guards + immutable TrackedItem (v0.4.0)
│   ├── drift_store.py                         # DRIFT-ELIM: atomic mutation API over tracked_items + backlog migration; lifecycle CLI (v0.4.0)
│   ├── drift_render.py                        # DRIFT-ELIM: deterministic renders of open_tasks/deferred_items/backlog/ERROR_LOG from tracked_items (sole authority); render CLI (v0.4.0)
│   └── drift_audit.py                         # DRIFT-ELIM: fail-loud session-boundary auditor — render parity, supersede refs, note/status, side-store scan, current_status freshness + FIX-1 integrity invariants (WAL/​.bak/​COLD/​placeholder/​template-key/​session-id) (v0.4.4)
├── tests/                                     # 1,299 tests (v0.4.10 release)
├── .github/                                   # FUNDING.yml + issue templates
├── formal/
│   ├── RAGKernel.tla                          # TLA+ state machine specification
│   ├── RAGKernel.cfg                          # TLC model checker configuration
│   └── TLC_RESULTS.md                         # Verification results (389K states, 8 safety + 3 liveness)
├── LICENSE                                    # AGPL-3.0
└── README.md
```

## Session Lifecycle

1. **BOOTING** — Load HOT, verify consistency, check WAL, probe tools.
2. **READY** — Accept tasks.
3. **WORKING / INGESTING** — Execute tasks, ingest files, extract knowledge.
4. **CHECKPOINTING** — Save atomically with backup rotation.
5. **CLOSING** — Audit findings, final save.

## Disclaimer & Known Limitations

- **Tier 1 is self-enforced** — the LLM follows the spec by instruction, not by hard runtime constraints. For hard guarantees, use Tier 2.
- **Persistence depends on platform** — full atomic writes with file/MCP access; manual file management on GPT Web (no atomic writes, no real token counter).
- **Context window ceiling** — the spec is ~100 KB / ~20K tokens; in Tier 1 it occupies the window, so very large projects may hit limits. Tier 2 keeps the spec out of the model via deterministic parsing.
- **Single-writer** — concurrent writes are detected and halted, not auto-merged.
- **Not a database** — this is structured file-based memory, not a production database replacement.

See [`docs/test_analysis_gpt_web.md`](docs/test_analysis_gpt_web.md) for platform-specific findings.

## Roadmap

See [`docs/ROADMAP.md`](docs/ROADMAP.md) for the complete roadmap.

| Line | Version | Status | Focus |
|---|---|---|---|
| Spec | **v3.2.5** | Released | **KA-8 — Session-Start & Session-End Rituals baked into the spec (GOVERNANCE-DETERMINISM / KA-10 arc, TierB).** KA-6 shipped the `session-start` / `session-end` runtime commands; KA-8 makes the spec *tell every deploy to run them*. New **§50** assembles the previously-scattered session-boundary steps (§17/§19/§20/§45) into explicit, ordered, fail-loud sequences and adds `operating_protocol.session_start_protocol` + `session_end_protocol` rag-config blocks, so a fresh `init --spec` **seeds** them into every RAG deterministically — closing the gap where a deploying agent had to hand-assemble the ritual and could skip a step (the freeze the first external deploy hit). Session-start = carry-forward gate (`verify` + `audit`, fail-loud → RECOVERY) → GC dry-run over `root_project` → open logger; session-end = checkpoint → close (KA-4 gate) → audit, any step's failure aborting the rest. Spec-only — no schema/runtime change (runtime stays v0.4.14, drift gate `268149294421` unchanged); `init --spec v3.2.5` seeds both ritual rules + the pre-existing 12 known-issues, `verify` OK + `audit --strict` clean, suite 1,398 (+6). |
| Spec | **v3.2.4** | Released | **STRICT-OBEY — Operator Fidelity Protocol (§49).** Promotes the operator-fidelity rule from this project's RAG into the universal spec so every project spawned from `init --spec` inherits it. A HARD RULE in four parts: obey the operator's literal instruction (no guesswork/improvisation/scope-creep/unrequested work, never substitute the model's preference); honest status (never report incomplete work as done/shipped/resolved; distinguish a developer checkpoint from a finished feature); bounded halt-and-ask (ask ONLY on genuine ambiguity or an operator-only decision — over-asking is as much a violation as over-doing; exercise delegated discretion); rendering discipline (every status/backlog/report render enumerates each item line by line, by ID, in plain language — never a bare count or glyph shorthand). New `operating_protocol.strict_obey` rag-config. Spec-only — no schema/runtime change; `init --spec v3.2.4` inherits exactly 12 known-issues + `strict_obey`, `verify` OK. |
| Spec | **v3.2.3** | Released | **FIX-7 T3 — Web Access Protocol decision table.** §26a rewritten from cost-ordered 3-tier prose to a deterministic **first-match-wins decision table** (unknown URL → search-for-discovery-only; API/connector/MCP-first; repeatable/persistent → on-disk script; one-off-to-disk → `curl`/`wget` fetch-to-disk per INS-044; one-off in-context → WebFetch) with explicit guards (JS-shell → browser escalation; restricted-domain → STOP, no route-around; `curl_cffi` header caution) and a violation definition; the `rag-config` `web_access_protocol` string + `pre_flight_gate` web clause reconciled to match. Resolves the FIX-7 T3 web-protocol churn from the eBay Session-Zero deploy audit. Spec-only — no schema/runtime change; `init --spec v3.2.3` inherits exactly 12 known-issues, `verify` OK. Prior v3.2.2: ENV-NORM — §3a tmux-mcp-primary tool hierarchy, `session_start_shell_rule`, §3 `doctor` boot preflight. |
| Runtime | **v0.4.21** | Released | **KA-7 — fail-loud audit when governance advanced past the session-log trail (GOVERNANCE-DETERMINISM / KA-10 arc).** The dual of KA-1: where KA-1 catches a *completed* session log newer than the checkpoint, KA-7 catches the inverse — `meta.written_by_session` advanced **past** the newest session-log-that-has-entries (`cp_ord > max logged ordinal`), i.e. the checkpoint moved but observability did not (the eBay "logs stopped at S1 while the machine ran on" freeze the auditor previously missed). New `drift_audit.check_observability_coherence` (ERROR) plus a `_session_log_has_entries` helper; **mutually exclusive with KA-1 by construction** (KA-1 = a completed log newer than the checkpoint; KA-7 = the checkpoint newer than every log) so the two never double-report. Self-skips a `BOOTING` / un-stamped / malformed-id RAG and a no-logger project — so a healthy RAG audits clean. Wired into `audit_file` so it runs at every session boundary. Dogfooded: a synthetic RAG whose `written_by_session` outran its newest non-empty log fails loud; the live RAG audits clean. `DRIFT_AUDIT_VERSION` 1.9.0 → 1.10.0. Ninth runtime increment of the KA-10 GOVERNANCE-DETERMINISM initiative (after KA-4, KA-6, KA-9, KA-3, KA-2A, KA-2B, KA-5, KA-1). No new module (19), health 20/20, drift gate `268149294421` unchanged (no schema/WAL/TLA+ change), **1,524 tests** (+25). |
| Runtime | **v0.4.20** | Released | **KA-1 — fail-loud audit on a ran-but-never-checkpointed session (GOVERNANCE-DETERMINISM / KA-10 arc).** Closes the auditor blind spot behind the **S88 eBay headline** ("deployed auditor passed clean while governance frozen at S0/seq1"): an agent ended sessions on `configure`/`audit` without ever `checkpoint`-ing, so `meta.written_by_session` stayed frozen while later sessions ran, and `audit --strict` never noticed. The KA-4 close gate prevents the *live* session from closing un-checkpointed; KA-1 adds the missing *at-rest audit* invariant so even an already-frozen RAG fails loud. New `drift_audit.check_uncheckpointed_session` (ERROR) flags any session log beside the RAG (`session_log_<sid>.jsonl`) that both carries a `session_end` marker (ran to a **clean close**) **and** has a numeric session ordinal greater than `meta.written_by_session` — the freeze signature. It keys on `session_end` so the in-flight current session (still-open / detached / crashed) is never false-positived, ignores any ordinal `<= written_by_session` (a historical checkpointed session whose log persists), and self-skips a `BOOTING` / un-stamped / malformed-id RAG and an empty RAG directory — so a healthy RAG audits clean. Wired into `audit_file` (every session boundary). Dogfooded: a synthetic S1-frozen RAG with a completed `session_log_S2` fails loud; the live RAG audits clean. `DRIFT_AUDIT_VERSION` 1.8.0 → 1.9.0. Eighth runtime increment of the KA-10 GOVERNANCE-DETERMINISM initiative (after KA-4, KA-6, KA-9, KA-3, KA-2A, KA-2B, KA-5). No new module (19), health 20/20, drift gate `268149294421` unchanged (no schema/WAL/TLA+ change), **1,499 tests** (+24). |
| Runtime | **v0.4.19** | Released | **KA-5 — single-source the `@rag-kernel-manifest` version (GOVERNANCE-DETERMINISM / KA-10 arc).** Closes **E-046**: the package manifest docstring hardcoded `version` / `spec_version` literals that drifted from the live authorities (frozen at `0.4.7` / spec `3.2.2` while the kernel had moved on to `0.4.18` / spec `3.2.5`), yet `audit --strict` passed clean. The manifest docstring no longer carries those literals — `rag_kernel.__version__` and the new `rag_kernel.__spec_version__` are the **sole** authorities, and `discover()` **injects** the version fields from them so a published manifest can no longer drift. New `drift_audit.check_manifest_version_binding` fails loud (ERROR) if a literal is re-introduced and disagrees with its authority, if `discover()`'s injected package manifest ≠ the authorities, or if an authority is missing/empty — pure introspection over the package, wired into `audit_hot` (always-on). `DRIFT_AUDIT_VERSION` 1.7.0 → 1.8.0. Seventh runtime increment of the KA-10 GOVERNANCE-DETERMINISM initiative (after KA-4, KA-6, KA-9, KA-3, KA-2A, KA-2B). No new module (19), health 20/20, drift gate `268149294421` unchanged (no schema/WAL/TLA+ change), **1,475 tests** (+6). |
| Runtime | **v0.4.18** | Released | **KA-2 increment B — governed sessions_recent row-repair/dedup verb (GOVERNANCE-DETERMINISM / KA-10 arc).** The repair half that completes KA-2: increment A (v0.4.17) made the kernel fail loud on duplicate-bootstrap `sessions_recent` rows (two rows sharing a checkpoint timestamp `d` — the eBay S0/S1 signature) but offered no governed way to fix them, and a hand-edit is exactly the drift the project forbids. New `drift_store.dedup_sessions_recent` (pure) + `dedup_sessions_recent_file` (atomic) remove the phantom duplicate(s), keeping one row per timestamp: **group-correct** (3+ rows in a group), **idempotent**, **order-preserving**, honoring `--keep first\|last`; rows with a missing/blank `d` are never touched; the file verb writes through the atomic `tmp → verify → .bak → rename` path and is a true no-op on a clean ledger. New CLI `dedup-sessions [--keep first\|last] [--dry-run]`. **Detect == repair:** the duplicate-detection predicate (`sessions_recent_duplicate_pairs`) now lives in `drift_store` and is consumed by both the auditor (flag) and this verb (repair), so a flagged row is exactly a removed row; the shared date coercers moved down with it and are re-exported from `drift_audit`. Also unblocks the eBay deploy's B-3. Dogfooded: a synthetic S0/S1 RAG dedups to clean then audits clean; the live RAG is untouched. `DRIFT_STORE_VERSION` 1.1.0 → 1.2.0. KA-2 now **RESOLVED**. Sixth runtime increment of the KA-10 GOVERNANCE-DETERMINISM initiative (after KA-4, KA-6, KA-9, KA-3, KA-2A). CLI/store-only — no new module (19), health 20/20, drift gate `268149294421` unchanged (no schema/WAL/TLA+ change), **1,469 tests** (+21). |
| Runtime | **v0.4.17** | Released | **KA-2 increment A — sessions_recent duplicate-bootstrap auditor (GOVERNANCE-DETERMINISM / KA-10 arc).** Closes another blind spot the eBay Session-Zero deploy exposed: its `sessions_recent` ledger carried duplicate bootstrap rows — S0 and S1 minted at the **same timestamp**, one never actually run — yet `audit --strict` reported 0 findings and there was no governed way to repair them. New `drift_audit.check_sessions_recent_coherence` fails loud (ERROR) when two rows share a checkpoint timestamp `d` (compared on the parsed UTC instant so a `Z`-suffixed value and its offset twin collide; an unparseable `d` falls back to the exact literal). **Order-agnostic by design** — the project legitimately writes `sessions_recent` both oldest-first (this kernel's live RAG) and newest-first (a fresh `init --auto-ready` RAG, `[S1, S0]`), and one session legitimately spans multiple rows (the S95/S95 pair, distinct timestamps); a shared timestamp is the only phantom-duplicate signal safe across every shape, so directional id/timestamp monotonicity was deliberately not enforced (it would false-positive on a clean deploy). Self-skips when `sessions_recent` is absent / < 2 rows / a row's `d` is missing; wired into `audit_hot`. Dogfooded: a synthetic eBay S0/S1 shared-timestamp RAG fails loud; the live RAG and a fresh `init --auto-ready` RAG both audit clean. **Increment A (detection)**; the paired increment B — a governed row-repair/dedup verb — remains open (KA-2 stays IN_PROGRESS). `DRIFT_AUDIT_VERSION` 1.6.0 → 1.7.0. Fifth runtime increment of the KA-10 GOVERNANCE-DETERMINISM initiative (after KA-4, KA-6, KA-9, KA-3). CLI/audit-only — no new module (19), health 20/20, drift gate `268149294421` unchanged (no schema/WAL/TLA+ change), **1,448 tests** (+21). |
| Runtime | **v0.4.16** | Released | **KA-3 — current_status internal-coherence auditor (GOVERNANCE-DETERMINISM / KA-10 arc).** Closes another silent blind spot the eBay Session-Zero deploy exposed: `current_status` denormalizes two facts from `meta` inside the same RAG — the session that last wrote it (`current_status.session` vs `meta.written_by_session`) and the day it was last updated (`current_status.last_updated` vs `meta.last_updated_utc`) — yet nothing asserted they agreed. The eBay deploy froze `current_status.session` at `S0` while the machine moved on and ran `last_updated` two days behind `meta`, and `audit --strict` still reported 0 findings. New `drift_audit.check_current_status_coherence` fails loud (ERROR) when `current_status.session != meta.written_by_session` or the UTC calendar day of `current_status.last_updated` differs from that of `meta.last_updated_utc` (day-granularity, since one is a date and the other a full instant). Distinct from the E-043 `check_current_status_freshness` guard (which checks version / git HEAD — authorities *outside* the RAG); this checks two facts denormalized *inside* it. Self-skips when either side is absent/unparseable, so this kernel's own key-less `current_status` audits clean. Wired into `audit_hot`. Dogfooded: a synthetic stale-session/stale-date RAG fails loud; the live RAG audits clean. `DRIFT_AUDIT_VERSION` 1.5.0 → 1.6.0. Fourth runtime increment of the KA-10 GOVERNANCE-DETERMINISM initiative (after KA-4, KA-6, KA-9). CLI/audit-only — no new module (19), health 20/20, drift gate `268149294421` unchanged (no schema/WAL/TLA+ change), **1,427 tests** (+17). |
| Runtime | **v0.4.15** | Released | **KA-9 — project_context placeholder gate (GOVERNANCE-DETERMINISM / KA-10 arc).** Closes the last born-clean hole the eBay Session-Zero deploy exposed: a deployed RAG carrying unfilled `<from user>` tokens in `project_context.brief` / `domain` / `end_goal` that `audit --strict` passed clean. The FIX-1 `check_placeholder_tokens` scan only matched whole-value **UPPER_SNAKE** parser tokens (`<SPEC_VERSION>`, `<ISO>`), so the **human-fill** session-zero placeholders (`<from user>`, `<absolute path>` — lowercase/spaced, filled by the LLM at deploy) slipped through. Two parts: **(1)** a new `drift_audit.check_project_context_placeholders` walks the `project_context` subtree and fails loud on any surviving human-fill `<…>` placeholder (leaving UPPER_SNAKE tokens to `check_placeholder_tokens`, no double-report), wired into `audit_hot`; **(2)** per spec §1182, `cmd_init` now nulls every unfilled `project_context` placeholder so a fresh `init` / `--auto-ready` is born clean by construction (same discipline as FIX-9). Dogfooded: the synthetic eBay-defective RAG fails loud; the live RAG + a fresh `init --auto-ready` audit clean. `DRIFT_AUDIT_VERSION` 1.4.0 → 1.5.0. Third increment of the KA-10 GOVERNANCE-DETERMINISM initiative (after KA-4, KA-6). CLI/audit-only — no new module (19), health 20/20, drift gate `268149294421` unchanged (no schema/WAL/TLA+ change), **1,410 tests** (+12). |
| Runtime | **v0.4.14** | Released | **KA-6 — machine-enforced session-start / session-end rituals (GOVERNANCE-DETERMINISM / KA-10 arc).** Collapses each session-boundary ritual into ONE ordered, fail-loud CLI command, removing the hand-scripting surface where a step gets skipped (eBay S2/S4: the deploy closed on `configure`/`audit` without ever `checkpoint`-ing, freezing `meta.written_by_session`). **`session-start <id>`** = carry-forward gate (the inverse of the KA-4 close gate — fails loud on an incoherent/unbanked inherited RAG via `verify` + `audit`, sanctioned `--force` override) → gc dry-run → open logger. **`session-end`** = checkpoint → close (the KA-4 gate now passes) → audit, where any step's non-zero exit aborts the rest so a session can never end half-ritualed. Reuses the existing `verify`/`audit`/`gc`/`checkpoint`/`session` primitives (no behavior drift) and both commands are excluded from the bootstrap-log wrapper. Dogfooded: `session-start S92` gated green on this repo's live RAG and opened the session. Second increment of the KA-10 GOVERNANCE-DETERMINISM initiative (after KA-4). CLI-only — no new module (19), health 20/20, drift gate `268149294421` unchanged (no schema/WAL/TLA+ change), **1,392 tests** (+11). |
| Runtime | **v0.4.13** | Released | **KA-4 — checkpoint-to-close enforcement (GOVERNANCE-DETERMINISM / KA-10 arc).** The kernel now **refuses** to close a started session on the CLI unless that session checkpointed first. Root cause (eBay S4): an agent ended sessions on `configure` / `audit` (or a scratch script) without ever running `checkpoint`, leaving `meta.written_by_session` stale across sessions — a silent governance freeze that recurred even after the S89 prose-only guide fix, proving enforcement must be **code, not prose**. `session close <id>` evaluates a checkpoint gate (`meta.written_by_session == <id>`, the precise inverse of the freeze signature) and refuses with a non-zero exit + remediation hint when absent; a sanctioned `--force` override closes anyway with a loud warning so a blocked agent does not resort to an unsanctioned scratch script. The programmatic `KernelApp.close()` already force-checkpoints on close (ENH-006) — this closes the standalone-CLI hole the deploy actually froze on; a no-op close (no log) stays a no-op. First increment of the KA-10 GOVERNANCE-DETERMINISM initiative. CLI-only — no new module (19), health 20/20, drift gate `268149294421` unchanged (no schema/WAL/TLA+ change), **1,381 tests** (+9). |
| Runtime | **v0.4.12** | Released | **Release bundle — FIX-9 … FIX-12** (eBay Session-Zero deploy-audit lane, U1–U4) bundled into one runtime release. **FIX-9**: `init --auto-ready` routed through the first stamping checkpoint, so a born-ready RAG is stamped (`written_by_session` / seq) with a byte-parity `.bak` and audits clean. **FIX-10**: `configure` persists via `atomic_write_json(mirror_bak=True)`, closing the K6 / FIX-4 `.bak` parity-mirror gap. **FIX-11**: a sanctioned, non-loaded `RAG_CONTEXT.json` project-context store + `context` CLI (`set` / `get` / `list`) + `configure --consume` — governed, zero-boot-token project context. **FIX-12**: the CLI bootstrap session log now captures real events (`SessionLogger.attach()` / `detach()` + a central dispatch wrapper emitting a real `tool_invocation` per verb), fixing empty / marker-only logs and a spurious second `session_start`. No new module (19), health 20/20, drift gate `268149294421` unchanged (no schema/WAL/TLA+ change), 1,372 tests. |
| Runtime | **v0.4.11** | Released | **FIX-8 — CLI checkpoint parity-mirror `.bak` (E-045).** The standalone CLI `checkpoint` verb now passes `mirror_bak=True` to `atomic_write_json`, so a session closed on `checkpoint` alone refreshes `RAG_MASTER.json.bak` to byte-parity with the just-committed HOT — matching `api.checkpoint` do_full and the FIX-4 / K6 contract. Previously it left `.bak` one seq behind, which `audit.check_bak_parity` correctly failed loud on unless a later mirroring write (`render --apply`) happened to follow. One-line wiring fix + 3 regression tests (CLI checkpoint → byte-parity `.bak`, audit-clean with no follow-up, parity holds across repeated checkpoints). No new module (19), health 20/20, drift gate `268149294421` unchanged (no schema/WAL/TLA+ change), 1,302 tests. |
| Runtime | **v0.4.10** | Released | **FIX-7 T1 — live pre-write side-store guard (T1)** from the eBay Session-Zero deploy audit: the Rule 13 / E-039 parallel-store invariant (a Cowork-memory `MEMORY.md` / `feedback_*.md` / `project_*.md`, or a stray `*_context.json` beside the RAG) now fires **at write time** — a new `persistence.assert_no_side_stores` guard, opt-in via `guard_side_stores=True` on the canonical RAG-state writers (full checkpoint/close, `drift_store`, `drift_render`), **refuses to commit** while such a store is live, instead of only flagging it after the fact at `audit`. The side-store patterns + scan logic are single-sourced in `persistence`; `drift_audit`'s two existing checks now delegate to it (DRY) so guard and audit cannot diverge. The live guard is scoped to the RAG directory subtree (fast write-time tripwire); the comprehensive project-root sweep stays with `audit`. **(T3 — `web_access_protocol` as a decision table — ships separately as spec v3.2.3.)** No new module (19), health 20/20, drift gate `268149294421` unchanged (no schema/WAL/TLA+ change), 1,299 tests. |
| Runtime | **v0.4.9** | Released | **FIX-6 — layout-aware `--rag` default (K9)** from the eBay Session-Zero deploy audit: a shared `_default_rag_path()` resolver finds `RAG_MASTER.json` whether a command runs from the project root or inside the RAG dir (first existing candidate, never doubling `RAG/RAG`), applied to every RAG-taking command. CLI-only, no new module (19), health 20/20, 1,279 tests. |
| Runtime | **v0.4.6** | Released | **FIX-3 — init/configure build-time hygiene (K3+K5+K7)** from the eBay Session-Zero deploy audit: `spec_parser` substitutes the build-deterministic `<ISO>` placeholder and strips `_`-prefixed `:template` keys from `operating_protocol` so a fresh deploy is born clean, and `KernelApp` mints a canonical `S<int>` session id (not `S-{pid}-…`) and stamps `meta.written_by_session` on every checkpoint — preventing at build the defects FIX-1 could only detect. Dogfooded: `init --spec v3.2.2` went from 3 audit findings to 0. No new module (19), health 20/20, 1,219 tests. |
| Runtime | **v0.4.5** | Released | **FIX-2 — single self-version token + `verify` gate (K4+K8)** from the eBay Session-Zero deploy audit: the spec's HOT/COLD templates now carry one `<SPEC_VERSION>` token that `spec_parser` substitutes and stamps into the COLD `init_prompt_reference` from the spec's own version — root-causing the COLD↔HOT version drift FIX-1 could only detect. New deterministic `rag_kernel verify` post-init coherence gate; `init` fails loud on any unsubstituted token; SESSION_ZERO verify gate rewritten off the file-size heuristic onto `verify`/`audit`. No new module (19), health 20/20, 1,202 tests. |
| Runtime | **v0.4.4** | Released | **FIX-1 — integrity auditor + WAL hardening (K1+K2)** from the eBay Session-Zero deploy audit: seven fail-loud integrity invariants (WAL monotonicity, RAG↔.bak parity, COLD↔HOT spec-version, unsubstituted-placeholder, leaked-template-key, non-empty `written_by_session`, session-id coherence) + a `health` WAL-replay self-test. Dogfooded live — caught a real latent COLD↔HOT drift in this repo's own RAG. No new module (19), health 20/20, 1,180 tests. |
| Runtime | **v0.4.3** | Released | **AUDIT-CS-FRESHNESS** — `audit` now guards the `current_status` narrative against the live runtime version + git HEAD (E-043), failing loud on a stale snapshot; new `audit --git-head` flag with best-effort auto-resolution. No new module (19), health 20/20, 1,159 tests. |
| Runtime | **v0.4.2** | Released | **ENV-NORM** — `doctor` preflight (env summary, fail-closed stale-`.git/index.lock` `--fix`, shell-policy first-move, `--emit-runner`) + guarded `add` verb (closes the no-ADD-verb gap), paired with spec v3.2.2 tmux-primary tool hierarchy. No new module (19), health 20/20, 1,142 tests. |
| Runtime | **v0.4.1** | Released | Kernel hardening from the eBay S0 deployment audit: `audit-env` fetch/VCS/shell tooling enumeration (INS-045) + `init` fail-loud on missing `--spec` (INS-046), bundling DRIFT-ELIM inc 6. No new module (19), health 20/20, 1,123 tests. |
| Runtime | **v0.3.0** | Released | 13 modules, 758 tests. TLA+ guards **enforced** at runtime (FV-PHASE3/4) — transition table generated from the model, `guardgen`/`generated_guards` registered; **M-009** kernel-enforced context-truncation policy (per-region token accounting, deterministic eviction, HOT never evicted, checkpoint/evict/halt). |
| Runtime | **v0.2.7** | Released | 12 modules, 676 tests. Graduated POV, delta checkpoints, conflict auto-categorization engine, session logger, session/checkpoint/gc CLI, spec enforcement. |
| Runtime | **v0.2.0** | Released | Zero-touch bootstrap (`rag_kernel init`), capability self-discovery (`discover()`), project configuration (`rag_kernel configure`). |
| Runtime | **v0.4.0** | Released | **Graph Orchestrator** — DAG execution, dependency tracking, deterministic-levels + OS-process parallel scheduling, checkpoint-per-node, transactional rollback, and an observable agent/session supervisor; runtime-wired via `KernelApp.run_graph`, CLI `rag_kernel graph run`, and MCP `rag_graph_run`. **DRIFT-ELIM** (deterministic project-state layer) — item-lifecycle core, atomic mutation API over `tracked_items` + backlog migration, the `rag_kernel resolve\|defer\|…` lifecycle CLI, deterministic **renders** making `tracked_items` the sole authority (legacy `open_tasks`/`deferred_items`/backlog become projections via `rag_kernel render`), and a fail-loud session auditor that asserts render == canonical. 19 modules, health 20/20, 1,082 tests. |

## Reporting Issues

Found a bug? Please [open an issue](../../issues/new/choose) using the provided templates. See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Support

**Developer:** Artem Pakhol
**LinkedIn:** [linkedin.com/in/pakhol](https://www.linkedin.com/in/pakhol)

## License

Licensed under the [GNU Affero General Public License v3.0](https://www.gnu.org/licenses/agpl-3.0.html) — see [LICENSE](LICENSE).

**What this means:** you may use, modify, and distribute this software, but any modified version you deploy (including as a network service) must also be released under AGPL-3.0 with attribution to the original project.
