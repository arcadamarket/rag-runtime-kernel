# COMPREHENSIVE TODO PLAN — RAG Runtime Kernel v3.1.3

> Consolidated from: IMPLEMENTATION_REPORT_v3.1.3.md, GPT Web test analysis (S6), all session findings S1–S6, and user directives.
> Generated: 2026-05-04 | Session: S6

---

## 1. Master Matrix

| ID | Category | Issue Name | Description | Impact | Type | Exec Order | Target | Dependencies | Action / Fix Strategy |
|---|---|---|---|---|---|---|---|---|---|
| M-001 | Bootstrap | POV Config Hard Block | Step 5 blocks session-zero if user doesn't define POVs. No skip path. | Critical | Defect | 1 | v3.1.4 | None | Make POV optional. Default `pov_mandate: {count:0, mode:"disabled"}`. Allow skip without breaking flow. |
| M-002 | Boot | Boot Scan Missing at Session-Zero | Boot scan offer (§19 2.10) only triggers at session 2+, not during session-zero. | High | Defect | 2 | v3.1.4 | None | Add boot scan offer after RAG creation + pointer block confirmation in §31. |
| M-003 | Ingestion | Archive Post-Scan Prompt Missing | After scan, archives (.zip) are cataloged but user is never offered extraction. | Medium | Defect | 3 | v3.1.4 | None | Detect archives post-scan, prompt user with extract option + token cost warning. |
| M-004 | Onboarding | Quick Start Flow Incorrect | README told users to paste init prompt into Instructions field (size-limited). Must go into session. | Critical | Defect | 4 | v3.1.4 | None | Rewrite Quick Start: drop file into session, not Instructions. Platform-specific steps. **DONE in S6.** |
| M-005 | Legal | License Absent / Wrong | Repo had MIT license; project requires AGPL-3.0 to prevent unauthorized derivatives. | Critical | Defect | 5 | v3.1.4 | None | Replace with AGPL-3.0 full text. Update README. **DONE in S6.** |
| M-006 | Positioning | Benchmark Not Prominent | Benchmark existed in docs/ but README didn't surface it. Public doesn't see value prop. | High | Defect | 6 | v3.1.4 | None | Add head-to-head summary table + differentiators to README. **DONE in S6.** |
| M-007 | GitHub | Issue Templates Missing | No structured way for public to report bugs or request features. | High | Defect | 7 | v3.1.4 | None | Create .github/ISSUE_TEMPLATE/ with bug_report.md + feature_request.md. **DONE in S6.** |
| M-008 | GitHub | CONTRIBUTING.md Missing | No contribution policy documented. | Medium | Defect | 8 | v3.1.4 | None | Create CONTRIBUTING.md with issues-only policy. **DONE in S6.** |
| M-009 | Spec | Context Truncation Policy Undefined | No defined behavior when platform truncates context mid-session. | High | Weakness | 9 | v3.1.4 | None | Add to §15 or new section: emergency checkpoint before truncation. Persist evicted content to WAL. |
| M-010 | Spec | Conversation Search Limitation Undocumented | `conversation_search` indexes saved past chats only, not active conversation. | Low | Weakness | 10 | v3.1.4 | None | Document in §3a tool limitations table. |
| M-011 | Spec | GPT Web Atomic Write Disclaimer Missing | §37 doesn't explicitly state GPT Web atomic writes are advisory-only. | Low | Weakness | 11 | v3.1.4 | None | Add note to §37 cross-platform table. |
| M-012 | Security | GitHub PAT Exposed | Previous PAT was live and embedded in git remote URL. | Critical | Defect | 12 | v3.1.4 | None | Revoke old PAT, configure new PAT, remove from any committed files. **Old PAT revoked per user.** |
| M-013 | Config | Chrome Extension Missing github.com | Chrome MCP rejects github.com — not in approved sites list. | Medium | Limitation | 13 | v3.1.4 | User action | User must add github.com to Chrome extension approved sites. Document in README. |
| M-014 | Platform | Token Estimation Unreliable on GPT Web | No real token counter; 75%/80% thresholds are estimates. | Medium | Weakness | 14 | v3.2 | Platform API | Conservative bias (trigger early). No fix without platform support. |
| M-015 | Platform | COLD Lazy-Loading Simulated on GPT Web | Cannot load partitions on demand; user must provide manually. | Medium | Weakness | 15 | v3.2 | M-023 | Partition manifest in HOT with pre-fetch hints (ENH-003). Real fix: v3.2 filesystem bridge. |
| M-016 | Integrity | Split-Brain Risk in Partitioned COLD | Session A modifies partition 1, Session B modifies partition 2 — no global coordination. | High | Weakness | 16 | v3.2 | M-023 | Enforce checkpoint_seq match across all partitions at boot. Real fix: v3.2 centralized bridge. |
| M-017 | Reliability | Recovery Depends on User Discipline | WAL/bak/COLD reconstruction requires user to have saved files. | High | Weakness | 17 | v3.2 | M-023 | Real fix: v3.2 automated backup. Interim: boot warnings if expected files missing. |
| M-018 | Security | Adversarial Poisoning Detection is Heuristic | No formal verification of ingested data quality. Subtle poisoning may evade. | Low | Weakness | 18 | v3.3 | M-025 | Formal verification track. Interim: conflict ledger + user decision gate sufficient. |
| M-019 | Architecture | Concurrency is Detect-and-Halt Only | Multi-session writes detected and stopped, never auto-merged. | Low | Weakness | 19 | v4.0 | M-024 | By design (safety > convenience). Auto-merge in v4.0 graph orchestrator. |
| M-020 | Platform | No Atomic Writes on GPT Web | All writes simulated via download/upload. | High | Limitation | 20 | v3.2 | M-023 | v3.2 filesystem bridge daemon. |
| M-021 | Platform | No Background Persistence on GPT Web | Every state change requires explicit user download. | High | Limitation | 21 | v3.2 | M-023 | v3.2 background daemon. |
| M-022 | Platform | WAL Advisory-Only on GPT Web | WAL entries generated but not written to disk automatically. | Medium | Limitation | 22 | v3.2 | M-023 | v3.2 real WAL with fsync. |
| M-023 | Architecture | OS-Level Runtime Not Built | v3.2 filesystem bridge daemon is planned but not started. | High | Enhancement | 23 | v3.2 | None | Python 3.10+, local HTTP/MCP server, localhost-only. Single pip install. |
| M-024 | Architecture | Graph Orchestrator Not Built | v4.0 DAG execution engine is roadmap only. | Medium | Enhancement | 24 | v4.0 | M-023, M-025 | LangGraph-class engine with dependency tracking, parallel execution, rollback. |
| M-025 | Architecture | Formal State Transition Verification | State machine transitions are spec-defined but LLM-enforced (honor system). | High | Enhancement | 25 | v3.2 | None | TLA+/Alloy model → auto-generate transition guards → embed in Python runtime. |
| M-026 | Platform | MCP Bridge for GPT Web | GPT Web has no filesystem access. | High | Enhancement | 26 | v3.2 | M-023 | Local HTTP API + GPT Actions. User runs `python -m rag_kernel serve`. |
| M-027 | UX | Graduated POV Enforcement | POV is all-or-nothing. No "advisory" mode. | Medium | Enhancement | 27 | v3.3 | M-001 | Add advisory mode: POVs generate analysis but don't block decisions. |
| M-028 | UX | Conflict Auto-Categorization | All conflicts require manual user decision. | Medium | Enhancement | 28 | v3.3 | None | Auto-classify by type (temporal drift, source disagreement, data quality). |
| M-029 | Performance | Delta-Only Checkpoints | Full HOT state saved at each checkpoint. | Low | Enhancement | 29 | v3.3 | None | Save only changed fields since last checkpoint. |
| M-030 | Integrity | Hash Verification at Boot | SHA-256 hashes computed but not auto-verified cross-session on GPT Web. | Medium | Enhancement | 30 | v3.2 | M-023 | Boot step: compute hash, compare vs stored, alert on mismatch. |
| M-031 | Ingestion | Archive Extraction with Depth Control | Archives cataloged but extraction is binary (all or nothing). | Low | Enhancement | 31 | v3.3 | M-003 | Configurable depth: top-level only, recursive, selective. |
| M-032 | Platform | Cross-Platform Partition Sync is Manual | No built-in sync between Claude and GPT instances. | Medium | Limitation | 32 | v3.2 | M-023 | v3.2 centralized filesystem bridge. |
| M-033 | Platform | Context Window Ceiling ~128K | Spec ~16K + COLD loads + conversation compress working space. | Medium | Limitation | 33 | v3.2 | M-023 | v3.2 COLD partition manager in system RAM. |
| M-034 | Validation | Unit Tests Not Yet Executed | 65 tests written (32 Claude, 33 GPT) but not run against fresh sessions. | High | Defect | 34 | v3.1.4 | M-001 thru M-008 | Run in fresh sessions after all v3.1.4 fixes committed. |
| M-035 | GitHub | Repo Still Private | Cannot collect public issue reports while private. | High | Defect | 35 | v3.1.4 | M-005, M-012 | Flip to public after license + PAT finalized. |
| M-036 | Onboarding | No Cowork/Claude Code Guidance | README didn't explain how to use with Cowork or Claude Code. | Medium | Defect | 36 | v3.1.4 | None | Add dedicated sections for each platform. **DONE in S6.** |
| M-037 | Onboarding | RAG Kernel Positioning Unclear | README didn't clearly explain the system wraps around projects, not replaces them. | Medium | Defect | 37 | v3.1.4 | None | Add positioning section explaining memory + orchestration layer concept. **DONE in S6.** |
| M-038 | Spec | v3.2 Runtime Bridge Design | §37 references v3.2 direction but no design exists. | Medium | Enhancement | 38 | v3.2 | None | Architecture design session required. |
| M-039 | Reliability | Emergency Checkpoint Before Audit | If tab closes, state may be lost before §17 audit completes. | Medium | Enhancement | 39 | v3.3 | None | Reverse order: save state FIRST, then audit. |
| M-040 | UX | Conflict Accumulation Warning | Conflicts grow silently, consuming tokens at every COLD load. | Low | Enhancement | 40 | v3.3 | None | Boot warning: "X unresolved conflicts consuming ~Y tokens." |
| M-041 | UX | BOOTING Stall on GPT Web | Tool verification blocks waiting for user confirmation of tool absence. | Low | Enhancement | 41 | v3.3 | None | Auto-detect tool absence, skip with logged gap, proceed to fallback chain. |
| M-042 | Documentation | Disclaimer Section Missing | README had no disclaimer about limitations and self-enforcement nature. | Medium | Defect | 42 | v3.1.4 | None | Add disclaimer section. **DONE in S6.** |

