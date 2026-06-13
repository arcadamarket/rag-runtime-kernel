# Development Roadmap — RAG Runtime Kernel

> Organized by release target. Each item references its source finding from test analysis.

---

## v0.2.0 — Released (2026-05-22)

**Paradigm shift: fully autonomous OS-level Python backbone.** LLM role reduced to task assignor, results checker, orchestrator. All bootstrapping, state management, validation, and persistence run as deterministic Python scripts consuming zero LLM tokens.

| Component | Status |
|---|---|
| `spec_parser.py` — deterministic MD→RAG parser (610 lines) | Shipped |
| `rag_kernel init --spec` — zero-touch bootstrap from spec | Shipped |
| `rag_kernel configure` — project-specific context merge | Shipped |
| `discover()` — capability self-discovery registry | Shipped |
| `@rag-kernel-manifest` — structured module metadata | Shipped (all 12 modules) |
| Invocation protocol — MUST_USE_KERNEL vs DIRECT_IO_OK | Shipped |
| 64 new tests (401 total) | Shipped |

---

## v3.2.2 — Released (2026-06-11)

ENV-NORM — shell-execution normalization. §3a tool hierarchy rewritten to **tmux-mcp primary** for all composed shell/git/test commands (run verbatim — no `&&`/`;`/`|`/`$()` stripping, no `2>&1`→`1` orphan); `wsl-exec` demoted to an atomic-single-command fallback with its wrapper-tax documented; PowerShell last resort; Desktop Commander excluded for parenthesized paths; Cowork sandbox bash banned. New `session_start_shell_rule` (first shell action of every session via tmux-mcp). §3 adds a `doctor`/preflight boot step (extends the v3.2.1 Step-0 `audit-env` from REPORT to PREPARE). Paired with runtime v0.4.2 (`doctor` + guarded `add` verb). No schema change. Regression `init --spec v3.2.2` inherits exactly 12 known-issues, validation PASSED.

---

## v3.2.1 — Released (2026-06-10)

Known-issues reconciliation + environment-audit hardening (Track A2). 51 sections, no schema change. §41 known-issues registry: the human-readable table and the machine-readable `rag-config` block reconciled to the same **12 universal keys** — added `sandbox_mount_truncation` (table), `dc_start_process_quotes` (machine block), and `fetch_to_disk` to both (web_fetch lands off-mount; use curl/wget into the project tree — INS-044). Project-specific entries (git-worktree, credential path) scoped into per-project RAG registries via a new Maintenance note. §37 enumerates fetch/VCS/shell tooling and references `rag_kernel audit-env --json` (INS-045). §31 session-zero Step 0: environment audit (INS-043). Regression `init --spec` inherits exactly 12 known-issues, validation PASSED.

---

## v3.2.0 — Released (2026-05-27)

Operational hardening release: 51 sections. New §26a Web Access Protocol, §37 Environment Audit. Strengthened Rule 5 (env-switch gate), Rule 9 (web tier gate). Session-zero: requirements.txt + known-issues inheritance. AskUserQuestion echo-back. §41: curl_cffi + Python 3.14 entries. All 8 eBay audit findings (INS-010–017) shipped as spec prose.

---

## v3.1.8 — Released (2026-05-22)

Machine-parseable specification: 25 `rag-config` fenced JSON blocks for deterministic parsing by `spec_parser.py`. Dual-audience document (human prose + structured data). Zero-touch bootstrap target.

---

## v3.1.7 — Released (2026-05-20)

RAG/Memory Reconciliation Release: 48 sections. All behavioral rules consolidated from platform-specific memory into RAG_MASTER.json. New sections: File Sync Protocol (§42), Context Window Management (§43), Resolved Item Protocol (§44), Garbage Collector (§45), RAG as Single Source of Truth with portability guarantee (§46). Known-issues registry expanded.

**Portability milestone:** RAG_MASTER.json is now fully self-contained — a project can be transferred to any LLM platform (Claude, GPT, or any other) by providing either the init prompt OR the RAG file. Both contain the complete behavioral rule set.

---

## v3.1.6 — Released (2026-05-14)

Specification release: 43 sections. Pre-flight gate enforcement, known-issues registry, tool hierarchy with wsl-exec.

All v3.1.4 defect fixes (DEF-001 through DEF-003) and spec clarifications shipped in earlier patch releases.

---

## v0.1.0 — Released (2026-05-14), evolved to v0.2.0

