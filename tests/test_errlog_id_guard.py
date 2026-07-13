"""ERRLOG-ID-GUARD (P1/G1, S140/S141) — ERROR_LOG heading error-id coherence.

Exercises the formally-verified GUARD == I0 /\\ I1 /\\ I2 (formal/ErrlogIdGuard.tla)
as implemented by ``drift_audit.check_errlog_id_coherence``:

  * I0  errlog_id_malformed — a leading-id heading that is neither a definition
        (id + ':' / '—') nor a recurrence (id + recurrence marker).
  * I1  errlog_id_reuse     — an id with more than one definition heading.
  * I2  errlog_id_dangling  — an id mentioned in a heading with no definition.

Plus the classifier's positional discipline: a recurrence marker inside a
descriptive parenthetical (e.g. "(recurring, non-fatal):") or in the description
tail must NOT flip a definition into a recurrence, and a legitimate Dfn+Rcr pair
for one id is accepted (the naive "each id heads once" guard's false positive).
"""

from __future__ import annotations

from rag_kernel import drift_audit
from rag_kernel.drift_audit import (
    check_errlog_id_coherence,
    _errlog_classify_heading,
    audit_hot,
)


def _write(tmp_path, text):
    p = tmp_path / "ERROR_LOG.md"
    p.write_text(text, encoding="utf-8")
    return p


def _checks(findings):
    return sorted(f.check for f in findings)


# --- self-skip -------------------------------------------------------------

def test_none_path_skips_clean():
    assert check_errlog_id_coherence(None) == []


def test_absent_file_skips_clean(tmp_path):
    assert check_errlog_id_coherence(tmp_path / "nope.md") == []


# --- classifier unit (Dfn / Rcr / Mfd) ------------------------------------

def test_classify_colon_is_definition():
    assert _errlog_classify_heading(": a plain colon definition") == "Dfn"


def test_classify_provenance_then_emdash_is_definition():
    assert _errlog_classify_heading(" (S137, assigned S138) — title") == "Dfn"


def test_classify_recurrence_word_outside_parens_is_recurrence():
    assert _errlog_classify_heading(" recurrence (S127) — later") == "Rcr"


def test_classify_recurring_inside_parens_stays_definition():
    # "(recurring, non-fatal):" is a descriptor, not a recurrence marker.
    assert _errlog_classify_heading(" (recurring, non-fatal): def") == "Dfn"


def test_classify_recurrence_word_in_description_tail_stays_definition():
    # A recurrence word AFTER the ':' description delimiter is prose, not structure.
    assert _errlog_classify_heading(": recurring wrapper tax was recovered") == "Dfn"


def test_classify_no_delimiter_is_malformed():
    assert _errlog_classify_heading(" no delimiter just words") == "Mfd"


# --- I1 reuse --------------------------------------------------------------

def test_reuse_two_definitions_flagged(tmp_path):
    p = _write(tmp_path, "### E-001: first\n\n### E-001: second\n")
    f = check_errlog_id_coherence(p)
    assert _checks(f) == ["errlog_id_reuse"]
    assert f[0].item_id == "E-001"


def test_single_definition_clean(tmp_path):
    p = _write(tmp_path, "### E-001: only one\n")
    assert check_errlog_id_coherence(p) == []


# --- I2 dangling -----------------------------------------------------------

def test_dangling_recurrence_without_definition_flagged(tmp_path):
    p = _write(tmp_path, "### E-002 recurrence (S9) — refers to nothing\n")
    f = check_errlog_id_coherence(p)
    assert "errlog_id_dangling" in _checks(f)
    assert any(x.item_id == "E-002" for x in f)


def test_cross_reference_in_heading_needs_definition(tmp_path):
    # A secondary id mentioned in a heading is a mention and must be defined.
    p = _write(tmp_path, "### E-001: def\n\n### E-001 recurrence + E-050 (S1)\n")
    f = check_errlog_id_coherence(p)
    assert any(x.check == "errlog_id_dangling" and x.item_id == "E-050" for x in f)


def test_defined_cross_reference_is_clean(tmp_path):
    p = _write(tmp_path,
               "### E-001: def one\n\n### E-050: def fifty\n\n"
               "### E-001 recurrence + E-050 (S1)\n")
    assert check_errlog_id_coherence(p) == []


# --- I0 malformed ----------------------------------------------------------

def test_malformed_heading_flagged(tmp_path):
    p = _write(tmp_path, "### E-003 garbled no delimiter word\n")
    assert "errlog_id_malformed" in _checks(check_errlog_id_coherence(p))


# --- legitimate Dfn + Rcr (naive-guard false positive is accepted) --------

def test_legit_definition_plus_recurrence_clean(tmp_path):
    p = _write(tmp_path,
               "### E-004: the definition\n\n"
               "### E-004 recurrence (S9) — the later recurrence\n")
    assert check_errlog_id_coherence(p) == []


# --- audit_hot wiring ------------------------------------------------------

def test_audit_hot_runs_guard_when_error_log_given(tmp_path):
    p = _write(tmp_path, "### E-001: def\n\n### E-001: dup\n")
    hot = {"meta": {"last_updated_utc": "2026-07-13T00:00:00Z"}, "tracked_items": []}
    report = audit_hot(hot, error_log_path=p)
    assert any(f.check == "errlog_id_reuse" for f in report.findings)


def test_audit_hot_skips_guard_without_error_log():
    hot = {"meta": {"last_updated_utc": "2026-07-13T00:00:00Z"}, "tracked_items": []}
    report = audit_hot(hot, error_log_path=None)
    assert not any(str(f.check).startswith("errlog_id_") for f in report.findings)
