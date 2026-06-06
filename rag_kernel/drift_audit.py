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

from rag_kernel.drift_control import ItemStatus, TERMINAL_STATUSES
from rag_kernel.drift_store import (
    TRACKED_ITEMS_KEY,
    DriftStoreError,
    TrackedItemStore,
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
# Aggregate runners
# ---------------------------------------------------------------------------

def _as_store(source) -> TrackedItemStore:
    if isinstance(source, TrackedItemStore):
        return source
    if isinstance(source, dict):
        return TrackedItemStore.from_hot(source)
    return TrackedItemStore(source)


def audit_hot(hot: dict, *, root: Optional[Path | str] = None) -> AuditReport:
    """Run every check over a loaded HOT dict (and ``root`` for the side-store scan).

    The side-store check runs only when ``root`` is given (no path = no scan); the
    other three checks are pure over the in-memory state.
    """
    findings: list[AuditFinding] = []
    findings += check_render_parity(hot)
    store = TrackedItemStore.from_hot(hot)
    findings += check_supersede_refs(store)
    findings += check_note_status_contradiction(store)
    if root is not None:
        findings += check_side_rule_stores(root)
    return AuditReport(tuple(findings))


def audit_file(
    path: Path | str,
    *,
    root: Optional[Path | str] = None,
    scan_root: bool = True,
) -> AuditReport:
    """Load a RAG file and audit it. Fail loud on bad JSON (DriftStoreError).

    ``root`` defaults to the file's grandparent (``RAG/RAG_MASTER.json`` ->
    project root) so the side-store scan covers the project root by default; pass
    ``scan_root=False`` to skip it, or an explicit ``root`` to override.
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
    return audit_hot(hot, root=use_root)


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
    "audit_hot",
    "audit_file",
    "assert_clean",
]
