# Changelog

All notable changes to the RAG Runtime Kernel specification and tooling.

## [Unreleased] — `main` (post-v0.3.0)

_Work in progress toward the v4.0 Graph Orchestrator. The headline announcement
is intentionally deferred until the orchestrator is complete and runtime-wired._

### Added — Graph Orchestrator: Pure DAG Core (GRAPH-ORCH, increment 1)
- **`graph_orchestrator.py`** — deterministic, stdlib-only directed-acyclic-graph core. Zero dependencies, execution-free, fully self-contained.
- **`OrchestratorNode`** — immutable, hashable work-unit descriptor (`id`, `deps`, optional `action`/`payload`); `payload` is excluded from identity so nodes stay hashable. Self-dependencies and malformed ids are rejected at construction.
- **`ExecutionDAG`** — fail-loud construction: duplicate ids, dangling dependencies, and **cycles** all raise `DAGBuildError` (cycle detection via Kahn's algorithm), so a constructed graph is *always* a valid DAG.
- **Deterministic topological order + level assignment** — `levels()[k]` is the set of mutually-independent, **parallel-eligible** nodes at depth `k`; ordering is reproducible (ids sorted within each level) regardless of input order.
- **Guarded node-status lifecycle** — `PENDING → READY → RUNNING → DONE | FAILED`, plus `SKIPPED`; illegal moves raise `NodeStateError`. The status table is a small adjacency-list state machine, validated at import — the same discipline `state_machine.py` applies to sessions.
- **Pure scheduling queries** — `ready_nodes()` / `next_ready()` (deterministic, lowest-id-first) expose exactly the nodes a scheduler may dispatch now.
- **Deterministic failure propagation** — `mark_failed()` SKIPs the entire downstream closure of a failed node and returns the skipped set; siblings and completed work are untouched.
- **Dual-POV posture:** same-level nodes are *scheduling-eligible* for concurrency, but every future node result will commit through the serialized propose → validate → commit pipeline — concurrency is a scheduling property here, never a state-mutation race. _LLM proposes, system decides, state persists._
- **Scope boundary (deliberate):** not yet registered in `_KERNEL_MODULES` / `discover()` / `cmd_health` — the `@rag-kernel-manifest` block is present and discovery-ready, and wiring lands with the execution engine (mirrors how FV-PHASE3 shipped before FV-PHASE4 enforced it). Functional module count therefore unchanged at 13 until then.
- 41 new tests (`tests/test_graph_orchestrator.py`). **799 total tests**, all passing; zero regressions; `guardgen --check` drift gate green; health 14/14.

### Added — Graph Orchestrator: Execution Engine (GRAPH-ORCH, increment 2)
- **`GraphExecutor`** — drives DAG nodes through the kernel's serialized propose → validate → commit pipeline (a node's "work" IS its proposal; no arbitrary code is executed in the engine). KernelApp is duck-typed under `TYPE_CHECKING`, so the module never imports `api.py` at runtime — no import cycle.
- **Checkpoint-per-node** through the guarded `CHECKPOINTING` transition (via the delta-checkpoint manager, so the per-node cost is a small delta), plus a per-node `GRAPH_NODE_EXECUTED` WAL event — each committed node is a durable, auditable crash-recovery boundary.
- **Deterministic failure-closure** — a rejected proposal / failed commit marks the node `FAILED`, SKIPs its downstream closure, and never mutates HOT or takes a checkpoint; independent branches keep running unless `stop_on_failure`.
- 18 new tests (`tests/test_graph_executor.py`). **817 total tests**, all passing; zero regressions; `guardgen --check` green; health 14/14.

### Added — Graph Orchestrator: Deterministic-Levels Scheduling (GRAPH-ORCH, increment 3)
- **`Schedule.LEVELS`** — schedules the DAG one topological *level* at a time; the nodes within a level are mutually independent and therefore parallel-eligible, and the schedule names that batch explicitly via `levels_executed`.
- Every node **still** commits through the one serialized propose → validate → commit pipeline in deterministic id order, so `LEVELS` is **provably equivalent to `SEQUENTIAL`** — identical executed order, final HOT, and WAL event sequence (proven in tests over diamond / multi-level / multi-root graphs incl. failure closure).
- **Single-writer made explicit** — `_assert_single_writer()` fails loud unless the executor holds the project file-mutex (`concurrency.ProjectLock`) for its own session before committing a level. Concurrency is a *scheduling* property, never a state-mutation race.
- 21 new tests (`tests/test_graph_levels.py`). **838 total tests**, all passing; zero regressions; `guardgen --check` green; health 14/14.