---

## 2. Defect List (Broken Behaviors)

- **DEF-001 (M-001):** POV configuration is a hard block at session-zero bootstrap — no skip path
- **DEF-002 (M-002):** Boot scan offer triggers only at session 2+, missing from session-zero
- **DEF-003 (M-003):** Archive post-scan extraction prompt missing — archives stay opaque
- **DEF-004 (M-004):** Quick Start directed users to paste into Instructions (size-limited) — **FIXED S6**
- **DEF-005 (M-005):** LICENSE was MIT, not AGPL-3.0 — **FIXED S6**
- **DEF-006 (M-006):** Benchmark buried in docs/, not surfaced in README — **FIXED S6**
- **DEF-007 (M-007):** No issue templates for public bug/feature reports — **FIXED S6**
- **DEF-008 (M-008):** No CONTRIBUTING.md — **FIXED S6**
- **DEF-009 (M-012):** GitHub PAT exposed — **OLD PAT REVOKED**
- **DEF-010 (M-034):** Unit tests written but never executed
- **DEF-011 (M-035):** Repo still private — cannot collect public feedback
- **DEF-012 (M-036):** No Cowork/Claude Code usage guidance — **FIXED S6**
- **DEF-013 (M-037):** RAG Kernel positioning unclear — **FIXED S6**
- **DEF-014 (M-042):** Disclaimer section missing — **FIXED S6**

