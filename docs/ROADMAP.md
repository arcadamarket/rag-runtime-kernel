# Development Roadmap — RAG Runtime Kernel

> Organized by release target. Each item references its source finding from test analysis.

---

## v3.1.6 — Released (2026-05-14)

Specification release: 43 sections. Pre-flight gate enforcement, known-issues registry, tool hierarchy with wsl-exec.

All v3.1.4 defect fixes (DEF-001 through DEF-003) and spec clarifications shipped in earlier patch releases.

---

## v3.2 — Released (2026-05-14)

Runtime Bridge: 8 Python modules, 337 tests, 5811 lines. ENFORCED mode live.

| Component | Status |
|---|---|
| State machine engine | Shipped |
| Persistence engine (atomic writes, WAL, hash verification) | Shipped |
| COLD partition manager | Shipped |
| Concurrency guard (lock manager, write collision detection) | Shipped |
| HTTP API (FastAPI) | Shipped |
| MCP transport | Shipped |
| CLI entry point (serve / mcp) | Shipped |
| Pydantic schemas | Shipped |

---

## Formal Verification — Phase 1 Complete

| Phase | Work | Status |
|---|---|---|
| 1 — Model | TLA+ specification: 7 states, 8 safety invariants, 3 liveness properties (555 lines) | **Complete** |
| 2 — Verify | Run TLC model checker against spec | Not started |
| 3 — Generate | Auto-generate transition guard code from formal model | Not started |
| 4 — Integrate | Embed generated guards into Python runtime (ENFORCED mode) | Blocked on Phase 2–3 |

---

## v3.3 — Planned

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

## v4.0 — Graph Orchestrator (Planned)

Target: Multi-step workflow orchestration with dependency tracking.

| Component | Description |
|---|---|
| DAG execution engine | LangGraph-class directed graph for multi-step workflows |
| Dependency tracking | Task B waits for Task A completion before starting |
| Parallel execution | Independent tasks run concurrently where safe |
| Checkpoint-per-node | Each graph node checkpoints independently |
| Rollback support | Failed node rolls back to last valid state without corrupting siblings |

### Prerequisites
- Formal verification Phase 2+ (transition guards must be provably correct before graph nodes enforce them)

---

## MCP Layer for GPT Web (Research)

Target: Give GPT Web real filesystem access without requiring platform changes.

Recommended path: **Local HTTP API + GPT Actions** — user runs `python -m rag_kernel serve` locally, configures GPT custom action pointing to `http://localhost:PORT`. All file operations route through local API. Already supported by v3.2 Runtime Bridge.

---

## Priority Matrix

| Priority | Items | Target |
|---|---|---|
| **SHIPPED** | v3.1.4–v3.1.6 spec fixes, v3.2 Runtime Bridge, Formal Verification Phase 1 | Done |
| **HIGH** | TLC model checking (Phase 2), ENH-004 graduated POV | v3.3 |
| **MEDIUM** | Conflict auto-categorization, delta checkpoints | v3.3 |
| **LOW** | Graph orchestrator, formal guard generation | v4.0 |