### Added — Graph Orchestrator: Transactional Rollback/Recovery (GRAPH-ORCH, increment 4)
- **`rollback_on_failure`** — opt-in mode (default OFF, so the keep-committed-prefix behaviour of increments 2–3 is unchanged) that makes a DAG run **all-or-nothing**: on any node `FAILED`, the whole run is undone back to the pre-run HOT baseline.
- The restore goes through the kernel's RECOVERY path (**`KernelApp.rollback_to_snapshot`**): `force_state(RECOVERY)` (the sanctioned escape — `READY → RECOVERY` is not a normal transition), atomic HOT restore (refreshing `.bak`), a `GRAPH_ROLLBACK` WAL event, delta-base reset, then a legal `RECOVERY → READY`. The kernel — never the executor — owns the mutation, so single-writer + WAL-recoverability are preserved; no TLA+/`guardgen` change is needed (the RECOVERY transitions already exist).
- 14 new tests (`tests/test_graph_rollback.py`). **852 total tests**, all passing; zero regressions; `guardgen --check` green; health 14/14.

### Changed — Graph Orchestrator: Registration (GRAPH-ORCH, increment 5)
- **`graph_orchestrator` is now registered** in `_KERNEL_MODULES`, `discover()`, and `cmd_health` — it is a discovered capability module and appears in the package manifest `modules` dict. The deliberate FV-PHASE3→FV-PHASE4-style scope boundary held across increments 1–4 and is now closed.
- **Functional module count reconciled 13 → 14** (documented convention in `__init__.py`); **health is now 15/15** (14 capability modules + `__main__`).
- No new behaviour and no new tests in this increment — purely registration + documentation reconciliation (Rule 11). **852 total tests**, all passing; `guardgen --check` green.
- Still **unreleased**: increments 6 (OS-process parallel work / serialized commit) and 7 (agent/session supervisor) remain before the v4.0 Graph Orchestrator is complete and runtime-wired; the headline announcement stays deferred until then.

## [v0.3.0] — 2026-06-01

This release bundles the formal-verification enforcement work (FV-PHASE3 +
FV-PHASE4, previously unreleased on `main`) together with the new
kernel-enforced context-truncation policy (M-009).

### Added — Kernel-Enforced Context-Truncation Policy (M-009)
- **`context_policy.py`** — deterministic, stdlib-only policy for context-window management. Per-region token accounting (`MemoryRegion`: HOT / COLD / WAL / CONVERSATION) over a `TokenLedger`; **HOT is pinned and structurally never evictable** (the source-of-truth guarantee).
- Three strictly-increasing threshold bands drive the action: **NONE → CHECKPOINT → EVICT-to-COLD → HALT**. `evaluate()` is a pure function — identical ledger + policy + scores always yield an identical decision and an identical ordered eviction plan.
- **Dual-POV resolution:** an optional `candidate_scores` relevance signal (ML) may only *reorder candidates within the evictable tier*; ordering, atomicity, and the HOT guarantee are owned by the deterministic policy. _LLM proposes, system decides, state persists._
- **`KernelApp.enforce_context_policy()`** — kernel-owned enforcement (not LLM discretion): persists a full safe point through the guarded `CHECKPOINTING` transition, then frees evictable regions in deterministic order (COLD partitions via `cold.evict`, WAL via `truncate`), emits conversation drop directives, and HALTs with a transfer directive when eviction cannot drop below the hard ceiling without touching HOT.
- New proposal action `truncate_context` and WAL event `CONTEXT_TRUNCATION`; the action routes through the propose → validate → commit pipeline without merging its payload into HOT.
- `context_policy` registered in `_KERNEL_MODULES`, `discover()`, and `cmd_health`. **Module count reconciled to 13 functional modules.**
- 30 new tests (`tests/test_context_policy.py`). **758 total tests**, all passing.

