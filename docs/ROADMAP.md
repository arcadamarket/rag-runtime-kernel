# Development Roadmap — RAG Runtime Kernel

> Organized by release target. Each item references its source finding from test analysis.

---

## v3.1.4 — Patch Release (Error Elimination)

Target: Specification-level fixes. No new features. Pure correctness.

### Defect Fixes

| ID | Fix | Source | Spec Sections |
|---|---|---|---|
| DEF-001 | Make POV configuration optional at bootstrap. Default to `pov_mandate: {count: 0, mode: "disabled"}` when user skips. System proceeds without POV enforcement. Users can enable POVs later via RAG edit. | Step 5 hard block | §31, §16 |
| DEF-002 | Add boot scan offer to session-zero flow. After RAG creation + pointer block confirmation, system MUST offer: "Run boot scan? This will scan root_project, build inventory, and extract knowledge into COLD." | Missing session-zero scan | §19, §31 |
| DEF-003 | Add post-scan archive detection prompt. After scan completion, detect `.zip`/`.tar`/`.7z` files in scanned root. Prompt user with extraction option + token cost warning. | Archive handling gap | §10c |

### Spec Clarifications

| Item | Change | Section |
|---|---|---|
| Context truncation policy | Define explicit behavior when conversation context is truncated by platform (not by token pressure halt). Currently undefined — system may lose in-flight state without checkpoint. Add: "If context truncation is detected or suspected, system MUST attempt emergency checkpoint before any further operations." | §15 (new clause) |
| Conversation search limitation | Document that conversation_search tool indexes saved past chats only — cannot recover truncated content from the active conversation. Add to §3a tool limitations table. | §3a |
| GPT Web atomic write disclaimer | Add explicit note in §37 cross-platform table: "GPT Web: atomic writes are advisory only. All persistence requires explicit user download." | §37 |

---

## v3.2 — OS-Level Runtime (Planned)

Target: Eliminate platform-dependent limitations. Single background process serves any LLM.

### Core Components

| Component | Purpose | Addresses |
|---|---|---|
| Filesystem bridge daemon | Background process exposing read/write/verify operations via local HTTP or MCP protocol | LIM-001, LIM-004, LIM-005 |
| COLD partition manager | Loads partitions into system RAM, serves on-demand to LLM context | LIM-003, WEAK-002, ENH-003 |
| WAL writer | Real filesystem WAL with fsync guarantees | LIM-005, WEAK-004 |
| Atomic write engine | Write-tmp → verify → rename pattern with .bak rotation | LIM-001 |
| Hash verification at boot | Compute SHA-256 of loaded files, compare against stored hashes, alert on mismatch | ENH-007 |
| Cross-platform sync bridge | Single filesystem source of truth accessible by Claude Desktop, GPT, and any MCP-capable client | LIM-006, WEAK-003 |

### Implementation Notes

- Stack: Python 3.10+, zero external deps beyond stdlib
- Protocol: Local HTTP API or MCP server (compatible with Claude Desktop, Cursor, etc.)
- Security: Localhost-only binding, no network exposure
- Packaging: Single `pip install` or standalone binary

---

## v3.3 — Robustness & UX (Planned)

Target: Reduce user friction, improve autonomous-mode reliability.

### Enhancements

| ID | Enhancement | Priority | Source |
|---|---|---|---|
| ENH-004 | Graduated POV enforcement: add "advisory" mode (POVs generate analysis but don't block decisions) | HIGH | DEF-001, user onboarding friction |
| ENH-005 | Conflict auto-categorization: classify by type (temporal drift, source disagreement, data quality) with suggested resolution | MEDIUM | WEAK-005, user decision fatigue |
| ENH-006 | Delta-only checkpoints: save only changed fields since last checkpoint | MEDIUM | Token cost reduction |
| ENH-008 | Archive extraction with depth control: top-level only, recursive, or selective | LOW | DEF-003 |

### Troubleshooting Improvements

| Issue | Current State | Planned Fix |
|---|---|---|
| TS-002 (BOOTING stall) | User must manually confirm tool availability | Auto-detect tool absence, skip verification with logged gap, proceed to fallback chain without blocking |
| TS-005 (Conflict accumulation) | Conflicts grow silently | Add conflict count warning at boot: "X unresolved conflicts consuming ~Y tokens. Review recommended." |
| TS-006 (Session close without audit) | Lost findings if tab closes | Emergency checkpoint before audit — save state first, then audit. Reverses current order. |

---

## v4.0 — Graph Orchestrator (Roadmap)

Target: Multi-step workflow orchestration with dependency tracking.

| Component | Description |
|---|---|
| DAG execution engine | LangGraph-class directed graph for multi-step workflows |
| Dependency tracking | Task B waits for Task A completion before starting |
| Parallel execution | Independent tasks run concurrently where safe |
| Checkpoint-per-node | Each graph node checkpoints independently |
| Rollback support | Failed node rolls back to last valid state without corrupting siblings |

### Prerequisites
- v3.2 OS-Level Runtime (filesystem bridge required)
- Formal state transition verification (ENH-001) — must be complete before graph nodes can enforce transition guards

---

## Formal Verification Track (Research)

Target: Provably correct state transitions. Eliminates spec-violation class entirely.

| Phase | Work | Status |
|---|---|---|
| 1 — Model | Express state machine as TLA+ or Alloy specification | Not started |
| 2 — Verify | Prove all transitions satisfy safety invariants (no silent state skip, no unguarded mutation) | Not started |
| 3 — Generate | Auto-generate transition guard code from formal model | Not started |
| 4 — Integrate | Embed generated guards into Python runtime kernel (ENFORCED mode) | Blocked on v3.2 |

---

## MCP Layer for GPT Web (Research)

Target: Give GPT Web real filesystem access without requiring platform changes.

| Approach | Feasibility | Notes |
|---|---|---|
| Local MCP server + browser extension bridge | MEDIUM | Extension relays MCP calls from GPT Web to local server. Privacy concerns. Browser extension review process. |
| Local HTTP API + GPT Actions | HIGH | GPT custom actions call localhost API. Requires user to configure GPT Actions. Simplest path. |
| Tunneled MCP via cloud relay | LOW | Adds network dependency, latency, security surface. Against zero-deps principle. |

Recommended path: **Local HTTP API + GPT Actions** — user runs `python -m rag_kernel serve` locally, configures GPT custom action pointing to `http://localhost:PORT`. All file operations route through local API.

---

## Priority Matrix

| Priority | Items | Target |
|---|---|---|
| **CRITICAL** | DEF-001, DEF-002, DEF-003, spec clarifications | v3.1.4 |
| **HIGH** | Filesystem bridge, WAL writer, hash verification, ENH-004 | v3.2 |
| **MEDIUM** | COLD partition manager, conflict auto-categorization, delta checkpoints | v3.2–v3.3 |
| **LOW** | Archive depth control, formal verification, MCP layer research | v3.3–v4.0 |
