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

## spec v3.2.6 — Released (2026-06-21)

KA-11 inc3 — session-end claim-reconciliation pass baked into the universal spec (GOVERNANCE-DETERMINISM / KA-10 arc, TierB). §50's session-end ritual gains a generic claim-reconciliation pass as its FIRST step (reconcile → checkpoint → close → audit): before checkpoint, reconcile every published status-claim declared on the per-project `meta.reconciliation_surfaces` (TierC) against the tracked records — universalizing the formerly project-specific Rule 11 / INS-018 recurring reconcile so every fresh `init --spec` inherits it (no per-project re-authoring, KA-10 TierB). Self-version 3.2.5 → 3.2.6; `session_end_protocol` rag-config updated + renumbered; `test_ka8` repointed to the v3.2.6 spec plus a new reconciliation-step test. Spec-only — no schema, WAL-format, TLA+, or runtime change (runtime stays v0.4.21, drift gate `268149294421` unchanged). Regression `init --spec v3.2.6` seeds the reconciliation step + `reconciliation_surfaces` + Rule 11, order reconcile<checkpoint<close<audit, `policy_version` 3.2.6, `verify` OK + `audit --strict` clean, full suite 1,534 → 1,535 green (+1).

---

## spec v3.2.5 — Released (2026-06-20)

KA-8 — bake the GC-first carry-forward gate + session-end ritual into the universal spec (GOVERNANCE-DETERMINISM / KA-10 arc, TierB). KA-6 shipped the runtime commands (`session-start` / `session-end`); KA-8 makes the spec tell every deploy to run them. The session-boundary steps already existed but lived scattered across §17 (close audit), §19 (boot sequence), §20 (recovery) and §45 (garbage collector), so a deploying agent had to hand-assemble the ritual — exactly how the first external deploy skipped its checkpoint and froze its governance lineage. New §50 — Session-Start & Session-End Rituals (governed) — assembles both into explicit, ordered, fail-loud sequences and adds `operating_protocol.session_start_protocol` + `session_end_protocol` rag-config blocks so a fresh `init --spec` seeds them into every RAG deterministically (no per-project re-authoring, KA-10 TierB). Session-start = carry-forward gate (integrity `verify` + drift `audit`, fail-loud → RECOVERY) → gc dry-run over `root_project` → open logger; session-end = checkpoint → close (KA-4 checkpoint-gate) → audit, any step's failure aborting the rest. Runtime wrapper present (v0.4.14+): each ritual is one governed command; autonomous mode performs the steps manually, in order, halting on failure. Spec-only — no schema, WAL-format, TLA+, or runtime change (runtime stays v0.4.14, drift gate `268149294421` unchanged). Regression `init --spec v3.2.5` seeds both ritual rules (plus the pre-existing `garbage_collector` + `strict_obey`), `policy_version` 3.2.5, COLD ref v3.2.5, no residual `<SPEC_VERSION>`, `verify` OK + `audit --strict` clean, full suite 1,398 green (+6). 53 sections.

---

## v0.4.45 — Released (2026-07-22)

BOOT-INTEGRITY SPINE (S172) — two fail-loud boot-boundary guards. **BOOT-GUARD-FIRST-ACTION (KA-20)** closes the E-071/072/073/075/076 recurrence (five consecutive fresh boots where the agent read the canonical RAG via the banned Cowork sandbox before the governed ritual). The kernel cannot observe a sandbox read from inside, so it kills the *trigger* and records *proof*: `session-start` now renders a deterministic boot-state briefing (inference-ledger OPEN/overdue, `next_session_directive`, backlog counts) so the agent never needs to open the RAG directly, writes a `boot_guard` first-action marker, and prints an explicit E-071-class notice; the load-bearing prevention is the out-of-band Project-Instructions rewrite that mandates `session-start` as the first action. **CLOSE-SEAL-ENFORCE (KA-21)** closes the S157 gap (independently reproduced as the eBay S14 CLOSE-GAP, Rule 15): the carry-forward gate now refuses to open a new session over an unsealed predecessor — a session with a `session_log` on disk whose seq exceeds the last *sealed* session's (sealed = `transfer_ready` **and** `AUDIT_CANONICAL_REPORT_<sid>.md` present, Rule 23) — gated on the close protocol being in use (legacy RAGs untouched) and `--force`-overridable. Runtime `__version__` 0.4.44 → 0.4.45, `__spec_version__` unchanged (3.2.7); no new capability module (still 20), health 21/21, drift gate `268149294421` unchanged; full suite 2,002 → 2,021 green (+19).

---

## v0.4.44 — Released (2026-07-22)

BOOTMAP-BOOTROOT-FIX (E-074) — the domain boot-map's `boot_root` is pinned to the project root, decoupled from `--gc-path`/CWD. v0.4.43 seals its baseline against the project root and audits against `p.parent.parent`, but `cmd_session_start` derived the boot-line `boot_root` from `--gc-path` (default `Path(".")`, always truthy → the intended `else rag_dir.parent` branch was dead), so a run from `RAG/` diffed RAG-relative paths against the project-root-keyed baseline and emitted a spurious full `+N/-M` boot line (cosmetic — `session_start_line` is READ-ONLY, the persisted seal was always correct). `boot_root` is now unconditionally `rag_dir.parent`; `--gc-path` governs only the GC scan. +regression test `test_session_start_bootmap_root_is_project_root_not_gcpath`. Runtime `__version__` 0.4.43 → 0.4.44, `__spec_version__` unchanged (3.2.7); no new capability module (still 20), health 21/21, drift gate `268149294421` unchanged; full suite 2,001 → 2,002 green (+1).