### Added — Runtime Enforcement of the Verified Model (FV-PHASE4)
- The state machine's `TRANSITIONS` table is now **derived** from `generated_guards.GENERATED_TRANSITIONS` (the TLA+-generated projection) instead of a hand-maintained literal — one source of truth, so the runtime can never silently drift from what TLC proved.
- `StateMachine.transition()` enforces legality through the generated `legal_transition()` predicate (non-bypassable structural guard; `force_state()` remains the only sanctioned recovery bypass). Contextual policy guards via `add_guard()` are unchanged.
- Import-time drift guard: the `State` enum and the generated state space must match exactly or import fails loud.
- `generated_guards` and `guardgen` registered in `_KERNEL_MODULES`, `discover()`, and `cmd_health` (INS-019). **Module count reconciled to 12 functional modules** (manifest dict); convention documented to close INS-003.
- 10 new enforcement/registration tests. **728 total tests**, all passing; `guardgen --check` drift gate green.

### Added — TLA+ → Python Guard Generator (FV-PHASE3)
- **`guardgen.py`** — deterministic, stdlib-only, zero-LLM generator that parses `formal/RAGKernel.tla` and emits `generated_guards.py` (transition table + per-action enabling guards). Fail-loud on any unrecognized precondition; byte-deterministic output with source SHA-256 provenance and a `--check` drift gate.
- **`generated_guards.py`** — generated artifact: `GENERATED_TRANSITIONS`, `KernelContext`, 8 per-action guards, `ACTION_GUARDS`, `legal_transition()`.

## [v0.2.7] — 2026-05-27

### Added — Conflict Auto-Categorization (ENH-005)
- **`conflict_engine.py`** — rule-based conflict classification engine. Zero dependencies, zero ML. Categorizes data conflicts by type with suggested resolution paths.
- 7 conflict categories: `TEMPORAL_DRIFT`, `SOURCE_DISAGREEMENT`, `DATA_QUALITY`, `SCHEMA_MISMATCH`, `DUPLICATE_ENTRY`, `PRIORITY_CONFLICT`, `UNCATEGORIZED`.
- Pattern-matching classifier: analyzes difference text, field names, value types, source relationships, and timestamps. Scoring-based with confidence levels (high/medium/low).
- Auto-resolution for low-risk, high-confidence conflicts: temporal drift (accept newer), duplicates (keep first), data quality (prefer valid value). Source disagreement, schema mismatch, and priority conflicts always escalate to user.
- `ConflictRecord`: full §11-compatible record with ENH-005 extensions (category, suggested_resolution, auto_resolved).
- `ConflictEngine`: stateful lifecycle manager — add, classify, resolve, load/export ledger, summary by category.
- `validate_conflict_payload()`: proposal validation for add_conflict actions.
- `KernelApp` integration: `add_conflict()`, `resolve_conflict()`, `get_conflict_summary()` methods.
- 3 new HTTP endpoints: `POST /conflicts/add`, `POST /conflicts/resolve`, `GET /conflicts/summary`.
- Proposal pipeline: `add_conflict` proposals auto-validated for required fields.
- Module registered in `discover()` and health check (12 modules total).
- 77 new tests across 9 test classes. **676 total tests**, all passing.

## [v0.2.3] — 2026-05-23

### Added — Session Logger (Universal Observability)
- **`session_logger.py`** — structured JSONL session logger for debug/patch/release cycles. Universal (not project-specific), self-contained logs interpretable by Claude without additional context.
- `SessionLogger`: open/close lifecycle, fsync guarantees, monotonic sequence, level filtering.
- Convenience methods: `state_transition()`, `io_operation()`, `rag_mutation()`, `checkpoint()`, `error()`, `warning()`, `tool_invocation()`, `validation()`, `recovery()`.
- `timed()` context manager for automatic duration measurement.
- `load_session_log()`: read back JSONL logs into structured entries.
- `summarize_session_log()`: produce LLM-friendly analysis summaries (level counts, state transitions, I/O summary, error listing).
- Module registered in `discover()` with `@rag-kernel-manifest` block.
- 53 new tests across 9 test classes. **540 total tests**, all passing.

## [v0.2.2] — 2026-05-23