Runtime Bridge: 8 Python modules, 337 tests, 5811 lines. ENFORCED mode live. Superseded by v0.2.0+ (12 modules, 676 tests, zero-touch bootstrap, graduated POV, delta checkpoints, conflict engine, session CLI).

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

## Formal Verification — Phase 2 Complete

| Phase | Work | Status |
|---|---|---|
| 1 — Model + Safety | TLA+ spec: 7 states, 8 safety invariants, WAL model. TLC verified: 136K states, 0 violations. | **Complete** (9f37dc1) |
| 2 — Liveness | WALCompaction action, 3 liveness properties. TLC verified: 389K states, 0 violations. | **Complete** (ddd7af6) |
| 3 — Generate | Auto-generate transition guard code from formal model | Not started |
| 4 — Integrate | Embed generated guards into Python runtime (ENFORCED mode) | Blocked on Phase 3 |

---

## UX & Efficiency Milestone — Released (2026-05-27, delivered as runtime v0.2.7)

> Note: this roadmap milestone was historically labelled "v0.3.0"; it shipped as
> runtime **v0.2.7**. The runtime semver **v0.3.0** is a later, distinct release
> (FV-PHASE3/4 enforcement + M-009 context-truncation) — see the section below.

**Milestone complete.** All UX & efficiency enhancements shipped. 12 modules, 676 tests.

### Enhancements

| ID | Enhancement | Priority | Status |
|---|---|---|---|
| ENH-004 | Graduated POV enforcement (STRICT/ADVISORY/SILENT modes) | HIGH | **Shipped v0.2.1** |
| ENH-006 | Delta-only checkpoints: save only changed fields since last checkpoint | MEDIUM | **Shipped v0.2.2** |
| ENH-005 | Conflict auto-categorization: 7 categories, rule-based classifier, auto-resolve | MEDIUM | **Shipped v0.2.7** |
| ENH-007 | Session logger: universal JSONL observability, KernelApp auto-wiring | MEDIUM | **Shipped v0.2.3** |
| ENH-008 | Session/Checkpoint/GC CLI: `session start/close`, `checkpoint`, `gc` commands | MEDIUM | **Shipped v0.2.5** |
| ENH-009 | Spec v3.2.0 kernel enforcement: audit-env, init --requirements, tier gate, echo-back | MEDIUM | **Shipped v0.2.6** |

### Troubleshooting Improvements

| Issue | Current State | Planned Fix |
|---|---|---|
| TS-002 (BOOTING stall) | User must manually confirm tool availability | Auto-detect tool absence, skip verification with logged gap, proceed to fallback chain without blocking |
| TS-005 (Conflict accumulation) | Conflicts grow silently | Add conflict count warning at boot: "X unresolved conflicts consuming ~Y tokens. Review recommended." |
| TS-006 (Session close without audit) | Lost findings if tab closes | Emergency checkpoint before audit — save state first, then audit. Reverses current order. |

---

## v0.3.0 — Released (2026-06-01)

**Runtime release.** Bundles the formal-verification enforcement work with the
kernel-enforced context-truncation policy. 13 modules, 758 tests.

| ID | Item | Status |
|---|---|---|
| FV-PHASE3 | Deterministic TLA+ → Python guard generator (`guardgen` + `generated_guards`) | **Shipped** |
| FV-PHASE4 | Runtime enforces the generated guards; `TRANSITIONS` derived from the verified model; one source of truth | **Shipped** |
| M-009 | Kernel-enforced context-truncation policy: per-region token accounting, deterministic eviction order (HOT never evicted), checkpoint/evict/halt threshold actions, WAL-logged through the proposal pipeline | **Shipped** |

---

## v4.0 — Graph Orchestrator (Released in v0.4.0 — 2026-06-06)

Target: Multi-step workflow orchestration with dependency tracking.

Built incrementally (one milestone per session), behind a deliberate scope
boundary. All seven core increments (1–7) plus runtime-wiring landed on `main`
and **shipped in the single-shot v0.4.0** (2026-06-06), together with DRIFT-ELIM.

