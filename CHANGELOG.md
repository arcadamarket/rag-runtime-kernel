# Changelog

All notable changes to the RAG Runtime Kernel specification and tooling.

## [Unreleased]

## [v0.4.32] — 2026-07-14

_P1 control-integrity release — the P1/G1+G2 guard batch (ERRLOG-ID-GUARD, KA-CS-PROSE-DRIFT, KA-SECRETS-BOUNDARY) plus the REPORT-PRIORITY-GROUPS burn-down render (inc1+inc2), now deployed into the governance runtime. Runtime `__version__` 0.4.31 → 0.4.32, `__spec_version__` unchanged (3.2.6); no new capability module (still 19), health 20/20, drift gate `268149294421` unchanged (no schema/WAL/TLA+ change); full suite 1,733 → 1,806 green (+73). Module versions: `drift_audit` 1.11.0 → 1.13.0, `drift_store` 1.4.0 → 1.6.0, `drift_control` (LIFECYCLE) 1.0.0 → 1.1.0, `drift_render` 1.1.0 → 1.2.0._

**ERRLOG-ID-GUARD (P1/G1) — ERROR_LOG error-id headings are now a fail-loud auditor invariant.** A new `drift_audit.check_errlog_id_coherence` enforces the formally-verified `GUARD == I0 ∧ I1 ∧ I2` (`formal/ErrlogIdGuard.tla`, master theorem `GUARD ⇔ Legit`, TLC-exhaustive) over `ERROR_LOG.md`:

- **I0 (`errlog_id_malformed`)** — a heading that leads with an error id but is neither a definition (`id + ':' / '—'`) nor a recurrence (`id + recurrence marker`) fails loud, so the heading convention cannot silently drift (self-stabilizing).
- **I1 (`errlog_id_reuse`)** — an id defined by more than one heading is caught. This is the pre-S140 blind spot: `check_record_coverage` de-duped every heading before checking, so a reused id (two definitions) was invisible.
- **I2 (`errlog_id_dangling`)** — an id mentioned in a heading with no definition heading is caught.

The classifier is **positional, never prose-inferred** (the model's ML-lens mandate): a recurrence marker inside a descriptive parenthetical (`(recurring, non-fatal):`) or in the description tail stays a definition, and a legitimate `Dfn + Rcr` pair for one id is accepted (the naive "each id heads once" guard's false positive, refuted by counterexample in `ErrlogIdGuard_naive.cfg`). Scope is heading-only, matching the verified model. Wired into `audit_hot` (self-skips when no `ERROR_LOG.md`). `+tests/test_errlog_id_guard.py` (17); full suite 1,733 → 1,750 green (+17), health 20/20, no schema/WAL/`RAGKernel.tla` change (drift gate `268149294421` unchanged, no new capability module).

**KA-CS-PROSE-DRIFT (P1/G1) — every labeled RUNTIME RELEASE token in `current_status` is now guarded and refreshed, not just the leading one.** The E-043 freshness guard + refresh re-stamped only the leading version token and git HEAD, so a secondary `RUNTIME RELEASE vX` / `runtime-vX` claim embedded in the same `current_status` field stayed frozen while audit passed clean. A third label-anchored sub-check (shared `_CS_RELEASE_RE` / `_CS_RELEASE_FIELDS`) asserts **every** labeled release token equals live `__version__`, paired with an all-occurrences refresh (`_refresh_all_tokens`). Unlabeled `Prior: vX`, spec, and sub-component versions are untouched by construction. `drift_audit` 1.11.0 → 1.12.0, `drift_store` 1.4.0 → 1.5.0. +11 tests (1,750 → 1,761).

**KA-SECRETS-BOUNDARY (P1/G2) — a fail-loud secrets-boundary auditor.** New `drift_audit.check_secrets_boundary` (`drift_audit` 1.12.0 → 1.13.0): declared-secret values (defaults `config/**`, `.env*`, `*.pem` / `*.key`, `credentials*` / `secrets*`; widenable via `meta.secret_paths`, never narrowable) must not appear verbatim in the RAG. Findings are redaction-safe (`sha256:<12>` + source location, never the secret). Wired into `audit_hot` under the root-gated block; self-skips clean when no declared-secret file exists. +12 tests (1,761 → 1,773). Lane-A from the eBay S129 field audit (Rule 15 deployment-test triage).

**REPORT-PRIORITY-GROUPS inc1 — a structured per-item Rule 21 priority bucket in the canonical store.** `drift_control` (LIFECYCLE) 1.0.0 → 1.1.0: `TrackedItem.priority_group` (P1..P5 / `''` unassigned), fail-loud validation, `with_priority()`, and `PRIORITY_GROUPS` / `ALLOWED_PRIORITY_GROUPS`; omitted from `to_dict` when empty (the `increments[]` precedent) so untouched items serialize byte-for-byte. `drift_store` 1.5.0 → 1.6.0: guarded `store.set_priority` + `set_priority_in_file` (atomic, `.bak`-refreshed; status never touched, no history event). New `priority` CLI verb (dry-run, fail-loud on unknown id / bad bucket). +27 tests (1,773 → 1,800).

**REPORT-PRIORITY-GROUPS inc2 — the priority burn-down render in the canonical report.** `render_priority_burndown()` (`drift_render` 1.1.0 → 1.2.0) groups the task backlog by each item's `priority_group` into Rule 21 P1..P5 + an Unassigned catch-all, listing active / deferred / shipped-this-session items id-sorted, empty groups omitted. Emitted as a subsection of `render_status_report` section 4 (design option A — one report carries the burn-down; no renumbering of sections 5–7, so the deterministic close render carries it with zero hand-touch). Until items are bucketed, the render honestly shows everything under Unassigned. +6 tests (1,800 → 1,806).

## [v0.4.31] — 2026-07-12

**REPORT-VERB-FIDELITY + REPORT-VERB-WIRE-CLOSE — the canonical status report now renders faithfully and is machine-emitted at close.** Two related fixes to the deterministic report (Rule 12):

