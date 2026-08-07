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
    with pytest.raises(IngestError, match="undeclared INGEST kind"):
        extract_claims("INGEST: WIDGET foo — nope\n")


# --------------------------------------------------------------------------- #
# INGEST-KIND-UNVALIDATED (S187) — an undeclared kind may not be answered with
# SILENCE. CLAIM_RE constrained the kind slot to [A-Z]+, so only a validly-SHAPED
# unknown kind ("WIDGET") was refused; "Decision", "DECISION-LOG" and "task" failed
# the pattern outright and the line was skipped without a word. Nothing extracted
# means nothing unrouted, which means the exit predicate reports COMPLETE over
# claims it never read — how a parent handoff declaring four invented kinds landed
# as "COMPLETE 8 of 8" over records the receiver had re-authored.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("line", [
    "INGEST: Decision D1 — mixed case",
    "INGEST: DECISION-LOG D1 — hyphenated",
    "INGEST: task T1 — lowercase",
    "INGEST: Task2 T1 — digit",
    "INGEST: décision D1 — non-ascii",
])
def test_malformed_kind_refuses_instead_of_silently_skipping(line):
    """The regression: each of these previously yielded ZERO claims, silently."""
    with pytest.raises(IngestError, match="undeclared INGEST kind"):
        extract_claims(line + "\n")


def test_refusal_names_the_offending_kind_and_the_enumeration_verb():
    with pytest.raises(IngestError) as ex:
        extract_claims("INGEST: DECISION-LOG D1 — x\n")
    msg = str(ex.value)
    assert "DECISION-LOG" in msg           # what the sender wrote
    assert "list-kinds" in msg             # how to find out what is allowed
    assert "RULE" in msg and "TASK" in msg  # the declared set, inline


def test_a_malformed_marker_poisons_the_whole_document():
    """One undeclared kind must fail the document, not be quietly dropped from it."""
    doc = "INGEST: TASK good — fine\nINGEST: Decision bad — not fine\n"
    with pytest.raises(IngestError, match="undeclared INGEST kind"):
        extract_claims(doc)


def test_valid_kinds_are_unaffected_by_the_stricter_detector():
    from rag_kernel.ingest import KINDS
    doc = "".join(f"INGEST: {k} id{i} — text\n" for i, k in enumerate(KINDS))
    claims = extract_claims(doc)
    assert [c.kind for c in claims] == list(KINDS)


def test_prose_mentioning_ingest_is_not_a_marker():
    """The detector keys off the 'INGEST:' label at line start, not the word."""
    assert extract_claims("We should ingest this later.\n") == []
    assert extract_claims("The INGEST protocol is described below.\n") == []


def test_marker_with_no_id_still_refuses_a_bad_kind():
    with pytest.raises(IngestError, match="undeclared INGEST kind"):
        extract_claims("INGEST: Decision\n")


# --------------------------------------------------------------------------- #
# list-kinds — the enumerable half of the contract
# --------------------------------------------------------------------------- #

def test_declared_kinds_renders_from_the_enforced_data():
    from rag_kernel.ingest import DESTINATION, KINDS, declared_kinds
    rows = declared_kinds()
    assert [r["kind"] for r in rows] == list(KINDS)
    assert all(r["destination"] == DESTINATION[r["kind"]] for r in rows)


class TestListKindsCLI:
    def test_text_output_lists_every_kind(self, capsys):
        from rag_kernel.ingest import KINDS
        from rag_kernel.__main__ import main
        assert main(["list-kinds"]) == 0
        out = capsys.readouterr().out
        for k in KINDS:
            assert k in out
        assert "REFUSED" in out

    def test_json_output_is_machine_readable(self, capsys):
        import json as _json
        from rag_kernel.ingest import KINDS
        from rag_kernel.__main__ import main
        assert main(["list-kinds", "--json"]) == 0
        rows = _json.loads(capsys.readouterr().out)
        assert [r["kind"] for r in rows] == list(KINDS)


# --------------------------------------------------------------------------- #
# INGEST-PREDICATE-SLUG-COUPLING (S187) — the exit predicate compared a claim id
# to a landing id by RAW string equality, and an inferred claim's id is a SLUG of
# its heading. A record landed under a semantic id therefore read as unlanded
# forever. Operator ruling S187: normalize ids for EVERY kind, and let an INFERRED
# claim additionally match a tracked_item by TITLE — rendered distinctly, because a
# title match is a weaker equivalence than an id match.
# --------------------------------------------------------------------------- #

from rag_kernel.ingest import Claim, normalize_id, resolve_landing_ex


@pytest.mark.parametrize("a,b", [
    ("FOO_BAR", "foo-bar"),
    ("Foo Bar", "foo-bar"),
    ("foo--bar", "FOO-BAR"),
    ("  foo_bar  ", "foo-bar"),
    ("Foo.Bar", "foo-bar"),
])
def test_normalize_id_unifies_case_and_separators(a, b):
    assert normalize_id(a) == normalize_id(b)


def test_normalize_id_does_not_merge_distinct_names():
    assert normalize_id("foo-bar") != normalize_id("foo-baz")


def _claim(cid, kind="TASK", explicit=True):
    return Claim(id=cid, kind=kind, text="", line=1, explicit=explicit)