## v0.4.43 — Released (2026-07-21)

ROOT-FILE-MANIFEST — a deterministic domain boot-map. New `rag_kernel/bootmap.py` walks the project root at session-open, emitting `{path, sha256, size, mtime, class, owner}` per file (GC exclude set + `GIT WORKTREES` dev tree), persisted to a machine-generated `BOOTMAP_MANIFEST.json` sidecar under `.bak` parity, diffed by content-hash against the prior-session baseline into a `since-S<last>` `+new/~changed/-deleted` boot line. `owner=operator` classification closes S166 F3. A fail-loud `check_map_coverage` invariant runs in `audit`. Folded into `session-start` [2/4] and `session-end` close (Step 1c reseal), no per-boot GitHub read. Resolves ROOT-FILE-MANIFEST. Runtime `__version__` 0.4.42 → 0.4.43, `__spec_version__` unchanged (3.2.7); new verb-support module `bootmap` (count stays 20), health 21/21, drift gate unchanged; full suite 1,964 → 2,001 green (+37).

## v0.4.42 — Released (2026-07-21)

REUSE-REGISTRY-GUARD — a baked-asset registry + reuse-before-rewrite guard closing the anti-redundancy gap where agents re-author already-baked artifacts. New `asset_registry` verb-support module (inventory of `{asset_id, path, purpose, sha256}`, a pre-write reuse guard, a fail-loud auditor); the inventory lives in the non-loaded `RAG_CONTEXT.json` `baked_assets` partition with only a concise `reuse_registry_guard` pointer in HOT. New verbs `register-asset` + `reuse-check`; `drift_audit.check_asset_registry` fails loud on a vanished/diverged/duplicate-path asset. Runtime `__version__` 0.4.41 → 0.4.42, `__spec_version__` unchanged (3.2.7); no new capability module (still 20), health 21/21; full suite 1,946 → 1,964 green (+18).

## v0.4.41 — Released

Scaffold transplant verb on classification Authority A (spec-derived) — `rag_kernel/transplant.py`, additive-only, fail-loud on collision (never overwrite), dry-run line-by-line, atomic FIX-4 + `meta.transplants` audit trail, wired in `__main__`. Resolves TRANSPLANT-CLASSIFY-AUTHORITY. Runtime `__version__` 0.4.40 → 0.4.41; suite 1,929 → 1,946 green.

## v0.4.40 — Released

MIGRATE-INITPROMPT-REPAIR-PATH — unconditional `init_prompt`/`policy` coherence with an auditor invariant `check_policy_initprompt_coherence`, closing the coherence-pair gap surfaced in E-068. Runtime `__version__` bump into 0.4.40.

## v0.4.39 — Released

Spec `v3.2.7` self-adoption + `migrate` init_prompt pairing — the kernel adopts its own newest spec generation and pairs the `init_prompt` with `policy_version` during migrate. `__spec_version__` → 3.2.7.

## v0.4.38 — Released

KA-SCHEMA-MIGRATE — a governed, deployment-facing schema/version migration verb (`migrate`) with a declared additive-idempotent ladder; never assumes direction, refuses a deployment that is ahead, preserves project-owned state in place, and appends a `meta.migrations` audit entry. New `schema_migrate` capability module (the 20th). Runtime `__version__` 0.4.37 → 0.4.38; suite 1,861 → 1,911 green (+50).

## v0.4.37 — Released (2026-07-16)

REPORT-{RULE21-FIDELITY, BACKLOG-DEDUP, PRIORITY-COMPLETE} — the three report-content findings of the S153 transfer-surface audit, fixed together in the section-4 renderer (operator-authorized batch, S155). The Rule 21 priority burn-down now always surfaces **P1** (the priority spine) and the **active group** — an empty group emits an explicit `clear ✓` row instead of being silently omitted — and marks the lowest-numbered group still holding active work `← ACTIVE`, so the burn-down actually names the active group + what shipped this session + what remains (REPORT-RULE21-FIDELITY). Active/deferred items are now **referenced by id** in the burn-down rather than re-listed with their titles, leaving the flat Open / Blocked / Deferred lists as the single itemized authority (Rule 16), so no backlog item prints twice; items resolved *this* session (already gone from the flat lists) keep their title as the shipped signal (REPORT-BACKLOG-DEDUP). The un-triaged catch-all carries an explicit `Unprioritized · needs a P-group` label instead of a silent `Unassigned` bucket, and section 2 / the at-a-glance milestone cell now name the **live released build** (`release_ref`/`version`, gathered from git by the caller) rather than a stale newest-`RELEASE` tracked-item — folding the S154 cosmetic gap where the report read `RELEASE-v0.4.35` after v0.4.36 had shipped without a minted RELEASE item (REPORT-PRIORITY-COMPLETE). Runtime `__version__` 0.4.36 → 0.4.37, `__spec_version__` unchanged (3.2.6). No new module (still 19), health 20/20, drift gate `268149294421` unchanged (no schema/WAL/TLA+ change), full suite 1,859 → 1,861 green (+2). `drift_render` 1.2.0 → 1.3.0.

## v0.4.36 — Released (2026-07-16)

