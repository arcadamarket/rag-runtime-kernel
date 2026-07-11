"""Fail-loud session auditor over the canonical tracked_items array — DRIFT-ELIM increment 5.

Increment 1 (``drift_control``) gave the pure item-lifecycle core; increment 2
(``drift_store``) the deterministic, atomic mutation API + the one canonical
``tracked_items`` array; increment 3 the ``resolve|defer|...`` CLI + registration;
increment 4 (``drift_render``) turned every other place that records item status
into a *render* of that array. This module closes the loop: it is the
**session-boundary auditor** that asserts every rendered surface still matches the
canonical array and **fails loud** on any divergence — turning the E-040 incident
("one item carried two contradictory statuses with no canonical field") into a
standing regression check instead of a manual best-effort reconciliation pass.

What it checks (each deterministic, zero-LLM)
---------------------------------------------
* **render parity** (E-040 regression, ERROR): the persisted legacy
  ``open_tasks`` / ``deferred_items`` arrays MUST equal the render of the canonical
  ``tracked_items`` array. A hand-edit of a legacy array (the exact drift inc 4
  removed) makes the render diverge and is caught here.
* **supersede referential integrity** (ERROR): every ``SUPERSEDED`` item's
  ``superseded_by`` must point to an id that actually exists in the array — a
  dangling supersede ref is a broken record.
* **note/status contradiction** (WARNING, INS-038): an active (OPEN / IN_PROGRESS)
  item whose one-line ``note`` *claims* completion ("done", "resolved", "shipped",
  …) contradicts its own canonical status — the stale-note class INS-038 surfaced
  when inc 4 found no guarded note-update path existed. Heuristic, so a warning,
  not a hard failure; the guarded ``set_note`` verb (inc 5) is the fix.
* **no side rule/state stores** (Rule 13, ERROR): no Cowork-memory-style side files
  (``MEMORY.md``, ``feedback_*.md``, ``project_*.md``) may exist inside the project
  root — the RAG is the single source of truth (E-039). Scanned within the project
  root ONLY (filesystem_boundary / E-026).

Fail-loud contract
------------------
``audit_*`` returns an :class:`AuditReport` (never raises for a *finding*).
:func:`assert_clean` raises :class:`DriftAuditError` if the report carries any
ERROR finding (and, with ``strict=True``, on warnings too). The CLI ``audit``
command and the session-close discipline call ``assert_clean`` so a divergence
stops the session instead of silently shipping.

Design philosophy
-----------------
CS lens: an auditor is a pure predicate over persisted state — it recomputes the
derived surfaces from the one authority and asserts equality. A mismatch is a
detectable defect with an exact location, not a judgement call; ERROR findings are
hard invariants (fail-closed), the one heuristic is a clearly-labelled WARNING.

ML lens: the LLM no longer reconciles status by reading prose and "deciding" what's
current (the surface that drifts). The auditor decides — deterministically, at zero
token cost — and the LLM only acts on a compact pass/fail report, which protects
the no-compaction context budget.

Convergence: "LLM proposes. System decides. State persists." — and now the system
also *verifies* that every render still equals the state it persisted.

@rag-kernel-manifest
{
  "module": "rag_kernel.drift_audit",
  "capability": "state_audit",
  "description": "Fail-loud session-boundary auditor: asserts the rendered legacy open_tasks/deferred_items match the canonical tracked_items array (E-040 regression), supersede refs resolve, notes don't contradict status (INS-038), and no Cowork-memory side stores exist in the project root (Rule 13) — DRIFT-ELIM increment 5",
  "exports": ["AuditFinding", "AuditReport", "DriftAuditError",
              "ERROR", "WARNING", "DRIFT_AUDIT_VERSION",
              "check_render_parity", "check_supersede_refs",
              "check_note_status_contradiction", "check_side_rule_stores",
              "check_ledger_consistency", "check_record_coverage",
              "check_repo_claim_reconciliation", "check_current_status_freshness",
              "check_current_status_coherence", "check_manifest_version_binding",
              "check_placeholder_tokens", "check_project_context_placeholders",
              "check_template_keys",
              "check_written_by_session", "check_session_id_coherence",
              "check_sessions_recent_coherence",
              "check_wal_integrity", "check_bak_parity", "check_cold_hot_version",
              "canonical_facts", "audit_hot", "audit_file", "assert_clean"],
  "use_when": "Verifying at a session boundary that every status render still matches the canonical tracked_items array and no parallel state store exists",
  "never_bypass": true
}
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from rag_kernel.drift_control import (
    ItemKind,
    ItemStatus,
    RETIRED_STATUSES,
    TERMINAL_STATUSES,
)
from rag_kernel.drift_store import (
    INFERENCE_LEDGER_KEY,
    TRACKED_ITEMS_KEY,
    DriftStoreError,
    TrackedItemStore,
    _coerce_utc_date,
    _coerce_utc_instant,
    _CS_HEAD_FIELDS,
    _CS_HEAD_RE,
    _CS_VERSION_FIELD,
    _CS_VERSION_TOKEN_RE,
    ledger_disposition_to_status,
    load_hot,
    sessions_recent_duplicate_pairs,
)
from rag_kernel import drift_render
from rag_kernel import persistence

# Bump when a check's contract or the report shape changes in a way a test pins.
# 1.1.0 — added check_current_status_freshness (E-043): guards the current_status
#         narrative against the live runtime version / git HEAD authorities.
# 1.2.0 — FIX-1 (K1+K2): added the integrity-invariant family the auditor was blind
#         to — WAL monotonicity, RAG<->.bak parity, unresolved-<PLACEHOLDER> scan,
#         _-prefixed template-key scan, COLD<->HOT spec-version coherence, non-empty
#         written_by_session, and session-id coherence. All fail-loud ERRORs (same
#         family as the E-040 render check); each self-skips when its source is absent.
# 1.3.0 — FIX-4 (K6): check_bak_parity now asserts true BYTE-PARITY between HOT and
#         its .bak (operator-settled parity-mirror contract), replacing the FIX-1
#         seq-based equal-or-one-behind allowance — the one-behind branch was the
#         rollback-prev contract the operator rejected. Paired with the enforce half
#         (mirror_bak=True on the canonical writers).
#   1.4.0 — FIX-5/P2: check_context_side_stores flags a stray ``*_context.json``
#         persisted in the RAG directory (a redundant copy of state already merged
#         into the canonical RAG by ``configure`` — the eBay ``ebay_context.json``
#         side-file). Extends the Rule 13 side-store family from the project root
#         (Cowork-memory MDs) to the RAG dir (context inputs).
# 1.4.0 (cont.) — FIX-7/T1: check_side_rule_stores + check_context_side_stores now
#         DELEGATE to the persistence side-store finders (single source of truth),
#         so the after-the-fact audit and the new live pre-write guard
#         (persistence.assert_no_side_stores) cannot diverge. Behavior/report shape
#         unchanged — same findings, same messages — so the version stays 1.4.0.
# 1.5.0 — KA-9 (KA-10 GOVERNANCE-DETERMINISM): check_project_context_placeholders
#         fails loud on residual lowercase/spaced human-fill ``<...>`` tokens in
#         project_context that the UPPER_SNAKE scan missed (the eBay session-zero
#         ``<from user>`` defect).
# 1.6.0 — KA-3 (KA-10 GOVERNANCE-DETERMINISM): check_current_status_coherence
#         asserts current_status's denormalized self-facts agree with ``meta`` —
#         current_status.session == meta.written_by_session and the calendar day
#         of current_status.last_updated == that of meta.last_updated_utc. The eBay
#         deploy froze current_status.session at S0 and ran last_updated two days
#         behind meta while audit --strict reported 0 findings. Fail-loud ERROR,
#         self-skips when either side is absent/unparseable (this kernel's own RAG
#         omits these keys, so it stays clean).
# 1.7.0 — KA-2 (KA-10 GOVERNANCE-DETERMINISM): check_sessions_recent_coherence
#         fails loud on duplicate-bootstrap rows in sessions_recent — two rows that
#         share a checkpoint timestamp ``d`` (the eBay S0/S1 signature: both minted
#         at one instant, one never actually run, while audit --strict reported 0
#         findings and there was no governed repair path). Order-agnostic by design:
#         the project legitimately writes sessions_recent both oldest-first (this
#         kernel's live RAG) and newest-first (a fresh init --auto-ready RAG), and
#         one session legitimately spans multiple rows (the S95/S95 pair), so a
#         SHARED timestamp is the only phantom-duplicate signal safe across every
#         shape — directional id/timestamp monotonicity would false-positive on a
#         clean deploy. Self-skips when sessions_recent is absent/<2 rows or a row's
#         d is missing. (Increment A; the governed row-repair/dedup verb is the
#         paired increment B.)
# 1.8.0 — KA-5 (KA-10 GOVERNANCE-DETERMINISM): check_manifest_version_binding fails
#         loud if the @rag-kernel-manifest version fields drift from the single-source
#         authorities (rag_kernel.__version__ / __spec_version__). The docstring no
#         longer hardcodes version/spec_version literals — discover() injects them —
#         so the E-046 drift (docstring frozen at 0.4.7 / spec 3.2.2 while live was
#         0.4.12 / spec 3.2.4) is now a standing regression check. Pure introspection
#         over the package, always-on; self-skips only if rag_kernel can't import.
# 1.9.0 — KA-1 (KA-10 GOVERNANCE-DETERMINISM): check_uncheckpointed_session fails
#         loud (at rest) when a COMPLETED session log (session_end) sits NEWER than
#         meta.written_by_session — the ran-but-never-checkpointed governance freeze
#         (eBay S0/seq1) the auditor previously missed; keys on session_end so the
#         in-flight session is never false-positived.
# 1.10.0 — KA-7 (KA-10 GOVERNANCE-DETERMINISM): check_observability_coherence fails
#         loud when meta.written_by_session advanced PAST the newest session log that
#         holds entries (cp_ord > max logged ordinal) — meta advanced but the
#         observability trail did not (the eBay logs stopped at S1 while meta kept
#         advancing, audit still clean). Complement of KA-1 (a completed log newer
#         than the checkpoint); the two are mutually exclusive. Self-skips on BOOTING,
#         missing/non-canonical written_by, and when NO session log carries entries.
# 1.11.0 — KA-11 (KA-10 GOVERNANCE-DETERMINISM): the Rule 11 published-doc
#         reconciliation surfaces are now resolved from the per-project manifest
#         meta.reconciliation_surfaces (TierC) via reconciliation_surfaces(), instead
#         of the formerly hardcoded README/CHANGELOG/docs-ROADMAP doc_paths. Absent/
#         empty/malformed manifest falls back to the universal defaults, so the
#         auditor is no longer kernel-repo-specific yet stays byte-for-byte back-
#         compatible for every RAG that has not declared a manifest.
DRIFT_AUDIT_VERSION = "1.11.0"

# Severities.
ERROR = "error"      # a hard invariant violation — assert_clean always raises
WARNING = "warning"  # a heuristic concern — raises only under strict=True

# Active (non-terminal) statuses — an item here should not claim it is finished.
_ACTIVE_STATUSES = frozenset({ItemStatus.OPEN, ItemStatus.IN_PROGRESS})

# Words in a note that *claim completion*. On an active item that is a
# note/status contradiction (the stale-note class, INS-038). Matched on word
# boundaries, case-insensitive, so "fix the bug" never trips but "fixed" does.
_COMPLETION_CLAIM_WORDS: tuple[str, ...] = (
    "done",
    "resolved",
    "shipped",
    "complete",
    "completed",
    "merged",
    "fixed",
    "closed",
    "finished",
)
_COMPLETION_CLAIM_RE = re.compile(
    r"\b(" + "|".join(_COMPLETION_CLAIM_WORDS) + r")\b", re.IGNORECASE
)

# Forbidden parallel rule/state store patterns (Cowork-memory MDs in the project
# root + stray ``*_context.json`` beside the RAG), Rule 13 / E-039 / FIX-5 P2.
# FIX-7 / T1 relocated these patterns + their scan logic into ``persistence`` as
# the SINGLE source of truth, so the after-the-fact audit checks below and the
# live pre-write guard (persistence.assert_no_side_stores) cannot drift apart.
# The two ``check_*`` functions delegate to the persistence finders.

# --- Rule 11 published-doc reconciliation (increment 6) --------------------
# Closed vocabulary of PENDING / not-done status words. A published doc line that
# pairs one of these with a tracked id whose canonical status is RESOLVED is the
# E-033 / E-040 drift: public content claiming finished work is still pending.
# Word-boundaried at the use site (see _PENDING_CLAIM_RE).
#
# NOTE — deliberately EXCLUDES "unreleased"/"released": resolution and release are
# orthogonal axes here. An item can be RESOLVED (built, on `main`) yet correctly
# "unreleased" — a status this project uses constantly — so "unreleased" near a
# RESOLVED id is NOT a contradiction. Release state is reconciled separately by the
# headline current-version check (and RELEASE-kind items), not by this word set.
_PENDING_CLAIM_WORDS: tuple[str, ...] = (
    "planned", "deferred", "under development", "under-development",
    "known issue", "known-issue", "in progress", "in-progress", "not yet",
    "not registered", "upcoming", "wip", "todo", "to do",
)
# Word-boundaried so a status word never matches inside an identifier — e.g.
# "deferred" must NOT fire on the code/array name "deferred_items" (the "_" is a
# word char, so \bdeferred\b excludes it) while still matching "deferred to …".
_PENDING_CLAIM_RE = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in _PENDING_CLAIM_WORDS) + r")\b", re.IGNORECASE
)
# KA-RECON-PROXIMITY: the §2 id-anchored check pairs a PENDING word with a RESOLVED
# id only when they share a SENTENCE, not merely the same (often long, multi-clause)
# line. A pending word describing one feature must not be read as the status of an
# unrelated id elsewhere on the line — e.g. ROADMAP "`--dry-run` prints the planned
# old->new token diff" sits three sentences away from the RESOLVED KA-CS-REFRESH /
# FIX-4 ids on the same paragraph line. Splitting on sentence / semicolon boundaries
# ONLY (never dashes or table pipes) preserves the genuine single-line "ID — planned"
# / "ID: deferred" contradiction while dropping the cross-sentence false positive.
# Sentence dots always take a following space, so version dots (0.4.27,
# rag_kernel.__version__) never split. The change can only make §2 MORE conservative,
# so docs that already reconcile clean stay clean.
_SENTENCE_SPLIT_RE = re.compile(r"(?:\.\s+|;\s+)")
# A line naming a SUPERSEDED/past runtime version is a historical record that
# legitimately describes a past state — exempt it from the current-state checks
# (the headline-fact check still reconciles current-version numbers elsewhere).
_PAST_VERSION_RE = re.compile(
    r"(v?0\.[0-3]\.\d+|runtime-v?0\.[0-3]\.\d+|v3\.1\.\d+)", re.IGNORECASE
)
_HISTORICAL_MARKERS: tuple[str, ...] = (
    "superseded", "historical", "at that time", "previously", "deprecated",
)
# "N modules" claim and a drift-gate "sha <hex>" claim.
_MODULES_RE = re.compile(r"(\d+)\s+(?:capability\s+|runtime\s+)?modules?", re.IGNORECASE)
_SHA_RE = re.compile(r"sha[`'\s:=]{0,6}([0-9a-f]{12,64})", re.IGNORECASE)
# Forensic ``E-###`` record ids as they head an ERROR_LOG entry (markdown heading).
_ERROR_HEADING_RE = re.compile(r"^#+\s*(E-\d+)\b", re.MULTILINE)

# --- current_status freshness (E-043) --------------------------------------
# current_status is a human-readable narrative block that DENORMALIZES facts
# whose authorities live OUTSIDE the RAG: the kernel version
# (``rag_kernel.__version__``) and the published git HEAD. Those are not RAG
# state, so they cannot be *rendered* from the RAG — the discipline is to GUARD
# them: extract the stated fact from the narrative and assert it still equals the
# live authority. The fields below are the conventional carriers of each fact.
# The field-names + leading-token regexes (``_CS_VERSION_FIELD``, ``_CS_HEAD_FIELDS``,
# ``_CS_VERSION_TOKEN_RE``, ``_CS_HEAD_RE``) now live in ``drift_store`` (the lower
# module) and are imported above. This makes the guard that DETECTS a stale
# current_status here and the ``refresh_current_status_file`` verb that REPAIRS it
# there read the IDENTICAL token definitions — one source of truth, so detection and
# repair can never disagree (KA-CS-REFRESH; same pattern as the shared date coercers).
# They are re-exported below for backward-compatible ``drift_audit._CS_*`` access.

# --- integrity invariants (FIX-1 / K1+K2) ----------------------------------
# An unresolved build-time placeholder: a value that IS *exactly* an
# UPPER_SNAKE token in angle brackets (e.g. "<ISO>", "<SESSION_ID>"). Matched
# whole-string ONLY — so a timestamp field literally holding "<ISO>" is caught
# (K3) while rule PROSE that merely mentions a template token (e.g. the report
# heading "S<NN> close" inside operating_protocol) is NOT a false positive.
_PLACEHOLDER_VALUE_RE = re.compile(r"^<[A-Z][A-Z0-9_]*>$")
# A residual HUMAN-FILL placeholder (KA-9): an angle-bracketed token of word
# chars (e.g. "<from user>", "<absolute path>", "<your project>"). Unlike the
# UPPER_SNAKE _PLACEHOLDER_VALUE_RE that the spec parser substitutes at build
# time, these session-zero tokens are filled by the LLM at deploy — so they are
# lowercase/spaced and the parser-token scan never caught them. Matched as a
# SUBSTRING (a partially-filled "Build <from user>'s store" is caught too). The
# token-must-start-with-a-letter anchor keeps a math comparison ("a < b and c >
# d", whose first '<' is followed by a space) from being a false positive, and
# the reporter filters to tokens carrying a lowercase letter or space so a pure
# UPPER_SNAKE token in project_context is left to check_placeholder_tokens (no
# double-report). This is the KA-9 gap: the eBay deploy shipped project_context
# brief/domain/end_goal as a verbatim "<from user>".
_HUMAN_PLACEHOLDER_RE = re.compile(r"<[A-Za-z][A-Za-z0-9 _/.\-]{0,48}>")
_HUMAN_PLACEHOLDER_SIGNAL_RE = re.compile(r"[a-z ]")
# A semver-ish version token (first X.Y.Z found), used for COLD<->HOT coherence.
_SEMVER_RE = re.compile(r"(\d+\.\d+\.\d+)")
# A malformed/machine-minted session id: "S" immediately followed by "-<digit>"
# (the negative-looking S-12488-1781260490 the eBay deploy minted, K7). A
# canonical id is "S" + a non-negative integer (S0, S70, ...).
_BAD_SESSION_ID_RE = re.compile(r"^S-\d")
# A canonical session id is "S" + a non-negative integer (S0, S70, ...); KA-1
# compares the numeric ORDINAL of a completed session log against the last
# checkpoint's session. (S-<digit> malformed ids are check_session_id_coherence's
# concern and parse to None here, so they are skipped — no double-report.)
_SESSION_ID_INT_RE = re.compile(r"^S(\d+)$")
# Conventional per-session log filename beside the RAG (RAG/session_log_<sid>.jsonl)
# and the close-marker event written by SessionLogger.close(emit_end=True). A log
# carrying this event ran to a CLEAN close (not still-open / detached / crashed).
_SESSION_LOG_GLOB = "session_log_*.jsonl"
_SESSION_LOG_PREFIX = "session_log_"
_SESSION_END_EVENT = "session_end"
# State in which written_by_session is legitimately not-yet-stamped: a freshly
# init'd RAG sits BOOTING before its first checkpoint stamps the session id.
_PRE_CHECKPOINT_STATE = "BOOTING"
# Conventional sibling filenames beside RAG_MASTER.json (overridable via
# meta.rag_files) for the file-based integrity checks.
_DEFAULT_WAL_NAME = "WAL.jsonl"
_DEFAULT_COLD_NAME = "RAG_COLD.json"
# KA-11 (TierC surface manifest): the file-based published surfaces the Rule 11
# reconciliation reads, RELATIVE to docs_root. Universal default for a kernel-style
# repo; per-project override lives in meta.reconciliation_surfaces (a list of
# paths). Absent/empty manifest -> these defaults, so every pre-KA-11 RAG and every
# deployment that has not declared surfaces still reconciles exactly as before.
_DEFAULT_RECONCILIATION_SURFACES = ("README.md", "CHANGELOG.md", "docs/ROADMAP.md")


# ---------------------------------------------------------------------------
# Errors + report
# ---------------------------------------------------------------------------

class DriftAuditError(Exception):
    """Raised by :func:`assert_clean` when the audit is not clean.

    Fail-loud by design: a render that no longer matches the canonical array, a
    dangling supersede ref, or a parallel state store must STOP the session — the
    silent-divergence path is precisely what E-040 / E-039 cost the project.
    """


@dataclass(frozen=True)
class AuditFinding:
    """One audit defect: which check, where, what, and how serious."""

    check: str
    severity: str
    detail: str
    item_id: Optional[str] = None

    def __str__(self) -> str:
        loc = f" [{self.item_id}]" if self.item_id else ""
        return f"{self.severity.upper()} {self.check}{loc}: {self.detail}"

    def to_dict(self) -> dict:
        return {
            "check": self.check,
            "severity": self.severity,
            "detail": self.detail,
            "item_id": self.item_id,
        }


@dataclass(frozen=True)
class AuditReport:
    """The result of an audit run: an ordered list of findings (may be empty)."""

    findings: tuple[AuditFinding, ...] = field(default_factory=tuple)

    @property
    def errors(self) -> tuple[AuditFinding, ...]:
        return tuple(f for f in self.findings if f.severity == ERROR)

    @property
    def warnings(self) -> tuple[AuditFinding, ...]:
        return tuple(f for f in self.findings if f.severity == WARNING)

    @property
    def ok(self) -> bool:
        """True iff there are no ERROR findings (warnings do not break ``ok``)."""
        return not self.errors

    def is_clean(self, *, strict: bool = False) -> bool:
        """No errors (and, under ``strict``, no warnings either)."""
        if strict:
            return not self.findings
        return self.ok

    def summary(self) -> str:
        if not self.findings:
            return "audit clean: 0 findings (renders match canonical, refs resolve, no side stores)"
        return (
            f"audit: {len(self.errors)} error(s), {len(self.warnings)} warning(s)\n"
            + "\n".join(f"  - {f}" for f in self.findings)
        )

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "errors": len(self.errors),
            "warnings": len(self.warnings),
            "findings": [f.to_dict() for f in self.findings],
        }


# ---------------------------------------------------------------------------
# Individual checks (each pure; returns a list of findings)
# ---------------------------------------------------------------------------

def check_render_parity(hot: dict) -> list[AuditFinding]:
    """ERROR if the persisted legacy arrays != the render of tracked_items.

    This is the E-040 regression assertion: ``open_tasks`` and ``deferred_items``
    are renders (inc 4), so the bytes on disk must reproduce exactly from the
    canonical array. A hand-edit of either array — the drift inc 4 removed — makes
    them diverge and is caught here. An ABSENT legacy array is not a parity error
    (nothing was hand-edited); it is simply un-rendered.
    """
    findings: list[AuditFinding] = []
    store = TrackedItemStore.from_hot(hot)

    if "open_tasks" in hot:
        expected = drift_render.render_open_tasks(store)
        actual = hot["open_tasks"]
        if actual != expected:
            findings.append(AuditFinding(
                check="render_parity",
                severity=ERROR,
                detail=(
                    "persisted open_tasks does not match the render of tracked_items "
                    f"({len(actual) if isinstance(actual, list) else '?'} persisted "
                    f"vs {len(expected)} rendered) — hand-edited? run "
                    "`rag_kernel render --apply`"
                ),
            ))

    if "deferred_items" in hot:
        expected_d = drift_render.render_deferred_items(store)
        actual_d = hot["deferred_items"]
        if actual_d != expected_d:
            findings.append(AuditFinding(
                check="render_parity",
                severity=ERROR,
                detail=(
                    "persisted deferred_items does not match the render of "
                    f"tracked_items ({len(actual_d) if isinstance(actual_d, list) else '?'} "
                    f"persisted vs {len(expected_d)} rendered) — hand-edited? run "
                    "`rag_kernel render --apply`"
                ),
            ))
    return findings


def check_supersede_refs(source) -> list[AuditFinding]:
    """ERROR if any SUPERSEDED item points at an id not present in the array."""
    store = _as_store(source)
    ids = set(store.ids())
    findings: list[AuditFinding] = []
    for it in store:
        if it.status == ItemStatus.SUPERSEDED:
            ref = it.superseded_by
            if not ref or ref not in ids:
                findings.append(AuditFinding(
                    check="supersede_refs",
                    severity=ERROR,
                    detail=(
                        f"SUPERSEDED item references superseded_by={ref!r} "
                        "which is not a tracked id"
                    ),
                    item_id=it.id,
                ))
    return findings


def check_note_status_contradiction(source) -> list[AuditFinding]:
    """WARNING if an active item's note claims completion (stale note, INS-038)."""
    store = _as_store(source)
    findings: list[AuditFinding] = []
    for it in store:
        if it.status in _ACTIVE_STATUSES and it.note:
            m = _COMPLETION_CLAIM_RE.search(it.note)
            if m:
                findings.append(AuditFinding(
                    check="note_status_contradiction",
                    severity=WARNING,
                    detail=(
                        f"status is {it.status.value} but note claims "
                        f"'{m.group(0)}' — refresh via `rag_kernel note {it.id} ...` "
                        "or transition the item"
                    ),
                    item_id=it.id,
                ))
    return findings