def _rag_with_item(item_id, title):
    return _rag(items=[{"id": item_id, "title": title, "status": "OPEN"}])


# -- normalized id matching, now uniform across kinds ----------------------- #

def test_task_lands_on_a_differently_cased_id():
    rag = _rag_with_item("PRIORITY_ACTIONS", "whatever")
    lid, action, basis = resolve_landing_ex(_claim("priority-actions"), rag)
    assert (lid, action, basis) == ("PRIORITY_ACTIONS", "present", "id")


def test_rule_normalization_is_no_longer_a_private_guess():
    rag = _rag(op={"token_economy": {}})
    lid, action, basis = resolve_landing_ex(_claim("TOKEN-ECONOMY", kind="RULE"), rag)
    assert (lid, action, basis) == ("token_economy", "present", "id")


def test_asset_matching_is_normalized_too():
    claim = _claim("SCRIPTS/Run_Audit.py", kind="ASSET")
    ctx = {"baked_assets": [{"path": "scripts/run-audit.py"}]}
    lid, action, basis = resolve_landing_ex(claim, _rag(), context=ctx)
    assert (action, basis) == ("present", "id")
    assert lid == "scripts/run-audit.py"


def test_unmatched_claim_still_reports_create_with_no_basis():
    lid, action, basis = resolve_landing_ex(_claim("NOTHING-LIKE-THIS"), _rag())
    assert (lid, action, basis) == ("NOTHING-LIKE-THIS", "create", "none")


# -- title matching: inferred only ------------------------------------------ #

def test_inferred_claim_lands_on_a_semantically_named_record():
    """The reported defect: heading slug != record id, record exists anyway."""
    rag = _rag_with_item("PRIORITY-ACTIONS-STALE-SNAPSHOT", "Priority actions render")
    claim = _claim("priority-actions-render", explicit=False)
    lid, action, basis = resolve_landing_ex(claim, rag)
    assert (lid, action, basis) == ("PRIORITY-ACTIONS-STALE-SNAPSHOT", "present", "title")


def test_explicit_claim_is_held_to_the_id_it_declared():
    """A sender that names an id may not land a record it never named."""
    rag = _rag_with_item("PRIORITY-ACTIONS-STALE-SNAPSHOT", "Priority actions render")
    claim = _claim("priority-actions-render", explicit=True)
    assert resolve_landing_ex(claim, rag)[1:] == ("create", "none")


def test_id_match_wins_over_title_match():
    rag = _rag(items=[
        {"id": "ALPHA", "title": "beta"},
        {"id": "BETA", "title": "gamma"},
    ])
    lid, _action, basis = resolve_landing_ex(_claim("beta", explicit=False), rag)
    assert (lid, basis) == ("BETA", "id")


def test_title_index_is_deterministic_on_collision():
    rag = _rag(items=[
        {"id": "FIRST", "title": "Same Title"},
        {"id": "SECOND", "title": "Same Title"},
    ])
    lid, _a, basis = resolve_landing_ex(_claim("same-title", explicit=False), rag)
    assert (lid, basis) == ("FIRST", "title")  # first writer wins


# -- the weaker equivalence is always visible ------------------------------- #

def test_verdict_declares_how_many_landed_by_title():
    """COMPLETE is still COMPLETE, but it must SAY the equivalence was weaker."""
    rag = _rag_with_item("SEMANTIC-ID", "Backlog grooming")
    plan = plan_ingest("d.md", rag, text="## Backlog grooming\n")
    assert [r.basis for r in plan.routes] == ["title"]  # not vacuous
    ok, verdict = ingest_complete(plan, rag)
    assert ok is True
    assert "1 by heading->title match, not id" in verdict


def test_verdict_is_silent_about_title_when_every_match_was_by_id():
    rag = _rag_with_item("backlog-grooming", "Backlog grooming")
    plan = plan_ingest("d.md", rag, text="## Backlog grooming\n")
    assert [r.basis for r in plan.routes] == ["id"]
    ok, verdict = ingest_complete(plan, rag)
    assert ok is True and "title match" not in verdict


def test_render_plan_flags_title_matches_distinctly():
    rag = _rag_with_item("SEMANTIC-ID", "Backlog grooming")
    plan = plan_ingest("d.md", rag, text="## Backlog grooming\n")
    assert any(r.basis == "title" for r in plan.routes)  # not vacuous
    out = render_plan(plan)
    assert "MATCHED BY TITLE" in out
    assert "SEMANTIC-ID" in out
    assert "~ BACKLOG-GROOMING -> SEMANTIC-ID" in out  # _slug upper-cases


def test_render_plan_omits_the_title_block_when_there_are_none():
    rag = _rag_with_item("backlog-grooming", "Backlog grooming")
    plan = plan_ingest("d.md", rag, text="## Backlog grooming\n")
    assert "MATCHED BY TITLE" not in render_plan(plan)


def test_route_basis_defaults_to_id_for_legacy_construction():
    from rag_kernel.ingest import Route
    r = Route(claim=_claim("X"), destination="tracked_items",
              landing_id="X", action="present")
    assert r.basis == "id"


def test_resolve_landing_keeps_its_two_tuple_arity():
    """Existing callers/pins must be unaffected by the basis addition."""
    rag = _rag_with_item("X", "x")
    assert len(resolve_landing(_claim("X"), rag)) == 2


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