- **REPORT-VERB-FIDELITY** — the report render is brought back in line with the canonical 7-section spec on three points:
  - *Section 2* is now a **planned-vs-actual** table (`# | Increment | Plan | Status | RAG | Commit-S`) scoped to the **current build's** increments — sourced from a new `TrackedItem.increments[]` field — instead of dumping every historical milestone/release row. `Increment` is a frozen, all-string sub-record; it is display metadata only and never competes with an item's canonical `status`. Omitted from serialization when empty, so existing items round-trip byte-for-byte.
  - The at-a-glance **milestone cell** falls back to the milestone/release **shipped this session** (then the newest release) before the bare "(no active milestone)", so a session that completes its milestone still names it.
  - The **drift gate** can now reach GREEN from a **deployed package that ships no `formal/RAGKernel.tla`** (the governance-runtime norm). `guardgen` bakes a `GUARDS_SELF_SHA256` self-hash of the generated guard tables plus a `verify_self()`; when the `.tla` source is unreachable, the gate self-verifies its own guard integrity from baked provenance (True iff intact, False if hand-edited post-generation, None only if the machinery is absent). This removes the false-AMBER "unverified" a genuinely-green released build used to read. The honesty invariant holds: an unknown gate still pulls to AMBER (Rule 14).

- **REPORT-VERB-WIRE-CLOSE** — `session-end` now **machine-renders the deterministic canonical report verbatim** from the just-checkpointed RAG as the mandated close artifact, so the closing report can never be hand-authored (the S136 close-drift root cause). Rendering is the attestation; the report's external scalars (`--tests`, `--released`, `--claims-ok`, …) mirror the `report` verb, and `--no-report` opts out.

Tests: `+test_report_verb_fidelity.py` (12) and 2 new WIRE-CLOSE tests; full suite green.

## [v0.4.30] — 2026-07-11