def check_side_rule_stores(root: Path | str) -> list[AuditFinding]:
    """ERROR for each forbidden side rule/state store found inside ``root`` (Rule 13).

    Scans the project root ONLY (filesystem_boundary / E-026) — never Desktop,
    AppData, or the Cowork memory dir. Finds ``MEMORY.md`` and any
    ``feedback_*.md`` / ``project_*.md`` (the Cowork-memory file shapes that E-039
    forbade), skipping VCS/build dirs.
    """
    root_path = Path(root)
    findings: list[AuditFinding] = []
    for p in persistence.find_forbidden_rule_stores(root_path):
        try:
            rel = p.relative_to(root_path)
        except ValueError:
            rel = p
        findings.append(AuditFinding(
            check="side_rule_stores",
            severity=ERROR,
            detail=(
                f"forbidden parallel rule/state store inside project root: {rel} "
                "— all rules/state belong in the RAG (Rule 13 / E-039)"
            ),
        ))
    return findings


def check_context_side_stores(rag_dir: Path | str) -> list[AuditFinding]:
    """ERROR for each stray ``*_context.json`` persisted in the RAG directory (FIX-5 / P2).

    A ``*_context.json`` is a transient input to ``configure`` whose content is
    merged into the canonical RAG; a copy lingering beside RAG_MASTER.json is a
    redundant second artifact and a parallel-state hazard (the eBay
    ``ebay_context.json`` side-file the deploy audit flagged). Scans the RAG
    directory ONLY and non-recursively (``glob``, not ``rglob``), so a context
    file located elsewhere as a genuine one-off input is unaffected, and the
    filesystem_boundary rule (E-026) is respected. Returns one ERROR per hit.

    The sanctioned, persistent project-context store(s) in
    ``persistence.SANCTIONED_CONTEXT_STORES`` (e.g. ``RAG_CONTEXT.json``, the
    non-loaded FIX-11 / U3 partition) are excluded by the finder and are never
    flagged — only transient ``*_context.json`` inputs are.
    """
    findings: list[AuditFinding] = []
    for p in persistence.find_context_side_stores(rag_dir):
        findings.append(AuditFinding(
            check="context_side_stores",
            severity=ERROR,
            detail=(
                f"redundant context input persisted beside the RAG: {p.name} "
                "— its content is merged into RAG_MASTER.json by `configure`; "
                "remove the stray copy (Rule 13 / FIX-5 P2)"
            ),
        ))
    return findings


