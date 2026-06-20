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

## spec v3.2.5 — Released (2026-06-20)

KA-8 — bake the GC-first carry-forward gate + session-end ritual into the universal spec (GOVERNANCE-DETERMINISM / KA-10 arc, TierB). KA-6 shipped the runtime commands (`session-start` / `session-end`); KA-8 makes the spec tell every deploy to run them. The session-boundary steps already existed but lived scattered across §17 (close audit), §19 (boot sequence), §20 (recovery) and §45 (garbage collector), so a deploying agent had to hand-assemble the ritual — exactly how the first external deploy skipped its checkpoint and froze its governance lineage. New §50 — Session-Start & Session-End Rituals (governed) — assembles both into explicit, ordered, fail-loud sequences and adds `operating_protocol.session_start_protocol` + `session_end_protocol` rag-config blocks so a fresh `init --spec` seeds them into every RAG deterministically (no per-project re-authoring, KA-10 TierB). Session-start = carry-forward gate (integrity `verify` + drift `audit`, fail-loud → RECOVERY) → gc dry-run over `root_project` → open logger; session-end = checkpoint → close (KA-4 checkpoint-gate) → audit, any step's failure aborting the rest. Runtime wrapper present (v0.4.14+): each ritual is one governed command; autonomous mode performs the steps manually, in order, halting on failure. Spec-only — no schema, WAL-format, TLA+, or runtime change (runtime stays v0.4.14, drift gate `268149294421` unchanged). Regression `init --spec v3.2.5` seeds both ritual rules (plus the pre-existing `garbage_collector` + `strict_obey`), `policy_version` 3.2.5, COLD ref v3.2.5, no residual `<SPEC_VERSION>`, `verify` OK + `audit --strict` clean, full suite 1,398 green (+6). 53 sections.

---

## v0.4.18 — Released (2026-06-20)

KA-2 increment B — governed sessions_recent row-repair/dedup verb (GOVERNANCE-DETERMINISM / KA-10 arc). The repair half that completes KA-2: increment A (v0.4.17) made the kernel fail loud on duplicate-bootstrap `sessions_recent` rows (two rows sharing a checkpoint timestamp `d` — the eBay Session-Zero S0/S1 signature) but offered no governed way to fix them, and a hand-edit of the array is exactly the drift the project forbids. New `drift_store.dedup_sessions_recent` (pure on the dict) and `dedup_sessions_recent_file` (atomic) remove the phantom duplicate(s), keeping exactly one row per checkpoint timestamp: group-correct (handles 3+ rows sharing one instant), idempotent (a second run removes nothing), order-preserving, and honoring `--keep first|last`. Rows with a missing/blank `d` and non-dict rows are never touched. The file verb writes through the atomic `tmp → verify → .bak → rename` path (FIX-4 byte-parity `.bak` mirror) and is a true no-op when the ledger is clean (no spurious `.bak` churn). New CLI `dedup-sessions [--rag …] [--keep first|last] [--session …] [--dry-run]`. Single source of truth — detect == repair: the duplicate-detection predicate (`sessions_recent_duplicate_pairs` / `_sessions_recent_key`) now lives in `drift_store` and is consumed by both the KA-2 auditor (to flag) and this verb (to repair), so a flagged row is exactly a removed row; the shared date coercers moved down with it and are re-exported from `drift_audit` (public surface unchanged). Also unblocks the eBay deploy's B-3 (sessions_recent dedup), which was waiting on this verb. Dogfooded: a synthetic RAG with the S0/S1 shared-timestamp defect dedups to clean and then audits clean; the live project RAG is untouched (no duplicates). `DRIFT_STORE_VERSION` 1.1.0 → 1.2.0. KA-2 now RESOLVED. Sixth runtime increment of the KA-10 GOVERNANCE-DETERMINISM initiative (after KA-4, KA-6, KA-9, KA-3, KA-2A). CLI/store-only — no new module (19), health 20/20, drift gate `268149294421` unchanged (no schema/WAL/TLA+ change), full suite 1,448 → 1,469 green (+21).

