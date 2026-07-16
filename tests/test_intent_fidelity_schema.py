"""KA-INTENT-FIDELITY inc1 — next_session_directive (decision-of-record) contract.

Covers the shared foundation both increments build on: the structured directive
validator and the deterministic, stdlib-only normalized-match helpers. These are
the pieces that turn "directive banked" from an unverifiable prose claim (the
E-055 / S146 hole) into a gate-checkable fact.
"""

from rag_kernel.schemas import (
    validate_next_session_directive,
    normalize_directive_text,
    directive_matches,
)


# --- normalize_directive_text -------------------------------------------------

def test_normalize_collapses_whitespace_and_case():
    assert normalize_directive_text("  Ship   KA-INTENT\tFIDELITY  ") == \
        "ship ka-intent fidelity"


def test_normalize_non_str_is_empty():
    assert normalize_directive_text(None) == ""
    assert normalize_directive_text(123) == ""
    assert normalize_directive_text({"x": 1}) == ""


def test_normalize_blank_is_empty():
    assert normalize_directive_text("   \n\t ") == ""


# --- directive_matches --------------------------------------------------------

def test_match_tolerates_whitespace_and_case_only():
    stored = "Ship KA-INTENT-FIDELITY inc1 first"
    stated = "  ship  ka-intent-fidelity   INC1 first "
    assert directive_matches(stated, stored) is True


def test_match_rejects_semantic_reword():
    # Meaning-preserving reword must NOT match — matching is exact-in-substance,
    # deliberately not semantic (determinism > flexibility for a fail-loud gate).
    assert directive_matches(
        "Begin the intent-fidelity guardrail",
        "Ship KA-INTENT-FIDELITY inc1 first",
    ) is False


def test_match_empty_never_passes():
    assert directive_matches("", "anything") is False
    assert directive_matches("anything", "") is False
    assert directive_matches("", "") is False
    assert directive_matches(None, "anything") is False


# --- validate_next_session_directive ------------------------------------------

def _valid():
    return {
        "session": "S148",
        "for_session": "S149",
        "directive": "Ship KA-INTENT-FIDELITY inc1 first",
        "decision_ids": ["KA-INTENT-FIDELITY"],
        "authored_utc": "2026-07-15T08:19:45+00:00",
    }


def test_validate_accepts_well_formed():
    ok, errors = validate_next_session_directive(_valid())
    assert ok is True
    assert errors == []


def test_validate_accepts_minimal_required_only():
    ok, errors = validate_next_session_directive({
        "session": "S148", "for_session": "S149", "directive": "do the thing",
    })
    assert ok is True
    assert errors == []


def test_validate_rejects_non_dict():
    ok, errors = validate_next_session_directive("nope")
    assert ok is False
    assert any("must be a dict" in e for e in errors)


def test_validate_rejects_missing_required():
    d = _valid()
    del d["directive"]
    ok, errors = validate_next_session_directive(d)
    assert ok is False
    assert any("missing required field: 'directive'" in e for e in errors)


def test_validate_rejects_empty_directive():
    d = _valid()
    d["directive"] = "   "
    ok, errors = validate_next_session_directive(d)
    assert ok is False
    assert any("directive must not be empty" in e for e in errors)


def test_validate_rejects_non_str_required():
    d = _valid()
    d["for_session"] = 149
    ok, errors = validate_next_session_directive(d)
    assert ok is False
    assert any("for_session must be a string" in e for e in errors)


def test_validate_rejects_bad_decision_ids():
    d = _valid()
    d["decision_ids"] = ["ok", 5]
    ok, errors = validate_next_session_directive(d)
    assert ok is False
    assert any("decision_ids must be a list of strings" in e for e in errors)


def test_validate_rejects_non_str_authored_utc():
    d = _valid()
    d["authored_utc"] = 12345
    ok, errors = validate_next_session_directive(d)
    assert ok is False
    assert any("authored_utc must be a string" in e for e in errors)