# ---------------------------------------------------------------------------
# Record-store coverage + consistency (increment 6)
# ---------------------------------------------------------------------------

def check_ledger_consistency(hot: dict) -> list[AuditFinding]:
    """ERROR if an inference_ledger disposition disagrees with its canonical item.

    Increment 6 migrated every ``inference_ledger`` entry into a canonical
    INFERENCE ``tracked_item``. The ledger keeps its forensic prose, but its
    *status* is now a render of the canonical item: each entry's
    ``disposition`` must map (via :func:`ledger_disposition_to_status`) to the
    exact status of the same-id INFERENCE item. A drifted disposition — or a
    missing/mis-kinded canonical item — is caught here, extending the E-040
    parity guarantee to the ledger.
    """
    findings: list[AuditFinding] = []
    led = hot.get(INFERENCE_LEDGER_KEY, []) if isinstance(hot, dict) else []
    if not isinstance(led, list):
        return findings
    store = TrackedItemStore.from_hot(hot)
    # Pre-cutover gate: if NO INFERENCE items exist yet, the ledger has not been
    # migrated into tracked_items (the increment-6 cutover the user triggers
    # deliberately) — the ledger is still its own authority, so there is nothing
    # to reconcile. Enforcement turns on the moment any INFERENCE item exists.
    if not any(it.kind == ItemKind.INFERENCE for it in store):
        return findings
    by_id = {it.id: it for it in store}
    for e in led:
        rid = e.get("id")
        disp = e.get("disposition")
        try:
            want = ledger_disposition_to_status(disp)
        except DriftStoreError as exc:
            findings.append(AuditFinding(
                check="ledger_consistency", severity=ERROR,
                detail=str(exc), item_id=rid))
            continue
        it = by_id.get(rid)
        if it is None:
            findings.append(AuditFinding(
                check="ledger_consistency", severity=ERROR,
                detail=f"inference_ledger {rid} has no canonical tracked item",
                item_id=rid))
        elif it.kind != ItemKind.INFERENCE:
            findings.append(AuditFinding(
                check="ledger_consistency", severity=ERROR,
                detail=f"{rid} canonical item is kind={it.kind.value}, expected INFERENCE",
                item_id=rid))
        elif it.status != want:
            findings.append(AuditFinding(
                check="ledger_consistency", severity=ERROR,
                detail=(f"ledger disposition {disp!r} -> {want.value} but canonical "
                        f"status is {it.status.value}"),
                item_id=rid))
    return findings