### Added — Delta Checkpoints (ENH-006)
- **Delta checkpoint engine** in `persistence.py`: `DeltaOp` (RFC 6902-like ops), `DeltaCheckpoint` (base_seq + ops), `delta_compute()` (recursive dict diff), `delta_apply()` (in-place patching), `DeltaCheckpointManager` (lifecycle with configurable threshold).
- **Core invariant**: `apply(base, compute(base, current)) == current` — verified by roundtrip tests.
- **Smart routing** in `api.py`: first checkpoint after boot is always full; subsequent checkpoints use deltas; threshold (default 10) or session close triggers full. ~60% I/O reduction on typical sessions.
- 60 new tests across 8 test classes (DeltaOp, DeltaCheckpoint, ResolvePath, DeltaCompute, DeltaApply, DeltaCheckpointManager, KernelAppDeltaCheckpoint). **487 total tests**, all passing.

## [v0.2.1] — 2026-05-23

### Added — Graduated POV Enforcement (ENH-004)
- Three-tier POV mode: `STRICT` (both POVs required, blocks decisions), `ADVISORY` (POVs as internal analysis, single synthesized output), `SILENT` (POVs suppressed entirely).
- Auto-escalation: high-risk operations (state machine edits, persistence changes, concurrency modifications) automatically escalate to STRICT regardless of current mode.
- Manual override via proposal: user can force any mode at any time.
- `schemas.py`: `validate_pov_mode()`, `should_auto_escalate()`, `VALID_POV_MODES`, `AUTO_ESCALATE_OPERATIONS`, `update_pov_mode` action.
- `api.py`: `get_pov_mode()`, `set_pov_mode()`, `check_auto_escalate()`, 3 new endpoints (GET/PATCH `/config/pov_mode`, POST `/config/pov_mode/check`), `pov_mode` in status response.
- 26 new tests (16 schema + 10 API). **427 total tests**, all passing.

### Removed
- `ERROR_LOG.md` from git-tracked repo. Canonical error log lives in RAG/ (local project state, not repo content).

### Housekeeping
- Retired informal "v3.2"/"v3.3" version labels. Version scheme is now: spec/RAG = v3.1.x, Python rag_kernel = v0.x.x.

## [v0.2.0] — 2026-05-22

### Added — Zero-Touch Bootstrap & Capability Self-Discovery

**Paradigm shift: from semi-autonomous LLM-driven to fully autonomous OS-level deterministic Python backbone.** The LLM's role is now task assignor, results checker, and orchestrator only. All state management, validation, bootstrapping, and persistence run as OS-level Python scripts consuming zero LLM tokens.

- **`spec_parser.py`** (610 lines) — deterministic Markdown→RAG parser. Extracts machine-readable `rag-config` JSON blocks from the init prompt specification and produces RAG_MASTER.json + RAG_COLD.json. Zero LLM involvement.
- **`rag_kernel init --spec <path.md>`** — single-command RAG bootstrap from spec. Parses v3.1.8 structured blocks, validates schema, writes atomically.
- **`rag_kernel configure --rag <path> --context <path>`** — merges project-specific context (JSON or Markdown with rag-config blocks) into an existing RAG. Atomic deep-merge.
- **Capability self-discovery** — `rag_kernel.discover()` returns the full capability registry: 9 modules, 9 capabilities, invocation rules, CLI commands, critical module flags.
- **`@rag-kernel-manifest` docstring blocks** — every module carries structured JSON metadata (capabilities, exports, use_when, never_bypass) that `discover()` extracts at session start.
- **Invocation protocol** — formal rules defining when the LLM MUST use rag_kernel (state transitions, proposals, checkpoints, COLD, split-brain, RAG init) vs. when direct file I/O is acceptable (simple reads, status checks, error logs).
- 64 new tests for spec_parser (TestDeepMerge, TestVoidRAG, TestDataStructures, TestParseString, TestErrorHandling, TestParseFile, TestVersionExtraction, TestValidation, TestWrite, TestReport, TestMergeOrdering, TestFenceEdgeCases).
- **401 total tests** across 9 test files (up from 337).
- Package version bumped to 0.2.0.

## [v3.1.8] — 2026-05-22

### Added — Machine-Parseable Specification
- 25 `rag-config` fenced JSON blocks embedded throughout the specification alongside human-readable prose. Dual-audience document: humans read the prose, `spec_parser.py` reads the structured blocks.
- Target format for `rag_kernel init --spec` zero-touch bootstrap.
- All behavioral rules, state machine definitions, schema templates, and configuration defaults are now extractable deterministically.

## [v3.1.7] — 2026-05-20

