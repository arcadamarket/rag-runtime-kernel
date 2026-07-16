"""KA-INTENT-FIDELITY inc2 — session-START plan-vs-settled audit.

The opening counterpart to inc1's closing seal gate. inc1 guaranteed the prior
session's directive was PERSISTED verbatim as a structured
``next_session_directive`` (decision-of-record); inc2 guarantees the NEW session's
stated PLAN is FAITHFUL to that record — ID-binding (the plan binds to the
directive's ``decision_ids`` and every cited id resolves to a real tracked_item)
plus a normalized-exact restatement of the directive text. This closes the other
half of E-055 / the S146 drift: anchoring on a lossy handoff line and reciting a
stale blueprint instead of the settled decision-of-record.

Matching is deterministic, stdlib-only, zero-token — never semantic.

Two layers, mirroring inc1's split:
1. the pure ``audit_plan_against_directive`` function (schema layer);
2. the ``intent-audit`` CLI verb (integration layer), which also LOADS THE SOURCE
   DECISIONS (resolves decision_ids -> tracked_item records) so the session builds
   on the source of record, not the compressed handoff line.
"""

from __future__ import annotations

import json
from pathlib import Path

from rag_kernel.schemas import audit_plan_against_directive
from rag_kernel.__main__ import main


DIRECTIVE_TEXT = "Build KA-INTENT-FIDELITY inc2 then resolve it"


def _nsd(**over):
    d = {
        "session": "S151",
        "for_session": "S152",
        "directive": DIRECTIVE_TEXT,
        "decision_ids": ["KA-INTENT-FIDELITY"],
        "authored_utc": "2026-07-16T06:45:27+00:00",
    }
    d.update(over)
    return d


KNOWN = ["KA-INTENT-FIDELITY", "SYNC-TREE-VERB", "V05-SELF-HOST-HARNESS"]


# --- audit_plan_against_directive: happy path ---------------------------------

def test_audit_accepts_faithful_plan():
    ok, errors = audit_plan_against_directive(
        DIRECTIVE_TEXT, ["KA-INTENT-FIDELITY"], _nsd(), KNOWN
    )
    assert ok is True
    assert errors == []


def test_audit_tolerates_whitespace_and_case_in_restatement():
    stated = "  build   ka-intent-fidelity INC2   then  RESOLVE it "
    ok, errors = audit_plan_against_directive(
        stated, ["KA-INTENT-FIDELITY"], _nsd(), KNOWN
    )
    assert ok is True
    assert errors == []


# --- directive validity gate --------------------------------------------------

def test_audit_fails_on_absent_directive():
    ok, errors = audit_plan_against_directive(DIRECTIVE_TEXT, [], None, KNOWN)
    assert ok is False
    assert any("not auditable" in e for e in errors)


def test_audit_fails_on_malformed_directive():
    bad = _nsd()
    del bad["directive"]
    ok, errors = audit_plan_against_directive(
        DIRECTIVE_TEXT, ["KA-INTENT-FIDELITY"], bad, KNOWN
    )
    assert ok is False
    assert any("not auditable" in e for e in errors)


# --- normalized-exact restatement ---------------------------------------------

def test_audit_rejects_semantic_reword():
    # Meaning-preserving reword must NOT pass — the gate is exact-in-substance.
    ok, errors = audit_plan_against_directive(
        "Finish the intent guardrail and close it out",
        ["KA-INTENT-FIDELITY"], _nsd(), KNOWN,
    )
    assert ok is False
    assert any("normalized-match" in e for e in errors)


# --- ID-binding: cited set must equal the directive-pinned set ----------------

def test_audit_rejects_omitted_pinned_id():
    ok, errors = audit_plan_against_directive(
        DIRECTIVE_TEXT, [], _nsd(), KNOWN
    )
    assert ok is False
    assert any("omits directive-pinned decision id: KA-INTENT-FIDELITY" in e
               for e in errors)


def test_audit_rejects_unsanctioned_extra_id():
    ok, errors = audit_plan_against_directive(
        DIRECTIVE_TEXT,
        ["KA-INTENT-FIDELITY", "SYNC-TREE-VERB"],
        _nsd(), KNOWN,
    )
    assert ok is False
    assert any("did not sanction: SYNC-TREE-VERB" in e for e in errors)


# --- ID-binding: resolution ---------------------------------------------------

