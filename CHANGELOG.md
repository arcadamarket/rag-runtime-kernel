# Changelog

All notable changes to the RAG Runtime Kernel specification and tooling.

## [v3.2.1] — 2026-05-16

### Added
- **Formal verification with TLA+ and TLC model checker** — 136,193 states explored, 84,261 distinct, all 8 safety invariants verified with zero violations. Same technique used by Amazon for AWS infrastructure.
- `formal/TLC_RESULTS.md` — full verification report.
- GitHub Discussions tab launched with v3.2 announcement.

### Fixed
- `formal/RAGKernel.cfg` — fixed INIT/NEXT+SPEC conflict that prevented TLC from running; added CHECK_DEADLOCK FALSE for terminal CLOSING state.
- `formal/RAGKernel.tla` — strengthened fairness model: SF (strong fairness) on RecoveryComplete(READY) and WF on DirectTransition(READY) to prevent theoretical BOOTING-RECOVERY livelock.
- `.gitignore` — added TLC generated artifacts (states/, TTrace files).

## [v3.2.0] — 2026-05-14

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
- **v3.2 Architecture Design Document** (`docs/v3.2_ARCHITECTURE_DESIGN.md`) — complete design for the OS-level runtime bridge: localhost HTTP API, MCP server transport, state machine engine, persistence engine (atomic writes, WAL with fsync, hash verification), COLD partition manager, concurrency guard with split-brain detection. 13 sections, implementation-ready.
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

**Current:** v3.1.6 specification (43 sections) and v3.2 Runtime Bridge (8 modules, 337 tests, 5811 lines) both released and shipped. Formal verification Phase 1 complete (TLA+ spec).

**Next:** v3.3 — UX improvements (graduated POV, conflict auto-categorization, delta checkpoints).

**Repository:** [github.com/arcadamarket/rag-runtime-kernel](https://github.com/arcadamarket/rag-runtime-kernel)
**Developer:** Artem Pakhol