AUDIT-XFER-SURFACE-ATTEST (P1/F1) — the operator-facing canonical report is now bound to a verbatim attested file, closing the transfer-surface (chat-relay) hole that the S153 audit isolated as the F1 root cause of the S152 close failure. `session-end` already machine-rendered the deterministic, `report-attest`'d report to stdout (S139 WIRE-CLOSE), and Rule 22 (`report_render_attestation`, v0.4.34) guards that RENDER — but nothing persisted the render or bound what the agent pastes into chat to it, so a bare-count paraphrase with a hand-appended token slipped through at the S152 close. The close (`_drive_close` step 4/4) now also writes that exact attested text VERBATIM to a deterministic transfer-surface file, `RAG/AUDIT_CANONICAL_REPORT_<SID>.md`, and prints a pointer instructing the agent to present THAT FILE (re-checkable with `report --verify <file>`) rather than retyping it. A new governed `transfer_surface_attestation` rule binds the operator-facing canonical report to the verbatim attested file only — Rule 22 guards the render; this rule binds the RELAY, the one boundary the kernel previously could not see. Runtime `__version__` 0.4.35 → 0.4.36, `__spec_version__` unchanged (3.2.6). No new module (still 19), health 20/20, drift gate `268149294421` unchanged (no schema/WAL/TLA+ change), full suite 1,857 → 1,859 green (+2, `tests/test_ka16_atomic_resumable_close.py`).

## v0.4.35 — Released (2026-07-16)

