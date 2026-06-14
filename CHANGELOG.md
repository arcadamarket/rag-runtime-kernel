# Changelog

All notable changes to the RAG Runtime Kernel specification and tooling.

## [Unreleased]

_Nothing yet._

## [spec v3.2.3] — 2026-06-14

**FIX-7 T3 — Web Access Protocol decision table.** Completes FIX-7 (T1 shipped in runtime v0.4.10): the second, spec-side half of the eBay Session-Zero deploy audit's web-protocol finding. §26a is rewritten from cost-ordered 3-tier prose — whose tier *selection* was open to interpretation (the "web-protocol churn") — into a deterministic **first-match-wins decision table**: unknown URL → search for *discovery only*; a dedicated API/connector/MCP → use it; repeatable or must-persist data → on-disk script (`curl_cffi`/`requests`/`httpx`); one-off content that must land on disk → `curl`/`wget` **fetch-to-disk** (INS-044); one-off in-context read → WebFetch. Adds explicit **guards** (JS-shell → escalate to a JS-capable browser tool; restricted-domain → STOP, no route-around; `curl_cffi` header caution) and a clear **violation** definition. The machine-readable `rag-config` `web_access_protocol` string is rewritten to match, and the `pre_flight_gate` web clause is reconciled to reference the table. Spec-only — no schema, WAL-format, TLA+, or runtime change (runtime stays v0.4.10). Regression: `init --spec v3.2.3` writes a non-void RAG inheriting exactly 12 known-issues, `policy_version` 3.2.3, COLD `init_prompt_reference` v3.2.3, no residual `<SPEC_VERSION>`, `verify` OK; full suite 1,299 green.

## [v0.4.10] — 2026-06-14

**FIX-7 T1 — live pre-write side-store guard (T1).** Turns the Rule 13 / E-039 parallel-store invariant from an after-the-fact `audit` finding into a **write-time guard**. Until now a forbidden parallel rule/state store — a Cowork-memory `MEMORY.md` / `feedback_*.md` / `project_*.md`, or a stray `*_context.json` beside the RAG (whose content `configure` merges *into* the canonical RAG) — was only caught when `audit` ran later; the triggering incident (T1 of the eBay Session-Zero deploy audit) was a side store fixed only after operator pushback. A new `persistence.assert_no_side_stores` guard, opt-in via `guard_side_stores=True` on the canonical RAG-state writers (full checkpoint / session close, `drift_store` mutations, `drift_render` apply — the same set that opt into `mirror_bak`), now **refuses to commit** a canonical write while such a store is live, so the divergence can never reach disk. The side-store patterns and scan logic are single-sourced in `persistence` (the dependency-free leaf every writer imports); `drift_audit.check_side_rule_stores` and `check_context_side_stores` now **delegate** to those finders (DRY), so the live guard and the after-the-fact audit cannot drift apart. Scope is deliberately layered: the live guard is a fast tripwire over the RAG directory subtree, while the comprehensive project-root recursive sweep remains the auditor's job — keeping the per-write cost bounded and deterministic. **(T3 — rewriting `web_access_protocol` as an unambiguous decision table — is a spec change and ships separately as spec v3.2.3.)** Dogfooded: a guarded write into a dir holding a `feedback_*.md` fails loud and atomically (original intact, no `.tmp` left); the live RAG (no side stores present) checkpoints clean. **+20 tests (1,299 total)**; no new module (health 20/20), no schema/WAL-format/TLA+ change (drift gate `268149294421`), no `DRIFT_AUDIT`/`DRIFT_STORE` version change (audit behavior/report unchanged — same findings, internals only).

## [v0.4.9] — 2026-06-14

**FIX-6 — layout-aware `--rag` default (K9).** Closes the last structural finding from the eBay Session-Zero deploy audit. The CLI default `RAG/RAG_MASTER.json` assumed a run-from-root working directory; in a nested deploy layout (`rag_kernel/` living *under* `RAG/`), running a command from inside the RAG dir made that default resolve to `RAG/RAG/RAG_MASTER.json` — a doubled path that simply errors "not found" (K9). A new shared `_default_rag_path()` resolver returns the first existing candidate — `RAG/RAG_MASTER.json` (project root) then `RAG_MASTER.json` (inside the RAG dir) — falling back to the canonical root-layout path when neither exists, so it never prepends `RAG/` to a path already in the RAG dir and cannot double `RAG/RAG`. Applied to every RAG-taking command (`audit`, `items`, `render`, `verify`, `note`, `add`, `add-rule`, and the lifecycle transitions), so the same defect can't recur on any of them. Dogfooded: `audit` run from inside this repo's live RAG dir with no `--rag` now resolves correctly (0 findings) instead of erroring on the doubled path. **+12 tests (1,279 total)**; no new module (health 20/20), no schema/WAL-format/TLA+ change (drift gate `268149294421`); CLI-only, no `DRIFT_STORE`/`DRIFT_AUDIT` version change.

## [v0.4.8] — 2026-06-14