| Component | Description | Status |
|---|---|---|
| Pure DAG core | Fail-loud build, topological order + level assignment, guarded node-status lifecycle | Done — increment 1 |
| DAG execution engine | Drives nodes through propose → validate → commit; checkpoint-per-node + `GRAPH_NODE_EXECUTED` WAL event | Done — increment 2 |
| Deterministic-levels scheduling | `Schedule.LEVELS` names parallel-eligible batches; provably equivalent to `SEQUENTIAL`; single-writer enforced | Done — increment 3 |
| Transactional rollback | Opt-in `rollback_on_failure` undoes the whole run to the pre-run baseline via the kernel RECOVERY path | Done — increment 4 |
| Registration | `graph_orchestrator` wired into `_KERNEL_MODULES` / `discover()` / `cmd_health`; module count 13 → 14; health 15/15 | Done — increment 5 |
| OS-process parallel work | `Schedule.PROCESS_LEVELS` — a level's nodes run their pure work in separate OS processes; commit stays serialized in deterministic sorted-id order under the file-mutex | Done — increment 6 |
| Agent / session supervisor | `agent_supervisor.py` — thin observable spawn/monitor/collect layer over the off-process workers (live PID/state/exit code as an `AgentView`); owns no authoritative state; module count 14 → 15; health 16/16 | Done — increment 7 |
| Runtime-wiring | `KernelApp.run_graph` + CLI `graph run` + MCP `rag_graph_run` — invokable through the kernel runtime from a JSON-serializable DAG spec; no new schema/WAL/TLA+; 925 tests, health 16/16 | Done — final gate |
| v4.0 release | Cut the `runtime-v0.4.0` release / tag + publish the headline announcement; headline counts reconciled to a released v0.4.0 | **Shipped — v0.4.0 (2026-06-06)** |

### Prerequisites
- Formal verification Phase 2+ (transition guards must be provably correct before graph nodes enforce them) — **met** (FV-PHASE3/4 enforced at runtime).

---

## DRIFT-ELIM — Deterministic Project-State Layer (Released in v0.4.0 — 2026-06-06)

Target: eliminate the cross-store status-drift class (E-034 / E-037 / E-039 /
E-040) by giving every tracked project item **one** canonical status, mutated only
through a deterministic, guarded, atomic API — generalizing the `guardgen`
"rules-as-data, fail-loud" discipline to the operating protocol's own state.
Built incrementally behind a deliberate scope boundary; ships together with the
Graph Orchestrator as the single-shot **v0.4.0** (no interim release).

| Component | Description | Status |
|---|---|---|
| Item-lifecycle pure core | `drift_control.py` — `ItemStatus` enum + `LIFECYCLE` table + fail-loud guards + immutable `TrackedItem` (append-only history) | Done — increment 1 |
| Mutation API + migration | `drift_store.py` — `TrackedItemStore` over the canonical `tracked_items` array; guarded transitions, atomic persistence (`.bak` refresh), one-time backlog migration | Done — increment 2 |
| Lifecycle CLI + registration | `rag_kernel resolve\|defer\|reopen\|start\|discard\|supersede` + read-only `items`; `drift_control` + `drift_store` registered (`_KERNEL_MODULES` / `discover()` / `cmd_health`); module count 15 → 17; health 18/18 | Done — increment 3 |
| Renders | `drift_render.py` — deterministic, idempotent renderers regenerate legacy `open_tasks` / `deferred_items` + the ERROR_LOG backlog summary + the Rule 12 status-report backlog *from* the canonical `tracked_items` array (never re-authored); `apply_renders[_file]` rewrite the legacy arrays atomically; `rag_kernel render [--apply]` CLI; `drift_render` registered (critical); module count 17 → 18; health 19/19 | Done — increment 4 |
| Fail-loud session auditor + guarded note verb | `drift_audit.py` — deterministic session-boundary auditor: render parity (legacy arrays == render of `tracked_items`, the E-040 regression) + supersede referential integrity + note/status contradiction (stale-note class INS-038) + no Cowork-memory side stores in the project root (Rule 13 / E-039); `assert_clean` fails loud, `rag_kernel audit [--strict]` CLI. Plus the guarded note-update path (`with_note` → `set_note` → `rag_kernel note`) closing INS-038. `drift_audit` registered (critical); module count 18 → 19; health 20/20; 1082 tests. Dogfooded clean on the project RAG | Done — increment 5 |
| Record migration + Rule 11 doc reconciliation (INS-039) | `inference_ledger` dispositions + ERROR_LOG `E-###` records folded into the canonical `tracked_items` array (`kind=INFERENCE`/`ERROR`) via a guarded additive migration; task renders scoped to `BACKLOG_KINDS` so records don't leak; new auditor checks — ledger consistency, record coverage, and the **Rule 11 published-doc reconciliation** (headline facts + id-anchored status claims vs the live kernel; historical/CHANGELOG exemptions). Migration prepared + verified on a copy (project RAG migrates 22 → 102 when triggered); auditor gated pre-cutover so the live RAG stays clean until migration. +34 tests; 1116 total; health 20/20; no new module | Done — increment 6 code (post-v0.4.0, **unreleased**; project-RAG migration deferred per user) |