def check_record_coverage(
    hot: dict, *, error_log_path: Optional[Path | str] = None
) -> list[AuditFinding]:
    """ERROR for any forensic record NOT migrated into the canonical array.

    Every ``inference_ledger`` entry must have a canonical INFERENCE item, and
    every ``E-###`` heading in ERROR_LOG.md must have a canonical ERROR item
    (the increment-6 single-source-of-truth invariant). A new forensic record
    added without a canonical tracked item re-opens the very un-audited region
    INS-039 closed, so it fails loud. The ERROR_LOG scan runs only when
    ``error_log_path`` is given and exists.
    """
    findings: list[AuditFinding] = []
    store = TrackedItemStore.from_hot(hot)
    # Count only NON-RETIRED members of each forensic kind. A SUPERSEDED/DISCARDED
    # item has been withdrawn from the live canonical set, so it must NOT keep the
    # per-kind cutover gate latched ON: a mis-kinded item that is discarded (or an
    # un-add) has to let the gate fall back to its pre-migration (empty) state, or
    # the mis-kind is unrecoverable — every un-migrated E-###/ledger record would
    # fail loud with no way to clear it (KA-CUTOVER-GATE). RESOLVED stays counted
    # (a completed record is still a real canonical fact that needs coverage).
    inf_ids = {it.id for it in store
               if it.kind == ItemKind.INFERENCE and it.status not in RETIRED_STATUSES}
    err_ids = {it.id for it in store
               if it.kind == ItemKind.ERROR and it.status not in RETIRED_STATUSES}

    # Pre-cutover gate (per kind): coverage is enforced only once that record kind
    # has been migrated (any NON-retired item of the kind exists). Before the
    # deliberate increment-6 cutover the legacy stores remain authoritative, so an
    # empty canonical set is the correct pre-migration state, not a coverage gap.
    led = hot.get(INFERENCE_LEDGER_KEY, []) if isinstance(hot, dict) else []
    if inf_ids:
        for e in led or []:
            rid = e.get("id")
            if rid and rid not in inf_ids:
                findings.append(AuditFinding(
                    check="record_coverage", severity=ERROR,
                    detail=f"inference_ledger {rid} not migrated into tracked_items (kind=INFERENCE)",
                    item_id=rid))

    if error_log_path is not None and err_ids:
        p = Path(error_log_path)
        if p.exists():
            text = p.read_text(encoding="utf-8")
            seen: set[str] = set()
            for m in _ERROR_HEADING_RE.finditer(text):
                rid = m.group(1)
                if rid in seen:
                    continue
                seen.add(rid)
                if rid not in err_ids:
                    findings.append(AuditFinding(
                        check="record_coverage", severity=ERROR,
                        detail=f"ERROR_LOG {rid} has no canonical tracked item (kind=ERROR)",
                        item_id=rid))
    return findings


# ---------------------------------------------------------------------------
# Rule 11 published-doc reconciliation (increment 6)
# ---------------------------------------------------------------------------

def check_repo_claim_reconciliation(
    doc_paths,
    source,
    *,
    version: Optional[str] = None,
    module_count: Optional[int] = None,
    drift_sha: Optional[str] = None,
) -> list[AuditFinding]:
    """Rule 11: published docs must not contradict the canonical records.

    Two deterministic, fail-loud reconciliations over README / CHANGELOG /
    ROADMAP (whichever paths are given):

    1. **Headline facts** — any line asserting the CURRENT runtime ``version``
       token together with an "N modules" count must have ``N == module_count``;
       any ``sha <hex>`` drift-gate mention must equal ``drift_sha``. Lines that
       name a *past* version are historical records and are exempt from the module
       count (those numbers legitimately differ).
    2. **ID-anchored status claims** — over README + ROADMAP (CHANGELOG is
       append-only history by design and exempt), a line pairing a tracked id whose
       canonical status is RESOLVED with a PENDING word (planned / deferred /
       unreleased / …) is the E-033 / E-040 drift (public content understating
       shipped reality) and is an ERROR. Lines naming a past version or marked
       historical are exempt.

    BOUNDARY (documented, never an excuse): a pure-narrative claim carrying no
    tracked id and no headline number is not deterministically reconcilable here.
    That residual is covered by the recurring manual reconcile pass + the headline
    numeric check — it is a *stated* limit, not a silent assumption of cleanliness.
    """
    findings: list[AuditFinding] = []
    store = _as_store(source)
    resolved_ids = sorted((it.id for it in store if it.status == ItemStatus.RESOLVED),
                          key=len, reverse=True)  # longest-first: stable, deterministic order
    # KA-19: match a tracked id only at its token boundaries, never as a bare
    # substring. IDs are ``[A-Za-z0-9-]`` tokens, so "FIX-1" must NOT match inside
    # "FIX-12" (the prior ``rid in ln`` substring test flagged a RESOLVED FIX-1 as
    # "still pending" whenever a longer FIX-1x id appeared on a pending-claim line).
    # A boundary = start/end-of-line or any char that is neither a word char nor a
    # hyphen, so neither a digit/letter suffix (FIX-12) nor a hyphen extension
    # (FIX-1-alpha) can spuriously match.
    resolved_id_res = [
        (rid, re.compile(r"(?<![\w-])" + re.escape(rid) + r"(?![\w-])"))
        for rid in resolved_ids
    ]
    ver_re = None
    if version:
        ver_re = re.compile(r"v?" + re.escape(version.lstrip("v")) + r"\b")

    for dp in doc_paths or []:
        p = Path(dp)
        if not p.exists():
            findings.append(AuditFinding(
                check="repo_claim", severity=WARNING,
                detail=f"published doc not found for reconciliation: {p}"))
            continue
        is_changelog = p.name.lower().startswith("changelog")
        text = p.read_text(encoding="utf-8")
        if ver_re and not ver_re.search(text):
            findings.append(AuditFinding(
                check="repo_claim_headline", severity=WARNING,
                detail=f"{p.name}: does not mention the current runtime version v{version.lstrip('v')}"))
        for ln in text.splitlines():
            low = ln.lower()
            historical = bool(_PAST_VERSION_RE.search(ln)) or any(
                m in low for m in _HISTORICAL_MARKERS)
            # 1a. module count on a current-version line
            if (module_count is not None and ver_re and ver_re.search(ln)
                    and not historical):
                for m in _MODULES_RE.finditer(ln):
                    n = int(m.group(1))
                    if n != module_count:
                        findings.append(AuditFinding(
                            check="repo_claim_headline", severity=ERROR,
                            detail=(f"{p.name}: claims {n} modules on a current-version line "
                                    f"but canonical is {module_count}: {ln.strip()[:110]}")))
            # 1b. drift-gate sha anywhere
            if drift_sha is not None:
                for m in _SHA_RE.finditer(ln):
                    got = m.group(1).lower()
                    cs = drift_sha.lower()
                    if not (got.startswith(cs) or cs.startswith(got)):
                        findings.append(AuditFinding(
                            check="repo_claim_headline", severity=ERROR,
                            detail=(f"{p.name}: drift-gate sha {got[:12]} != canonical "
                                    f"{drift_sha}: {ln.strip()[:90]}")))
            # 2. id-anchored pending-status contradiction (README + ROADMAP only).
            # KA-RECON-PROXIMITY: require the PENDING word and the RESOLVED id to
            # co-occur in the SAME sentence, not merely the same line. The outer
            # line-level search is a cheap fast-path; the per-sentence loop is the
            # actual association test (see _SENTENCE_SPLIT_RE).
            if not is_changelog and not historical and _PENDING_CLAIM_RE.search(ln):
                flagged = False
                for seg in _SENTENCE_SPLIT_RE.split(ln):
                    if not _PENDING_CLAIM_RE.search(seg):
                        continue
                    for rid, rid_re in resolved_id_res:
                        if rid_re.search(seg):
                            findings.append(AuditFinding(
                                check="repo_claim_status", severity=ERROR,
                                detail=(f"{p.name}: claims RESOLVED record {rid} is still pending: "
                                        f"{seg.strip()[:110]}"),
                                item_id=rid))
                            flagged = True
                            break
                    if flagged:
                        break  # one finding per line is enough
    return findings


def canonical_facts() -> tuple[Optional[str], Optional[int], Optional[str]]:
    """Best-effort (version, module_count, drift_sha) from live kernel introspection.

    The Rule 11 reconciliation compares the docs against THESE — the runtime's own
    authoritative values: ``rag_kernel.__version__``, the count of registered
    capability modules (``_KERNEL_MODULES`` minus ``__main__``), and the source
    .tla SHA-256 embedded in ``generated_guards`` (12-char prefix, as the docs and
    ``guardgen --check`` print it). Any piece that cannot be read is returned as
    ``None`` and simply not reconciled.
    """
    version: Optional[str] = None
    module_count: Optional[int] = None
    drift_sha: Optional[str] = None
    try:
        import rag_kernel
        version = getattr(rag_kernel, "__version__", None)
        km = getattr(rag_kernel, "_KERNEL_MODULES", None) or []
        caps = [m for m in km if not str(m).endswith("__main__")]
        module_count = len(caps) or None
    except Exception:
        pass
    try:
        from rag_kernel import generated_guards
        s = getattr(generated_guards, "SOURCE_SHA256", None)
        if s:
            drift_sha = s[:12]
    except Exception:
        pass
    return version, module_count, drift_sha


# ---------------------------------------------------------------------------
# manifest version single-source binding (KA-5 / E-046)
# ---------------------------------------------------------------------------