---

## 3. Weakness / Risk List

- **WEAK-001 (M-014):** Token estimation unreliable on GPT Web — no real counter
- **WEAK-002 (M-015):** COLD lazy-loading simulated on GPT Web — user provides manually
- **WEAK-003 (M-016):** Split-brain risk with partitioned COLD across sessions
- **WEAK-004 (M-017):** Recovery quality depends on user file discipline
- **WEAK-005 (M-018):** Adversarial poisoning detection is heuristic, not formal
- **WEAK-006 (M-019):** Concurrency is detect-and-halt only, no auto-merge
- **WEAK-007 (M-009):** Context truncation behavior undefined — potential silent state loss
- **WEAK-008 (M-010):** Conversation search limitation undocumented
- **WEAK-009 (M-011):** GPT Web atomic write advisory nature undocumented

---

## 4. Troubleshooting Playbook (User-Resolvable)

| ID | Issue | Cause | Fix |
|---|---|---|---|
| TS-001 | RAG not loading at session start (GPT Web) | Pointer block missing or RAG file not uploaded | Verify pointer block in Project Instructions. Upload RAG_MASTER.json at session start. |
| TS-002 | State machine stuck in BOOTING | Tool verification blocks on GPT Web | Confirm "no filesystem access — use user-assisted mode" when prompted. |
| TS-003 | Checkpoint fails / no file downloaded | GPT Web requires manual download | Download generated JSON immediately when system says "checkpoint complete." |
| TS-004 | COLD partition not loading | Partition file not uploaded | Upload the specific partition file when system requests it. |
| TS-005 | Conflict ledger growing unbounded | User keeps deferring resolution | Periodically review and resolve conflicts. Unresolved conflicts cost tokens. |
| TS-006 | Session closes without audit | Tab closed or session timeout | Always use explicit "close session" to trigger §17 audit. |
| TS-007 | Cross-platform RAG mismatch | Files not synced between Claude and GPT | Copy latest RAG files to shared project folder after every session. |

---

## 5. Known Limitations (Non-Resolvable by Users)