KA-INTENT-FIDELITY inc2 — the plan-vs-settled ID-bound audit, completing the intent-fidelity milestone whose inc1 (the `next_session_directive` decision-of-record + session-end handoff-persistence seal gate) shipped in v0.4.34. inc1 guaranteed the prior session's directive was *persisted* verbatim; inc2 guarantees a new session's *plan* is faithful to it. A new `schemas.audit_plan_against_directive` and a fail-loud `intent-audit` CLI verb enforce two deterministic, stdlib-only, zero-token checks: **ID-binding** — the plan's cited decision ids must equal the directive's pinned `decision_ids` (no omitted settled decision, no unsanctioned extra) and every cited *and* pinned id must resolve to a real `tracked_items` entry; and a **normalized-exact restatement** — the stated plan must match the stored directive text under whitespace/case normalization only, never a semantic reword (reusing inc1's `directive_matches`; determinism over flexibility for a fail-loud gate). The verb additionally **loads the SOURCE decisions** — it resolves the directive's `decision_ids` to their live tracked_item records (id, status, title) rather than echoing the compressed handoff line — closing the other half of E-055 / the S146 drift, where a session anchored on a lossy handoff and recited a stale blueprint instead of the settled decision-of-record. Runtime `__version__` 0.4.34 → 0.4.35, `__spec_version__` unchanged (3.2.6). No new module (still 19), health 20/20, drift gate `268149294421` unchanged (no schema/WAL/TLA+ change), full suite 1,841 → 1,857 green (+16).

## v0.4.34 — Released (2026-07-16)

REPORT-RENDER-ATTEST — the canonical status report is now machine-attested against re-prosing, closing the recurrence of the report-drift that hit at E-060 (S136) and again at E-062 (S149). `report` appends a `report-attest: sha256(<normalized body>)` trailer; a new `report --verify <file>` recomputes it and fails loud on any mismatch or absence, so a hand-typed / summarized / reflowed canonical report is machine-detectable; the governed `report_render_attestation` rule makes an unattested canonical report INVALID. This mechanizes Rule 12 for the in-chat / on-demand render path that the S139 WIRE-CLOSE fix (session-end only) had left unguarded. The release also ships **KA-INTENT-FIDELITY inc1** already on main — a `next_session_directive` decision-of-record plus a session-end handoff-persistence seal gate that refuses to seal unless a stated handoff is persisted verbatim (increment 1 of 2; the plan-vs-settled ID-bound audit, inc2, is still to come). Runtime `__version__` 0.4.33 → 0.4.34, `__spec_version__` unchanged (3.2.6). No new module (still 19), health 20/20, drift gate `268149294421` unchanged (no schema/WAL/TLA+ change), full suite 1,813 → 1,841 green (+28: +19 inc1, +9 REPORT-RENDER-ATTEST).

## v0.4.33 — Released (2026-07-15)

SECRETS-INGEST-GUARD (P1/G2) — the ingest-time complement to v0.4.32's audit-time KA-SECRETS-BOUNDARY, cut as the settled P1 tagged release (Rule 21 diversion trigger) and redeployed into the governance runtime (`RAG/rag_kernel/`). KA-SECRETS-BOUNDARY made a declared-secret value appearing verbatim in the RAG a fail-loud *audit* finding — a detective control that fires only after the value has landed. **`KernelApp.validate_secrets_ingest`** (`rag_kernel/api.py`) adds the *preventive* control: a proposal carrying a declared-secret value is rejected at `propose()`, the `BOOT → INGEST → VALIDATE → COMMIT` pipeline's ingest transition, so the secret never reaches `commit()`. Detection and prevention share one source of truth — `drift_audit.collect_declared_secret_values` (`drift_audit` 1.13.0 → 1.14.0) backs both, so they can never disagree on what counts as a declared secret (defaults `config/**`, `.env*`, `*.pem`/`*.key`, `credentials*`/`secrets*`; widenable via `meta.secret_paths`, never narrowable). Rejections are redaction-safe (`sha256:<12>` + location, never the secret value). Runtime `__version__` 0.4.32 → 0.4.33, `__spec_version__` unchanged (3.2.6). No new module (still 19), health 20/20, drift gate `268149294421` unchanged (no schema/WAL/TLA+ change), full suite 1,806 → 1,813 green (+7). Lane-A from the eBay S129 field audit (Rule 15).

---

## v0.4.32 — Released (2026-07-14)

P1 control-integrity release — the P1/G1+G2 guard batch plus the REPORT-PRIORITY-GROUPS burn-down render, cut as the settled P1 tagged release and deployed into the governance runtime (`RAG/rag_kernel/`). Bundles the five commits landed on main since v0.4.31. **ERRLOG-ID-GUARD (P1/G1)** — `drift_audit.check_errlog_id_coherence` makes ERROR_LOG error-id headings a fail-loud invariant, enforcing the formally-verified `GUARD == I0 ∧ I1 ∧ I2` (`formal/ErrlogIdGuard.tla`, TLC-exhaustive) over malformed (I0), reused (I1 — the pre-S140 blind spot `check_record_coverage` de-duped away), and dangling (I2) ids; classifier is positional, never prose-inferred. **KA-CS-PROSE-DRIFT (P1/G1)** — the E-043 `current_status` freshness guard + refresh re-stamped only the leading version token and git HEAD, leaving a secondary `RUNTIME RELEASE vX` / `runtime-vX` claim in the same field frozen while audit passed clean; a third label-anchored sub-check now asserts every labeled release token equals live `__version__`, with a paired all-occurrences refresh (`drift_audit` 1.11.0 → 1.12.0, `drift_store` 1.4.0 → 1.5.0). **KA-SECRETS-BOUNDARY (P1/G2)** — new `check_secrets_boundary` (`drift_audit` 1.12.0 → 1.13.0): declared-secret values (defaults `config/**`, `.env*`, `*.pem`/`*.key`, `credentials*`/`secrets*`; widenable via `meta.secret_paths`, never narrowable) must not appear verbatim in the RAG; redaction-safe findings (`sha256:<12>` + location, never the secret); Lane-A from the eBay S129 field audit (Rule 15). **REPORT-PRIORITY-GROUPS inc1** — a structured per-item Rule 21 `priority_group` bucket (`drift_control` 1.0.0 → 1.1.0: `TrackedItem.priority_group`, fail-loud validation, `with_priority()`; `drift_store` 1.5.0 → 1.6.0: guarded `set_priority` + `set_priority_in_file`, atomic and `.bak`-refreshed, status never touched) plus a `priority` CLI verb. **inc2** — `render_priority_burndown()` (`drift_render` 1.1.0 → 1.2.0) groups the backlog into Rule 21 P1..P5 + an Unassigned catch-all, emitted as a subsection of report section 4 (design option A; no renumbering of sections 5–7). Runtime `__version__` 0.4.31 → 0.4.32, `__spec_version__` unchanged (3.2.6). No new module (still 19), health 20/20, drift gate `268149294421` unchanged (no schema/WAL/TLA+ change), full suite 1,733 → 1,806 green (+73).

---

## v0.4.31 — Released (2026-07-12)

REPORT-VERB-FIDELITY + REPORT-VERB-WIRE-CLOSE — the canonical status report now renders faithfully to the 7-section spec and is machine-emitted at close. **REPORT-VERB-FIDELITY** fixes three divergences: (a) section 2 becomes a plan/actual table (`# | Increment | Plan | Status | RAG | Commit-S`) scoped to the current build's increments — sourced from a new `TrackedItem.increments[]` frozen sub-record (display metadata only; omitted from serialization when empty so existing items round-trip byte-for-byte) — instead of dumping every historical milestone/release; (b) the at-a-glance milestone cell falls back to the milestone/release shipped this session (then the newest release) before the bare "(no active milestone)"; (c) the drift gate can reach GREEN from a deployed package that ships no `formal/RAGKernel.tla` (the governance-runtime norm, Rule 19) — `guardgen` bakes a `GUARDS_SELF_SHA256` self-hash of the guard tables plus `verify_self()`, and when the `.tla` is unreachable the gate self-verifies its own guard integrity from baked provenance (True iff intact, False if hand-edited post-generation, None only if the machinery is absent), removing the false-AMBER a genuinely-green released build used to read. The honesty invariant holds: an unknown gate still pulls to AMBER (Rule 14). **REPORT-VERB-WIRE-CLOSE** makes `session-end` machine-render the deterministic canonical report verbatim from the just-checkpointed RAG as the mandated close artifact, so the closing report can never be hand-authored (the S136 close-drift root cause); rendering is the attestation and its external scalars mirror the `report` verb. Dogfooded live: this v0.4.31 close first tripped the E-043 freshness guard (stale `current_status` version/HEAD) and the README/ROADMAP headline warnings, which were cleared before the close set `transfer_ready`. Runtime `__version__` 0.4.30 → 0.4.31, `__spec_version__` unchanged (3.2.6). No new module (still 19), health 20/20, drift gate `268149294421` unchanged (no schema/WAL/TLA+ change), full suite 1,719 → 1,733 green (+14).

## v0.4.30 — Released (2026-07-11)

REPORT-VERB — the closing/transfer status report becomes a deterministic kernel render instead of a hand-authored prose block. Rule 12 (`report_before_transfer`) has always required the 7-section canonical status report to be a *deterministic render of the RAG canonical fields* ("the report equals the RAG by construction"), but until now that render existed only as a discipline the agent performed by hand — so a hand-assembled report could drift from the RAG even when the RAG itself was clean. That is precisely the transfer-drift gap the operator's manual report-paste cross-check was compensating for; this closes it. New `report` verb — `drift_render.render_status_report` (pure projector) + `__main__.cmd_report` (fact-gatherer) — renders all 7 sections (At-a-glance R/A/G + verdict; Build milestones/releases; This session; Backlog; Risks & deviations; Ledger & errors; Verification & handoff). Sourcing discipline (operator decision S136): every fact is **structured** (read from `meta` / `tracked_items` / `inference_ledger`), **live-computed** by the caller (health via `_KERNEL_MODULES`, drift-gate sha via `generated_guards.SOURCE_SHA256`, git HEAD, `.bak` parity, bytes), or a genuinely **external** scalar passed as an explicit argument (`--context-pct`, `--tests`, `--released`, `--claims-ok`). It never scrapes `current_status` prose and never invents a value — an unknown fact renders `n/a` and can only pull the verdict toward AMBER, never a false GREEN (Rule 14 increment-status-honesty). R/A/G thresholds are objective: RED = any hard gate failing (tests / health / drift / a published repo-claim contradicting reality); AMBER = unreleased or any gate unknown; GREEN = released AND tests/health/drift green AND repo-claims reconciled. The backlog is fully enumerated line-by-line, never a bare count (STRICT-OBEY / Rule 16), and the renderer reuses `render_backlog_section` so there is no duplicate backlog logic. Runtime `__version__` 0.4.29 → 0.4.30, `__spec_version__` unchanged (3.2.6). The renderer lives in the existing `drift_render` module — no new capability module (still 19), health 20/20, drift gate `268149294421` unchanged (no schema, WAL-format, or TLA+ change), `DRIFT_RENDER_VERSION` 1.0.0 → 1.1.0, full suite 1,693 → 1,719 green (+26).

---

## v0.4.29 — Released (2026-07-11)

KA-RECON-PROXIMITY + KA-RECON-DECLARE — the two follow-on gaps that blocked turning the close-time reconciliation (KA-13) on for this project, both closed. v0.4.28 wired the Rule 11 published-doc reconciliation into the session close; two gaps kept it dormant here — a same-line false positive and the absence of a governed way to declare where the published docs live. **KA-RECON-PROXIMITY** (`drift_audit.check_repo_claim_reconciliation`) — the id-anchored §2 check paired a PENDING word with a RESOLVED id at *line* granularity, so a pending word anywhere on a long multi-clause paragraph line was read as the status of any RESOLVED id elsewhere on the line. The live false positive was this ROADMAP's own v0.4.27 entry: a single paragraph line whose later `--dry-run` clause describing an old→new diff sits several sentences away from the RESOLVED `KA-CS-REFRESH` / `FIX-4` ids mentioned earlier. Because both shared one physical line, the auditor mis-read those finished ids as unfinished. The check now segments each line on sentence / semicolon boundaries (`. ` / `; ` only — never dashes or table pipes, so version dots and genuine single-line "`ID — planned`" claims are untouched) and requires the pending word and the id to co-occur in the **same sentence**; it can only make §2 more conservative, so already-clean docs stay clean. New `_SENTENCE_SPLIT_RE`. **KA-RECON-DECLARE** (`__main__` — `configure`) — KA-13 resolves its surface root from `meta.reconciliation_docs_root`, but no governed verb set that key, so declaring it meant a forbidden hand-edit of `RAG_MASTER.json`. `configure` gains a `--reconciliation-docs-root PATH` flag that rides the existing `deep_merge` + `atomic_write_json(mirror_bak=True)` path — atomic, HOT↔.bak parity by construction; `--context` is now optional (flag usable alone or with a context overlay, an explicit flag winning over any context-file value), and `--consume` without `--context` fails loud. Together these are the prerequisite for declaring `meta.reconciliation_docs_root` on this project and letting every governed close reconcile the surfaces automatically. Runtime `__version__` 0.4.28 → 0.4.29, `__spec_version__` unchanged (3.2.6). CLI/audit-only — no new module (19), health 20/20, drift gate `268149294421` unchanged (no schema, WAL-format, or TLA+ change), `DRIFT_STORE_VERSION` unchanged (1.4.0), full suite 1,680 → 1,693 green (+13).

---

## v0.4.28 — Released (2026-07-11)

KA-13 + KA-19 — the close-time published-doc reconciliation, now actually wired into the runtime `session-end`, plus a word-boundary fix so it no longer mis-fires on id substrings. The spec (v3.2.6, KA-11 inc3) declared that the session close must reconcile every published status-claim against the tracked records — but the runtime close never performed it: `_drive_close` ran its step-3 audit with `docs_root=None`, so the Rule 11 published-doc reconciliation stayed dormant at close. That was the exact recurring pass **RECONCILE-PASS-RECURRING** wanted mechanized. **KA-13** (`__main__` — `session-end` / `session-resume`) — the close now resolves a `docs_root` for its step-3 audit with a back-compatible precedence: `--no-reconcile` (opt-out) > `--docs-root PATH` (per-invocation override) > `meta.reconciliation_docs_root` (the project's declared surface root) > skip. A declared/override path may be absolute or relative (relative resolves against the project root); an un-migrated RAG that declares nothing and passes no flag closes byte-for-byte as before (`docs_root=None`, reconciliation dormant, no new close friction). New `_resolve_close_docs_root` planner; `--docs-root` / `--no-reconcile` added to both close verbs. This is the runtime half of the KA-11 inc3 spec step — the published docs are reconciled against the live canonical facts (`rag_kernel.__version__`, capability-module count, drift-gate sha) as part of the fail-loud close audit. **KA-19** (`drift_audit.check_repo_claim_reconciliation`) — the id-anchored pending-status check matched a tracked id with a bare substring (`rid in ln`), so a RESOLVED `FIX-1` was reported "still pending" against a line that only named `FIX-12` (a distinct, OPEN id); the longest-first ordering only masked it when the longer id was itself RESOLVED and present on the line. The id is now matched at its token boundaries (`(?<![\w-])…(?![\w-])`), so neither a digit/letter suffix (`FIX-12`) nor a hyphen extension (`FIX-1-alpha`) can trigger a shorter id, while an exact, boundary-delimited mention still fires. Together these close **RECONCILE-PASS-RECURRING** — the recurring repo-claim↔reality↔record pass is mechanized by declaring `meta.reconciliation_docs_root` once, after which every governed close reconciles the surfaces automatically. Runtime `__version__` 0.4.27 → 0.4.28, `__spec_version__` unchanged (3.2.6). CLI/close/audit-only — no new module (19), health 20/20, drift gate `268149294421` unchanged (no schema, WAL-format, or TLA+ change), `DRIFT_STORE_VERSION` unchanged (1.4.0), full suite 1,664 → 1,680 green (+16).

---

## v0.4.27 — Released (2026-07-06)

KA-CS-REFRESH — a governed `refresh-current-status` verb: the atomic REPAIR half of the E-043 freshness guard. `current_status` denormalizes two facts whose authority lives OUTSIDE the RAG — the runtime `rag_kernel.__version__` and the published git HEAD — and `drift_audit.check_current_status_freshness` (E-043) fails loud when the narrative drifts from the live authority, but there was no governed way to *repair* that drift: a mid-session dev commit bumped the version / moved HEAD, `current_status` went stale, and the only fix was a hand-edit of `RAG_MASTER.json` — exactly the drift the project forbids (it cost a manual atomic-writer reconcile at S116 and again at S127). New **`refresh-current-status`** (`drift_store` / CLI) re-stamps the runtime-version token (`current_status.rag_kernel_version` ← live `rag_kernel.__version__`) and the published git HEAD (`current_status.github_repo`'s "LATEST COMMIT &lt;sha&gt;" ← the auto-resolved worktree HEAD, reusing the auditor's own `_resolve_git_head`), plus optionally the `unit_tests` count (`--tests`, never fabricated). Backed by new `drift_store.compute_current_status_refresh` (pure planner) / `refresh_current_status_file` (atomic), reusing the FIX-4 `tmp → verify → .bak → rename` byte-parity path so a `current_status` mutation keeps HOT↔.bak parity by construction. Deterministic and idempotent — a no-op (no write, `.bak` untouched) when already fresh; `--dry-run` prints the planned old→new token diff; `--strict` fails loud on a missing target token; re-stamps only the machine-fact token in place, leaving surrounding narrative to the agent (increment_status_honesty). DRY invariant: the leading-token field-names + regexes the guard uses to *detect* staleness moved **down** into `drift_store` (the lower module) as the single source of truth, and `drift_audit` re-exports them — so detection and repair read the identical definitions and can never disagree (same pattern as the shared date coercers). Dogfooded live: the v0.4.27 version bump tripped the freshness audit until `refresh-current-status` cleared it by construction. Runtime `__version__` 0.4.26 → 0.4.27, `__spec_version__` unchanged (3.2.6). CLI/store/audit-only — no new module (19), health 20/20, drift gate `268149294421` unchanged (no schema, WAL-format, or TLA+ change), `DRIFT_STORE_VERSION` 1.3.0 → 1.4.0, full suite 1,639 → 1,664 green (+25).

---

## v0.4.26 — Released (2026-07-06)

T1 GATE — three governance-hardening fixes bundled as one runtime release, closing the last items of the T1 kernel-fix gate cleared in S126 (KA-CTX-RAGFLAG + KA-CKPT-PARITY-GATE + KA-18). **KA-CTX-RAGFLAG** — the `context set/get/list` verb mis-routed when handed a `--rag <file>` path (it expected a directory), so passing the RAG file — the natural invocation used everywhere else — silently wrote the partition to the wrong location; `context` now tolerates a `--rag <file>` and routes to the file's parent directory, reconciling its `--rag` semantics with every other verb. **KA-CKPT-PARITY-GATE** (E-049) — a mid-session dev commit could leave the legacy `open_tasks` / `deferred_items` renders stale relative to the canonical `tracked_items` and `checkpoint` sealed anyway; `checkpoint` now auto-renders the legacy arrays from `tracked_items` at seal (render-parity by construction) plus a defensive fail-loud if a stale render is detected at the gate — the `audit` render==canonical invariant (E-040 regression guard) now enforced at the checkpoint boundary, not only at session-end. **KA-18** (E-044/E-045) — a permanent guard against the recurring session-start ordering slip: `checkpoint` refuses to run without an open session log, so the mechanized `session-start` must precede it (CLI default ON; `--no-require-session-log` the explicit, audited bypass). Runtime `__version__` 0.4.25 → 0.4.26, `__spec_version__` unchanged (3.2.6). CLI/checkpoint/context-only — no new module (19), health 20/20, drift gate `268149294421` unchanged (no schema, WAL-format, or TLA+ change), `DRIFT_STORE_VERSION` unchanged (1.3.0), full suite 1,623 → 1,639 green (+16).

---

## v0.4.25 — Released (2026-07-04)

KA-CUTOVER-GATE — the record-coverage cutover gate now counts only non-retired records, plus a governed `un-add` verb that makes a mis-`add` recoverable. Two coupled defects made a mis-kinded forensic item (`kind=ERROR`/`INFERENCE`) unrecoverable: `check_record_coverage` treated a kind as migrated (gate ON) by ANY item of that kind regardless of status, so a single mis-`add` latched the per-kind cutover gate ON and demanded full ERROR_LOG/ledger coverage; and the store had no un-add path, so a mis-kinded item could be discarded/superseded but never removed — and since discard/supersede leave `kind` intact, the status-blind gate stayed latched (a deadlock). Gate fix: new `drift_control.RETIRED_STATUSES` = `{SUPERSEDED, DISCARDED}` (a strict subset of `TERMINAL_STATUSES`; `RESOLVED` stays counted), and `check_record_coverage` now counts only NON-retired `INFERENCE`/`ERROR` members, so retiring a mis-kinded item lets the per-kind gate fall back to its correct pre-migration (empty) state. Un-add verb: new `TrackedItemStore.remove` + atomic `drift_store.remove_item_file` + the `un-add` CLI verb — the guarded, atomic inverse of `add`, permitted ONLY on a PRISTINE (empty-history) item so a real transitioned item is protected; fail-loud on an unknown id or a historied item, writing nothing. Recovers a mis-`add` without a hand-edit (the E-037/E-040 drift). Runtime `__version__` 0.4.24 → 0.4.25, `__spec_version__` unchanged (3.2.6). CLI/store/audit-only — no new module (19), health 20/20, drift gate `268149294421` unchanged (no schema/WAL/TLA+ change), `DRIFT_STORE_VERSION` 1.2.0 → 1.3.0, full suite 1,606 → 1,623 green (+17).

---

## v0.4.24 — Released (2026-06-30)

UPDATE-RULE-VERB — governed re-set of an existing `operating_protocol` rule through the guarded atomic store. The counterpart to `add-rule` (which only *appends* a new rule, fail-loud on an existing key), with the inverse default and two capabilities `add-rule` lacks. `update-rule` (`71befae`) re-sets a rule that **must already exist** (UPDATE default; `--create` to add instead), and adds **`--json`** (re-set a structured rule like `tool_hierarchy` with a dict/list value wholesale, not only a string) and **`--subkey`** (trim/re-set one sub-entry of a dict rule at a time). Backed by new `drift_store.set_operating_protocol_rule` (pure) / `set_operating_protocol_rule_file` (atomic), reusing the FIX-4 atomic `tmp → verify → .bak → rename` byte-parity `.bak`-mirror write path so an `operating_protocol` mutation keeps HOT↔.bak parity by construction. Unblocks the `tool_hierarchy` dict-trim — the last remaining piece of RAG-LEAN-PROSE. Runtime `__version__` 0.4.23 → 0.4.24, `__spec_version__` unchanged (3.2.6). CLI/store-only — no new module (19), health 20/20, drift gate `268149294421` unchanged (no schema/WAL/TLA+ change), `DRIFT_STORE_VERSION` unchanged (1.2.0), full suite 1,569 → 1,603 green (+34).

---

## v0.4.23 — Released (2026-06-23)

KA-14 + KA-16 + KA-17 — the session-resilience arc (bundled runtime release). Packages the three runtime increments merged to `main` since v0.4.22, hardening the session boundary against the fresh-deploy and interrupted-close failure modes the eBay Session-Zero audit surfaced. **KA-16** (`aa34e97`) — atomic, resumable session close: a `session_close` marker tracks the close as a forward-progress transaction and sets `transfer_ready` only after checkpoint + idempotent ERROR_LOG fold + logger close + audit all pass; `session-resume` finishes an interrupted close (+12 tests). **KA-14** (`e34691b`) — session-start rule-load attestation gate: two-phase token-attested start (`BOOT → RULES_LOADED(attested) → READY`) closing the fresh-deploy unloaded-rules root cause (+15 tests). **KA-17** (`dc5f0c0`) — declared, single-sourced supported-Python matrix (3.12–3.14) with a `doctor` ENV check, reconciling the former unsubstantiated `>=3.10` claim across manifest + README + 4 docs (+7 tests). Runtime `__version__` 0.4.22 → 0.4.23, `__spec_version__` unchanged (3.2.6). No new module (19), health 20/20, drift gate `268149294421` unchanged (no schema/WAL/TLA+ change), full suite 1,569 green. Its companion token-economy / context-emission doctrine is live in `operating_protocol`; the INIT-spec v3.2.7 seeding and a `python314_pip` accuracy fix follow in the next spec bump. The eBay deploy inherits via an `init --spec` upgrade.

---

## v0.4.22 — Released (2026-06-21)

KA-11 inc4 — TierC kernel reconciliation-surface manifest population + docs reconcile; the runtime release that bundles KA-11 inc1–4 (GOVERNANCE-DETERMINISM / KA-10 arc). Closes KA-11 (universalize the repo-claim↔reality↔record reconciliation pass) and completes the Track A kernel-hardening arc the eBay Session-Zero audit surfaced. inc1–2 added the per-project `meta.reconciliation_surfaces` manifest — schema + reader (`drift_audit.reconciliation_surfaces`) wired through `audit_file` / `audit --docs-root`, replacing the hardcoded kernel-specific doc list with a per-project declaration (universal default README / CHANGELOG / docs/ROADMAP) so the Rule 11 auditor is no longer kernel-repo-specific, byte-for-byte back-compatible for any RAG that has not declared a manifest. inc3 added the TierB INIT-spec session-end claim-reconciliation pass (spec v3.2.5 → v3.2.6). inc4 populates the kernel's own TierC manifest (`meta.reconciliation_surfaces` = README / CHANGELOG / docs/ROADMAP) and reconciles the published docs against the live canonical facts (`rag_kernel.__version__`, capability-module count, drift-gate sha). Runtime `__version__` 0.4.21 → 0.4.22, `__spec_version__` 3.2.5 → 3.2.6. No new module (19), health 20/20, drift gate `268149294421` unchanged (no schema/WAL/TLA+ change), full suite 1,535 green. Eleventh runtime increment of the KA-10 GOVERNANCE-DETERMINISM initiative; unblocks the eBay re-init (INS-047).

---

## v0.4.21 — Released (2026-06-21)

KA-7 — fail-loud audit when governance advanced past the session-log trail (GOVERNANCE-DETERMINISM / KA-10 arc). The dual of KA-1: where KA-1 catches a *completed* session log newer than the checkpoint (a session ran to a clean close but `meta.written_by_session` never caught up), KA-7 catches the inverse — `meta.written_by_session` advanced past the newest session-log-that-has-entries (`cp_ord > max logged ordinal`), i.e. the checkpoint moved forward but the observability trail did not. This is the second half of the eBay Session-Zero freeze signature ("logs stopped at S1 while the machine ran on"): KA-1 detects the case where a later session left a completed log the checkpoint ignored; KA-7 detects the case where the checkpoint marched ahead of every log that actually recorded work. New `drift_audit.check_observability_coherence` (ERROR) plus a `_session_log_has_entries` helper that distinguishes a real activity log from a marker-only / empty file. The two checks are mutually exclusive by construction — KA-1 fires only when a completed log is newer than the checkpoint, KA-7 only when the checkpoint is newer than every log — so a given RAG can trip at most one and they never double-report. Self-skips a `BOOTING` / un-stamped / malformed-id RAG and a no-logger project, so a healthy RAG (the checkpoint session is also the newest non-empty log) audits clean. Wired into `audit_file` so it runs at every session boundary. Dogfooded: a synthetic RAG whose `written_by_session` outran its newest non-empty log fails loud on `observability_coherence`; the live project RAG audits clean. `DRIFT_AUDIT_VERSION` 1.9.0 → 1.10.0. Ninth runtime increment of the KA-10 GOVERNANCE-DETERMINISM initiative (after KA-4, KA-6, KA-9, KA-3, KA-2A, KA-2B, KA-5, KA-1). No new module (19), health 20/20, drift gate `268149294421` unchanged (no schema/WAL/TLA+ change), full suite 1,499 → 1,524 green (+25).

---

## v0.4.20 — Released (2026-06-21)

KA-1 — fail-loud audit on a ran-but-never-checkpointed session (GOVERNANCE-DETERMINISM / KA-10 arc). Closes the auditor blind spot behind the S88 eBay headline ("deployed auditor passed clean while governance frozen at S0/seq1"): an agent ended sessions on `configure`/`audit` without ever `checkpoint`-ing, so `meta.written_by_session` stayed frozen while later sessions ran, and `audit --strict` never noticed. The KA-4 close gate already prevents the *live* session from closing un-checkpointed; KA-1 adds the missing *at-rest audit* invariant so even an already-frozen RAG fails loud. New `drift_audit.check_uncheckpointed_session` (ERROR) flags any session log beside the RAG (`session_log_<sid>.jsonl`) that both carries a `session_end` marker (ran to a clean close) and has a numeric session ordinal greater than `meta.written_by_session` — the freeze signature. It keys on `session_end` so the in-flight current session (still-open / detached / crashed, no end marker) is never false-positived; ignores any ordinal `<= written_by_session` (a historical checkpointed session whose log persists); and self-skips a `BOOTING` / un-stamped / malformed-id RAG and an empty RAG directory — so a healthy RAG (newest completed log *is* `written_by_session`) audits clean. Wired into `audit_file` so it runs at every session boundary. Dogfooded: a synthetic S1-frozen RAG with a completed `session_log_S2` fails loud; the live RAG audits clean. `DRIFT_AUDIT_VERSION` 1.8.0 → 1.9.0. Eighth runtime increment of the KA-10 GOVERNANCE-DETERMINISM initiative (after KA-4, KA-6, KA-9, KA-3, KA-2A, KA-2B, KA-5). No new module (19), health 20/20, drift gate `268149294421` unchanged (no schema/WAL/TLA+ change), full suite 1,475 → 1,499 green (+24).

---

## v0.4.19 — Released (2026-06-20)

KA-5 — single-source the `@rag-kernel-manifest` version (GOVERNANCE-DETERMINISM / KA-10 arc). Closes E-046: the package manifest in `rag_kernel/__init__.py`'s docstring hardcoded `version` / `spec_version` literals that had drifted from the live authorities (frozen at `0.4.7` / spec `3.2.2` while the kernel had moved on to `0.4.18` / spec `3.2.5`), yet `audit --strict` passed clean. The manifest docstring no longer carries those literals; `rag_kernel.__version__` and the new `rag_kernel.__spec_version__` are the sole authorities, and `discover()` injects the version fields from them so a published manifest can no longer drift (derived, not duplicated). New `drift_audit.check_manifest_version_binding` is a fail-loud (ERROR) standing regression check: it fires if an authority constant is missing/empty, if a `version` / `spec_version` literal is re-introduced into the docstring and disagrees with its authority, or if `discover()`'s injected manifest disagrees with the authorities. Pure introspection over the kernel package (no RAG input), wired into `audit_hot` so it runs at every session boundary; self-skips only if `rag_kernel` cannot import. Dogfooded: a re-introduced stale literal and a deleted authority each fail loud; the live package binds clean. `DRIFT_AUDIT_VERSION` 1.7.0 → 1.8.0. Seventh runtime increment of the KA-10 GOVERNANCE-DETERMINISM initiative (after KA-4, KA-6, KA-9, KA-3, KA-2A, KA-2B). No new module (19), health 20/20, drift gate `268149294421` unchanged (no schema/WAL/TLA+ change), full suite 1,469 → 1,475 green (+6).

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