def check_manifest_version_binding() -> list[AuditFinding]:
    """ERROR if the ``@rag-kernel-manifest`` version fields drift from the kernel's
    single-source authorities (KA-5 / E-046).

    The runtime version (``rag_kernel.__version__``) and the targeted INIT-spec
    version (``rag_kernel.__spec_version__``) are the SOLE source of truth. The
    manifest docstring deliberately carries NO ``version`` / ``spec_version``
    literal — :func:`rag_kernel.discover` injects them from the authorities — so a
    published manifest can no longer drift the way E-046 caught (the docstring copy
    frozen at 0.4.7 / spec 3.2.2 while the real values were 0.4.12 / spec 3.2.4).

    Pure introspection over the kernel package itself (no RAG input), so it is an
    always-on guard. Three fail-loud sub-checks:

    1. **authority present** — ``__version__`` / ``__spec_version__`` must exist and
       be non-empty strings (a missing authority IS the defect).
    2. **no re-introduced literal** — the raw docstring manifest must not hardcode a
       ``version`` / ``spec_version`` that disagrees with its authority (the
       single-source contract: the literal should be absent, and is certainly wrong
       if present and divergent).
    3. **injection matches** — ``discover()["package"]`` must report each field equal
       to its authority (the derive-from-authority contract).

    Self-skips entirely if ``rag_kernel`` cannot be imported.
    """
    findings: list[AuditFinding] = []
    try:
        import rag_kernel
    except Exception:
        return findings

    bindings = (
        ("version", "__version__", getattr(rag_kernel, "__version__", None)),
        ("spec_version", "__spec_version__", getattr(rag_kernel, "__spec_version__", None)),
    )

    # (1) authorities present + non-empty.
    for field_name, attr, value in bindings:
        if not isinstance(value, str) or not value.strip():
            findings.append(AuditFinding(
                check="manifest_version_binding", severity=ERROR,
                detail=(f"single-source authority rag_kernel.{attr} for manifest "
                        f"field {field_name!r} is missing or empty (KA-5/E-046)")))

    # (2) the raw docstring manifest must not carry a divergent hardcoded literal.
    try:
        raw = rag_kernel._extract_manifest(rag_kernel.__doc__) or {}
    except Exception:
        raw = {}
    for field_name, attr, value in bindings:
        if field_name in raw and value is not None and raw[field_name] != value:
            findings.append(AuditFinding(
                check="manifest_version_binding", severity=ERROR,
                detail=(f"@rag-kernel-manifest hardcodes {field_name}={raw[field_name]!r} "
                        f"but the authority rag_kernel.{attr} is {value!r} — remove the "
                        f"literal; discover() injects it (KA-5/E-046)")))

    # (3) discover()'s injected package manifest must equal the authorities.
    try:
        pkg = (rag_kernel.discover() or {}).get("package", {}) or {}
    except Exception:
        pkg = {}
    for field_name, attr, value in bindings:
        if value is not None and pkg.get(field_name) != value:
            findings.append(AuditFinding(
                check="manifest_version_binding", severity=ERROR,
                detail=(f"discover() package manifest {field_name}={pkg.get(field_name)!r} "
                        f"!= authority rag_kernel.{attr} {value!r} (KA-5/E-046)")))
    return findings


# ---------------------------------------------------------------------------
# current_status freshness (E-043)
# ---------------------------------------------------------------------------

def check_current_status_freshness(
    hot: dict,
    *,
    version: Optional[str] = None,
    git_head: Optional[str] = None,
) -> list[AuditFinding]:
    """ERROR if the ``current_status`` narrative's stated runtime version / git
    HEAD disagree with the live canonical authorities (E-043).

    Why this is a GUARD, not a render: ``current_status`` denormalizes two facts
    whose source of truth lives OUTSIDE the RAG — the kernel version
    (``rag_kernel.__version__``) and the published git HEAD. Those are not RAG
    state, so they cannot be rendered *from* the RAG the way ``open_tasks`` is
    rendered from ``tracked_items``. The stale-snapshot drift E-043 caught — a
    ``current_status`` frozen at an old session (S62) while the version and HEAD
    had moved on — is therefore detectable only by extracting the stated fact and
    asserting it still equals the live authority, failing loud on mismatch.

    Self-skipping (mirrors the other conditional checks): each sub-check runs only
    when BOTH the ``current_status`` field is present and parseable AND the
    canonical fact is supplied (``None`` = not reconciled). A deployed project
    with no ``current_status`` block, or one audited with no git context, is
    therefore clean rather than falsely flagged.
    """
    findings: list[AuditFinding] = []
    cs = hot.get("current_status") if isinstance(hot, dict) else None
    if not isinstance(cs, dict):
        return findings

    # 1. runtime version: the leading vX.Y.Z token of current_status.rag_kernel_version
    #    must equal the live rag_kernel.__version__.
    if version:
        raw = cs.get(_CS_VERSION_FIELD)
        if isinstance(raw, str):
            m = _CS_VERSION_TOKEN_RE.search(raw)
            want = version.lstrip("v")
            if m and m.group(1) != want:
                findings.append(AuditFinding(
                    check="current_status_freshness", severity=ERROR,
                    detail=(
                        f"current_status.{_CS_VERSION_FIELD} states version "
                        f"{m.group(1)} but live rag_kernel.__version__ is {want} "
                        "— stale snapshot (E-043); refresh current_status"
                    ),
                ))

    # 2. git HEAD: the "LATEST COMMIT <sha>" in current_status.github_repo must
    #    equal the live HEAD (prefix-compared, since one side is a short sha).
    if git_head:
        want_head = git_head.lower()
        for fld in _CS_HEAD_FIELDS:
            raw = cs.get(fld)
            if not isinstance(raw, str):
                continue
            m = _CS_HEAD_RE.search(raw)
            if m:
                got = m.group(1).lower()
                if not (got.startswith(want_head) or want_head.startswith(got)):
                    findings.append(AuditFinding(
                        check="current_status_freshness", severity=ERROR,
                        detail=(
                            f"current_status.{fld} states HEAD {got[:12]} but live "
                            f"git HEAD is {git_head} — stale snapshot (E-043); "
                            "refresh current_status"
                        ),
                    ))
            break  # only the first head-bearing field is the canonical pointer
    return findings


# ``_coerce_utc_date`` / ``_coerce_utc_instant`` moved to ``drift_store`` (the lower
# module) so the KA-2 dedup verb can share them with this auditor without an import
# cycle; they are re-imported above so this module's public surface is unchanged.


def check_current_status_coherence(hot: dict) -> list[AuditFinding]:
    """ERROR if ``current_status``'s denormalized self-facts contradict ``meta`` (KA-3).

    Where :func:`check_current_status_freshness` guards two facts whose authority
    lives OUTSIDE the RAG (the kernel ``__version__`` and the git HEAD), this guards
    two facts that ``current_status`` denormalizes from ``meta`` INSIDE the same
    RAG and must therefore equal exactly:

      * ``current_status.session`` == ``meta.written_by_session``
      * calendar day of ``current_status.last_updated`` == that of
        ``meta.last_updated_utc``

    The eBay Session-Zero deploy froze ``current_status.session`` at ``S0`` while
    the machine had moved on, and carried a ``current_status.last_updated`` (06-16)
    a full two days behind ``meta.last_updated_utc`` (06-18); ``audit --strict``
    still reported 0 findings because no invariant compared the two — the same
    governance blind spot the KA-10 arc exists to close. Each sub-check self-skips
    when either side is absent or unparseable (mirrors the other conditional
    checks), so a RAG whose ``current_status`` omits these keys — like this
    kernel's own — audits clean rather than being falsely flagged. The date is
    compared by calendar day (UTC), not by exact instant: ``current_status``
    records a day, ``meta`` a full timestamp, and only the day is the shared
    denormalized fact.
    """
    findings: list[AuditFinding] = []
    cs = hot.get("current_status") if isinstance(hot, dict) else None
    meta = hot.get("meta") if isinstance(hot, dict) else None
    if not isinstance(cs, dict) or not isinstance(meta, dict):
        return findings

    # 1. session identity: current_status.session must name the session that last
    #    wrote the RAG. Both sides must be present and non-empty to compare (an
    #    empty written_by_session is check_written_by_session's concern, not this).
    cs_session = cs.get("session")
    wbs = meta.get("written_by_session")
    if (isinstance(cs_session, str) and cs_session.strip()
            and isinstance(wbs, str) and wbs.strip()
            and cs_session.strip() != wbs.strip()):
        findings.append(AuditFinding(
            check="current_status_coherence", severity=ERROR,
            detail=(
                f"current_status.session = {cs_session.strip()!r} but "
                f"meta.written_by_session = {wbs.strip()!r} — current_status is "
                "frozen at a stale session (KA-3); refresh it at checkpoint"
            ),
        ))

    # 2. last-updated day: current_status.last_updated and meta.last_updated_utc
    #    must agree on the calendar day. Skip silently if either is unparseable.
    cs_day = _coerce_utc_date(cs.get("last_updated"))
    meta_day = _coerce_utc_date(meta.get("last_updated_utc"))
    if cs_day is not None and meta_day is not None and cs_day != meta_day:
        findings.append(AuditFinding(
            check="current_status_coherence", severity=ERROR,
            detail=(
                f"current_status.last_updated day {cs_day.isoformat()} != "
                f"meta.last_updated_utc day {meta_day.isoformat()} — stale "
                "current_status snapshot (KA-3); refresh it at checkpoint"
            ),
        ))
    return findings


# ---------------------------------------------------------------------------
# Integrity invariants (FIX-1 / K1+K2) — the family the auditor was blind to
# ---------------------------------------------------------------------------
#
# The eBay Session-Zero deploy produced a RAG on which ``audit --strict`` reported
# "0 findings" while it carried a broken WAL, a stale backup, unsubstituted
# placeholders, leaked template keys, a COLD pinned to the wrong spec version, an
# empty written_by_session and a machine-minted negative session id (audit report
# K1, K3–K7). An integrity product whose integrity check green-lights a defective
# artifact has no moat, so each defect below becomes a deterministic, fail-loud
# ERROR — the same fail-closed family as the E-040 render-parity check. Every check
# SELF-SKIPS when its source is absent (no WAL file, no COLD, no meta, a BOOTING
# pre-checkpoint RAG), so a healthy or not-yet-populated deployment audits clean
# rather than being falsely flagged.

