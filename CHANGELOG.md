# Changelog

All notable changes to the RAG Runtime Kernel specification and tooling.

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

**Current:** v3.1.4 specification complete and committed. Unit test suites written (42 Claude Desktop + 43 GPT Web). v3.2 architecture designed.

**Next:** v3.2 OS-level runtime bridge implementation — Python 3.10+, zero external dependencies, localhost HTTP + MCP transport.

**Repository:** [github.com/arcadamarket/rag-runtime-kernel](https://github.com/arcadamarket/rag-runtime-kernel)
**Developer:** Artem Pakhol