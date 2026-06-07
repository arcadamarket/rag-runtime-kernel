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
              "check_repo_claim_reconciliation", "canonical_facts",
              "audit_hot", "audit_file", "assert_clean"],
  "use_when": "Verifying at a session boundary that every status render still matches the canonical tracked_items array and no parallel state store exists",
  "never_bypass": true
}
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from rag_kernel.drift_control import ItemKind, ItemStatus, TERMINAL_STATUSES
from rag_kernel.drift_store import (
    INFERENCE_LEDGER_KEY,
    TRACKED_ITEMS_KEY,
    DriftStoreError,
    TrackedItemStore,
    ledger_disposition_to_status,
    load_hot,
)
from rag_kernel import drift_render

# Bump when a check's contract or the report shape changes in a way a test pins.
DRIFT_AUDIT_VERSION = "1.0.0"

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

# Forbidden parallel rule/state stores (Cowork memory + side MDs), Rule 13 / E-039.
# Exact name + two glob patterns; scanned inside the project root ONLY.
_FORBIDDEN_STORE_NAMES: frozenset[str] = frozenset({"MEMORY.md"})
_FORBIDDEN_STORE_GLOBS: tuple[str, ...] = ("feedback_*.md", "project_*.md")
# Directories never scanned (VCS internals / build caches).
_SKIP_DIRS: frozenset[str] = frozenset({".git", "__pycache__", ".pytest_cache"})

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
    if not root_path.exists():
        return findings
    for p in sorted(root_path.rglob("*")):
        if not p.is_file():
            continue
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        name = p.name
        hit = name in _FORBIDDEN_STORE_NAMES or any(
            p.match(g) for g in _FORBIDDEN_STORE_GLOBS
        )
        if hit:
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
    inf_ids = {it.id for it in store if it.kind == ItemKind.INFERENCE}
    err_ids = {it.id for it in store if it.kind == ItemKind.ERROR}

    # Pre-cutover gate (per kind): coverage is enforced only once that record kind
    # has been migrated (any item of the kind exists). Before the deliberate
    # increment-6 cutover the legacy stores remain authoritative, so an empty
    # canonical set is the correct pre-migration state, not a coverage gap.
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
                          key=len, reverse=True)  # longest-first: avoid id-substring overlap
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
            # 2. id-anchored pending-status contradiction (README + ROADMAP only)
            if not is_changelog and not historical and _PENDING_CLAIM_RE.search(ln):
                for rid in resolved_ids:
                    if rid in ln:
                        findings.append(AuditFinding(
                            check="repo_claim_status", severity=ERROR,
                            detail=(f"{p.name}: claims RESOLVED record {rid} is still pending: "
                                    f"{ln.strip()[:110]}"),
                            item_id=rid))
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
) -> AuditReport:
    """Run every check over a loaded HOT dict.

    Always-on (pure over the in-memory state): render parity, supersede refs,
    note/status contradiction, ledger consistency, and record coverage of the
    inference_ledger. Conditional: the ERROR_LOG coverage scan runs when
    ``error_log_path`` is given; the Rule 11 published-doc reconciliation runs
    when ``doc_paths`` is given; the Rule 13 side-store scan runs when ``root``
    is given (no path = no scan).
    """
    findings: list[AuditFinding] = []
    findings += check_render_parity(hot)
    store = TrackedItemStore.from_hot(hot)
    findings += check_supersede_refs(store)
    findings += check_note_status_contradiction(store)
    findings += check_ledger_consistency(hot)
    findings += check_record_coverage(hot, error_log_path=error_log_path)
    if doc_paths:
        findings += check_repo_claim_reconciliation(
            doc_paths, store,
            version=version, module_count=module_count, drift_sha=drift_sha)
    if root is not None:
        findings += check_side_rule_stores(root)
    return AuditReport(tuple(findings))


def audit_file(
    path: Path | str,
    *,
    root: Optional[Path | str] = None,
    scan_root: bool = True,
    error_log_path: Optional[Path | str] = None,
    docs_root: Optional[Path | str] = None,
) -> AuditReport:
    """Load a RAG file and audit it. Fail loud on bad JSON (DriftStoreError).

    ``root`` defaults to the file's grandparent (``RAG/RAG_MASTER.json`` ->
    project root) so the side-store scan covers the project root by default; pass
    ``scan_root=False`` to skip it, or an explicit ``root`` to override.

    ``error_log_path`` defaults to ``ERROR_LOG.md`` beside the RAG file (so E-###
    coverage is checked by default). The Rule 11 published-doc reconciliation runs
    only when ``docs_root`` is given (the published docs live in the git worktree,
    not next to the RAG): it reconciles ``README.md`` / ``CHANGELOG.md`` /
    ``docs/ROADMAP.md`` under ``docs_root`` against the live canonical facts.
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
    version = module_count = drift_sha = None
    if docs_root is not None:
        dr = Path(docs_root)
        doc_paths = [dr / "README.md", dr / "CHANGELOG.md", dr / "docs" / "ROADMAP.md"]
        version, module_count, drift_sha = canonical_facts()

    return audit_hot(
        hot, root=use_root, error_log_path=elp, doc_paths=doc_paths,
        version=version, module_count=module_count, drift_sha=drift_sha)


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
    "check_ledger_consistency",
    "check_record_coverage",
    "check_repo_claim_reconciliation",
    "canonical_facts",
    "audit_hot",
    "audit_file",
    "assert_clean",
]