def _load_json_bom(path: Path) -> dict:
    """Load a JSON object tolerant of a UTF-8 BOM (``load_hot`` is not).

    The COLD / .bak siblings can carry a BOM (the production COLD does). A BOM is
    benign and must not cause the integrity check to silently self-skip, so these
    file-reading checks decode with ``utf-8-sig`` rather than the strict
    ``load_hot``. Raises on genuinely malformed JSON.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"root must be a JSON object, got {type(data).__name__}")
    return data


def _walk_strings(node, path=()):
    """Yield (dotted-path, str-value) for every string leaf in a JSON value."""
    if isinstance(node, str):
        yield "/".join(path) or "<root>", node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from _walk_strings(v, path + (str(k),))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk_strings(v, path + (str(i),))


def check_placeholder_tokens(hot: dict) -> list[AuditFinding]:
    """ERROR for any value that is an unsubstituted ``<PLACEHOLDER>`` token (K3).

    Whole-string match only: a field literally holding ``"<ISO>"`` (the eBay
    ``created_utc`` / ``sessions_recent[].d`` defect) is caught, while rule prose
    that merely *mentions* a template token (``S<NN>``) is not flagged.
    """
    findings: list[AuditFinding] = []
    if not isinstance(hot, dict):
        return findings
    for loc, val in _walk_strings(hot):
        if _PLACEHOLDER_VALUE_RE.match(val.strip()):
            findings.append(AuditFinding(
                check="placeholder_tokens", severity=ERROR,
                detail=(f"unsubstituted placeholder {val.strip()!r} at {loc} — "
                        "init/configure must substitute real values at build time")))
    return findings


def check_project_context_placeholders(hot: dict) -> list[AuditFinding]:
    """ERROR for any residual human-fill ``<...>`` placeholder in project_context (KA-9).

    The universal spec ships ``project_context`` (brief / domain / end_goal /
    principals) with session-zero ``<from user>`` / ``<absolute path>`` tokens
    that the LLM must substitute at deploy. They are lowercase/spaced, so the
    UPPER_SNAKE :func:`check_placeholder_tokens` scan never caught them — the
    eBay Session-Zero deploy shipped ``brief``/``domain``/``end_goal`` as a
    verbatim ``"<from user>"`` and audited clean. This scan walks the whole
    ``project_context`` subtree and fails loud on every surviving human-fill
    placeholder (substring match, so a partially-filled value is caught too).
    Self-skips when ``project_context`` is absent. Pure UPPER_SNAKE tokens are
    deliberately left to :func:`check_placeholder_tokens` (no double-report).
    """
    findings: list[AuditFinding] = []
    pc = hot.get("project_context") if isinstance(hot, dict) else None
    if not isinstance(pc, (dict, list)):
        return findings
    seen: set[tuple[str, str]] = set()
    for loc, val in _walk_strings(pc):
        for tok in _HUMAN_PLACEHOLDER_RE.findall(val):
            if not _HUMAN_PLACEHOLDER_SIGNAL_RE.search(tok):
                continue  # pure UPPER_SNAKE token — owned by check_placeholder_tokens
            key = (loc, tok)
            if key in seen:
                continue
            seen.add(key)
            findings.append(AuditFinding(
                check="project_context_placeholders", severity=ERROR,
                detail=(f"unfilled placeholder {tok!r} at project_context/{loc} — "
                        "init/configure must substitute a real value at session zero")))
    return findings


def check_template_keys(hot: dict) -> list[AuditFinding]:
    """ERROR for any ``_``-prefixed template key leaked into operating_protocol (K5).

    The §32 ``:template`` scaffold carries ``_required`` / ``_note`` placeholder
    keys; they must be stripped at configure/commit and never appear in live
    governance, where they only dilute the rule set the LLM ingests each boot.
    """
    findings: list[AuditFinding] = []
    op = hot.get("operating_protocol") if isinstance(hot, dict) else None
    if isinstance(op, dict):
        for k in op:
            if isinstance(k, str) and k.startswith("_"):
                findings.append(AuditFinding(
                    check="template_keys", severity=ERROR,
                    detail=(f"template/placeholder key {k!r} leaked into "
                            "operating_protocol — strip _-prefixed keys at configure/commit")))
    return findings


def check_written_by_session(hot: dict) -> list[AuditFinding]:
    """ERROR if a checkpointed RAG has an empty ``meta.written_by_session`` (K7).

    Self-skips a freshly init'd ``BOOTING`` RAG (not yet checkpointed, so not yet
    stamped). Once the machine has left BOOTING, every checkpoint must stamp the
    session id — an empty field breaks the session lineage the RAG depends on.
    """
    findings: list[AuditFinding] = []
    meta = hot.get("meta") if isinstance(hot, dict) else None
    if not isinstance(meta, dict) or "written_by_session" not in meta:
        return findings
    if hot.get("state_machine_status") == _PRE_CHECKPOINT_STATE:
        return findings
    wbs = meta.get("written_by_session")
    if not isinstance(wbs, str) or not wbs.strip():
        findings.append(AuditFinding(
            check="written_by_session", severity=ERROR,
            detail=("meta.written_by_session is empty on a checkpointed RAG — every "
                    "checkpoint must stamp the canonical session id")))
    return findings


def check_session_id_coherence(hot: dict) -> list[AuditFinding]:
    """ERROR for a malformed/machine-minted session id (K7).

    A canonical id is ``S`` + a non-negative integer. The eBay deploy minted a
    negative-looking ``S-12488-1781260490`` (a signed PID/hash bug); that — and any
    ``S-<digit>`` shape — is flagged in ``meta.written_by_session`` and every
    ``sessions_recent[].id``. Plausible positive ids are left alone (no over-fit).
    """
    findings: list[AuditFinding] = []
    if not isinstance(hot, dict):
        return findings
    candidates: list[tuple[str, str]] = []
    meta = hot.get("meta")
    if isinstance(meta, dict):
        wbs = meta.get("written_by_session")
        if isinstance(wbs, str) and wbs.strip():
            candidates.append(("meta.written_by_session", wbs.strip()))
    sr = hot.get("sessions_recent")
    if isinstance(sr, list):
        for i, e in enumerate(sr):
            if isinstance(e, dict) and isinstance(e.get("id"), str):
                candidates.append((f"sessions_recent[{i}].id", e["id"].strip()))
    for loc, sid in candidates:
        if _BAD_SESSION_ID_RE.match(sid):
            findings.append(AuditFinding(
                check="session_id_coherence", severity=ERROR,
                detail=(f"{loc} = {sid!r} is a malformed/negative session id "
                        "(expected S<non-negative-int>) — fix the id generator")))
    return findings


def check_sessions_recent_coherence(hot: dict) -> list[AuditFinding]:
    """ERROR on duplicate-bootstrap rows in the ``sessions_recent`` ledger (KA-2).

    ``sessions_recent`` is the ledger of per-session checkpoints. The eBay
    Session-Zero deploy carried duplicate *bootstrap* rows — S0 and S1 minted at the
    SAME instant, one of which was never actually run — while ``audit --strict``
    reported 0 findings, and there was no governed way to repair them. The
    fail-loud invariant that closes that blind spot:

      * **no duplicate-bootstrap rows** — no two rows may share a checkpoint
        timestamp ``d``. Distinct sessions close at distinct instants; a shared
        timestamp means a row was minted as a phantom duplicate of another, not
        checkpointed on its own. Two rows carrying the same *unparseable* literal
        ``d`` count too (the eBay rows shared a ``<ISO>``-class placeholder).

    Deliberately NOT enforced — and why: a fixed array ORDER, strictly-unique ids,
    or directional timestamp/id monotonicity. The project legitimately writes
    ``sessions_recent`` in BOTH directions — this kernel's live RAG is oldest-first
    (S92…S95) while a fresh ``init --auto-ready`` RAG is newest-first (S1 then S0) —
    and one session legitimately spans multiple rows (this kernel's S95/S95
    multi-checkpoint pair, distinct timestamps). A SHARED timestamp is the one
    phantom-duplicate signal that is unambiguous across every legitimate shape;
    anything order- or id-directional would false-positive on a clean deploy.
    Self-skips when ``sessions_recent`` is absent / not a list / has fewer than two
    rows, and skips any row whose ``d`` is missing (a ``<PLACEHOLDER>`` ``d`` is
    :func:`check_placeholder_tokens`'s concern), so a healthy or not-yet-populated
    RAG — like this kernel's own — audits clean.
    """
    findings: list[AuditFinding] = []
    if not isinstance(hot, dict):
        return findings

    # Single source of detection: the duplicate-finding predicate lives in
    # drift_store and is shared with the KA-2 dedup *repair* verb, so a row this
    # auditor flags is exactly a row the verb removes (no detect/repair drift).
    # Compare on the parsed UTC instant when ``d`` is parseable (a Z-suffixed instant
    # and its offset twin collide); fall back to the exact literal for an unparseable
    # ``d`` so two identical placeholder timestamps are caught.
    for prior, i, kind, literal in sessions_recent_duplicate_pairs(hot.get("sessions_recent")):
        if kind == "instant":
            findings.append(AuditFinding(
                check="sessions_recent_coherence", severity=ERROR,
                detail=(f"sessions_recent[{prior}] and [{i}] share checkpoint "
                        f"timestamp {literal!r} — duplicate-bootstrap rows (KA-2); two "
                        "real checkpoints never land at the same instant. Repair "
                        "with the governed sessions_recent dedup verb")))
        else:  # identical unparseable literal d is still a duplicate
            findings.append(AuditFinding(
                check="sessions_recent_coherence", severity=ERROR,
                detail=(f"sessions_recent[{prior}] and [{i}] share timestamp "
                        f"literal {literal!r} — duplicate-bootstrap rows (KA-2)")))

    return findings


def _session_id_int(sid: object) -> Optional[int]:
    """Numeric ordinal of a canonical ``S<int>`` session id, else ``None``.

    ``"S98" -> 98``; ``"S0" -> 0``; a malformed/negative ``"S-12488-…"`` or any
    non-``S<int>`` value -> ``None`` (left to :func:`check_session_id_coherence`).
    """
    if not isinstance(sid, str):
        return None
    m = _SESSION_ID_INT_RE.match(sid.strip())
    return int(m.group(1)) if m else None


def _session_log_completed(path: Path) -> bool:
    """True iff a session log file contains a ``session_end`` marker entry.

    A ``session_end`` is written only by ``SessionLogger.close(emit_end=True)``,
    so its presence means the session ran to a CLEAN close — distinguishing a
    finished session from a still-open / detached (``emit_end=False``) / crashed
    one. Replay-tolerant: malformed lines are skipped, not treated as defects.
    """
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return False
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and entry.get("event") == _SESSION_END_EVENT:
            return True
    return False


def _session_log_has_entries(path: Path) -> bool:
    """True iff a session log file holds at least one parseable JSON entry.

    An absent, empty, whitespace-only, or wholly-unparseable file is NOT a trail.
    Replay-tolerant: malformed lines are skipped; a single good entry suffices.
    Distinct from :func:`_session_log_completed` (which requires a ``session_end``
    close marker) — here ANY entry proves the logger ran for that session.
    """
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return False
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            return True
    return False


def check_uncheckpointed_session(rag_dir: Path | str, hot: dict) -> list[AuditFinding]:
    """ERROR when a *completed* session ran but was never checkpointed (KA-1).

    The eBay governance freeze (S88 headline: "deployed auditor passed clean
    while governance frozen at S0/seq1") happened because an agent ended sessions
    on ``configure``/``audit`` (or a scratch script) without ever running
    ``checkpoint`` — so ``meta.written_by_session`` stayed behind while later
    sessions ran. The KA-4 close gate (:func:`_session_checkpoint_gate`) stops the
    LIVE session from closing un-checkpointed, but the AUDITOR itself never
    asserted it — the exact blind spot that let an already-frozen RAG report
    clean. This is that missing fail-loud invariant.

    Signal — a session log beside the RAG (``session_log_<sid>.jsonl``) that both
    (a) carries a ``session_end`` marker (ran to a clean close), and (b) has a
    numeric session ordinal GREATER than ``meta.written_by_session``'s ordinal. A
    clean close force-checkpoints (ENH-006) or is gated (KA-4), so on a healthy
    RAG the newest *completed* log IS ``written_by_session`` (equal, never
    greater) and the check is silent; only a session that closed without advancing
    the checkpoint — the freeze signature — sits newer than the last checkpoint.

    Deliberately NOT flagged, and why: a still-open / detached / crashed log (no
    ``session_end``) — that is the in-flight CURRENT session, legitimately newer
    than the last checkpoint until it closes, so keying on ``session_end`` avoids
    false-positiving the running session; and any log with an ordinal
    ``<= written_by_session`` (a historical session that did checkpoint, whose log
    persists). Self-skips on a pre-checkpoint ``BOOTING`` RAG, when
    ``written_by_session`` is missing / empty / not a canonical ``S<int>``
    (:func:`check_written_by_session` and :func:`check_session_id_coherence` own
    those), and when the RAG directory holds no session logs.
    """
    findings: list[AuditFinding] = []
    if not isinstance(hot, dict):
        return findings
    if hot.get("state_machine_status") == _PRE_CHECKPOINT_STATE:
        return findings
    meta = hot.get("meta")
    if not isinstance(meta, dict):
        return findings
    cp_ord = _session_id_int(meta.get("written_by_session"))
    if cp_ord is None:
        return findings
    d = Path(rag_dir)
    if not d.is_dir():
        return findings
    for log_path in sorted(d.glob(_SESSION_LOG_GLOB)):
        sid_ord = _session_id_int(log_path.stem[len(_SESSION_LOG_PREFIX):])
        if sid_ord is None or sid_ord <= cp_ord:
            continue
        if not _session_log_completed(log_path):
            continue  # still-open / detached / crashed: the in-flight session
        findings.append(AuditFinding(
            check="uncheckpointed_session", severity=ERROR,
            detail=(f"{log_path.name} ran to a clean close (session_end) as "
                    f"session S{sid_ord}, but the last checkpoint is frozen at "
                    f"meta.written_by_session={meta.get('written_by_session')!r} "
                    f"(S{cp_ord}) — ran-but-never-checkpointed (KA-1); a clean "
                    "close must checkpoint first (the eBay S0/seq1 freeze)")))
    return findings


def check_observability_coherence(rag_dir: Path | str, hot: dict) -> list[AuditFinding]:
    """ERROR when meta advanced past the newest session-log trail (KA-7).

    The eBay governance freeze had a second silent signature beside KA-1: the
    per-session logs stopped at S1 while ``meta.written_by_session`` kept advancing
    across later sessions — sessions ran and checkpointed but opened no logger, so
    no ``session_log_S<N>.jsonl`` trail was ever written, and ``audit --strict``
    still reported 0 findings. KA-1 catches a COMPLETED log NEWER than the
    checkpoint (ran-but-never-checkpointed); KA-7 is its dual — the checkpoint
    advancing NEWER than the last observability trail.

    Signal — among the session logs beside the RAG that hold at least one entry,
    the greatest ordinal is ``max_logged``; the check fires iff the checkpoint
    ordinal ``meta.written_by_session`` is STRICTLY GREATER than ``max_logged``
    (meta advanced beyond where logging stopped). Because KA-1 fires only when a log
    is newer than the checkpoint and KA-7 only when the checkpoint is newer than
    every log, the two are mutually exclusive and never double-report.

    Deliberately NOT keyed on ERROR_LOG.md: a clean session legitimately records no
    error, so a missing ERROR_LOG entry is not a defect — the session log is the
    mandatory per-session artifact (opened at session-start). Self-skips on a
    pre-checkpoint ``BOOTING`` RAG, when ``written_by_session`` is missing / empty /
    not a canonical ``S<int>`` (:func:`check_written_by_session` /
    :func:`check_session_id_coherence` own those), when the RAG directory is absent,
    and — crucially — when NO session log carries any entry (a project not using the
    logger, or a pure in-memory unit fixture: nothing to be coherent WITH).
    """
    findings: list[AuditFinding] = []
    if not isinstance(hot, dict):
        return findings
    if hot.get("state_machine_status") == _PRE_CHECKPOINT_STATE:
        return findings
    meta = hot.get("meta")
    if not isinstance(meta, dict):
        return findings
    cp_ord = _session_id_int(meta.get("written_by_session"))
    if cp_ord is None:
        return findings
    d = Path(rag_dir)
    if not d.is_dir():
        return findings
    logged: list[int] = []
    for log_path in d.glob(_SESSION_LOG_GLOB):
        o = _session_id_int(log_path.stem[len(_SESSION_LOG_PREFIX):])
        if o is not None and _session_log_has_entries(log_path):
            logged.append(o)
    if not logged:
        return findings  # no logger in use here: nothing to be coherent with
    max_logged = max(logged)
    if cp_ord <= max_logged:
        return findings  # trail kept pace (equal) or a newer log exists (KA-1's lane)
    findings.append(AuditFinding(
        check="observability_coherence", severity=ERROR,
        detail=(f"meta.written_by_session={meta.get('written_by_session')!r} "
                f"(S{cp_ord}) advanced past the newest session-log trail "
                f"(session_log_S{max_logged}.jsonl) — meta advanced but "
                "observability did not (KA-7); the eBay logs stopped at S1 while "
                "meta kept advancing, and audit still reported clean")))
    return findings


def check_wal_integrity(wal_path: Path | str) -> list[AuditFinding]:
    """ERROR if the WAL sequence is not strictly monotonic by +1 (K1).

    Replays the append-only ``WAL.jsonl`` and asserts ``seq[n+1] == seq[n] + 1``:
    a duplicate seq, a gap, or a decrease all violate the core WAL contract (the
    eBay WAL had two ``seq:3`` and no ``seq:4``). Self-skips when no WAL exists.
    Malformed lines are skipped (replay tolerance), not treated as a seq defect.
    """
    findings: list[AuditFinding] = []
    p = Path(wal_path)
    if not p.exists():
        return findings
    seqs: list[int] = []
    try:
        text = p.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return findings
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        s = d.get("seq")
        if isinstance(s, int):
            seqs.append(s)
    prev: Optional[int] = None
    for s in seqs:
        if prev is not None and s != prev + 1:
            if s == prev:
                kind = f"duplicate seq {s}"
            elif s < prev:
                kind = f"decreasing seq {prev} -> {s}"
            else:
                kind = f"gap {prev} -> {s} (skipped {prev + 1})"
            findings.append(AuditFinding(
                check="wal_integrity", severity=ERROR,
                detail=(f"WAL {p.name} is not strictly monotonic: {kind} — "
                        "a WAL must increment by exactly 1 (single seq allocator)")))
        prev = s
    return findings


def check_bak_parity(rag_path: Path | str, hot: dict) -> list[AuditFinding]:
    """ERROR if the ``.bak`` is not a BYTE-PARITY mirror of HOT (K6, FIX-4).

    FIX-4 settled the ``.bak`` contract as **parity-mirror** (operator decision):
    after a clean checkpoint / session close the ``.bak`` must be a byte-identical
    copy of HOT, so recovery restores the *exact* known-good state the
    ``recovery_protocol`` ("attempt .bak first") promises. The earlier FIX-1
    allowance for a rollback-prior (one-seq-behind) ``.bak`` is removed — that was
    the rollback-prev contract the operator rejected, and a backup whose bytes
    differ from HOT is the eBay K6 defect (HOT seq 3, ``.bak`` seq 0, different
    md5: a backup that cannot actually restore). The enforce half lives in the
    canonical writers (full checkpoint/close, drift_store, drift_render) which
    refresh ``.bak`` to parity via ``atomic_write_json(..., mirror_bak=True)``.

    Self-skips when no ``.bak`` exists. A ``.bak`` that fails to parse is flagged
    (a corrupt mirror is worse than none).
    """
    findings: list[AuditFinding] = []
    p = Path(rag_path)
    bak = p.with_suffix(p.suffix + ".bak")
    if not bak.exists():
        return findings
    # A backup that no longer parses cannot restore — flag it loud.
    try:
        bak_hot = _load_json_bom(bak)
    except Exception as exc:  # noqa: BLE001 — any load failure is a broken backup
        findings.append(AuditFinding(
            check="bak_parity", severity=ERROR,
            detail=f"{bak.name} does not parse as a valid RAG backup: {exc}"))
        return findings
    # Parity is byte-equality between the on-disk HOT and its .bak mirror.
    try:
        bak_bytes = bak.read_bytes()
        hot_bytes = p.read_bytes()
    except OSError as exc:
        findings.append(AuditFinding(
            check="bak_parity", severity=ERROR,
            detail=f"could not read {p.name}/{bak.name} for parity comparison: {exc}"))
        return findings
    if bak_bytes != hot_bytes:
        hot_sha = hashlib.sha256(hot_bytes).hexdigest()[:12]
        bak_sha = hashlib.sha256(bak_bytes).hexdigest()[:12]
        hot_seq = (hot.get("meta") or {}).get("last_checkpoint_seq")
        bak_seq = (bak_hot.get("meta") or {}).get("last_checkpoint_seq")
        findings.append(AuditFinding(
            check="bak_parity", severity=ERROR,
            detail=(f"{bak.name} is not a byte-parity mirror of {p.name} "
                    f"(HOT sha {hot_sha} seq {hot_seq} vs .bak sha {bak_sha} "
                    f"seq {bak_seq}) — stale/broken backup, parity-mirror contract "
                    "(FIX-4/K6)")))
    return findings


def check_cold_hot_version(cold_path: Path | str, hot: dict) -> list[AuditFinding]:
    """ERROR if COLD's init-prompt version disagrees with the HOT spec version (K4).

    ``RAG_COLD.json.init_prompt_reference`` records the spec the COLD scaffold was
    built from; it must equal the spec the HOT RAG runs (``meta.rag_files.init_prompt``
    filename version, falling back to ``meta.policy_version``). The eBay COLD pinned
    v3.1.9 while the deploy ran v3.2.2. Self-skips when COLD is absent/unparseable or
    either version token cannot be read.
    """
    findings: list[AuditFinding] = []
    p = Path(cold_path)
    if not p.exists():
        return findings
    try:
        cold = _load_json_bom(p)
    except Exception:  # noqa: BLE001 — a malformed COLD is out of scope for THIS check
        return findings

    cold_ver = None
    ipr = cold.get("init_prompt_reference") if isinstance(cold, dict) else None
    if isinstance(ipr, dict):
        raw = ipr.get("version") or ipr.get("filename")
        if isinstance(raw, str):
            m = _SEMVER_RE.search(raw)
            cold_ver = m.group(1) if m else None

    hot_ver = None
    meta = hot.get("meta") if isinstance(hot, dict) else None
    if isinstance(meta, dict):
        ip = (meta.get("rag_files") or {}).get("init_prompt")
        if isinstance(ip, str):
            m = _SEMVER_RE.search(ip)
            hot_ver = m.group(1) if m else None
        if hot_ver is None and isinstance(meta.get("policy_version"), str):
            m = _SEMVER_RE.search(meta["policy_version"])
            hot_ver = m.group(1) if m else None

    if cold_ver and hot_ver and cold_ver != hot_ver:
        findings.append(AuditFinding(
            check="cold_hot_version", severity=ERROR,
            detail=(f"COLD init_prompt_reference version {cold_ver} != HOT spec "
                    f"version {hot_ver} — rebuild/refresh COLD to the live spec")))
    return findings


# ---------------------------------------------------------------------------
# Aggregate runners
# ---------------------------------------------------------------------------

def _as_store(source) -> TrackedItemStore:
    if isinstance(source, TrackedItemStore):
        return source
    if isinstance(source, dict):
        return TrackedItemStore.from_hot(source)
    return TrackedItemStore(source)


def audit_hot(
    hot: dict,
    *,
    root: Optional[Path | str] = None,
    error_log_path: Optional[Path | str] = None,
    doc_paths=None,
    version: Optional[str] = None,
    module_count: Optional[int] = None,
    drift_sha: Optional[str] = None,
    git_head: Optional[str] = None,
) -> AuditReport:
    """Run every check over a loaded HOT dict.

    Always-on (pure over the in-memory state): render parity, supersede refs,
    note/status contradiction, ledger consistency, and record coverage of the
    inference_ledger. Conditional: the current_status freshness guard (E-043)
    asserts whichever of ``version`` / ``git_head`` is supplied; the ERROR_LOG
    coverage scan runs when ``error_log_path`` is given; the Rule 11 published-doc
    reconciliation runs when ``doc_paths`` is given; the Rule 13 side-store scan
    runs when ``root`` is given (no path = no scan).
    """
    findings: list[AuditFinding] = []
    findings += check_render_parity(hot)
    store = TrackedItemStore.from_hot(hot)
    findings += check_supersede_refs(store)
    findings += check_note_status_contradiction(store)
    findings += check_ledger_consistency(hot)
    findings += check_record_coverage(hot, error_log_path=error_log_path)
    findings += check_current_status_freshness(hot, version=version, git_head=git_head)
    findings += check_current_status_coherence(hot)
    # KA-5/E-046: single-source manifest version binding — pure introspection over
    # the kernel package (no hot input), always-on.
    findings += check_manifest_version_binding()
    # FIX-1 integrity invariants over the in-memory state (each self-skips when its
    # source is absent): unsubstituted placeholders, leaked template keys, empty
    # written_by_session, malformed session ids.
    findings += check_placeholder_tokens(hot)
    findings += check_project_context_placeholders(hot)
    findings += check_template_keys(hot)
    findings += check_written_by_session(hot)
    findings += check_session_id_coherence(hot)
    findings += check_sessions_recent_coherence(hot)
    if doc_paths:
        findings += check_repo_claim_reconciliation(
            doc_paths, store,
            version=version, module_count=module_count, drift_sha=drift_sha)
    if root is not None:
        findings += check_side_rule_stores(root)
    return AuditReport(tuple(findings))


def reconciliation_surfaces(
    hot: dict, docs_root: Path | str
) -> list[Path]:
    """KA-11: resolve the file-based reconciliation surfaces for a project.

    Returns absolute paths (under ``docs_root``) for the published docs the Rule 11
    reconciliation must read. The list comes from the per-project manifest at
    ``meta.reconciliation_surfaces`` (TierC: each project declares WHICH surfaces
    are drift-prone — kernel = README / CHANGELOG / ROADMAP); when that manifest is
    absent, empty, or malformed, the universal :data:`_DEFAULT_RECONCILIATION_SURFACES`
    apply. This is what replaces the formerly hardcoded ``doc_paths`` so the auditor
    is no longer kernel-repo-specific while staying byte-for-byte back-compatible for
    every RAG that has not (yet) declared a manifest.

    Surfaces are joined relative to ``docs_root`` (the git worktree where published
    docs live), mirroring the historical hardcoded behaviour.
    """
    dr = Path(docs_root)
    meta = hot.get("meta") or {}
    declared = meta.get("reconciliation_surfaces")
    if not isinstance(declared, (list, tuple)) or not declared:
        surfaces: tuple[str, ...] | list = _DEFAULT_RECONCILIATION_SURFACES
    else:
        # keep only non-empty string entries; an all-bad manifest falls back too
        surfaces = [s for s in declared if isinstance(s, str) and s.strip()]
        if not surfaces:
            surfaces = _DEFAULT_RECONCILIATION_SURFACES
    return [dr / s for s in surfaces]


def audit_file(
    path: Path | str,
    *,
    root: Optional[Path | str] = None,
    scan_root: bool = True,
    error_log_path: Optional[Path | str] = None,
    docs_root: Optional[Path | str] = None,
    git_head: Optional[str] = None,
) -> AuditReport:
    """Load a RAG file and audit it. Fail loud on bad JSON (DriftStoreError).

    ``root`` defaults to the file's grandparent (``RAG/RAG_MASTER.json`` ->
    project root) so the side-store scan covers the project root by default; pass
    ``scan_root=False`` to skip it, or an explicit ``root`` to override.

    ``error_log_path`` defaults to ``ERROR_LOG.md`` beside the RAG file (so E-###
    coverage is checked by default). The Rule 11 published-doc reconciliation runs
    only when ``docs_root`` is given (the published docs live in the git worktree,
    not next to the RAG): it reconciles the surfaces resolved by
    :func:`reconciliation_surfaces` (the per-project ``meta.reconciliation_surfaces``
    manifest, defaulting to ``README.md`` / ``CHANGELOG.md`` / ``docs/ROADMAP.md``)
    under ``docs_root`` against the live canonical facts.
    """
    p = Path(path)
    hot = load_hot(p)
    use_root: Optional[Path | str]
    if not scan_root:
        use_root = None
    elif root is not None:
        use_root = root
    else:
        use_root = p.parent.parent  # RAG/RAG_MASTER.json -> project root

    elp = error_log_path
    if elp is None:
        elp = p.parent / "ERROR_LOG.md"  # RAG/ERROR_LOG.md beside the RAG file

    doc_paths = None
    # canonical_facts() is cheap (pure introspection) and the current_status
    # freshness guard (E-043) needs the live version regardless of docs_root, so
    # compute it unconditionally; module_count / drift_sha are consumed only by
    # the Rule 11 doc reconciliation (and ignored by audit_hot without doc_paths).
    version, module_count, drift_sha = canonical_facts()
    if docs_root is not None:
        # KA-11: surfaces come from the per-project manifest (meta.reconciliation_
        # surfaces), falling back to the universal defaults — no longer hardcoded.
        doc_paths = reconciliation_surfaces(hot, docs_root)

    report = audit_hot(
        hot, root=use_root, error_log_path=elp, doc_paths=doc_paths,
        version=version, module_count=module_count, drift_sha=drift_sha,
        git_head=git_head)

    # FIX-1 file-based integrity invariants over the sibling files beside
    # RAG_MASTER.json (names overridable via meta.rag_files). Each self-skips when
    # its file is absent, so a project with no WAL/COLD still audits clean.
    rag_files = (hot.get("meta") or {}).get("rag_files") or {}
    wal_name = rag_files.get("wal") or _DEFAULT_WAL_NAME
    cold_name = rag_files.get("cold") or _DEFAULT_COLD_NAME
    extra: list[AuditFinding] = []
    extra += check_wal_integrity(p.parent / wal_name)
    extra += check_bak_parity(p, hot)
    extra += check_cold_hot_version(p.parent / cold_name, hot)
    # KA-1: ran-but-never-checkpointed — a completed session log (RAG/session_log_
    # <sid>.jsonl) newer than meta.written_by_session is the governance-freeze
    # signature the auditor previously missed. RAG dir = p.parent; self-skips clean.
    extra += check_uncheckpointed_session(p.parent, hot)
    # KA-7: observability-coherence — the dual of KA-1. If meta.written_by_session
    # advanced PAST the newest session log that holds entries, the per-session trail
    # stopped while the checkpoint kept moving (the eBay logs-stopped-at-S1 freeze).
    extra += check_observability_coherence(p.parent, hot)
    # FIX-5/P2: stray *_context.json beside the RAG (RAG dir = p.parent). Part of
    # the Rule 13 side-store family, so gated by the same scan_root toggle.
    if use_root is not None:
        extra += check_context_side_stores(p.parent)
    return AuditReport(report.findings + tuple(extra))


def assert_clean(report: AuditReport, *, strict: bool = False) -> None:
    """Raise :class:`DriftAuditError` if ``report`` is not clean.

    Always raises on ERROR findings; with ``strict=True`` also raises on warnings.
    The message lists every offending finding so the caller sees exactly what
    diverged and where.
    """
    if report.is_clean(strict=strict):
        return
    offending = report.findings if strict else report.errors
    raise DriftAuditError(
        "session audit failed:\n" + "\n".join(f"  - {f}" for f in offending)
    )


__all__ = [
    "AuditFinding",
    "AuditReport",
    "DriftAuditError",
    "ERROR",
    "WARNING",
    "DRIFT_AUDIT_VERSION",
    "check_render_parity",
    "check_supersede_refs",
    "check_note_status_contradiction",
    "check_side_rule_stores",
    "check_context_side_stores",
    "check_ledger_consistency",
    "check_record_coverage",
    "check_repo_claim_reconciliation",
    "reconciliation_surfaces",
    "check_current_status_freshness",
    "check_current_status_coherence",
    "check_placeholder_tokens",
    "check_project_context_placeholders",
    "check_template_keys",
    "check_written_by_session",
    "check_session_id_coherence",
    "check_sessions_recent_coherence",
    "check_uncheckpointed_session",
    "check_observability_coherence",
    "check_wal_integrity",
    "check_bak_parity",
    "check_cold_hot_version",
    "canonical_facts",
    "audit_hot",
    "audit_file",
    "assert_clean",
]