---

## MCP Layer for GPT Web (Research)

Target: Give GPT Web real filesystem access without requiring platform changes.

Recommended path: **Local HTTP API + GPT Actions** — user runs `python -m rag_kernel serve` locally, configures GPT custom action pointing to `http://localhost:PORT`. All file operations route through local API. Already supported by v0.1.0+ runtime.

---

## Priority Matrix

| Priority | Items | Target |
|---|---|---|
| **SHIPPED** | Spec v3.1.4–v3.2.0, rag_kernel v0.1.0–v0.3.0 (zero-touch bootstrap, graduated POV, delta checkpoints, session logger, conflict engine, session/checkpoint/gc CLI, spec enforcement), FV Phase 1+2 (389K states), FV-PHASE3/4 (guard generation enforced at runtime), M-009 (context-truncation policy), **rag_kernel v0.4.0 (2026-06-06) — Graph Orchestrator + DRIFT-ELIM; 19 modules, health 20/20, 1,082 tests**, **rag_kernel v0.4.1 (2026-06-09) — kernel hardening from the eBay S0 deployment audit: `audit-env` fetch/VCS/shell tooling enumeration (INS-045) + `init` fail-loud on missing `--spec` (INS-046), bundling DRIFT-ELIM inc 6; no new module (19), health 20/20, 1,123 tests**, **rag_kernel v0.4.2 (2026-06-11) — ENV-NORM shell-execution normalization: `doctor` preflight + guarded `add` verb, paired with spec v3.2.2 tmux-primary tool hierarchy; no new module (19), health 20/20, 1,142 tests**, **rag_kernel v0.4.3 (2026-06-11) — AUDIT-CS-FRESHNESS: `audit` guards the `current_status` narrative against the live runtime version + git HEAD (E-043), failing loud on a stale snapshot; new `audit --git-head` flag; no new module (19), health 20/20, 1,159 tests**, **rag_kernel v0.4.4 (2026-06-12) — FIX-1 integrity auditor + WAL hardening (K1+K2) from the eBay Session-Zero deploy audit: seven fail-loud integrity invariants (WAL monotonicity, RAG↔.bak parity, COLD↔HOT spec-version, unsubstituted-placeholder, leaked-template-key, non-empty `written_by_session`, session-id coherence) + a `health` WAL-replay self-test; dogfooded live (caught a real latent COLD↔HOT drift in this repo's own RAG); no new module (19), health 20/20, 1,180 tests**, **rag_kernel v0.4.5 (2026-06-13) — FIX-2 single self-version token + deterministic `verify` gate (K4+K8) from the eBay Session-Zero deploy audit: the spec's HOT/COLD templates carry one `<SPEC_VERSION>` token that `spec_parser` substitutes and stamps into the COLD `init_prompt_reference` from the spec's own version (root-causing the COLD↔HOT drift FIX-1 only detected); new `rag_kernel verify` post-init coherence gate; `init` fail-loud on any unsubstituted token; SESSION_ZERO verify gate rewritten onto `verify`/`audit`; no new module (19), health 20/20, 1,202 tests**, **rag_kernel v0.4.6 (2026-06-13) — FIX-3 init/configure build-time hygiene (K3+K5+K7) from the eBay Session-Zero deploy audit: `spec_parser` substitutes the build-deterministic `<ISO>` placeholder and strips `_`-prefixed `:template` keys from `operating_protocol` so a fresh deploy is born clean, and `KernelApp` mints a canonical `S<int>` session id (not `S-{pid}-…`) and stamps `meta.written_by_session` on every checkpoint — preventing at build the defects FIX-1 could only detect; no new module (19), health 20/20, 1,219 tests** | Done |
| **NEXT** | Post-v0.4.0: community engagement monitoring, donation links, v0.5 self-hosted SDK agent harness, third-party ecosystem integration research | TBD |