def test_audit_rejects_cited_id_that_does_not_resolve():
    ok, errors = audit_plan_against_directive(
        DIRECTIVE_TEXT,
        ["GHOST-ITEM"],
        _nsd(decision_ids=["GHOST-ITEM"]),
        KNOWN,
    )
    assert ok is False
    assert any("does not resolve to a tracked_item: GHOST-ITEM" in e
               for e in errors)


def test_audit_rejects_pinned_id_that_does_not_resolve():
    # Directive pins an id that no longer exists as a tracked_item; even if the
    # plan cites it, the SOURCE decision must resolve.
    ok, errors = audit_plan_against_directive(
        DIRECTIVE_TEXT,
        ["KA-INTENT-FIDELITY"],
        _nsd(),
        ["SYNC-TREE-VERB"],  # KA-INTENT-FIDELITY absent
    )
    assert ok is False
    assert any("does not resolve to a tracked_item: KA-INTENT-FIDELITY" in e
               for e in errors)


# --- degraded directive: no pinned decision_ids -------------------------------

def test_audit_no_pins_restatement_only_passes():
    ok, errors = audit_plan_against_directive(
        DIRECTIVE_TEXT, [], _nsd(decision_ids=[]), KNOWN
    )
    assert ok is True
    assert errors == []


def test_audit_no_pins_still_requires_cited_to_resolve():
    ok, errors = audit_plan_against_directive(
        DIRECTIVE_TEXT, ["GHOST"], _nsd(decision_ids=[]), KNOWN
    )
    assert ok is False
    assert any("does not resolve to a tracked_item: GHOST" in e for e in errors)


# --- input hygiene ------------------------------------------------------------

def test_audit_rejects_non_list_cited_ids():
    ok, errors = audit_plan_against_directive(
        DIRECTIVE_TEXT, "KA-INTENT-FIDELITY", _nsd(), KNOWN
    )
    assert ok is False
    assert any("cited_ids must be a list" in e for e in errors)


# --- CLI integration: intent-audit -------------------------------------------

def _write(p: Path, obj: dict) -> None:
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def _rag_with_directive(tmp_path: Path) -> Path:
    p = tmp_path / "RAG_MASTER.json"
    _write(p, {
        "meta": {"session_id": "S152", "written_by_session": "S151"},
        "next_session_directive": _nsd(),
        "tracked_items": [
            {"id": "KA-INTENT-FIDELITY", "title": "Intent-fidelity guardrail",
             "status": "IN_PROGRESS", "kind": "TASK"},
            {"id": "SYNC-TREE-VERB", "title": "sync-tree verb",
             "status": "OPEN", "kind": "TASK"},
        ],
    })
    return p


def test_cli_intent_audit_ok(tmp_path, capsys):
    p = _rag_with_directive(tmp_path)
    rc = main([
        "intent-audit", "--rag", str(p),
        "--plan", DIRECTIVE_TEXT, "--plan-decisions", "KA-INTENT-FIDELITY",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "intent-audit: OK" in out
    # loads the SOURCE decision (id + status + title), not the compressed line
    assert "KA-INTENT-FIDELITY [IN_PROGRESS] Intent-fidelity guardrail" in out


def test_cli_intent_audit_fail_on_mismatch(tmp_path, capsys):
    p = _rag_with_directive(tmp_path)
    rc = main([
        "intent-audit", "--rag", str(p),
        "--plan", "do something else entirely",
        "--plan-decisions", "KA-INTENT-FIDELITY",
    ])
    assert rc == 1
    err = capsys.readouterr().err
    assert "intent-audit: FAIL" in err
    assert "normalized-match" in err


def test_cli_intent_audit_fail_on_unsanctioned_id(tmp_path, capsys):
    p = _rag_with_directive(tmp_path)
    rc = main([
        "intent-audit", "--rag", str(p),
        "--plan", DIRECTIVE_TEXT,
        "--plan-decisions", "KA-INTENT-FIDELITY,SYNC-TREE-VERB",
    ])
    assert rc == 1
    err = capsys.readouterr().err
    assert "did not sanction: SYNC-TREE-VERB" in err


def test_cli_intent_audit_missing_rag(tmp_path, capsys):
    rc = main([
        "intent-audit", "--rag", str(tmp_path / "nope.json"),
        "--plan", DIRECTIVE_TEXT, "--plan-decisions", "KA-INTENT-FIDELITY",
    ])
    assert rc == 1
    assert "not found" in capsys.readouterr().err