### Added — RAG/Memory Reconciliation Release
- **§42 File Sync Protocol** — single-source editing, bidirectional git sync, mandatory `git add -A` staging.
- **§43 Context Window Management** — compression/compaction forbidden, 70% context halt-and-checkpoint protocol.
- **§44 Resolved Item Protocol** — mandatory 4-step resolution across all persistent stores, stale reminder prevention.
- **§45 Garbage Collector Protocol** — session-start cleanup, project-scoped only, standard targets table.
- **§46 RAG as Single Source of Truth** — portability guarantee: project transferable to any LLM platform via init prompt OR RAG_MASTER.json. Reconciliation procedure for release synchronization.
- §41 known-issues registry expanded: wsl-exec `&&` stripping, wsl-exec `~` non-expansion.

### Changed
- **All behavioral rules consolidated into RAG_MASTER.json** `operating_protocol`. Previously scattered across platform-specific memory files (Cowork `feedback_*.md`), now mirrored in both the RAG and the init prompt. RAG_MASTER.json is now truly self-contained — the only file needed to transfer a project to any LLM platform.
- 48 sections total (§0–§46 + §3a). Schema 5.3.

### Security
- `CLEANUP.ps1` updated: Cowork session data cleanup now enumerates individual session folders with age-based safety (≤3 days = skip). No longer offers to delete entire session storage as a unit.

## [Formal Verification — Phase 2] — 2026-05-19

### Added — Liveness Verification (TLA+ Phase 2)
- **`WALCompaction` action** added to `formal/RAGKernel.tla`, modeling real-world WAL truncation so the finite-bound liveness check no longer produces false counterexamples.
- TLC re-verification: **389,522 states explored (168,520 distinct), depth 19** — all **8 safety invariants + 3 liveness properties** (`EventualProgress`, `EventualTermination`, `ProposalEventuallyResolved`) pass with **zero violations**.
- Two genuine liveness bugs found and fixed: (1) BOOTING↔RECOVERY direct-transition livelock (fixed via strong fairness on `RecoveryComplete(READY)`); (2) **crash-at-full-WAL deadlock** (fixed by allowing WAL compaction during recovery).
- `formal/TLC_RESULTS.md` updated with full Phase 2 results. Commit `ddd7af6`.

## [v0.1.1] — 2026-05-16

### Added
- **Formal verification with TLA+ and TLC model checker (Phase 1 — safety)** — 136,193 states explored, 84,261 distinct, all 8 safety invariants verified with zero violations. Same technique used by Amazon for AWS infrastructure. (Liveness verified later in Phase 2 — see entry above.)
- `formal/TLC_RESULTS.md` — full verification report.
- GitHub Discussions tab launched.

### Fixed
- `formal/RAGKernel.cfg` — fixed INIT/NEXT+SPEC conflict that prevented TLC from running; added CHECK_DEADLOCK FALSE for terminal CLOSING state.
- `formal/RAGKernel.tla` — strengthened fairness model: SF (strong fairness) on RecoveryComplete(READY) and WF on DirectTransition(READY) to prevent theoretical BOOTING-RECOVERY livelock.
- `.gitignore` — added TLC generated artifacts (states/, TTrace files).

## [v0.1.0] — 2026-05-14

### Added
- **Runtime Bridge** — 8 Python modules implementing ENFORCED mode: `state_machine.py`, `persistence.py`, `cold_manager.py`, `concurrency.py`, `api.py`, `mcp_transport.py`, `schemas.py`, `__main__.py`.
- 337 unit tests across 8 test files, all passing.
- 5811 lines of source + tests.
- HTTP mode (`python -m rag_kernel serve`) for GPT Custom Actions or any HTTP client.
- MCP mode (`python -m rag_kernel mcp`) for Claude Desktop.
- Hard runtime validation of every state transition in ENFORCED mode.

## [v3.1.6] — 2026-05-14

### Added
- Pre-flight gate enforcement (§41) — mandatory written declaration before any 2+ tool sequence.
- Known-issues registry for tool/environment constraints.
- wsl-exec in tool hierarchy as primary shell MCP.
- 43 sections total (new §39–§43).

### Fixed
- §6 patched: file creation boundary + deletion guard.
- §21 patched: hard 2-strike circuit breaker.
- §26 patched: credential safety + git guards.

## [v3.1.5] — 2026-05-14

