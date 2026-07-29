"""Tests for the decision ledger (S181, B5 / DECISION-LEDGER-PRIMITIVE)."""
from __future__ import annotations

import pytest

from rag_kernel.decision_ledger import (
    DecisionError,
    audit_ledger,
    decisions_for,
    live_decisions,
    next_decision_id,
    read_ledger,
    record_decision,
    render_ledger,
)

OPTS = ["push now", "wait for the gate"]


def _rag(items=None, ledger=None) -> dict:
    rag = {"meta": {}, "tracked_items": items or []}
    if ledger is not None:
        rag["decision_ledger"] = ledger
    return rag


def _record(rag, **kw):
    base = dict(session="S181", question="Push or wait?", options=OPTS,
                chosen="push now")
    base.update(kw)
    return record_decision(rag, **base)


# --------------------------------------------------------------------------- #
# Recording
# --------------------------------------------------------------------------- #
def test_first_decision_gets_dec_0001():
    rag = _rag()
    assert next_decision_id(rag) == "DEC-0001"
    assert _record(rag).id == "DEC-0001"


def test_ids_increment_from_the_highest_present():
    rag = _rag(ledger=[{"id": "DEC-0007"}])
    assert _record(rag).id == "DEC-0008"


def test_record_is_appended_to_the_ledger_with_its_options():
    rag = _rag()
    _record(rag, rationale="repo claims must match reality")
    rec = read_ledger(rag)[0]
    assert rec["options"] == OPTS
    assert rec["chosen"] == "push now"
    assert rec["rationale"] == "repo claims must match reality"


def test_a_single_option_is_refused_as_governance_by_question():
    with pytest.raises(DecisionError, match="at least TWO distinct alternatives"):
        _record(_rag(), options=["only one"], chosen="only one")


def test_duplicate_options_collapse_and_then_refuse():
    with pytest.raises(DecisionError, match="at least TWO"):
        _record(_rag(), options=["same", "same"], chosen="same")


def test_chosen_must_be_one_of_the_offered_options():
    with pytest.raises(DecisionError, match="not among the options"):
        _record(_rag(), chosen="something else entirely")


def test_empty_question_is_refused():
    with pytest.raises(DecisionError, match="needs the question"):
        _record(_rag(), question="   ")


def test_empty_choice_is_refused():
    with pytest.raises(DecisionError, match="needs the option"):
        _record(_rag(), chosen="")


# --------------------------------------------------------------------------- #
# Binding
# --------------------------------------------------------------------------- #
def test_binding_a_known_tracked_item_is_recorded():
    rag = _rag(items=[{"id": "BIRTH-ADOPT-VERB"}])
    decision = _record(rag, binds=["BIRTH-ADOPT-VERB"])
    assert decision.binds == ["BIRTH-ADOPT-VERB"]


def test_binding_an_unknown_tracked_item_is_fail_loud():
    with pytest.raises(DecisionError, match="unknown tracked_item"):
        _record(_rag(), binds=["NOPE-DOES-NOT-EXIST"])


def test_nothing_is_appended_when_a_bind_is_rejected():
    rag = _rag()
    with pytest.raises(DecisionError):
        _record(rag, binds=["NOPE"])
    assert read_ledger(rag) == []


def test_decisions_for_finds_every_ruling_touching_an_item():
    rag = _rag(items=[{"id": "ITEM-A"}, {"id": "ITEM-B"}])
    _record(rag, binds=["ITEM-A"])
    _record(rag, question="Second?", binds=["ITEM-A", "ITEM-B"])
    assert len(decisions_for(rag, "ITEM-A")) == 2
    assert len(decisions_for(rag, "ITEM-B")) == 1


# --------------------------------------------------------------------------- #
# Superseding — the hook DIRECTIVE-SUPERSEDE-PATH needs
# --------------------------------------------------------------------------- #
def test_supersede_must_name_a_decision_in_the_ledger():
    with pytest.raises(DecisionError, match="not in the ledger"):
        _record(_rag(), supersedes="DEC-9999")


def test_a_superseded_ruling_drops_out_of_live_decisions():
    rag = _rag()
    first = _record(rag)
    _record(rag, question="Re-ruled?", supersedes=first.id)
    live = live_decisions(rag)
    assert len(read_ledger(rag)) == 2
    assert [d["id"] for d in live] == ["DEC-0002"]


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #
def test_audit_is_clean_on_a_well_formed_ledger():
    rag = _rag(items=[{"id": "ITEM-A"}])
    _record(rag, binds=["ITEM-A"])
    assert audit_ledger(rag) == []


def test_audit_catches_a_malformed_id():
    rag = _rag(ledger=[{"id": "oops", "options": OPTS, "chosen": OPTS[0]}])
    assert any("malformed id" in f for f in audit_ledger(rag))


def test_audit_catches_a_duplicate_id():
    rec = {"id": "DEC-0001", "options": OPTS, "chosen": OPTS[0]}
    assert any("duplicate id" in f for f in audit_ledger(_rag(ledger=[rec, dict(rec)])))


def test_audit_catches_a_chosen_value_outside_its_options():
    rag = _rag(ledger=[{"id": "DEC-0001", "options": OPTS, "chosen": "ghost"}])
    assert any("not among its options" in f for f in audit_ledger(rag))


def test_audit_catches_a_dangling_bind():
    rag = _rag(ledger=[{"id": "DEC-0001", "options": OPTS, "chosen": OPTS[0],
                        "binds": ["GONE"]}])
    assert any("unknown tracked_item" in f for f in audit_ledger(rag))


def test_audit_catches_a_self_supersede():
    rag = _rag(ledger=[{"id": "DEC-0001", "options": OPTS, "chosen": OPTS[0],
                        "supersedes": "DEC-0001"}])
    assert any("supersedes itself" in f for f in audit_ledger(rag))


def test_audit_catches_a_ledger_entry_with_one_option():
    rag = _rag(ledger=[{"id": "DEC-0001", "options": ["solo"], "chosen": "solo"}])
    assert any("fewer than two alternatives" in f for f in audit_ledger(rag))


# --------------------------------------------------------------------------- #
# Render
# --------------------------------------------------------------------------- #
def test_render_marks_the_chosen_option():
    rag = _rag()
    _record(rag)
    text = render_ledger(rag)
    assert "> push now" in text
    assert "  wait for the gate" in text


def test_render_is_bounded_and_can_show_live_only():
    rag = _rag()
    first = _record(rag)
    _record(rag, question="Second?", supersedes=first.id)
    assert "1 live ruling" in render_ledger(rag, live_only=True)
    assert "earlier (raise --limit)" in render_ledger(rag, limit=1)
