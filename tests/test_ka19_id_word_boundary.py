"""KA-19 — word-boundary id matching in the Rule 11 published-doc reconciliation.

``check_repo_claim_reconciliation`` flags a published line that pairs a PENDING
word with a tracked id whose canonical status is RESOLVED. The id was formerly
tested with a bare substring (``rid in ln``), so a RESOLVED short id (``FIX-1``)
spuriously matched inside a longer, unrelated id on the line (``FIX-12``) — the
auditor would report "RESOLVED record FIX-1 is still pending" against a line that
never mentioned FIX-1 at all. The longest-first ordering only masked it when the
longer id was itself RESOLVED and present; a longer id that was OPEN (hence not in
``resolved_ids``) left the false positive live.

KA-19 matches an id only at its token boundaries — a boundary being start/end of
line or any char that is neither a word char nor a hyphen — so neither a
digit/letter suffix (``FIX-12``) nor a hyphen extension (``FIX-1-alpha``) can
trigger a shorter id, while an exact, boundary-delimited mention still fires.
"""

from __future__ import annotations

from rag_kernel.drift_control import ItemKind, ItemStatus, TrackedItem
from rag_kernel import drift_audit


def _docs(tmp_path, readme="", changelog="", roadmap=""):
    (tmp_path / "README.md").write_text(readme, encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    (tmp_path / "docs").mkdir(exist_ok=True)
    (tmp_path / "docs" / "ROADMAP.md").write_text(roadmap, encoding="utf-8")
    return [tmp_path / "README.md", tmp_path / "CHANGELOG.md",
            tmp_path / "docs" / "ROADMAP.md"]


def _resolved(id):
    return TrackedItem(id=id, title="t", status=ItemStatus.RESOLVED,
                       kind=ItemKind.TASK)


def _status_findings(findings):
    return [x for x in findings if x.check == "repo_claim_status"]


# --- the core bug: a RESOLVED short id must not match a longer id -----------

def test_short_resolved_id_not_matched_inside_longer_digit_suffix(tmp_path):
    # FIX-1 is RESOLVED; the pending line names FIX-12 (a different, OPEN item).
    # The old substring test flagged FIX-1; the boundary match must not.
    docs = _docs(tmp_path, roadmap="FIX-12 is still planned for next quarter.")
    findings = drift_audit.check_repo_claim_reconciliation(docs, [_resolved("FIX-1")])
    assert _status_findings(findings) == []


def test_short_resolved_id_not_matched_inside_hyphen_extension(tmp_path):
    # FIX-4 is RESOLVED; the pending line names FIX-4-alpha (a distinct id).
    docs = _docs(tmp_path, roadmap="FIX-4-alpha is still deferred to a later release.")
    findings = drift_audit.check_repo_claim_reconciliation(docs, [_resolved("FIX-4")])
    assert _status_findings(findings) == []


def test_short_resolved_id_not_matched_as_prefixed_token(tmp_path):
    # A leading char must also be a boundary: XKA-1 must not trigger KA-1.
    docs = _docs(tmp_path, roadmap="XKA-1 remains unreleased and planned.")
    findings = drift_audit.check_repo_claim_reconciliation(docs, [_resolved("KA-1")])
    assert _status_findings(findings) == []


# --- regression guard: an exact, boundary-delimited mention still fires ------

def test_exact_resolved_id_still_flagged(tmp_path):
    docs = _docs(tmp_path, roadmap="FIX-1 is still planned for next quarter.")
    findings = drift_audit.check_repo_claim_reconciliation(docs, [_resolved("FIX-1")])
    hits = _status_findings(findings)
    assert any(x.item_id == "FIX-1" and x.severity == drift_audit.ERROR for x in hits)


def test_longer_resolved_id_flagged_and_shorter_untouched(tmp_path):
    # Both FIX-1 and FIX-12 RESOLVED; line claims FIX-12 pending. Only FIX-12
    # should fire — never FIX-1 riding the substring. (One finding per line.)
    docs = _docs(tmp_path, roadmap="FIX-12 is still a TODO.")
    findings = drift_audit.check_repo_claim_reconciliation(
        docs, [_resolved("FIX-1"), _resolved("FIX-12")])
    ids = {x.item_id for x in _status_findings(findings)}
    assert ids == {"FIX-12"}


def test_exact_id_with_internal_hyphens_still_flagged(tmp_path):
    docs = _docs(tmp_path, roadmap="KA-CS-REFRESH is still deferred.")
    findings = drift_audit.check_repo_claim_reconciliation(
        docs, [_resolved("KA-CS-REFRESH")])
    assert any(x.item_id == "KA-CS-REFRESH" for x in _status_findings(findings))