### Added
- Error log discipline (§39) — errors logged as they occur, blocking prerequisite before next task.
- Task-level tool verification (§40) — verify all required tools before starting work.
- Formal verification Phase 1: TLA+ specification of state machine (555 lines, 8 safety invariants, 3 liveness properties).

## [v3.1.4] — 2026-05-10

### Added
- **Runtime Architecture Design Document** (`docs/v3.2_ARCHITECTURE_DESIGN.md`) — complete design for the OS-level runtime bridge: localhost HTTP API, MCP server transport, state machine engine, persistence engine (atomic writes, WAL with fsync, hash verification), COLD partition manager, concurrency guard with split-brain detection. 13 sections, implementation-ready.
- Optional POV configuration at session-zero — users can skip multi-perspective validation entirely (`pov_mandate.mode: "disabled"`).
- Runtime POV redefinition without reinitialization — POVs can be changed mid-session, applying prospectively only.
- Session-zero boot scan offer — scan `root_project` immediately after RAG creation.
- Post-scan mandatory summary (§10c-post) — all files listed with paths, tiers, ingestion status.
- Archive detection during boot scan — `.zip`/`.rar`/`.7z` cataloged with extraction options and token cost warning.
- Conversation search limitation documented — `conversation_search`/`recent_chats` cannot recover truncated active session content.
- Platform persistence constraints documented — atomic writes advisory-only on GPT Web.

### Fixed
- Version strings in §32/§33 HOT/COLD templates updated from 3.1.3 to 3.1.4.
- `init_prompt` filename in templates corrected from `v3.1.3.md` to `v3.1.4.md`.
- Hash placeholder contradiction resolved — empty `state_hash` now treated as "not yet computed" with boot-time skip and first-checkpoint compute.
- "Degraded-but-functional" language in §3a replaced with "constrained-but-fully-enforced mode" (aligns with v3.1.1 architectural rejection of degraded modes).
- §37 scope reference updated to v3.1.4.

### Validated (no changes needed)
- §10c-post confirmed using MUST (not SHOULD) for post-scan summary.
- §3a conversation search limitation confirmed in prose.
- §37 GPT Web atomic write advisory confirmed present.

## [v3.1.3] — 2026-05-04

### Added
- Tool-to-filesystem mapping table and active health check at boot (§3).
- §3a Tool Fallback Chain — ordered fallback for read/write/list/copy with loop detection.
- COLD partitioning architecture — 4-domain split (sessions, inventory, conflicts, evidence) with sub-partitioning for partitions exceeding 200KB.
- Conflict cross-validation step 6a in ingestion pipeline (§10).
- Multi-account sharing protocol with session identity and write tagging (§27).
- Cross-platform interoperability guidance (§37).

## [v3.1.2] — 2026-05-03

### Added
- Patch queue system for incremental spec updates.
- 5 patches applied from v3.1.1 regression audit.

## [v3.1.1] — 2026-05-02

### Fixed
- Rejected v3.0.0 "degraded read-only" fallback as architectural regression.
- Restored prompt-only autonomy guarantee as non-negotiable requirement.

## [v3.0.0] — 2026-04-28

### Added
- Initial public specification.
- Three-layer architecture: LLM → Policy Layer → Runtime Kernel → Filesystem.
- HOT/COLD RAG memory tiers.
- Deterministic state machine: BOOT → INGEST → VALIDATE → COMMIT → DONE.
- JSON proposal/validation/commit model.
- Atomic writes with WAL, crash recovery, JSONL audit trail.

---

## Development Status

**Current:** Spec v3.2.0 (51 sections) and rag_kernel v0.3.0 (13 modules, 758 tests). Zero-touch bootstrap, capability self-discovery, graduated POV, delta checkpoints, session logger, conflict auto-categorization (ENH-005), the formally-verified guard generator now enforced at runtime (FV-PHASE3 + FV-PHASE4), and the kernel-enforced context-truncation policy (M-009) shipped. Formal verification complete through Phase 2: 389,522 states (168,520 distinct), 8 safety + 3 liveness invariants, 0 violations.

**Next:** the v4.0 Graph Orchestrator (DAG execution, dependency tracking, checkpoint-per-node, rollback).

**Repository:** [github.com/arcadamarket/rag-runtime-kernel](https://github.com/arcadamarket/rag-runtime-kernel)
**Developer:** Artem Pakhol