---

## v0.4.17 — Released (2026-06-20)

KA-2 increment A — sessions_recent duplicate-bootstrap auditor (GOVERNANCE-DETERMINISM / KA-10 arc). Closes another blind spot the eBay Session-Zero deploy exposed: its `sessions_recent` ledger carried duplicate bootstrap rows — S0 and S1 minted at the same timestamp, one never actually run — yet `audit --strict` reported 0 findings and there was no governed way to repair them. New `drift_audit.check_sessions_recent_coherence` fails loud (ERROR) when two rows share a checkpoint timestamp `d` (compared on the parsed UTC instant so a `Z`-suffixed value and its offset twin collide; an unparseable `d` falls back to the exact literal, catching two identical `<ISO>`-class placeholders). Order-agnostic by design: the project legitimately writes `sessions_recent` both oldest-first (this kernel's live RAG, S92…S95) and newest-first (a fresh `init --auto-ready` RAG, `[S1, S0]`), and one session legitimately spans multiple rows (the S95/S95 multi-checkpoint pair, distinct timestamps) — so a shared timestamp is the only phantom-duplicate signal safe across every shape; directional id/timestamp monotonicity would false-positive on a clean deploy and was deliberately not enforced. Self-skips when `sessions_recent` is absent / not a list / < 2 rows or a row's `d` is missing; wired into `audit_hot` so it runs at every session boundary. Dogfooded: a synthetic RAG with the eBay S0/S1 shared-timestamp defect fails loud on `sessions_recent_coherence`; the live project RAG and a fresh `init --auto-ready` RAG both audit clean. This is increment A (detection); the paired increment B — a governed row-repair/dedup verb — remains open (KA-2 stays IN_PROGRESS). `DRIFT_AUDIT_VERSION` 1.6.0 → 1.7.0. Fifth runtime increment of the KA-10 GOVERNANCE-DETERMINISM initiative (after KA-4, KA-6, KA-9, KA-3). CLI/audit-only — no new module (19), health 20/20, drift gate `268149294421` unchanged (no schema/WAL/TLA+ change), full suite 1,427 → 1,448 green (+21).

---

## v0.4.16 — Released (2026-06-20)

KA-3 — current_status internal-coherence auditor (GOVERNANCE-DETERMINISM / KA-10 arc). Closes another silent blind spot the eBay Session-Zero deploy exposed: `current_status` denormalizes two facts from `meta` inside the same RAG — the session that last wrote it (`current_status.session` vs `meta.written_by_session`) and the day it was last updated (`current_status.last_updated` vs `meta.last_updated_utc`) — yet no invariant asserted the two agreed. The eBay deploy froze `current_status.session` at `S0` while the machine had moved on and ran `last_updated` two full days behind `meta`, and `audit --strict` still reported 0 findings. New `drift_audit.check_current_status_coherence` fails loud (ERROR) when `current_status.session != meta.written_by_session`, or when the UTC calendar day of `current_status.last_updated` differs from that of `meta.last_updated_utc` (compared at day granularity, since `current_status` records a date and `meta` a full instant). It is distinct from the E-043 `check_current_status_freshness` guard, which checks two facts whose authority lives outside the RAG (the kernel `__version__` and git HEAD); this checks two facts denormalized from `meta` inside the RAG. Each sub-check self-skips when either side is absent or unparseable, so a RAG whose `current_status` omits these keys — like this kernel's own — audits clean rather than being falsely flagged; wired into `audit_hot` so it runs at every session boundary. Dogfooded: a synthetic RAG with the eBay stale-session/stale-date defect fails loud on `current_status_coherence`; the live project RAG audits clean. `DRIFT_AUDIT_VERSION` 1.5.0 → 1.6.0. Fourth runtime increment of the KA-10 GOVERNANCE-DETERMINISM initiative (after KA-4, KA-6, KA-9). CLI/audit-only — no new module (19), health 20/20, drift gate `268149294421` unchanged (no schema/WAL/TLA+ change), full suite 1,410 → 1,427 green (+17).

---

## v0.4.15 — Released (2026-06-20)

KA-9 — project_context placeholder gate (GOVERNANCE-DETERMINISM / KA-10 arc). Closes the last born-clean hole the eBay Session-Zero deploy exposed: a deployed RAG carrying unfilled `<from user>` tokens in `project_context.brief` / `domain` / `end_goal` that `audit --strict` passed clean. The FIX-1 `check_placeholder_tokens` integrity scan (K3) only matches whole-value UPPER_SNAKE parser tokens (`<SPEC_VERSION>`, `<ISO>`) — the spec parser's own substitution targets — so the human-fill session-zero placeholders (`<from user>`, `<absolute path>`: lowercase/spaced, filled by the LLM at deploy, not the parser) slipped straight through. Two complementary parts. (1) The gate: a new `drift_audit.check_project_context_placeholders` walks the whole `project_context` subtree and fails loud on any surviving human-fill `<…>` placeholder (substring match, so a half-filled value is caught too); it leaves pure UPPER_SNAKE tokens to `check_placeholder_tokens` (no double-report) and self-skips when `project_context` is absent; wired into `audit_hot` so it runs at every session boundary. (2) Born-clean init: per spec §1182 (skip → null), `cmd_init` now resolves every unfilled `project_context` placeholder to `null` instead of leaving the literal token, so a fresh `init` / `--auto-ready` is clean by construction rather than failing the gate — the same born-clean discipline FIX-9 applied to the K7 `written_by_session` residual. Dogfooded: the synthetic eBay-defective RAG (now carrying `<from user>` in `project_context`) fails loud on `project_context_placeholders`; the live project RAG and a fresh `init --auto-ready` both audit clean. `DRIFT_AUDIT_VERSION` 1.4.0 → 1.5.0. Third increment of the KA-10 GOVERNANCE-DETERMINISM initiative (after KA-4, KA-6). CLI/audit-only — no new module (19), health 20/20, drift gate `268149294421` unchanged (no schema/WAL/TLA+ change), full suite 1,398 → 1,410 green (+12).

---

## v0.4.14 — Released (2026-06-20)

KA-6 — machine-enforced session-start / session-end rituals (GOVERNANCE-DETERMINISM / KA-10 arc). Collapses each session-boundary ritual into ONE ordered, fail-loud CLI command, removing the hand-scripting surface where a step gets skipped. Root cause (eBay S2/S4): the opening/closing steps were run by hand and one was missed — the deploy closed on `configure`/`audit` without ever `checkpoint`-ing, freezing `meta.written_by_session`. KA-4 fixed the close-without-checkpoint hole; KA-6 removes the hand-scripting itself. `session-start <id>` runs, in order: (1) a carry-forward gate — the precise inverse of the KA-4 close gate — that fails loud on an incoherent/unbanked inherited RAG by running `verify` (HOT↔COLD coherence) + `audit` (renders==canonical, refs, notes, `.bak` parity, freshness, no side stores), refusing to open the session unless both are clean (sanctioned `--force`); (2) a gc dry-run (report-before-delete); (3) opening the session logger. `session-end --rag … --session … --summary …` runs, in order: (1) checkpoint (stamps `written_by_session`, bumps seq, parity-mirrors `.bak`); (2) close the logger — the KA-4 gate now passes because step 1 ran; (3) the fail-loud audit; any step's non-zero exit aborts the rest. Reuses the existing `verify`/`audit`/`gc`/`checkpoint`/`session` primitives (no behavior drift); both commands are excluded from the bootstrap-log wrapper. Dogfooded: `session-start S92` gated green on this repo's live RAG and opened the session. Second increment of the KA-10 GOVERNANCE-DETERMINISM initiative (after KA-4). CLI-only — no new module (19), health 20/20, drift gate `268149294421` unchanged, full suite 1,392 green (+11).

---

## v0.4.13 — Released (2026-06-20)

KA-4 — checkpoint-to-close enforcement (GOVERNANCE-DETERMINISM / KA-10 arc). The kernel now refuses to close a started session on the CLI unless that session checkpointed first. Root cause (eBay S4): an agent ended sessions on `configure`/`audit` (or a scratch script) without ever running `checkpoint`, leaving `meta.written_by_session` stale across sessions — a silent governance freeze that recurred even after the S89 prose-only guide fix, proving enforcement must be code, not prose. `session close <id>` evaluates a checkpoint gate (`meta.written_by_session == <id>`, the precise inverse of the freeze signature) and refuses with a non-zero exit + remediation hint when absent; a sanctioned `--force` override closes anyway with a loud warning so a blocked agent does not resort to an unsanctioned scratch script. The programmatic `KernelApp.close()` already force-checkpoints on close (ENH-006) — this closes the standalone-CLI hole the deploy actually froze on; a no-op close (no log) stays a no-op. First increment of the KA-10 GOVERNANCE-DETERMINISM initiative. CLI-only — no new module (19), health 20/20, drift gate `268149294421` unchanged, full suite 1,381 green (+9).

---

## v3.2.4 — Released (2026-06-14)

STRICT-OBEY — Operator Fidelity Protocol. New §49 promotes the operator-fidelity rule into the universal spec (was project-RAG-only): obey the operator's literal instruction (no guesswork/improvisation/scope-creep/unrequested work); honest status (never report incomplete work as done); bounded halt-and-ask (ask only on genuine ambiguity or an operator-only decision — over-asking is as much a violation as over-doing; exercise delegated discretion); and rendering discipline (every status/backlog render enumerates items line by line, by ID, in plain language — never a bare count or glyph shorthand). New `operating_protocol.strict_obey` rag-config. Spec-only — no schema or runtime change (runtime stays v0.4.11). Regression `init --spec v3.2.4` inherits exactly 12 known-issues + `strict_obey`, `verify` OK, full suite 1,302 green.

---

## v3.2.3 — Released (2026-06-14)

FIX-7 T3 — Web Access Protocol decision table. Completes FIX-7 (T1 shipped in runtime v0.4.10), the spec-side half of the eBay Session-Zero deploy audit's web-protocol finding. §26a is rewritten from cost-ordered 3-tier prose into a deterministic **first-match-wins decision table** (unknown URL → search-for-discovery-only; API/connector/MCP-first; repeatable/persistent → on-disk script; one-off-to-disk → `curl`/`wget` fetch-to-disk per INS-044; one-off in-context → WebFetch), with explicit guards (JS-shell → JS-capable browser escalation; restricted-domain → STOP, no route-around; `curl_cffi` header caution) and a violation definition. The `rag-config` `web_access_protocol` string and `pre_flight_gate` web clause are reconciled to match. Spec-only — no schema or runtime change (runtime stays v0.4.10). Regression `init --spec v3.2.3` inherits exactly 12 known-issues, `verify` OK, full suite 1,299 green.

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
| **SHIPPED** | Spec v3.1.4–v3.2.0, rag_kernel v0.1.0–v0.3.0 (zero-touch bootstrap, graduated POV, delta checkpoints, session logger, conflict engine, session/checkpoint/gc CLI, spec enforcement), FV Phase 1+2 (389K states), FV-PHASE3/4 (guard generation enforced at runtime), M-009 (context-truncation policy), **rag_kernel v0.4.0 (2026-06-06) — Graph Orchestrator + DRIFT-ELIM; 19 modules, health 20/20, 1,082 tests**, **rag_kernel v0.4.1 (2026-06-09) — kernel hardening from the eBay S0 deployment audit: `audit-env` fetch/VCS/shell tooling enumeration (INS-045) + `init` fail-loud on missing `--spec` (INS-046), bundling DRIFT-ELIM inc 6; no new module (19), health 20/20, 1,123 tests**, **rag_kernel v0.4.2 (2026-06-11) — ENV-NORM shell-execution normalization: `doctor` preflight + guarded `add` verb, paired with spec v3.2.2 tmux-primary tool hierarchy; no new module (19), health 20/20, 1,142 tests**, **rag_kernel v0.4.3 (2026-06-11) — AUDIT-CS-FRESHNESS: `audit` guards the `current_status` narrative against the live runtime version + git HEAD (E-043), failing loud on a stale snapshot; new `audit --git-head` flag; no new module (19), health 20/20, 1,159 tests**, **rag_kernel v0.4.4 (2026-06-12) — FIX-1 integrity auditor + WAL hardening (K1+K2) from the eBay Session-Zero deploy audit: seven fail-loud integrity invariants (WAL monotonicity, RAG↔.bak parity, COLD↔HOT spec-version, unsubstituted-placeholder, leaked-template-key, non-empty `written_by_session`, session-id coherence) + a `health` WAL-replay self-test; dogfooded live (caught a real latent COLD↔HOT drift in this repo's own RAG); no new module (19), health 20/20, 1,180 tests**, **rag_kernel v0.4.5 (2026-06-13) — FIX-2 single self-version token + deterministic `verify` gate (K4+K8) from the eBay Session-Zero deploy audit: the spec's HOT/COLD templates carry one `<SPEC_VERSION>` token that `spec_parser` substitutes and stamps into the COLD `init_prompt_reference` from the spec's own version (root-causing the COLD↔HOT drift FIX-1 only detected); new `rag_kernel verify` post-init coherence gate; `init` fail-loud on any unsubstituted token; SESSION_ZERO verify gate rewritten onto `verify`/`audit`; no new module (19), health 20/20, 1,202 tests**, **rag_kernel v0.4.6 (2026-06-13) — FIX-3 init/configure build-time hygiene (K3+K5+K7) from the eBay Session-Zero deploy audit: `spec_parser` substitutes the build-deterministic `<ISO>` placeholder and strips `_`-prefixed `:template` keys from `operating_protocol` so a fresh deploy is born clean, and `KernelApp` mints a canonical `S<int>` session id (not `S-{pid}-…`) and stamps `meta.written_by_session` on every checkpoint — preventing at build the defects FIX-1 could only detect; no new module (19), health 20/20, 1,219 tests**, **rag_kernel v0.4.7 (2026-06-13) — FIX-4 parity-mirror `.bak` contract (K6) from the eBay Session-Zero deploy audit: settles + enforces the `.bak` semantics FIX-1 left ambiguous (eBay backup sat 3 checkpoints stale, HOT seq 3 / `.bak` seq 0). The `.bak` is now a byte-identical parity-mirror of the last committed HOT, refreshed via opt-in `mirror_bak=True` on the canonical writers (full checkpoint/close, `drift_store`, `drift_render`); generic writes keep the prior-file crash backup. `check_bak_parity` asserts true byte-parity (rollback-prev one-behind allowance removed); `DRIFT_AUDIT_VERSION` → 1.3.0; no new module (19), health 20/20, 1,235 tests**, **rag_kernel v0.4.8 (2026-06-14) — FIX-5 guarded `add-rule` verb + RAG-dir context side-store scan (P3+P2) from the eBay Session-Zero deploy audit: `drift_store.add_operating_protocol_rule[_file]` + `rag_kernel add-rule` give `operating_protocol` a guarded, atomic, `.bak`-mirroring add path (fail-loud on an existing key) so new rules no longer require hand-editing JSON (E-037/E-039), and `drift_audit.check_context_side_stores` flags a stray `*_context.json` left in the RAG dir (the eBay `ebay_context.json` redundancy), extending the Rule 13 side-store family; `DRIFT_STORE_VERSION` → 1.1.0, `DRIFT_AUDIT_VERSION` → 1.4.0; no new module (19), health 20/20, 1,267 tests**, **rag_kernel v0.4.9 (2026-06-14) — FIX-6 layout-aware `--rag` default (K9) from the eBay Session-Zero deploy audit: a shared `_default_rag_path()` resolver finds `RAG_MASTER.json` whether a command is run from the project root or from inside the RAG dir (returning the first existing candidate, never doubling `RAG/RAG`), applied to every RAG-taking command; dogfooded by running `audit` from inside this repo's RAG dir with no `--rag` (0 findings, previously a not-found error); CLI-only, no new module (19), health 20/20, 1,279 tests**, **rag_kernel v0.4.10 (2026-06-14) — FIX-7 T1 live pre-write side-store guard from the eBay Session-Zero deploy audit: the Rule 13 / E-039 parallel-store invariant (Cowork-memory `MEMORY.md`/`feedback_*.md`/`project_*.md`, or a stray `*_context.json` beside the RAG) now fires at write time — `persistence.assert_no_side_stores`, opt-in via `guard_side_stores=True` on the canonical writers (full checkpoint/close, `drift_store`, `drift_render`), refuses to commit while a side store is live, instead of only flagging it after the fact at `audit`; side-store patterns single-sourced in `persistence` with `drift_audit` delegating (DRY); T3 (`web_access_protocol` decision table) ships separately as spec v3.2.3; no new module (19), health 20/20, drift gate `268149294421`, 1,299 tests**, **rag_kernel v0.4.11 (2026-06-14) — FIX-8 CLI checkpoint parity-mirror `.bak` (E-045): the standalone CLI `checkpoint` verb now passes `mirror_bak=True` so a session closed on `checkpoint` alone refreshes `RAG_MASTER.json.bak` to byte-parity with HOT (matching `api.checkpoint` do_full / FIX-4 K6), instead of leaving it one seq behind; one-line wiring fix + 3 regression tests; no new module (19), health 20/20, drift gate `268149294421`, 1,302 tests**, **rag_kernel v0.4.12 (2026-06-16) — release bundle of FIX-9…FIX-12 (eBay Session-Zero deploy-audit lane, U1–U4): FIX-9 `init --auto-ready` routed through the first stamping checkpoint (a born-ready RAG is stamped + carries a byte-parity `.bak` and audits clean); FIX-10 `configure` persists via `atomic_write_json(mirror_bak=True)`, closing the K6/FIX-4 `.bak` parity-mirror gap; FIX-11 sanctioned non-loaded `RAG_CONTEXT.json` store + `context` CLI (`set`/`get`/`list`) + `configure --consume` for governed, zero-boot-token project context; FIX-12 CLI bootstrap session log captures real events (`SessionLogger.attach()`/`detach()` + a central dispatch wrapper emitting a real `tool_invocation` per verb), fixing empty/marker-only logs + a spurious second `session_start`; no new module (19), health 20/20, drift gate `268149294421`, 1,372 tests**, **rag_kernel v0.4.13 (2026-06-20) — KA-4 checkpoint-to-close enforcement (first increment of the KA-10 GOVERNANCE-DETERMINISM initiative): the CLI `session close <id>` now refuses to close unless that session checkpointed first (`meta.written_by_session == <id>`, the inverse of the eBay-S4 ran-but-never-checkpointed freeze signature), with a non-zero exit + remediation hint and a sanctioned `--force` override; the programmatic `KernelApp.close()` already force-checkpoints (ENH-006), so this closes the standalone-CLI hole; CLI-only, no new module (19), health 20/20, drift gate `268149294421`, 1,381 tests**, **rag_kernel v0.4.14 (2026-06-20) — KA-6 machine-enforced session-start/session-end rituals (second increment of the KA-10 GOVERNANCE-DETERMINISM initiative): one ordered fail-loud CLI command per session boundary, removing the hand-scripting surface that let the eBay S2/S4 deploy skip `checkpoint` and freeze `meta.written_by_session`; `session-start <id>` = carry-forward gate (inverse of the KA-4 close gate — `verify` + `audit`, `--force` override) → gc dry-run → open logger, and `session-end` = checkpoint → close (KA-4 gate passes) → audit with any step's failure aborting the rest; reuses existing primitives, both excluded from the bootstrap-log wrapper; dogfooded by opening S92 on the live RAG; CLI-only, no new module (19), health 20/20, drift gate `268149294421`, 1,392 tests** | Done |
| **NEXT** | Post-v0.4.0: community engagement monitoring, donation links, v0.5 self-hosted SDK agent harness, third-party ecosystem integration research | TBD |
