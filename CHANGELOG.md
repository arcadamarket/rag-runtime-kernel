# Changelog

All notable changes to the RAG Runtime Kernel specification and tooling.

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

## [v0.1.1] — 2026-05-16

### Added
- **Formal verification with TLA+ and TLC model checker** — 136,193 states explored, 84,261 distinct, all 8 safety invariants verified with zero violations. Same technique used by Amazon for AWS infrastructure.
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

**Current:** Spec v3.1.8 (machine-parseable, 25 rag-config blocks) and rag_kernel v0.2.3 (10 modules, 540 tests). Zero-touch bootstrap, capability self-discovery, graduated POV, delta checkpoints, and session logger shipped. Formal verification complete: 389K states, 8 safety + 3 liveness invariants, 0 violations.

**Next:** Conflict auto-categorization (ENH-005).

**Repository:** [github.com/arcadamarket/rag-runtime-kernel](https://github.com/arcadamarket/rag-runtime-kernel)
**Developer:** Artem Pakhol