**FIX-5 — guarded `add-rule` verb + RAG-dir context side-store scan (P3+P2).** Closes two ergonomics/hygiene items from the eBay Session-Zero deploy audit. **(P3)** `operating_protocol` is the project's rule vault, but there was no governed path to introduce a *new* rule — additions meant hand-editing `RAG_MASTER.json`, the exact manual-JSON drift the project forbids (E-037 / E-039). New `drift_store.add_operating_protocol_rule[_file]` and the `rag_kernel add-rule <key> <value>` CLI verb make it a guarded, atomic, `.bak`-mirroring mutation (mirroring the tracked-items `add` verb): validate → fail-loud on an already-present key (no silent overwrite; `--allow-overwrite` to replace) → atomic write (tmp → verify → `.bak` parity → rename). Long rule bodies can be read from `--value-file`. `DRIFT_STORE_VERSION` → 1.1.0. **(P2)** `drift_audit.check_context_side_stores` flags a stray `*_context.json` persisted in the RAG directory — a `*_context.json` is a transient input to `configure` whose content is merged *into* the canonical RAG, so a copy left beside `RAG_MASTER.json` is a redundant parallel artifact (the eBay `ebay_context.json` side-file). It extends the Rule 13 side-store family from the project root (Cowork-memory MDs) to the RAG dir, scanning that dir only and non-recursively, gated by the same `--no-scan-root` toggle; `DRIFT_AUDIT_VERSION` → 1.4.0. Dogfooded: `add-rule --dry-run` proposes a rule and fails loud on an existing key against this repo's live RAG; the live `audit --strict` stays clean (no `*_context.json` present). **+32 tests (1,267 total)**; no new module (health 20/20), no schema/WAL-format/TLA+ change (drift gate `268149294421`).

## [v0.4.7] — 2026-06-13

**FIX-4 — parity-mirror `.bak` contract (K6).** Settles the `.bak` semantics that FIX-1 left ambiguous and enforces them, closing the eBay Session-Zero defect where the backup sat three checkpoints stale (HOT seq 3, `.bak` seq 0, different md5 — a backup that cannot actually restore). The contract is now **parity-mirror**: after any full checkpoint / session close — and after every governed `drift_store` / `drift_render` mutation — the `.bak` is refreshed to a **byte-identical** copy of the just-committed HOT, so recovery (`recovery_protocol` "attempt .bak first") restores the *exact* known-good state rather than a previous one. The rollback-prev alternative (a one-checkpoint-behind `.bak` for one-step undo) was considered and rejected: it breaks byte-parity auditing, would need a WAL cross-reference for integrity, and duplicates the event-sourced history the WAL already provides. *Enforce half:* a new opt-in `mirror_bak=True` on `persistence.atomic_write` / `atomic_write_json` refreshes the `.bak` to parity **after** the commit rename; the canonical RAG-state writers opt in. The generic write path keeps its prior-file crash backup (the N-1 copy that protects the write window) by default. *Audit half:* `drift_audit.check_bak_parity` now asserts true byte-parity between HOT and its `.bak`, replacing the FIX-1 equal-or-one-behind seq allowance (the one-behind branch was exactly the rejected rollback-prev contract). `DRIFT_AUDIT_VERSION` → 1.3.0. Dogfooded: the tightened auditor passes clean on this project's live RAG (`.bak == HOT`) and fails loud on a synthetic stale backup. **+16 tests (1,235 total)**; no new module (health 20/20), no schema/WAL-format/TLA+ change (drift gate `268149294421`).

## [v0.4.6] — 2026-06-13

**FIX-3 — init/configure build-time hygiene (K3+K5+K7).** Prevents at build the three init defects FIX-1 could only *detect* — the same root-cause-not-symptom move FIX-2 made for the COLD↔HOT version drift, so a fresh deploy is born clean rather than caught after the fact. (K3) `spec_parser` now substitutes the build-deterministic `<ISO>` placeholder with the build timestamp across HOT + COLD (the eBay `sessions_recent[].d` / `created_utc` defect); genuinely external session-zero placeholders (`<from user>`, `<absolute path>`) are deliberately left for the LLM. (K5) `spec_parser` strips `_`-prefixed `:template` scaffold keys (`_required`/`_note`) from `operating_protocol` at build, mirroring the `drift_audit.check_template_keys` invariant exactly (DRY). (K7) `KernelApp` mints a canonical `S<int>` session id instead of the old `S-{pid}-{epoch}` form whose `S-` prefix the auditor flags as malformed (the eBay `S-12488-…`), and stamps `meta.written_by_session` on every persisted checkpoint (covering session close and graph rollback) so the runtime can no longer leave the session lineage empty. Dogfooded: `init --spec v3.2.2` previously produced 3 audit findings (`<ISO>` + `_required` + `_note`); it now audits clean (0 findings) and passes `verify`. **+17 tests (1,219 total)**; no new module (health 20/20), no schema/WAL-format/TLA+ change (drift gate `268149294421`).

## [v0.4.5] — 2026-06-13

**FIX-2 — single self-version token + deterministic `verify` gate (K4+K8).** Root-causes the COLD↔HOT version drift that FIX-1 could only *detect*. The spec's `init` templates (§32 HOT, §33 COLD) previously hard-coded a stale version literal (`3.1.9`); the parser stamped the HOT `policy_version`/`init_prompt` from the spec's own version but copied the COLD `init_prompt_reference` verbatim, so every fresh deploy was born with a COLD pinned to the wrong spec version. The templates now carry a single `<SPEC_VERSION>` token that `spec_parser` deterministically substitutes across HOT + COLD and uses to stamp the COLD `init_prompt_reference` (version + filename) from one source — HOT and COLD can no longer disagree at init. `init` fails loud (non-zero exit, no write) if any `<SPEC_VERSION>` token survives. New `rag_kernel verify` command: a zero-token post-init coherence gate asserting HOT↔COLD self-version agreement and no residual placeholder (BOM-tolerant COLD read). The eBay SESSION_ZERO verification gate was rewritten off its miscalibrated file-size heuristic (and the nonexistent `recovery_protocol` key) onto `verify` + `audit --strict`. **+22 tests (1,202 total)**; no new module (health 20/20), no schema/WAL-format/TLA+ change (drift gate `268149294421`).

