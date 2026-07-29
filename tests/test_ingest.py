"""Tests for the ingest verb (S181, B4 / BLUEPRINT-INGEST-PROTOCOL).

The behaviour that matters is the exit predicate: ingestion is complete only
when the deployment answers what the document answers WITHOUT the document.
Partial ingestion must be visible, not silent.
"""
from __future__ import annotations

import pytest

from rag_kernel.ingest import (
    IngestError,
    classify_heading,
    extract_claims,
    ingest_complete,
    plan_ingest,
    render_plan,
    resolve_landing,
    unlanded_claims,
)

DOC = """\
# Clone Blueprint

INGEST: RULE py_script_mandate — anything that can be a script must be one
INGEST: TASK CLONE-SECRETS-BOUNDARY — set meta.secret_paths before birth
INGEST: ASSET scripts/boot.py — boot helper

## Runbook for birth
Some prose.

## Next steps
More prose.
"""


def _rag(op=None, items=None, meta=None) -> dict:
    return {
        "meta": meta or {},
        "operating_protocol": op or {},
        "tracked_items": items or [],
    }


# --------------------------------------------------------------------------- #
# Claim extraction
# --------------------------------------------------------------------------- #
def test_explicit_markers_are_extracted_with_kind_id_and_text():
    claims = {c.id: c for c in extract_claims(DOC)}
    assert claims["py_script_mandate"].kind == "RULE"
    assert claims["py_script_mandate"].explicit is True
    assert "must be one" in claims["py_script_mandate"].text


def test_headings_contribute_inferred_claims():
    claims = {c.id: c for c in extract_claims(DOC)}
    assert "RUNBOOK-FOR-BIRTH" in claims
    assert claims["RUNBOOK-FOR-BIRTH"].kind == "ASSET"
    assert claims["RUNBOOK-FOR-BIRTH"].explicit is False


def test_headings_with_no_hint_are_not_claims():
    claims = {c.id for c in extract_claims("# Clone Blueprint\n\nprose\n")}
    assert claims == set()


def test_explicit_marker_wins_over_a_heading_with_the_same_id():
    text = "INGEST: RULE TASK-LIST — governance\n\n## Task list\n"
    claims = [c for c in extract_claims(text) if c.id in ("TASK-LIST",)]
    assert len(claims) == 1
    assert claims[0].kind == "RULE" and claims[0].explicit


def test_unknown_kind_is_fail_loud():
    with pytest.raises(IngestError, match="unknown INGEST kind"):
        extract_claims("INGEST: WIDGET foo — nope\n")


def test_duplicate_claim_id_is_fail_loud():
    with pytest.raises(IngestError, match="duplicate claim id"):
        extract_claims("INGEST: TASK a — one\nINGEST: TASK a — two\n")


def test_html_comment_wrapped_markers_are_accepted():
    claims = extract_claims("<!-- INGEST: TASK HIDDEN — in a comment -->\n")
    assert claims and claims[0].id == "HIDDEN"


@pytest.mark.parametrize(
    "heading,kind",
    [
        ("Operating rules", "RULE"),
        ("Deployment protocol", "RULE"),
        ("Helper script", "ASSET"),
        ("Open backlog", "TASK"),
        ("Shipped deliverables", "DELIVERABLE"),
        ("Random musings", None),
    ],
)
def test_heading_classification(heading, kind):
    assert classify_heading(heading) == kind


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #
def test_rule_claim_resolves_present_when_the_key_exists():
    rag = _rag(op={"py_script_mandate": "..."})
    plan = plan_ingest("doc.md", rag, text=DOC)
    route = next(r for r in plan.routes if r.claim.id == "py_script_mandate")
    assert route.action == "present"
    assert route.destination == "operating_protocol"


def test_rule_claim_matches_a_normalised_key():
    claims = extract_claims("INGEST: RULE PY-SCRIPT-MANDATE — x\n")
    landing, action = resolve_landing(claims[0], _rag(op={"py_script_mandate": "v"}))
    assert (landing, action) == ("py_script_mandate", "present")


def test_task_claim_resolves_against_tracked_items():
    rag = _rag(items=[{"id": "CLONE-SECRETS-BOUNDARY"}])
    plan = plan_ingest("doc.md", rag, text=DOC)
    route = next(r for r in plan.routes if r.claim.id == "CLONE-SECRETS-BOUNDARY")
    assert route.action == "present"


def test_asset_claim_resolves_against_the_context_registry():
    ctx = {"baked_assets": [{"id": "scripts/boot.py", "path": "scripts/boot.py"}]}
    plan = plan_ingest("doc.md", _rag(), context=ctx, text=DOC)
    route = next(r for r in plan.routes if r.claim.id == "scripts/boot.py")
    assert route.action == "present"


def test_deliverable_claim_resolves_against_root_deliverables():
    text = "INGEST: DELIVERABLE site-build — the built site\n"
    rag = _rag(meta={"root_deliverables": ["site-build"]})
    plan = plan_ingest("d.md", rag, text=text)
    assert plan.routes[0].action == "present"


def test_reference_claims_always_need_creating():
    text = "INGEST: REFERENCE vendor-api — big API dump\n"
    plan = plan_ingest("d.md", _rag(), text=text)
    assert plan.routes[0].action == "create"
    assert plan.routes[0].destination == "cold"


def test_a_document_with_no_claims_is_fail_loud():
    with pytest.raises(IngestError, match="no claims found"):
        plan_ingest("empty.md", _rag(), text="# Title\n\njust prose\n")


def test_missing_document_is_fail_loud():
    with pytest.raises(IngestError, match="document not found"):
        plan_ingest("does-not-exist-12345.md", _rag())


# --------------------------------------------------------------------------- #
# Exit predicate — the reason this verb exists
# --------------------------------------------------------------------------- #
def test_ingestion_is_incomplete_while_any_claim_has_no_landing_record():
    rag = _rag()
    plan = plan_ingest("doc.md", rag, text=DOC)
    ok, verdict = ingest_complete(plan, rag)
    assert not ok and "INCOMPLETE" in verdict


def test_ingestion_is_complete_once_every_claim_resolves():
    text = (
        "INGEST: RULE alpha — a rule\n"
        "INGEST: TASK BETA-TASK — a task\n"
    )
    rag = _rag(op={"alpha": "v"}, items=[{"id": "BETA-TASK"}])
    plan = plan_ingest("doc.md", rag, text=text)
    ok, verdict = ingest_complete(plan, rag)
    assert ok and "COMPLETE" in verdict


def test_exit_predicate_re_resolves_against_live_state_not_the_plan():
    """A plan computed against empty state must pass once state catches up."""
    text = "INGEST: TASK LATE-TASK — added after planning\n"
    rag = _rag()
    plan = plan_ingest("doc.md", rag, text=text)
    assert not ingest_complete(plan, rag)[0]
    rag["tracked_items"].append({"id": "LATE-TASK"})
    assert ingest_complete(plan, rag)[0]


def test_unlanded_claims_names_the_work_ingestion_implies():
    rag = _rag(op={"py_script_mandate": "v"})
    plan = plan_ingest("doc.md", rag, text=DOC)
    ids = {c.id for c in unlanded_claims(plan)}
    assert "py_script_mandate" not in ids
    assert "CLONE-SECRETS-BOUNDARY" in ids


def test_render_marks_inferred_claims_distinctly_and_is_bounded():
    plan = plan_ingest("doc.md", _rag(), text=DOC)
    text = render_plan(plan, limit=1)
    assert "more (raise --limit)" in text
    assert "prefer explicit" in text