**REPORT-VERB — the closing/transfer status report is now a deterministic kernel render, not a hand-authored prose block.** Rule 12 (`report_before_transfer`) has always required the 7-section canonical status report to be a *deterministic render of the RAG canonical fields* ("the report equals the RAG by construction"), but until now the render existed only as a discipline the agent performed by hand — so a hand-assembled report could drift from the RAG even when the RAG itself was clean (the exact transfer-drift gap behind the operator's manual report-paste cross-check).

- **`report` verb** (`drift_render.render_status_report` + `__main__.cmd_report`) — renders the full 7 sections (At-a-glance R/A/G table + verdict, Build milestones/releases, This session, Backlog, Risks & deviations, Ledger & errors, Verification & handoff) as a pure, deterministic projection. Sourcing discipline (operator decision S136): every fact is **structured** (read from `meta` / `tracked_items` / `inference_ledger`), **live-computed** by the caller (health, drift-gate sha, git HEAD, `.bak` parity, bytes), or a genuinely **external** scalar passed as an explicit arg (`--context-pct`, `--tests`, `--released`, `--claims-ok`). It NEVER scrapes `current_status` prose and NEVER invents a value: an unknown fact renders `n/a` and can only pull the verdict toward AMBER, never to a false GREEN (Rule 14 increment-status-honesty). R/A/G thresholds are objective (RED = any hard gate failing; AMBER = unreleased or any gate unknown; GREEN = released AND tests/health/drift green AND repo-claims reconciled). The backlog is fully enumerated line-by-line, never a bare count (STRICT-OBEY / Rule 16). The renderer reuses `render_backlog_section` — no duplicate backlog logic.

Runtime `__version__` 0.4.29 → 0.4.30; `__spec_version__` unchanged (3.2.6). The renderer lives in the existing `drift_render` module — **no new capability module** (still 19), health 20/20, drift gate `268149294421` unchanged (no schema, WAL-format, or TLA+ change). `DRIFT_RENDER_VERSION` 1.0.0 → 1.1.0. Full suite 1,693 → 1,719 green (+26).

## [v0.4.29] — 2026-07-11

**KA-RECON-PROXIMITY + KA-RECON-DECLARE — the two gaps that blocked dogfooding the close-time reconciliation (KA-13) on this project, now both closed.** v0.4.28 wired the Rule 11 published-doc reconciliation into the session close, but two follow-on gaps kept it from being turned on here: it false-fired on long paragraph lines, and there was no governed way to declare where the published docs live.

- **KA-RECON-PROXIMITY** (`drift_audit.check_repo_claim_reconciliation`) — the id-anchored §2 check paired a PENDING word with a RESOLVED id at *line* granularity: any pending word anywhere on a long, multi-clause paragraph line was read as the status of any RESOLVED id elsewhere on that same line. The live false positive was ROADMAP's v0.4.27 entry, whose "`--dry-run` prints the planned old→new token diff" clause sits three sentences away from the RESOLVED `KA-CS-REFRESH` / `FIX-4` ids named earlier on the line. The check now segments the line on sentence / semicolon boundaries (`. ` / `; ` — never dashes or table pipes, so version dots and single-line "`ID — planned`" claims are untouched) and requires the pending word and the id to co-occur in the **same sentence**. It can only make §2 more conservative, so docs that already reconcile clean stay clean. New `_SENTENCE_SPLIT_RE`.
- **KA-RECON-DECLARE** (`__main__` — `configure`) — KA-13 resolves its published-doc surface root from `meta.reconciliation_docs_root`, but no governed verb *set* that key, so declaring it meant a hand-edit of `RAG_MASTER.json` — exactly the drift the project forbids. `configure` gains a `--reconciliation-docs-root PATH` flag that rides the existing `deep_merge` + `atomic_write_json(mirror_bak=True)` path, so the declaration is atomic and keeps HOT↔.bak parity by construction. `--context` is now optional (the flag may be used alone or alongside a context overlay; an explicit flag wins over any value a context file carries). `--consume` without a `--context` fails loud rather than reaching an unlink on a `None` path.

Runtime `__version__` 0.4.28 → 0.4.29; `__spec_version__` unchanged (3.2.6). CLI / audit-only — no new module (19), health 20/20, drift gate `268149294421` unchanged (no schema, WAL-format, or TLA+ change), `DRIFT_STORE_VERSION` unchanged (1.4.0). Full suite 1,680 → 1,693 green (+13: 6 proximity + 7 declare).

## [v0.4.28] — 2026-07-11

**KA-13 + KA-19 — the close-time published-doc reconciliation, now actually wired into `session-end` and no longer mis-firing on id substrings.** The spec (v3.2.6, KA-11 inc3) declared that the session close must reconcile every published status-claim against the tracked records, but the runtime close never did it — `_drive_close` ran its step-3 audit with `docs_root=None`, so the Rule 11 doc reconciliation stayed dormant at close (the exact recurring pass RECONCILE-PASS-RECURRING wanted mechanized). And the reconciliation's id matcher used a bare substring test, so a RESOLVED short id spuriously matched a longer, unrelated id on the line.

- **KA-13** (`__main__` — `session-end` / `session-resume`) — the close now resolves a `docs_root` for its step-3 audit with a back-compatible precedence: `--no-reconcile` (opt-out) > `--docs-root PATH` (per-invocation override) > `meta.reconciliation_docs_root` (the project's declared surface root) > skip. A declared/override path may be absolute or relative (relative resolves against the project root); an un-migrated RAG that declares nothing and passes no flag closes byte-for-byte as before (`docs_root=None`, reconciliation dormant). New `_resolve_close_docs_root` planner; `--docs-root` / `--no-reconcile` added to both `session-end` and `session-resume`. This is the runtime half of the KA-11 inc3 spec step — the published docs are reconciled against the live canonical facts as part of the fail-loud close audit.
- **KA-19** (`drift_audit.check_repo_claim_reconciliation`) — the id-anchored pending-status check matched a tracked id with `rid in ln` (bare substring), so a RESOLVED `FIX-1` was reported "still pending" against a line that only mentioned `FIX-12` (a different, OPEN id); the longest-first ordering only masked it when the longer id was itself RESOLVED and on the line. The id is now matched at its token boundaries (`(?<![\w-])…(?![\w-])`), so neither a digit/letter suffix (`FIX-12`) nor a hyphen extension (`FIX-1-alpha`) can trigger a shorter id, while an exact, boundary-delimited mention still fires.
- **RECONCILE-PASS-RECURRING** — the recurring repo-claim↔reality↔record reconcile pass is now mechanized by the KA-13 close wiring (declare `meta.reconciliation_docs_root` and every governed close reconciles the surfaces automatically), closing the standing manual-pass item.

Runtime `__version__` 0.4.27 → 0.4.28, `__spec_version__` unchanged (3.2.6). CLI/close/audit-only — no new module (19), health 20/20, drift gate `268149294421` unchanged (no schema, WAL-format, or TLA+ change), `DRIFT_STORE_VERSION` unchanged (1.4.0), **1,664 → 1,680 tests** (+16). Released S134.

## [v0.4.27] — 2026-07-06

**KA-CS-REFRESH — a governed `refresh-current-status` verb that re-stamps the `current_status` machine-facts, closing the last hand-edit path the freshness guard left open.** `current_status` denormalizes two facts whose authority lives OUTSIDE the RAG — the runtime `rag_kernel.__version__` and the published git HEAD — and the E-043 freshness guard (`drift_audit.check_current_status_freshness`) fails loud when the narrative drifts from the live authority. But there was no governed way to *repair* that drift: a mid-session dev commit bumped the version / moved HEAD, `current_status` went stale, and the only fix was a hand-edit of `RAG_MASTER.json` — exactly the drift the project forbids. It cost a manual atomic-writer reconcile two sessions running (S116, S127).

- **`refresh-current-status`** (`drift_store` / CLI) — the governed, atomic **repair half** of the E-043 guard. Re-stamps the runtime-version token (`current_status.rag_kernel_version` ← live `rag_kernel.__version__`) and the published git HEAD (`current_status.github_repo`'s "LATEST COMMIT &lt;sha&gt;" ← the auto-resolved worktree HEAD, reusing the auditor's own `_resolve_git_head`), plus optionally the `unit_tests` count (`--tests`, never fabricated). Backed by new `drift_store.compute_current_status_refresh` (pure planner) / `refresh_current_status_file` (atomic), reusing the FIX-4 `tmp → verify → .bak → rename` byte-parity write path so a `current_status` mutation keeps HOT↔.bak parity by construction. Deterministic and **idempotent** — a no-op (no write, `.bak` untouched) when already fresh; `--dry-run` prints the planned old→new token diff; `--strict` fails loud on a missing target token. It re-stamps only the machine-fact token in place, leaving surrounding narrative to the agent (increment_status_honesty).
- **DRY invariant** — the leading-token field-names + regexes the guard uses to *detect* staleness (`_CS_VERSION_FIELD`, `_CS_HEAD_FIELDS`, `_CS_VERSION_TOKEN_RE`, `_CS_HEAD_RE`) moved **down** into `drift_store` (the lower module) as the single source of truth; `drift_audit` imports and re-exports them. Detection and repair now read the IDENTICAL token definitions and can never disagree — the same pattern as the shared date coercers.

Runtime `__version__` 0.4.26 → 0.4.27, `__spec_version__` unchanged (3.2.6). CLI/store/audit-only — no new module (19), health 20/20, drift gate `268149294421` unchanged (no schema, WAL-format, or TLA+ change), `DRIFT_STORE_VERSION` `1.3.0 → 1.4.0`, **1,639 → 1,664 tests** (+25). Released S128.

## [v0.4.26] — 2026-07-06

**T1 GATE — three governance-hardening fixes that close the last self-hosting gate items (KA-CTX-RAGFLAG + KA-CKPT-PARITY-GATE + KA-18).** A bundled runtime release of the T1 kernel-fix gate cleared in S126; all three land as CLI/checkpoint/context hardening with no schema, WAL-format, or TLA+ change.

- **KA-CTX-RAGFLAG** (`context`) — `context set/get/list` mis-routed when handed a `--rag <file>` path (it expected a directory), so a caller passing the RAG file — the natural invocation — silently wrote to the wrong location. The `context` verb now tolerates a `--rag <file>` and routes the partition to the file's parent directory, matching every other verb's `--rag` semantics.
- **KA-CKPT-PARITY-GATE** (`checkpoint`, E-049) — a mid-session dev commit could leave the legacy `open_tasks` / `deferred_items` renders stale relative to the canonical `tracked_items`, and `checkpoint` sealed anyway. `checkpoint` now **auto-renders** the legacy arrays from `tracked_items` at seal, so render-parity holds by construction, plus a defensive fail-loud if a stale render is ever detected at the gate (`audit`'s render==canonical invariant enforced at the checkpoint boundary, not only at session-end).
- **KA-18** (`checkpoint`, E-044/E-045) — a permanent guard against the recurring session-start ordering slip: `checkpoint` now refuses to run without an open session log (the mechanized `session-start` must precede it). CLI default is ON; `--no-require-session-log` is the explicit, audited bypass.

Runtime `__version__` 0.4.25 → 0.4.26, `__spec_version__` unchanged (3.2.6). CLI/checkpoint/context-only — no new module (19), health 20/20, drift gate `268149294421` unchanged (no schema, WAL-format, or TLA+ change), `DRIFT_STORE_VERSION` unchanged (1.3.0), **1,623 → 1,639 tests** (+16). Released S127.

## [v0.4.25] — 2026-07-04

**KA-CUTOVER-GATE — the record-coverage cutover gate now counts only non-retired records, plus a governed `un-add` verb that makes a mis-`add` recoverable.** Two coupled defects made a mis-kinded forensic item (`kind=ERROR` / `INFERENCE`) unrecoverable: `check_record_coverage` treated a kind as "migrated" (gate ON) by ANY item of that kind regardless of status, so a single mis-`add` latched the per-kind cutover gate ON and demanded full ERROR_LOG / ledger coverage; and the store had no un-add path, so the mis-kinded item could be discarded or superseded but never removed — and since discard/supersede leave `kind` intact, the status-blind gate stayed latched. A mis-kind became a deadlock.

- **Gate fix** (`drift_control` / `drift_audit`) — new `drift_control.RETIRED_STATUSES` = `{SUPERSEDED, DISCARDED}` (a strict subset of `TERMINAL_STATUSES`; `RESOLVED` deliberately stays counted, since a completed record is still a real canonical fact). `check_record_coverage` now counts only NON-retired `INFERENCE` / `ERROR` members, so retiring a mis-kinded item lets the per-kind gate fall back to its correct pre-migration (empty) state instead of latching ON forever.
- **`un-add` verb** (`drift_store` / CLI) — new `TrackedItemStore.remove` + atomic `drift_store.remove_item_file` + the `un-add` CLI verb: the guarded, atomic inverse of `add`, permitted ONLY on a PRISTINE (empty-history) item, so a real, transitioned item is protected (use discard/supersede for those). An unknown id or a historied item fails LOUD and writes nothing. Recovers a mis-`add` without a hand-edit — the exact manual-JSON drift the project forbids (E-037 / E-040). Either fix alone breaks the deadlock; together they give clean recovery.

Runtime `__version__` 0.4.24 → 0.4.25, `__spec_version__` unchanged (3.2.6). CLI/store/audit-only — no new module (19), health 20/20, drift gate `268149294421` unchanged (no schema, WAL-format, or TLA+ change), `DRIFT_STORE_VERSION` `1.2.0 → 1.3.0`, **1,606 → 1,623 tests** (+17). Released S123.

## [v0.4.24] — 2026-06-30

**UPDATE-RULE-VERB — governed re-set of an existing `operating_protocol` rule through the guarded atomic store.** The counterpart to `add-rule`, with the inverse default and two capabilities `add-rule` lacks. Until now the kernel could *append* a new `operating_protocol` rule (`add-rule`, fail-loud on an existing key) but had no governed way to *re-set* one — and a hand-edit of `RAG_MASTER.json` is exactly the drift the project forbids — so trimming a structured rule like `tool_hierarchy` one entry at a time was blocked.

- **`update-rule`** (`71befae`, S116) — re-sets a rule that **must already exist** (UPDATE default; `--create` to add instead, the mirror of `add-rule`'s default). Two new capabilities over `add-rule`: **`--json`**, so a structured rule (e.g. `tool_hierarchy`) can be re-set with a dict/list value wholesale rather than only a string; and **`--subkey`**, to trim or re-set one sub-entry of a dict rule at a time. Backed by new `drift_store.set_operating_protocol_rule` (pure) / `set_operating_protocol_rule_file` (atomic), which reuse the FIX-4 atomic `tmp → verify → .bak → rename` byte-parity `.bak`-mirror write path, so an `operating_protocol` mutation keeps HOT↔.bak parity by construction. **+34 tests.**

Unblocks the `tool_hierarchy` dict-trim — the last remaining piece of **RAG-LEAN-PROSE**. Runtime `__version__` `0.4.23 → 0.4.24`, `__spec_version__` unchanged (`3.2.6`). CLI/store-only — no new module (19), health 20/20, drift gate `268149294421` unchanged (no schema, WAL-format, or TLA+ change), `DRIFT_STORE_VERSION` unchanged (`1.2.0`), **1,569 → 1,603 tests** (+34). Released S117.

## [v0.4.23] — 2026-06-23

**KA-14 + KA-16 + KA-17 — the session-resilience arc (bundled runtime release).** Packages the three runtime increments merged to `main` since v0.4.22, hardening the session boundary against the fresh-deploy and interrupted-close failure modes the eBay Session-Zero audit surfaced.

- **KA-16** (`aa34e97`, S106) — **atomic, resumable session close.** A `session_close` marker tracks the close as a forward-progress transaction and sets `transfer_ready` only after checkpoint + idempotent ERROR_LOG fold + logger close + audit all pass; `session-resume` finishes an interrupted close, and the carry-forward gate fails loud on a stranded `transfer_ready=false` prior close. +12 tests.
- **KA-14** (`e34691b`, S107) — **session-start rule-load attestation gate.** Two-phase token-attested start (`BOOT → RULES_LOADED(attested) → READY`): phase 1 renders the `operating_protocol` rule digest, writes a `rule_load` marker (`attested=false`) and a sha256 token without opening the logger; phase 2 `--attest <token>` verifies the token against the live digest before opening the logger. Closes the fresh-deploy unloaded-rules root cause. `--no-attest-gate` retains the one-shot path for CI. +15 tests.
- **KA-17** (`dc5f0c0`, S109) — **declared, single-sourced supported-Python matrix (3.12–3.14).** `SUPPORTED_PYTHON` single-sourced in `__init__` and manifest-injected, a pure `python_support_status()` classifier, and a `doctor` ENV check that blocks below-floor interpreters. Reconciles the former unsubstantiated `>=3.10` claim across the manifest + README + 4 docs (Rule 11). Validated: full suite under 3.12, import/discovery smoke under 3.13 + 3.14. +7 tests.

The companion **KA-15** token-economy / context-emission doctrine (bounded tool-output, malformed-emission as a circuit-breaker strike) is live in the project `operating_protocol`; its INIT-spec seeding (v3.2.7) and the `python314_pip` known-issue correction are tracked for the next spec bump.

Runtime `__version__` `0.4.22 → 0.4.23`, `__spec_version__` unchanged (`3.2.6`). No new module (19), health 20/20, drift gate `268149294421` unchanged (no schema, WAL-format, or TLA+ change), **1,569 tests**. Bundled release of the KA-14..17 session-resilience arc; the eBay deploy inherits via an `init --spec` upgrade. (S110)

## [v0.4.22] — 2026-06-21

**KA-11 inc4 — TierC kernel reconciliation-surface manifest population + docs reconcile; the runtime release that bundles KA-11 inc1–4.** This release closes **KA-11** (universalize the repo-claim↔reality↔record reconciliation pass) and completes the Track A kernel-hardening arc the eBay Session-Zero audit surfaced.

- **inc1–2** (`5b03f8e`) — the per-project `meta.reconciliation_surfaces` manifest: schema + reader (`drift_audit.reconciliation_surfaces`), wired through `audit_file` and `audit --docs-root`. Replaces the formerly hardcoded kernel-specific doc list with a per-project declaration (falling back to the universal default `README.md` / `CHANGELOG.md` / `docs/ROADMAP.md`), so the Rule 11 auditor is no longer kernel-repo-specific while staying byte-for-byte back-compatible for any RAG that has not declared a manifest. Also fixes a TierA leak.
- **inc3** (`5ef6395`) — the TierB INIT-spec session-end **claim-reconciliation pass** (§50, first step: reconcile → checkpoint → close → audit). Spec self-version `v3.2.5 → v3.2.6`.
- **inc4** — populates the kernel's own **TierC** manifest (`meta.reconciliation_surfaces` = README / CHANGELOG / docs/ROADMAP) and reconciles the published docs against the live canonical facts (`rag_kernel.__version__`, capability-module count, drift-gate sha).

Runtime `__version__` `0.4.21 → 0.4.22`, `__spec_version__` `3.2.5 → 3.2.6`. No new module (19), health 20/20, drift gate `268149294421` unchanged (no schema, WAL-format, or TLA+ change), **1,535 tests**. Eleventh increment of the KA-10 GOVERNANCE-DETERMINISM initiative; unblocks the eBay re-init (INS-047). (S104)

## [v0.4.21] — 2026-06-21

**KA-7 — fail-loud audit when governance advanced past the session-log trail (GOVERNANCE-DETERMINISM / KA-10 arc).** The **dual of KA-1**: where KA-1 catches a *completed* session log newer than the checkpoint (a session ran to a clean close but `meta.written_by_session` never caught up), KA-7 catches the inverse — `meta.written_by_session` advanced **past** the newest session-log-that-has-entries (`cp_ord > max logged ordinal`), i.e. the checkpoint moved forward but the observability trail did not. Together they close both halves of the eBay Session-Zero freeze signature ("logs stopped at S1 while the machine ran on"). New `drift_audit.check_observability_coherence` (ERROR) plus a `_session_log_has_entries` helper that tells a real activity log from a marker-only / empty file. The two checks are **mutually exclusive by construction** — KA-1 fires only when a completed log is newer than the checkpoint, KA-7 only when the checkpoint is newer than every log — so a RAG can trip at most one and they never double-report. Self-skips a `BOOTING` / un-stamped / malformed-id RAG and a no-logger project, so a healthy RAG audits clean. Wired into `audit_file` so it runs at every session boundary. Dogfooded: a synthetic RAG whose `written_by_session` outran its newest non-empty log fails loud; the live RAG audits clean. `DRIFT_AUDIT_VERSION` 1.9.0 → **1.10.0**; no schema, WAL-format, or TLA+ change (drift gate `268149294421` unchanged), no new module (19, health 20/20). Suite 1,499 → **1,524** (+25). Ninth runtime increment of the KA-10 GOVERNANCE-DETERMINISM initiative (after KA-4, KA-6, KA-9, KA-3, KA-2A, KA-2B, KA-5, KA-1). (S100)

## [v0.4.20] — 2026-06-21

**KA-1 — fail-loud audit on a ran-but-never-checkpointed session (GOVERNANCE-DETERMINISM / KA-10 arc).** Closes the auditor blind spot behind the **S88 eBay headline** ("deployed auditor passed clean while governance frozen at S0/seq1"): an agent ended sessions on `configure`/`audit` without ever running `checkpoint`, so `meta.written_by_session` stayed frozen while later sessions ran — and `audit --strict` never noticed. The KA-4 close gate already prevents the *live* session from closing un-checkpointed; KA-1 adds the missing *at-rest audit* invariant so even an already-frozen RAG fails loud. New `drift_audit.check_uncheckpointed_session` (ERROR) flags any session log beside the RAG (`session_log_<sid>.jsonl`) that both carries a `session_end` marker (ran to a **clean close**) **and** has a numeric session ordinal greater than `meta.written_by_session` — the freeze signature. It keys on `session_end` so the in-flight current session (still-open / detached / crashed, no end marker) is never false-positived, ignores any log with an ordinal `<= written_by_session` (a historical checkpointed session whose log persists), and self-skips a `BOOTING` / un-stamped / malformed-id RAG and an empty RAG directory — so a healthy RAG (newest completed log *is* `written_by_session`) audits clean. Wired into `audit_file` so it runs at every session boundary. `DRIFT_AUDIT_VERSION` 1.8.0 → **1.9.0**; no schema, WAL-format, or TLA+ change (drift gate `268149294421` unchanged), no new module (19, health 20/20). Suite 1,475 → **1,499** (+24). Eighth runtime increment of the KA-10 GOVERNANCE-DETERMINISM initiative (after KA-4, KA-6, KA-9, KA-3, KA-2A, KA-2B, KA-5). (S99)

## [v0.4.19] — 2026-06-20

**KA-5 — single-source the `@rag-kernel-manifest` version (GOVERNANCE-DETERMINISM / KA-10 arc).** Closes **E-046**: the package manifest embedded in `rag_kernel/__init__.py`'s docstring hardcoded `version` and `spec_version` literals that had silently drifted from the live authorities — frozen at `0.4.7` / spec `3.2.2` while the kernel had moved on to `0.4.18` / spec `3.2.5` — and nothing in `audit --strict` caught it. The fix makes the version fields **single-sourced** and **derived, not duplicated**: the manifest docstring no longer carries `version` / `spec_version` literals at all; `rag_kernel.__version__` and the new `rag_kernel.__spec_version__` module constants are the sole source of truth, and `discover()` injects them into the package manifest so every consumer sees the live values and there is no second copy to drift. New `drift_audit.check_manifest_version_binding` makes this a standing regression check: a fail-loud (ERROR) guard that fires if (1) an authority constant is missing/empty, (2) a `version` / `spec_version` literal is ever re-introduced into the docstring and disagrees with its authority, or (3) `discover()`'s injected package manifest disagrees with the authorities. It is pure introspection over the kernel package (no RAG input) and is wired into `audit_hot` so it runs at every session boundary; it self-skips only if `rag_kernel` cannot be imported. Dogfooded: a re-introduced stale literal and a deleted authority each fail loud; the live package binds clean. `DRIFT_AUDIT_VERSION` 1.7.0 → **1.8.0**; no schema, WAL-format, or TLA+ change (drift gate `268149294421` unchanged), no new module (19, health 20/20). Suite 1,469 → **1,475** (+6). Seventh runtime increment of the KA-10 GOVERNANCE-DETERMINISM initiative (after KA-4, KA-6, KA-9, KA-3, KA-2A, KA-2B). (S98)

## [v0.4.18] — 2026-06-20

**KA-2 increment B — governed `sessions_recent` row-repair/dedup verb (GOVERNANCE-DETERMINISM / KA-10 arc).** The *repair* half that completes KA-2: increment A (v0.4.17) made the kernel **fail loud** on duplicate-bootstrap `sessions_recent` rows (two rows sharing a checkpoint timestamp `d` — the eBay Session-Zero S0/S1 signature) but offered no governed way to fix them, and a hand-edit of the array is exactly the drift the project forbids. New `drift_store.dedup_sessions_recent` (pure) and `dedup_sessions_recent_file` (atomic) remove the phantom duplicate(s), keeping exactly one row per checkpoint timestamp; **group-correct** (handles 3+ rows sharing one instant), **idempotent** (a second run removes nothing), **order-preserving**, and honoring `--keep first|last`. Rows with a missing/blank `d` and non-dict rows are never touched. The file verb writes through the atomic `tmp → verify → .bak → rename` path (FIX-4 byte-parity `.bak` mirror) and is a true **no-op when the ledger is clean** (no spurious `.bak` churn). New CLI `dedup-sessions [--rag …] [--keep first|last] [--session …] [--dry-run]`. **Single source of truth — detect == repair:** the duplicate-detection predicate (`sessions_recent_duplicate_pairs` / `_sessions_recent_key`) now lives in `drift_store` and is consumed by **both** the KA-2 auditor (to flag) and this verb (to repair), so a row the auditor flags is exactly a row the verb removes; the shared date coercers moved down with it and are re-exported from `drift_audit` (public surface unchanged). Also unblocks the eBay deploy's B-3 (`sessions_recent` dedup), which was waiting on this verb. Dogfooded: a synthetic RAG with the S0/S1 shared-timestamp defect dedups to clean and then audits clean; the live project RAG is untouched (no duplicates). `DRIFT_STORE_VERSION` 1.1.0 → **1.2.0**; no schema, WAL-format, or TLA+ change (drift gate `268149294421` unchanged), no new module (19, health 20/20). Suite 1,448 → **1,469** (+21). KA-2 is now **RESOLVED**. Sixth runtime increment of the KA-10 GOVERNANCE-DETERMINISM initiative (after KA-4, KA-6, KA-9, KA-3, KA-2A). (S97)

## [v0.4.17] — 2026-06-20

**KA-2 increment A — `sessions_recent` duplicate-bootstrap auditor (GOVERNANCE-DETERMINISM / KA-10 arc).** Closes another blind spot the eBay Session-Zero deploy exposed: its `sessions_recent` ledger carried duplicate *bootstrap* rows — S0 and S1 minted at the **same timestamp**, one of which was never actually run — yet `audit --strict` reported 0 findings and there was no governed way to repair them. New `drift_audit.check_sessions_recent_coherence` fails loud (ERROR) when two rows share a checkpoint timestamp `d` (compared on the parsed UTC instant, so a `Z`-suffixed value and its offset twin collide; an unparseable `d` falls back to the exact literal, catching two identical `<ISO>`-class placeholders). The check is **order-agnostic by design**: the project legitimately writes `sessions_recent` both oldest-first (this kernel's live RAG, S92…S95) and newest-first (a fresh `init --auto-ready` RAG, `[S1, S0]`), and one session legitimately spans multiple rows (this kernel's S95/S95 multi-checkpoint pair, distinct timestamps) — so a *shared timestamp* is the only phantom-duplicate signal safe across every legitimate shape; directional id/timestamp monotonicity would false-positive on a clean deploy and was deliberately not enforced. Self-skips when `sessions_recent` is absent / not a list / has fewer than two rows, or a row's `d` is missing. Wired into `audit_hot` so it runs at every session boundary. Dogfooded: a synthetic RAG with the eBay S0/S1 shared-timestamp defect fails loud on `sessions_recent_coherence`; the live project RAG and a fresh `init --auto-ready` RAG both audit clean. This is **increment A (detection)**; the paired **increment B** — a governed row-repair/dedup verb — remains open (KA-2 stays IN_PROGRESS). `DRIFT_AUDIT_VERSION` 1.6.0 → **1.7.0**; no schema, WAL-format, or TLA+ change (drift gate `268149294421` unchanged), no new module (19, health 20/20). Suite 1,427 → **1,448** (+21). Fifth runtime increment of the KA-10 GOVERNANCE-DETERMINISM initiative (after KA-4, KA-6, KA-9, KA-3). (S96)

## [v0.4.16] — 2026-06-20

**KA-3 — current_status internal-coherence auditor (GOVERNANCE-DETERMINISM / KA-10 arc).** Closes another blind spot the eBay Session-Zero deploy exposed: `current_status` denormalizes two facts from `meta` inside the same RAG — the session that last wrote it (`current_status.session` vs `meta.written_by_session`) and the day it was last updated (`current_status.last_updated` vs `meta.last_updated_utc`) — yet nothing asserted the two agreed. The eBay deploy froze `current_status.session` at `S0` while the machine had moved on and ran `last_updated` two full days behind `meta`, and `audit --strict` still reported 0 findings. The fix adds `drift_audit.check_current_status_coherence`: a fail-loud (ERROR) guard that asserts `current_status.session == meta.written_by_session` and that the **UTC calendar day** of `current_status.last_updated` equals that of `meta.last_updated_utc` (day-granularity, since one records a date and the other a full instant). It is distinct from the E-043 `check_current_status_freshness` guard, which checks two facts whose authority lives *outside* the RAG (the kernel `__version__` and git HEAD); this checks two facts denormalized from `meta` *inside* the RAG. Each sub-check self-skips when either side is absent or unparseable, so a RAG whose `current_status` omits these keys — like this kernel's own — audits clean rather than being falsely flagged. Wired into `audit_hot` so it runs at every session boundary. Dogfooded: a synthetic RAG with the eBay stale-session/stale-date defect fails loud on `current_status_coherence`; the live project RAG audits clean. `DRIFT_AUDIT_VERSION` 1.5.0 → **1.6.0**; no schema, WAL-format, or TLA+ change (drift gate `268149294421` unchanged), no new module (19, health 20/20). Suite 1,410 → **1,427** (+17). Fourth runtime increment of the KA-10 GOVERNANCE-DETERMINISM initiative (after KA-4, KA-6, KA-9). (S95)

## [v0.4.15] — 2026-06-20

**KA-9 — project_context placeholder gate (GOVERNANCE-DETERMINISM / KA-10 arc).** Closes the last born-clean hole the eBay Session-Zero deploy exposed: a deployed RAG carrying unfilled `<from user>` tokens in `project_context.brief` / `domain` / `end_goal` that `audit --strict` passed clean. The existing FIX-1 `check_placeholder_tokens` scan (K3) only matches whole-value **UPPER_SNAKE** tokens (`<SPEC_VERSION>`, `<ISO>`) — the spec parser's own substitution targets — so the **human-fill** session-zero placeholders (`<from user>`, `<absolute path>`: lowercase/spaced, filled by the LLM at deploy, not the parser) slipped straight through. The fix is two complementary parts. **(1) The gate** — a new `drift_audit.check_project_context_placeholders` walks the whole `project_context` subtree and fails loud (ERROR) on any surviving human-fill `<…>` placeholder (substring match, so a half-filled value is caught too); it deliberately leaves pure UPPER_SNAKE tokens to `check_placeholder_tokens` (no double-report) and self-skips when `project_context` is absent. Wired into `audit_hot` so it runs at every session boundary. **(2) Born-clean init** — per spec §1182 (skip → null), `cmd_init` now resolves every unfilled `project_context` placeholder to `null` instead of leaving the literal token, so a fresh `init` / `--auto-ready` (the prescribed deploy path) is clean by construction rather than failing the new gate — the same born-clean discipline FIX-9 applied to the K7 `written_by_session` residual. Dogfooded: the synthetic eBay-defective RAG (now carrying `<from user>` in `project_context`) fails loud on `project_context_placeholders`; the live project RAG (real, filled context) and a fresh `init --auto-ready` both audit clean. `DRIFT_AUDIT_VERSION` 1.4.0 → **1.5.0**; no schema, WAL-format, or TLA+ change (drift gate `268149294421` unchanged), no new module (19, health 20/20). Suite 1,398 → **1,410** (+12). Third increment of the KA-10 GOVERNANCE-DETERMINISM initiative (after KA-4, KA-6). (S94)

## [spec v3.2.5] — 2026-06-20

**KA-8 — bake the GC-first carry-forward gate + session-end ritual into the universal spec (GOVERNANCE-DETERMINISM / KA-10 arc, TierB).** KA-6 shipped the *runtime* commands (`session-start` / `session-end`); KA-8 makes the *spec* tell every deploy to run them. The session-boundary steps already existed but lived scattered across §17 (close audit), §19 (boot sequence), §20 (recovery) and §45 (garbage collector), so a deploying agent had to hand-assemble the ritual — exactly how the first external deploy skipped its checkpoint and froze its governance lineage. New **§50 — Session-Start & Session-End Rituals (governed)** assembles both into explicit, ordered, fail-loud sequences and adds `operating_protocol.session_start_protocol` + `session_end_protocol` rag-config blocks so a fresh `init --spec` **seeds** them into every RAG deterministically — no per-project re-authoring (KA-10 TierB). Session-start = carry-forward gate (integrity `verify` + drift `audit`, fail-loud → RECOVERY) → GC dry-run over `root_project` → open logger; session-end = checkpoint → close (KA-4 checkpoint-gate) → audit, any step's failure aborting the rest. When the runtime wrapper is present (v0.4.14+) each ritual is one governed command; autonomous mode performs the steps manually, in order, halting on any failure. Spec-only — no schema, WAL-format, TLA+, or runtime change (runtime stays **v0.4.14**, drift gate `268149294421` unchanged). Regression: `init --spec v3.2.5` writes a non-void RAG seeding **both** ritual rules (plus the pre-existing `garbage_collector` + `strict_obey`), `policy_version` 3.2.5, COLD `init_prompt_reference` v3.2.5, no residual `<SPEC_VERSION>`, `verify` OK + `audit --strict` clean. Full suite 1,392 → **1,398** (+6). 53 sections. (S93)

## [v0.4.14] — 2026-06-20

**KA-6 — machine-enforced session-start / session-end rituals (GOVERNANCE-DETERMINISM / KA-10 arc).** Collapses each session-boundary ritual into ONE ordered, fail-loud CLI command, removing the hand-scripting surface where a step gets skipped. Root cause (eBay S2/S4): the opening and closing steps were run by hand and one was missed — the deploy closed on `configure`/`audit` without ever `checkpoint`-ing, freezing `meta.written_by_session`. KA-4 fixed the close-without-checkpoint hole; KA-6 removes the hand-scripting itself. **`session-start <id>`** runs, in order, (1) a **carry-forward gate** — the precise inverse of the KA-4 close gate — that fails loud on an incoherent or unbanked *inherited* RAG by running `verify` (HOT↔COLD coherence, no `<SPEC_VERSION>` survivor) + `audit` (renders == canonical `tracked_items`, supersede refs resolve, notes vs status, `.bak` parity, `current_status` freshness vs live HEAD, no side stores), refusing to open the session unless both are clean (sanctioned `--force` override); (2) a **gc dry-run** (report-before-delete); (3) opening the session logger. **`session-end --rag … --session … --summary …`** runs, in order, (1) **checkpoint** (stamps `written_by_session`, bumps seq, parity-mirrors `.bak`), (2) **close** the logger — the KA-4 gate now passes *because* step 1 ran, (3) the fail-loud **audit**; any step's non-zero exit aborts the rest and propagates, so a session can never end half-ritualed. Both commands are excluded from the bootstrap-log wrapper (they manage the logger themselves) and reuse the existing `verify`/`audit`/`gc`/`checkpoint`/`session` primitives, so behavior cannot drift from the standalone verbs. Dogfooded: `session-start S92` on this project's live RAG gated green (verify + audit clean) and opened the S92 session. CLI-only — no schema, WAL-format, or TLA+ change (drift gate `268149294421` unchanged), no new module (health 20/20). Suite 1,381 → **1,392** (+11: 3 gate-predicate + 5 session-start orchestration + 3 session-end orchestration). Second increment of the KA-10 GOVERNANCE-DETERMINISM initiative (after KA-4). (S92)

## [v0.4.13] — 2026-06-20

**KA-4 — checkpoint-to-close enforcement (GOVERNANCE-DETERMINISM / KA-10 arc).** The kernel now *refuses* to close a started session on the CLI unless that session checkpointed first. Root cause (eBay S4): an agent ended sessions on `configure`/`audit` (or a scratch script) without ever running `checkpoint`, leaving `meta.written_by_session` stale across sessions — a silent governance freeze that recurred even after the S89 prose-only guide fix, proving enforcement must be code, not prose. `session close <id>` now evaluates a checkpoint gate (`meta.written_by_session == <id>`, the precise inverse of the freeze signature) and refuses with a non-zero exit + remediation hint when it is absent; a sanctioned `--force` override closes anyway with a loud warning, so a blocked agent does not resort to an unsanctioned scratch script (the eBay deploy accumulated ~20 such scratch scripts). The programmatic `KernelApp.close()` already force-checkpoints on close (ENH-006) — this closes the standalone-CLI hole the deploy actually froze on. A no-op close (no log file) stays a harmless no-op. CLI-only — no schema, WAL-format, or TLA+ change (drift gate `268149294421` unchanged), no new module (health 20/20). Suite 1,372 → **1,381** (+9: a 4-case gate predicate + 5 end-to-end close paths). First increment of the KA-10 GOVERNANCE-DETERMINISM initiative. (S91)

## [v0.4.12] — 2026-06-16

**Release bundle — FIX-9 … FIX-12 (eBay Session-Zero deploy-audit lane, U1–U4).** Bundles the four universal kernel fixes that landed on `main` after v0.4.11 into a single runtime release. All are CLI / persistence / logger-level — no schema, WAL-format, or TLA+ change (drift gate `268149294421` unchanged), no new module (health 20/20). Suite 1,302 → **1,372** (+70).

- **FIX-9 — `init --auto-ready` yields a stamped, audit-clean RAG (U1; K7 residual).** `--auto-ready` previously bypassed the first session-stamping checkpoint, leaving a fresh RAG with an empty `meta.written_by_session` and seq 0 — exactly the state the FIX-1 integrity auditor flags. `--auto-ready` is now routed through that first stamping checkpoint, so a born-ready RAG carries `written_by_session` / seq and a byte-parity `.bak`, and audits clean out of the box. **+6 tests.** (757bdeb, S81)
- **FIX-10 — `configure` parity-mirrors `.bak` (U2; K6 / FIX-4 gap).** `cmd_configure` persisted through `SpecParser.write_rag` (its own tmp+replace) which never refreshed `.bak`, so a context-merge left the backup one state behind — the same parity-mirror gap FIX-8 closed for the CLI `checkpoint`. `configure` now writes via `atomic_write_json(mirror_bak=True)`, honoring the FIX-4 / K6 parity-mirror contract. **+5 tests.** (bbf947e, S82)
- **FIX-11 — sanctioned, non-loaded project-context store (U3).** A governed home for project context that must persist but must NOT load into the HOT token budget. **inc1:** `RAG_CONTEXT.json` — a sanctioned, non-loaded, COLD-style store; `persistence.SANCTIONED_CONTEXT_STORES` allowlists it at the single side-store choke point so both the live pre-write guard and the auditor honor the sanction (transient `*_context.json` are still flagged); `cold_manager.ProjectContextManager` gives lazy / partitioned / atomic reads with no `.bak`. **inc2:** a `context` CLI group (`set` / `get` / `list`) over that store — a governed path to land context without hand-editing JSON. **inc3:** `configure --consume` deletes a transient merge-input after a verified merge, refusing canonical / sanctioned files. **+44 tests.** (a1cb242 / c465523 / f2710d0, S83–S85)
- **FIX-12 — bootstrap session log captures real CLI events (U4).** Short-lived CLI processes left an empty / marker-only session log. `SessionLogger` gains `attach()` / `detach()` (parametrized `emit_start` / `emit_end`) so a CLI process appends to an ongoing log without re-emitting lifecycle markers; a new central dispatch wrapper appends a real `tool_invocation` for every verb (read-only audit/verify/health and mutating alike, so observability can never break the command); and session close now attaches instead of `open()`, fixing a spurious second `session_start`. A CLI session start → verb → close now yields `session_start` → real `tool_invocation` → clean `session_end`. **+15 tests.** (9793016, S86)

## [spec v3.2.4] — 2026-06-14

**STRICT-OBEY — Operator Fidelity Protocol (§49).** Promotes the operator-fidelity rule from this project's RAG into the universal spec so every project spawned from `init --spec` inherits it. New §49 + `operating_protocol.strict_obey` rag-config block define a HARD RULE in four parts: (1) obey the operator's literal instruction — no guesswork, improvisation, scope creep, or unrequested work, and never substitute the model's own preference; (2) honest status — never report work as done/shipped/resolved/complete unless it actually is, and distinguish a developer checkpoint from a finished feature; (3) bounded halt-and-ask — ask the operator ONLY on genuine ambiguity or a decision only the operator can make, never bounce back a decision the model can make itself or one with a safe default (over-asking is as much a violation as over-doing), and exercise delegated discretion; (4) rendering discipline — every status/backlog/report render enumerates each item line by line, by ID, in plain language across all sections, never a bare count (e.g. "Deferred: 6") or glyph shorthand that forces the operator to guess whether items were lost. Spec-only — no schema, WAL-format, TLA+, or runtime change (runtime stays v0.4.11). Regression: `init --spec v3.2.4` writes a non-void RAG inheriting exactly 12 known-issues + `strict_obey`, `policy_version` 3.2.4, COLD `init_prompt_reference` v3.2.4, no residual `<SPEC_VERSION>`, `verify` OK; full suite 1,302 green. 52 sections.

## [v0.4.11] — 2026-06-14

**FIX-8 — CLI `checkpoint` parity-mirror `.bak` (E-045).** Closes the last gap in the FIX-4 / K6 `.bak` contract. `api.KernelApp.checkpoint` (do_full) already refreshed `.bak` to byte-parity via `mirror_bak=True`, but the standalone CLI `checkpoint` verb (`cmd_checkpoint`) wrote with a plain `atomic_write_json(rag_path, rag)` — leaving `RAG_MASTER.json.bak` one seq behind. A session closed on the CLI `checkpoint` alone (no follow-up `render --apply`) therefore left a stale backup that `audit.check_bak_parity` correctly failed loud on — surfaced live during the S77 close, logged as E-045, and recurring at the S78 close (worked around both times by trailing the checkpoint with `render --apply`). The fix wires `mirror_bak=True` into `cmd_checkpoint` so the CLI close honors the parity-mirror contract on its own. **+3 tests (1,302 total)** (CLI checkpoint → byte-parity `.bak`; audit-clean with no follow-up write; parity holds across repeated checkpoints); no new module (health 20/20), no schema/WAL-format/TLA+ change (drift gate `268149294421`), no `DRIFT_AUDIT`/`DRIFT_STORE` version change.

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