## [v0.4.4] — 2026-06-12

**FIX-1 — integrity auditor + WAL hardening (K1+K2).** Closes the headline finding of the eBay Session-Zero deploy audit: the kernel's own `audit --strict` reported "0 findings" over a RAG that carried a broken WAL, a stale backup, unsubstituted placeholders, leaked template keys, a COLD pinned to the wrong spec version, an empty `written_by_session` and a negative machine-minted session id. An integrity product whose integrity check green-lights a defective artifact has no moat, so the auditor now grows seven fail-loud integrity invariants (same fail-closed family as the E-040 render check), and the WAL gets a replay-based monotonicity self-test surfaced in `health`. Dogfooded live: the new auditor caught a real latent COLD↔HOT drift (3.1.2 vs 3.2.2) in this project's own production RAG that every prior session passed clean. `DRIFT_AUDIT_VERSION` → 1.2.0; **+21 tests (1,180 total)**; no new module (health 20/20), no schema/WAL-format/TLA+ change (drift gate `268149294421`).

### Added — integrity invariants (FIX-1 / K1+K2)

- **`check_wal_integrity`** — replays `WAL.jsonl` and fails loud unless the sequence is strictly monotonic by +1 (a duplicate, gap, or decrease all violate the WAL contract — the eBay WAL had two `seq:3` and no `seq:4`).
- **`check_bak_parity`** — the `.bak` must be a parity-mirror (equal checkpoint seq) or the rollback-prior (one behind); a backup that fails to parse or sits multiple checkpoints stale (eBay: HOT seq 3, `.bak` seq 0) is flagged as unable to actually restore.
- **`check_cold_hot_version`** — `RAG_COLD.json.init_prompt_reference` version must equal the live HOT spec version (eBay COLD pinned v3.1.9 under a v3.2.2 deploy). BOM-tolerant read so a benign UTF-8 BOM cannot mask the drift.
- **`check_placeholder_tokens`** — any value that is *exactly* an unsubstituted `<PLACEHOLDER>` token (the eBay `<ISO>` timestamps) is an error; rule prose merely mentioning a template token (`S<NN>`) is not a false positive (whole-value match only).
- **`check_template_keys`** — `_`-prefixed `:template` scaffold keys (`_required`/`_note`) must never leak into live `operating_protocol`.
- **`check_written_by_session`** — a checkpointed RAG must carry a non-empty `meta.written_by_session` (self-skips a pre-checkpoint `BOOTING` RAG).
- **`check_session_id_coherence`** — flags a malformed/negative machine-minted session id (`S-12488-…`) in `written_by_session` or any `sessions_recent[].id`.
- **`WAL.verify_integrity()` + `health` WAL-replay self-test** — a broken write-ahead log can no longer read as 20/20-healthy.

Each check **self-skips when its source is absent** (no WAL/COLD/`.bak`, a `BOOTING` RAG), so a healthy or not-yet-populated deployment audits clean. The full suite dogfoods a synthetic reproduction of the exact eBay-defective RAG and asserts every invariant fires.

## [v0.4.3] — 2026-06-11

**AUDIT-CS-FRESHNESS (E-043).** The `audit` command now guards the human-readable `current_status` narrative against the live authorities its facts denormalize — `rag_kernel.__version__` and the git HEAD — failing loud on a stale snapshot (the S62→S67 drift where `current_status` froze at an old version while the runtime moved on). New `check_current_status_freshness` auditor check (`DRIFT_AUDIT_VERSION` → 1.1.0), a new `audit --git-head` flag with best-effort auto-resolution from the RAG's worktree pointer, and 17 tests. No new module (health 20/20), no schema/WAL/TLA+ change (drift gate `268149294421`). **1,159 tests.**

### Added — current_status freshness guard (AUDIT-CS-FRESHNESS / E-043)

- **`check_current_status_freshness`** — extracts the leading version token from `current_status.rag_kernel_version` and the `LATEST COMMIT <sha>` from `current_status.github_repo`, then asserts each still equals the live authority (version vs `__version__`; HEAD vs git, prefix-compared). It is a **guard, not a render**: these facts' source of truth lives outside the RAG, so they cannot be rendered from it the way `open_tasks` renders from `tracked_items`. Self-skipping — a sub-check runs only when both the `current_status` field and the canonical fact are present, so a deployed project with no `current_status` block or no git context is audited cleanly.
- **`audit --git-head`** — overrides the HEAD used by the freshness guard; the default auto-resolves via `git -C <worktree> rev-parse --short HEAD` from the RAG's `current_status.git_worktree_path`, returning `None` (skip) on any failure (no git, not a repo, foreign-OS path).

## [v0.4.2] — 2026-06-11