- **LIM-001 (M-020):** No atomic writes on GPT Web — platform constraint → v3.2
- **LIM-002 (M-021):** No background persistence on GPT Web → v3.2
- **LIM-003 (M-022):** WAL advisory-only on GPT Web → v3.2
- **LIM-004 (M-032):** Cross-platform partition sync is manual → v3.2
- **LIM-005 (M-033):** Context window ceiling ~128K → v3.2 COLD manager
- **LIM-006 (M-013):** Chrome extension requires manual github.com approval → user config

---

## 6. Enhancement Opportunities

- **ENH-001 (M-023):** OS-Level Runtime — filesystem bridge daemon (v3.2)
- **ENH-002 (M-025):** Formal state transition verification — TLA+/Alloy (v3.2)
- **ENH-003 (M-026):** MCP bridge for GPT Web — local HTTP API (v3.2)
- **ENH-004 (M-027):** Graduated POV enforcement — advisory mode (v3.3)
- **ENH-005 (M-028):** Conflict auto-categorization (v3.3)
- **ENH-006 (M-029):** Delta-only checkpoints (v3.3)
- **ENH-007 (M-030):** Hash verification at boot (v3.2)
- **ENH-008 (M-031):** Archive extraction with depth control (v3.3)
- **ENH-009 (M-024):** Graph Orchestrator — DAG execution engine (v4.0)
- **ENH-010 (M-038):** v3.2 runtime bridge architecture design (v3.2)
- **ENH-011 (M-039):** Emergency checkpoint before audit (v3.3)
- **ENH-012 (M-040):** Conflict accumulation boot warning (v3.3)
- **ENH-013 (M-041):** Auto-skip tool verification on GPT Web (v3.3)

---

## 7. Execution Phases

### Phase 1 — v3.1.4: Stabilization (Unblock UX + Correctness)

**Scope:** Spec patches + repo finalization. No new architecture.

| Order | ID | Action |
|---|---|---|
| 1 | M-001 | Patch §31: make POV optional, add skip path |
| 2 | M-002 | Patch §19/§31: add boot scan offer to session-zero |
| 3 | M-003 | Patch §10c: add post-scan archive extraction prompt with token warning |
| 4 | M-009 | Patch §15: define context truncation emergency checkpoint behavior |
| 5 | M-010 | Patch §3a: document conversation_search limitation |
| 6 | M-011 | Patch §37: add GPT Web atomic write advisory note |
| 7 | M-034 | Execute unit tests in fresh Claude Desktop + GPT Web sessions |
| 8 | M-035 | Make repo public (all blockers resolved) |

**Deliverable:** INIT_UNIVERSAL_RUNTIME_KERNEL_v3.1.4.md + git push

### Phase 2 — v3.2.0: Core Architecture (Runtime + Persistence)

**Scope:** Build the OS-level runtime. Eliminate platform-dependent limitations.

| Order | ID | Action |
|---|---|---|
| 1 | M-038 | Architecture design for v3.2 runtime bridge |
| 2 | M-023 | Build filesystem bridge daemon (Python, localhost HTTP/MCP) |
| 3 | M-023 | Build real WAL writer with fsync |
| 4 | M-023 | Build atomic write engine (write-tmp → verify → rename + .bak) |
| 5 | M-030 | Implement hash verification at boot |
| 6 | M-025 | Phase 1-2 of formal verification (TLA+ model + proof) |
| 7 | M-026 | Build MCP bridge / GPT Actions endpoint |
| 8 | M-023 | Build COLD partition manager (RAM-based, on-demand serving) |

**Dependencies:** None external. Python 3.10+ stdlib only.

### Phase 3 — v3.3.0: Reliability + UX

**Scope:** Reduce friction, improve autonomous-mode robustness.

| Order | ID | Action |
|---|---|---|
| 1 | M-027 | Implement graduated POV enforcement (advisory mode) |
| 2 | M-028 | Implement conflict auto-categorization |
| 3 | M-029 | Implement delta-only checkpoints |
| 4 | M-039 | Reverse checkpoint/audit order (save first, audit second) |
| 5 | M-040 | Add conflict accumulation boot warning |
| 6 | M-041 | Auto-skip tool verification when tools clearly absent |
| 7 | M-031 | Archive extraction with depth control |
| 8 | M-025 | Phase 3-4 of formal verification (code generation + integration) |

**Dependencies:** v3.2 runtime for ENH-007 integration.

### Phase 4 — v4.0.0: Graph Orchestrator

**Scope:** Multi-step workflow orchestration.

| Order | ID | Action |
|---|---|---|
| 1 | M-024 | DAG execution engine design |
| 2 | M-024 | Dependency tracking + parallel execution |
| 3 | M-024 | Checkpoint-per-node + rollback support |
| 4 | M-019 | Auto-merge for concurrent sessions (replacing detect-and-halt) |

**Dependencies:** v3.2 runtime + formal verification complete.
