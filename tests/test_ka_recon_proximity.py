"""KA-RECON-PROXIMITY — same-sentence association in the Rule 11 doc reconciliation.

``check_repo_claim_reconciliation`` §2 flags a published line that pairs a PENDING
word with a tracked id whose canonical status is RESOLVED. The former test paired
them at *line* granularity: any pending word anywhere on a (often long, multi-clause)
paragraph line was read as the status of any RESOLVED id anywhere else on that line.

The live false positive was ROADMAP's v0.4.27 entry — one paragraph line whose
"``--dry-run`` prints the planned old->new token diff" clause sits three sentences
away from the RESOLVED ``KA-CS-REFRESH`` / ``FIX-4`` ids named earlier on the line.
"planned" describes the dry-run diff, not the resolution status of those ids.

KA-RECON-PROXIMITY tightens the association: the pending word and the id must fall in
the SAME sentence (split on ``. ``/``; `` only — never dashes or table pipes), so a
genuine single-line "ID — planned" / "ID: deferred" contradiction still fires while
the cross-sentence co-occurrence does not.
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


def _resolved(id, kind=ItemKind.TASK):
    return TrackedItem(id=id, title="t", status=ItemStatus.RESOLVED, kind=kind)


def _status_findings(findings):
    return [x for x in findings if x.check == "repo_claim_status"]


# --- the false positive: pending word in a DIFFERENT sentence must not fire -----

def test_pending_word_in_later_sentence_not_flagged(tmp_path):
    # Id resolved-and-described in sentence 1; the pending word "planned" belongs to
    # an unrelated feature two sentences later. Must NOT fire.
    line = ("KA-CS-REFRESH — a governed refresh-current-status verb, shipped on main. "
            "It re-stamps the version token by construction. "
            "Separately, --dry-run prints the planned old->new token diff.")
    docs = _docs(tmp_path, roadmap=line)
    findings = drift_audit.check_repo_claim_reconciliation(docs, [_resolved("KA-CS-REFRESH")])
    assert _status_findings(findings) == []


def test_pending_word_in_later_semicolon_clause_not_flagged(tmp_path):
    # Semicolons are sentence-grade boundaries too: id in clause 1, "deferred" in a
    # later ;-delimited clause about something else.
    line = ("FIX-4 reuses the tmp -> verify -> rename byte-parity path; "
            "the optional --tests count is deferred to a follow-up.")
    docs = _docs(tmp_path, roadmap=line)
    findings = drift_audit.check_repo_claim_reconciliation(docs, [_resolved("FIX-4")])
    assert _status_findings(findings) == []


def test_two_resolved_ids_only_the_same_sentence_one_matters(tmp_path):
    # KA-A resolved & clean in sentence 1; KA-B resolved but wrongly called "planned"
    # in sentence 2. Only KA-B should fire — never KA-A riding the same line.
    line = "KA-A shipped and is done. KA-B is still planned for later."
    docs = _docs(tmp_path, roadmap=line)
    findings = drift_audit.check_repo_claim_reconciliation(
        docs, [_resolved("KA-A"), _resolved("KA-B")])
    ids = {x.item_id for x in _status_findings(findings)}
    assert ids == {"KA-B"}


# --- regression guard: a genuine SAME-sentence contradiction still fires ---------

def test_same_sentence_id_and_pending_word_still_flagged(tmp_path):
    docs = _docs(tmp_path, roadmap="DRIFT-ELIM is still deferred to a later release.")
    findings = drift_audit.check_repo_claim_reconciliation(
        docs, [_resolved("DRIFT-ELIM", ItemKind.MILESTONE)])
    hits = _status_findings(findings)
    assert any(x.item_id == "DRIFT-ELIM" and x.severity == drift_audit.ERROR for x in hits)


def test_same_sentence_em_dash_status_still_flagged(tmp_path):
    # A dash is NOT a sentence boundary: "ID — planned" stays one sentence and fires.
    docs = _docs(tmp_path, roadmap="KA-CS-REFRESH — still planned, not yet built.")
    findings = drift_audit.check_repo_claim_reconciliation(docs, [_resolved("KA-CS-REFRESH")])
    assert any(x.item_id == "KA-CS-REFRESH" for x in _status_findings(findings))


def test_finding_detail_scoped_to_sentence(tmp_path):
    # The reported detail quotes the offending SENTENCE, not the whole paragraph.
    line = ("Intro sentence with no id at all. "
            "DRIFT-ELIM is still a TODO for next quarter.")
    docs = _docs(tmp_path, roadmap=line)
    findings = _status_findings(
        drift_audit.check_repo_claim_reconciliation(
            docs, [_resolved("DRIFT-ELIM", ItemKind.MILESTONE)]))
    assert findings and "Intro sentence" not in findings[0].detail
    assert "DRIFT-ELIM" in findings[0].detail