**ENV-NORM — shell-execution normalization.** Makes `tmux-mcp` the primary shell/git/test transport (composed commands run verbatim; the `wsl-exec` wrapper-tax — `&&`/`;`/`|`/`$()` stripping, the `2>&1`→`1` orphan, `../..`→`//` collapse — is demoted to an atomic-only fallback), ships a `doctor` boot preflight and the guarded `add` verb, and rewrites the spec to v3.2.2 (incl. a `configure` sweep of the project RAG's GitHub deploy/metadata methods to tmux-primary). No new module (health 20/20), no schema/WAL/TLA+ change (drift gate `268149294421`). **1,142 tests.**

### Added — `doctor` preflight + guarded `add` verb (ENV-NORM)

- **`rag_kernel doctor`** — a deterministic, fail-closed preflight: (1) ENV — best
  working Python, broken-pip flags, and the fetch/VCS/shell tooling set, rendered
  from the *same* `build_env_audit` authority as `audit-env` (extracted, no second
  copy to drift); (2) LOCK — detects a stale `.git/index.lock` and, only with
  `--fix` and only when `diagnose_index_lock` proves it clearable (no git process
  running **and** aged past `--stale-after`), clears it; a LIVE lock is never
  touched. This turns the recurring stale-lock waste (E-042 / S61 / S62) into an
  enforced check. (3) SHELL — prints the prescribed first move (tmux-mcp primary),
  rendered from `operating_protocol.session_start_shell_rule` when `--rag` is given
  (no second copy of the rule). `--emit-runner PATH` writes the script-file runner
  template (the anti-mangling pattern from E-036/E-042).
- **`rag_kernel add`** — the missing CLI path to introduce a **new** canonical
  tracked item, wiring the existing `drift_store.add_items_file` (lifecycle verbs
  only *transition* existing items; `migrate_backlog` refuses a non-empty array).
  One validated spec → unique-id invariant → atomic write; a duplicate id, unknown
  status/kind, or a `SUPERSEDED` add without `--by` fails loud and writes nothing.
  Closes the long-flagged no-ADD-verb gap (E-037/E-040 context).
- CLI-only — **no new module** (health stays 20/20), **no schema/WAL/TLA+ change**
  (drift gate `268149294421` unchanged). +19 tests (`tests/test_doctor.py`,
  `tests/test_add_verb.py`).

### Changed — INIT spec v3.2.1 → v3.2.2: tmux-primary tool hierarchy (ENV-NORM)

- `INIT_UNIVERSAL_RUNTIME_KERNEL_v3.2.2.md` supersedes v3.2.1 (retained as history).
  §3a tool hierarchy makes **tmux-mcp the PRIMARY** shell/git/test transport (runs
  `&&`/`;`/`|`/`$()`/`2>&1` verbatim, no orphan `1` file); `wsl-exec` is demoted to
  an **atomic-single-command** fallback with its wrapper-tax documented; PowerShell
  is last resort; Desktop Commander excluded for parenthesized paths; the Cowork
  sandbox bash is banned. New `session_start_shell_rule` (first shell action of
  every session via tmux-mcp). §3 gains a `doctor`/preflight boot step
  (REPORT→PREPARE on the v3.2.1 Step-0 `audit-env`). The project RAG's
  `github_deploy_method`/`github_metadata_ops` were swept to tmux-primary via the
  sanctioned `configure` deep-merge. Regression `init --spec v3.2.2` inherits
  exactly 12 known-issues, validation PASSED.

### Changed — INIT spec v3.2.1: known-issues reconciliation + environment-audit hardening (INS-043/044/045)

- `INIT_UNIVERSAL_RUNTIME_KERNEL_v3.2.1.md` supersedes v3.2.0 (v3.2.0 retained as
  history). The §41 known-issues registry's two representations — the
  human-readable table and the machine-readable `rag-config` block that
  session-zero inherits — were **out of sync** (12 table rows vs 10 machine keys);
  they are now reconciled to the **same 12 universal keys**. Added
  `sandbox_mount_truncation` to the table, `dc_start_process_quotes` to the machine
  block, and a new `fetch_to_disk` entry to **both** (platform `web_fetch` lands
  off-mount; use `curl`/`wget` into the project tree — **INS-044**). The two
  project-specific entries (`git_worktree_location`, `pat_outside_workspace`) were
  scoped out of the universal template into per-project RAG registries, with a new
  Maintenance note codifying the universal-vs-project boundary.
- §37 environment audit now enumerates the fetch/VCS/shell `tooling` set
  (curl/wget/git/gh/jq/pwsh/powershell.exe, present/version/path) and references
  the `rag_kernel audit-env --json` command (**INS-045**, mirroring the v0.4.1
  runtime capability into the prompt spec). §31 session-zero gains **Step 0:
  environment audit** (**INS-043**). No schema change; regression `init --spec`
  inherits exactly 12 known-issues, validation PASSED.

### Fixed — stale version assertion in the test suite (E-041)

- `tests/test_drift_inc6.py::test_canonical_facts_live` pinned the kernel version
  as the frozen literal `"0.4.0"` and was not updated when v0.4.1 bumped
  `rag_kernel.__version__` to `0.4.1`. The assertion therefore failed **at the
  `runtime-v0.4.1` tag** — the "1,123 total, all passing" note in the v0.4.1
  section below did not reflect this one stale, **test-only** assertion. Corrected
  to `"0.4.1"`; the full suite is green again (1,123 passing), health 20/20, drift
  gate `268149294421` unchanged. **No runtime code is affected.** A follow-up to
  replace the frozen-literal version tripwire with a single-source assertion
  against `rag_kernel.__version__` (so a future bump can never re-redden it) is
  tracked for a later session.

## [v0.4.1] — 2026-06-09

Kernel hardening derived from the eBay Session-0 deployment audit (Track A1).
The RAG Runtime Kernel is a **universal** system deployed onto other projects, so
field findings on a deployment become test-result input that hardens the kernel
for every deployment (operating_protocol Rule 15). This release closes two
bootstrap failure modes and bundles the previously-unreleased DRIFT-ELIM
increment 6.

### Added — `audit-env` fetch/VCS/shell tooling enumeration (INS-045)

- `audit-env` now enumerates the canonical fetch/VCS/shell tool set — **curl,
  wget, git, gh, jq, pwsh, powershell.exe** — alongside the existing Python /
  pip / package-manager discovery. Each tool is reported with a `present` flag,
  `version`, and resolved `path` (in both `--json` and human output), so a fresh
  project deterministically knows its full tooling ground truth at Step 0 instead
  of rediscovering curl/wget/git live (the eBay S0 thrash, F-19). New `tooling`
  key in the `audit-env --json` payload.

### Changed — `init` is now fail-loud on a missing `--spec` (INS-046)

- `init` no longer silently builds a **void RAG** (no governance) when `--spec`
  is omitted. It now requires an explicit `--allow-void` to create an empty
  structural RAG; otherwise it prints a clear error naming both `--spec` and
  `--allow-void` and exits **non-zero** (the fix for F-09/R-5, silent governance
  loss). The guard fires before any work, including under `--dry-run`.
- **Migration note:** scripts that relied on `init` with no `--spec` creating a
  void RAG must now pass `--allow-void` explicitly.

Tests: **+7** (`tests/test_main.py` — 3 tooling-enumeration, 4 init fail-loud),
**1,123 total**, all passing; zero regressions; `guardgen --check` drift gate
green (sha `268149294421`, no model drift — no schema/WAL/TLA+ change); health
20/20; **no new module** (CLI-only changes in `__main__.py`).

### Added — DRIFT-ELIM: record migration + Rule 11 doc reconciliation (increment 6, INS-039)

Post-v0.4.0 hardening that closes the last un-audited region of the
single-source-of-truth model. The two remaining legacy state stores — the
`inference_ledger` dispositions and the ERROR_LOG `E-###` records — are folded
into the **same canonical `tracked_items` array** (new `kind=INFERENCE` /
`kind=ERROR`), so the session auditor governs their status too. The forensic
prose stays in `inference_ledger` / `ERROR_LOG.md`; only the *status* becomes
canonical.

- **`drift_store`** — a guarded, atomic additive migration path (`add_items` /
  `add_items_file`), the explicit fail-loud `inference_ledger` disposition→status
  bridge (`ledger_disposition_to_status`; `SCHEDULED`/`DONE`→`RESOLVED`,
  `DEFERRED`→`DEFERRED`, …), and `inference_specs_from_hot` deriving INFERENCE
  records from the ledger.
- **`drift_render`** — the task-backlog renders (`open_tasks` / `deferred_items`
  / Rule 12 backlog) are now scoped to `BACKLOG_KINDS` (TASK/MILESTONE/RELEASE)
  so the ~80 migrated forensic records do not leak into the task arrays (the
  E-040 parity guarantee holds byte-for-byte); record kinds get their own
  `render_records_by_kind` projection.
- **`drift_audit`** — three new fail-loud checks: **ledger consistency** (each
  `inference_ledger` disposition must match its canonical INFERENCE item),
  **record coverage** (every ledger entry + every `E-###` ERROR_LOG heading has a
  canonical item), and the **Rule 11 published-doc reconciliation** — headline
  facts (current-version module count + drift-gate sha vs the live kernel) plus
  id-anchored status-claim reconciliation (a doc claiming a RESOLVED record is
  still pending is the E-033/E-040 drift), with documented historical-line /
  CHANGELOG exemptions to stay deterministic. New `audit --docs-root` flag.
- **Pre-cutover gate:** the new ledger-consistency / record-coverage checks stay
  dormant until that record kind has been migrated (any item of the kind exists),
  so the capability ships without forcing the cutover — migration is a deliberate
  step the operator triggers.
- Migration **prepared and verified on a copy** of the project RAG (22 → 102
  `tracked_items`; `audit --strict` clean incl. doc reconciliation). The live
  project-RAG migration is **deferred** (operator validates v0.4.0 on a fresh
  project + reviews the migration guide first); until then the live RAG stays at
  22 items and audits clean via the pre-cutover gate. **+34 tests
  (`tests/test_drift_inc6.py`); 1,116 total**, all passing; zero regressions;
  `guardgen --check` green (sha `268149294421`, no model drift); health 20/20.
  Extends existing `drift_*` modules — no new module. **Unreleased** on `main`.

## [v0.4.0] — 2026-06-06

The single-shot **v0.4.0** ships two layers that were developed across many
increments on `main` and are released together: the **v4.0 Graph Orchestrator**
(deterministic DAG execution, deterministic-levels + OS-process parallel
scheduling, checkpoint-per-node, transactional rollback, an observable
agent/session supervisor, and runtime entry points `KernelApp.run_graph` / CLI
`graph run` / MCP `rag_graph_run`) and **DRIFT-ELIM**, the deterministic
project-state layer that makes a single canonical `tracked_items` array the sole
status authority — guarded item-lifecycle, atomic mutation API, lifecycle CLI,
deterministic renders of the legacy stores, and a fail-loud session auditor that
asserts render == canonical. 19 capability modules, health 20/20, **1,082
tests**, all passing; `guardgen --check` drift gate green (sha `268149294421`,
no model drift). The per-increment development history follows.

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
- Still **unreleased** at this increment: increments 6–7 remained before the v4.0 Graph Orchestrator was complete and runtime-wired.

### Added — Graph Orchestrator: OS-Process Parallel Work / Serialized Commit (GRAPH-ORCH, increment 6)
- **`Schedule.PROCESS_LEVELS`** — a level's work-bearing nodes run their pure, picklable `work(*work_args)` callable in OS subprocesses (`concurrent.futures.ProcessPoolExecutor`) for real parallelism on wide, I/O-bound levels.
- Workers are handed **no kernel handle** and return a picklable proposal payload; the **parent stays sole writer** and commits every node through the one serialized propose → validate → commit pipeline in deterministic **sorted-id order (not completion order)** under the project file-mutex, so HOT/WAL/checkpoints are **byte-identical** to `LEVELS`/`SEQUENTIAL`. Speedup is bounded to the work phase (Amdahl); the serialized-commit floor is the permanent integrity tax.
- A worker that raises (or returns a non-Mapping) routes its node to the same deterministic failure-closure / opt-in rollback path as a kernel-rejected proposal. **No** schema/WAL/TLA+/`guardgen` change — the commit path is untouched.
- 25 new tests (`tests/test_graph_process.py`). **878 total tests**, all passing; zero regressions; `guardgen --check` green (sha `268149294421`, no model drift); health 15/15. Still **unreleased**.

### Added — Graph Orchestrator: Agent/Session Supervisor (GRAPH-ORCH, increment 7 — last core increment)
- **`agent_supervisor.py`** — a thin, observable spawn/monitor/collect layer over the same pure off-process work contract. An opt-in `GraphExecutor(..., supervisor=AgentSupervisor())` replaces the bare pool in the `PROCESS_LEVELS` work phase with one that exposes **live per-worker PID, lifecycle state** (`PENDING → RUNNING → DONE | FAILED`), **exit code, and timing** as a renderable **`AgentView`** (the "agent view" UX).
- **Owns no authoritative state** — the supervisor is handed no kernel handle; it only spawns, observes, and collects payloads. The parent kernel stays sole writer and still commits in deterministic sorted-id order, so the supervised path is **byte-identical** to `PROCESS_LEVELS` without a supervisor (proven by equivalence tests). Default (`supervisor=None`) is exactly the increment-6 behaviour. **No** schema/WAL/TLA+/`guardgen` change — the commit path is untouched.
- **`agent_supervisor` is now registered** in `_KERNEL_MODULES`, `discover()`, and `cmd_health`; **functional module count reconciled 14 → 15**, **health now 16/16**.
- 30 new tests (`tests/test_agent_supervisor.py` + a registration test). **908 total tests**, all passing; zero regressions; `guardgen --check` green (sha `268149294421`, no model drift); health 16/16.
- The Graph Orchestrator's core increments (1–7) are now **all on `main`**. It remains **unreleased**: runtime-wiring and the v4.0 release / headline announcement are deferred until the orchestrator is wired into the runtime entry points.

### Added — Graph Orchestrator: Runtime-wiring (GRAPH-ORCH, final gate before v4.0)
- **`KernelApp.run_graph(nodes, *, schedule, stop_on_failure, rollback_on_failure)`** — the orchestrator is now invokable **through the kernel runtime**, not merely importable. Callers pass a JSON-serializable node spec (`{id, deps?, action, payload?}`); the kernel builds the DAG fail-loud and drives every node through its one serialized `propose → validate → commit → per-node-checkpoint` pipeline via `GraphExecutor`. The kernel stays **sole writer**; the method adds **no new state mutation, WAL event type, or schema** (the existing `GRAPH_NODE_EXECUTED` events remain the audit trail). Bad spec / unknown schedule / wrong state **fail closed** with an `{"error": …}` and no HOT mutation.
- **CLI `rag_kernel graph run <spec.json>`** — boots the app, runs the spec through `run_graph`, prints the JSON report (`--project`, `--session-id`, `--schedule`, `--stop-on-failure`, `--rollback-on-failure`).
- **MCP tool `rag_graph_run`** — the same entry over JSON-RPC (tool count 11 → 12).
- Only `sequential` and `levels` schedules cross the serialized (JSON/CLI/MCP) boundary; `process_levels` needs picklable `work` callables and stays an in-process `GraphExecutor` option.
- 17 new tests (`tests/test_runtime_wiring.py`) across all three surfaces + updated MCP tool-inventory assertions. **925 total tests**, all passing; zero regressions; `guardgen --check` green (sha `268149294421`, no model drift); health 16/16. Still **unreleased** — the v4.0 release / headline announcement (INS-026) is the next, separate milestone.

### Added — DRIFT-ELIM: Item-Lifecycle Pure Core (DRIFT-ELIM, increment 1)
- **`drift_control.py`** — generalizes the `guardgen` "rules-as-data, fail-loud" discipline from state-machine *transitions* to the operating protocol's own *project state*. Pure, deterministic, stdlib-only, zero-LLM.
- **`ItemStatus`** — the one constrained status vocabulary (`OPEN`, `IN_PROGRESS`, `RESOLVED`, `DEFERRED`, `SUPERSEDED`, `DISCARDED`); **`LIFECYCLE`** — the frozen transition table (`OPEN → IN_PROGRESS → {RESOLVED | DEFERRED | SUPERSEDED | DISCARDED}`, `DEFERRED ↔ OPEN`, three terminal), validated at import; **`legal_status_transition` / `assert_status_transition`** — fail-loud guards (`ItemStateError`) so an illegal move stops the caller, never a silent field-set.
- **`TrackedItem`** — immutable item with **one** canonical status, append-only `history`, the `superseded_by` invariant, and JSON round-trip. A status change returns a *new* item; the audit trail is intrinsic.
- **Scope boundary (deliberate):** not registered in `_KERNEL_MODULES` / `discover()` / `cmd_health` — the persistence/mutation layer, CLI, renders, and auditor land in later increments. 45 new tests (`tests/test_drift_control.py`). **970 total tests**, all passing; `guardgen --check` green (sha `268149294421`, no model drift); health 16/16. **Unreleased**.

### Added — DRIFT-ELIM: Deterministic Mutation API + Backlog Migration (DRIFT-ELIM, increment 2)
- **`drift_store.py`** — the persistence + mutation layer over increment 1. Normalizes project state into ONE array — **`tracked_items`** in `RAG_MASTER.json` — read into / written from a **`TrackedItemStore`** keyed by id (unique-id invariant, deterministic id-sorted serialization).
- **Guarded mutations only** — every status change routes through `TrackedItem.with_status`; an illegal transition, unknown id, or duplicate id **fails loud and writes nothing**. There is deliberately no "set the field" path — that path is exactly how status drift entered the project (E-034 / E-037 / E-039 / E-040).
- **Atomic persistence** — `mutate_hot` / `transition_in_file` load → apply a guarded transition → write via `persistence.atomic_write_json` (tmp → verify → `.bak` → rename), as one transaction. A tripped guard leaves the prior `RAG_MASTER.json` and its `.bak` intact. **No hand-edited JSON** — the bytes on disk are produced by the deterministic serializer over validated items.
- **Backlog migration** — `seed_items` / `migrate_backlog[_file]` perform the one-time seeding of `tracked_items` from the legacy `open_tasks` + `deferred_items` backlog (each item's status is an explicit human-authored proposal, not a parse of the legacy prose). Refuses to clobber a non-empty array unless `allow_overwrite`.
- **Scope boundary (deliberate):** not yet registered — the `rag_kernel resolve|defer` CLI + registration is increment 3; rendering the legacy stores / ERROR_LOG / status-report *from* this canonical array is increment 4; the fail-loud session auditor is increment 5. 32 new tests (`tests/test_drift_store.py`). **1002 total tests**, all passing; zero regressions; `guardgen --check` green (sha `268149294421`, no model drift); health 16/16. **Unreleased**.

### Added / Changed — DRIFT-ELIM: Lifecycle CLI + Registration (DRIFT-ELIM, increment 3)
- **Item-lifecycle CLI** — six top-level verbs over `drift_store`: `rag_kernel resolve | defer | reopen | start | discard | supersede <item-id> --session <S> [--rag PATH] [--reason …]` (and `supersede … --by <other-id>`). The verb selects the target `ItemStatus`; `drift_control`'s lifecycle guard decides legality and `drift_store` persists atomically. An illegal move, unknown id, or missing file **fails loud and writes nothing** (exit 1); `--dry-run` reports legality without writing. There is deliberately no "set the field" path on the CLI either.
- **`rag_kernel items [--status S] [--kind K] [--json]`** — a read-only render of the canonical `tracked_items` array (never mutates), the direct renderer the later status-report / ERROR_LOG renders (increment 4) build on.
- **`drift_control` + `drift_store` are now registered** in `_KERNEL_MODULES`, `discover()`, and `cmd_health`, and appear in the package manifest `modules` dict (both declare `never_bypass` → they surface as critical modules). The deliberate scope boundary that held across increments 1–2 is now closed.
- **Functional module count reconciled 15 → 17** (documented convention in `__init__.py`); **health is now 18/18** (17 capability modules + `__main__`).
- 21 new tests (19 in `tests/test_drift_cli.py` + 2 registration tests in `tests/test_fv_phase4_enforcement.py`; the manifest-count test updated 15 → 17). **1023 total tests**, all passing; zero regressions; `guardgen --check` green (sha `268149294421`, no model drift); health 18/18. Still **unreleased** — renders (increment 4) and the fail-loud session auditor (increment 5) remain before the single-shot v0.4.0.

### Added — DRIFT-ELIM: Renders (DRIFT-ELIM, increment 4)
- **`drift_render.py`** — deterministic, idempotent renderers that project the canonical `tracked_items` array into every other surface that records item status, making `tracked_items` the **sole authority** and every status mention a *derived render*: `render_open_tasks` (the legacy `open_tasks` array, now holding only non-terminal OPEN/IN_PROGRESS items, one stable line each), `render_deferred_items` (the legacy `deferred_items` array, DEFERRED only), `render_backlog_section` / `render_backlog_markdown` (the Rule 12 status-report backlog: Open / Blocked-or-user-gated / Deferred), and `render_error_log_backlog` (the ERROR_LOG backlog-status summary).
- **`apply_renders` / `apply_renders_file`** regenerate the legacy arrays in a HOT dict / RAG file *from* the canonical array, atomically (`atomic_write_json`: tmp → verify → .bak → rename). Pure on the canonical array (it is never mutated), so the operation is idempotent: rendering a rendered RAG is a no-op. Hand-editing the legacy arrays afterwards is exactly the drift the increment-5 session auditor will catch.
- **`rag_kernel render [--what open_tasks|deferred_items|backlog|error_log|all] [--apply] [--rag PATH] [--json]`** — dry-run prints the render; `--apply` rewrites the legacy `open_tasks` + `deferred_items` arrays atomically. The project's own backlog was regenerated through this path (dogfooded); the rich per-increment narrative now lives in the CHANGELOG and session directives, not duplicated as prose in `open_tasks`.
- **`drift_render` is now registered** in `_KERNEL_MODULES`, `discover()`, and `cmd_health` (declares `never_bypass` → critical: renders must not be hand-authored). **Functional module count reconciled 17 → 18**; **health is now 19/19** (18 capability modules + `__main__`).
- **Scope boundary (deliberate):** ERROR_LOG *forensic* E-### records and the `inference_ledger` dispositions are not migrated into `tracked_items` yet — only their backlog/status *view* is rendered here. Those record kinds and the fail-loud session auditor are increment 5. 35 new tests (`tests/test_drift_render.py` + render-CLI tests in `tests/test_drift_cli.py` + a registration test; the manifest-count test updated 17 → 18). **1051 total tests**, all passing; zero regressions; `guardgen --check` green (sha `268149294421`, no model drift); health 19/19. Still **unreleased** — the session auditor (increment 5) remains before the single-shot v0.4.0.

### Added — DRIFT-ELIM: Fail-Loud Session Auditor + Guarded Note Verb (DRIFT-ELIM, increment 5)
- **`drift_audit.py`** — the session-boundary auditor that turns the E-040 incident ("one item carried two contradictory statuses with no canonical field") into a standing, deterministic regression check instead of a manual reconciliation pass. Four checks, each zero-LLM: **render parity** (ERROR — the persisted legacy `open_tasks` / `deferred_items` arrays must equal the render of `tracked_items`; a hand-edit is caught), **supersede referential integrity** (ERROR — every `SUPERSEDED` item's `superseded_by` must point at a tracked id), **note/status contradiction** (WARNING — an active item whose `note` *claims* completion contradicts its own canonical status, the stale-note class INS-038), and **no side rule/state stores** (ERROR, Rule 13 / E-039 — no `MEMORY.md` / `feedback_*.md` / `project_*.md` inside the project root, scanned within the root **only** per the filesystem boundary).
- **Fail-loud contract** — `audit_hot` / `audit_file` return an `AuditReport` (never raise for a finding); **`assert_clean`** raises `DriftAuditError` on any ERROR (and, under `strict=True`, on warnings too). `rag_kernel audit [--rag PATH] [--strict] [--no-scan-root] [--json]` exits non-zero on a dirty audit so a divergence stops the session.
- **Guarded note-update verb (INS-038)** — `TrackedItem.with_note` (core) → `TrackedItemStore.set_note` / `set_note_in_file` (store, atomic, `.bak`-refreshed) → **`rag_kernel note <id> "<text>" --session <S>`** (CLI). Refreshing a note never changes `status` and appends no history event (a note is metadata, not the canonical authority); previously a note could only be set at creation/migration, so it went stale while status stayed correct — the exact gap the auditor's note check now also flags.
- **`drift_audit` is now registered** in `_KERNEL_MODULES`, `discover()`, and `cmd_health` (declares `never_bypass` → critical). **Functional module count reconciled 18 → 19**; **health is now 20/20** (19 capability modules + `__main__`).
- **Dogfooded** on the project's own RAG: the auditor reported render parity intact + flagged two stale notes (`DRIFT-ELIM`, `RECONCILE-PASS-RECURRING`); both were refreshed through the new guarded `note` verb, the legacy arrays re-rendered, and the auditor re-run **clean (0 findings)** — the full detect → guarded-fix → re-render → verify loop. 31 new tests (`tests/test_drift_audit.py`; the manifest-count test updated 18 → 19). **1082 total tests**, all passing; zero regressions; `guardgen --check` green (sha `268149294421`, no model drift); health 20/20. DRIFT-ELIM is feature-complete and ships with the Graph Orchestrator as the single-shot **v0.4.0** (this release).

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

**Current:** Spec v3.2.0 (51 sections) and rag_kernel v0.4.0 (19 modules, 1,082 tests). Zero-touch bootstrap, capability self-discovery, graduated POV, delta checkpoints, session logger, conflict auto-categorization (ENH-005), the formally-verified guard generator enforced at runtime (FV-PHASE3 + FV-PHASE4), the kernel-enforced context-truncation policy (M-009), the v4.0 Graph Orchestrator (DAG execution, deterministic-levels + OS-process scheduling, checkpoint-per-node, transactional rollback, agent/session supervisor, runtime-wired), and the DRIFT-ELIM deterministic project-state layer (canonical `tracked_items`, guarded lifecycle, deterministic renders, fail-loud session auditor) all shipped. Formal verification complete through Phase 2: 389,522 states (168,520 distinct), 8 safety + 3 liveness invariants, 0 violations.

**Next:** post-v0.4.0 — community engagement, donation links, and the v0.5 self-hosted SDK agent harness.

**Repository:** [github.com/arcadamarket/rag-runtime-kernel](https://github.com/arcadamarket/rag-runtime-kernel)
**Developer:** Artem Pakhol