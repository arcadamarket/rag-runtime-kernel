"""KA-3: fail-loud when current_status's self-facts contradict ``meta``.

``current_status`` denormalizes two facts from ``meta`` inside the same RAG —
the session that last wrote it (``session`` vs ``meta.written_by_session``) and
the day it was last updated (``last_updated`` vs ``meta.last_updated_utc``). The
eBay Session-Zero deploy froze ``current_status.session`` at ``S0`` while the
machine had moved on and ran ``last_updated`` two days behind ``meta``, yet
``audit --strict`` reported 0 findings because nothing compared the two.
``check_current_status_coherence`` (KA-3, part of the KA-10 GOVERNANCE-DETERMINISM
arc) closes that gap. These tests pin the new fail-loud invariant, prove the
day-granularity date comparison, prove it self-skips an absent / key-less /
unparseable source (no false positives — this kernel's own RAG omits the keys),
and prove it composes into the ``audit_hot`` boundary gate.
"""

from __future__ import annotations

from datetime import date

from rag_kernel import drift_audit
from rag_kernel.drift_audit import (
    _coerce_utc_date,
    check_current_status_coherence,
)


def _checks(findings) -> set[str]:
    return {f.check for f in findings}


# --------------------------------------------------------------------------
# the KA-3 gap: current_status frozen behind meta
# --------------------------------------------------------------------------

def test_session_mismatch_is_error():
    hot = {
        "meta": {"written_by_session": "S94", "last_updated_utc": "2026-06-20T13:00:00+00:00"},
        "current_status": {"session": "S0", "last_updated": "2026-06-20"},
    }
    f = check_current_status_coherence(hot)
    assert len(f) == 1
    assert _checks(f) == {"current_status_coherence"}
    assert "S0" in f[0].detail and "S94" in f[0].detail


def test_last_updated_day_mismatch_is_error():
    # the exact eBay shape: last_updated 06-16 vs meta 06-18
    hot = {
        "meta": {"written_by_session": "S0", "last_updated_utc": "2026-06-18T09:00:00+00:00"},
        "current_status": {"session": "S0", "last_updated": "2026-06-16"},
    }
    f = check_current_status_coherence(hot)
    assert len(f) == 1
    assert "2026-06-16" in f[0].detail and "2026-06-18" in f[0].detail


def test_both_facts_stale_reports_two_findings():
    hot = {
        "meta": {"written_by_session": "S94", "last_updated_utc": "2026-06-20T13:00:00+00:00"},
        "current_status": {"session": "S0", "last_updated": "2026-06-16"},
    }
    f = check_current_status_coherence(hot)
    assert len(f) == 2
    assert _checks(f) == {"current_status_coherence"}


# --------------------------------------------------------------------------
# day-granularity date comparison (no false positive on a same-day instant)
# --------------------------------------------------------------------------

def test_same_day_different_time_is_clean():
    # current_status records a day; meta a full instant on that same day → clean.
    hot = {
        "meta": {"written_by_session": "S94", "last_updated_utc": "2026-06-20T13:37:55+00:00"},
        "current_status": {"session": "S94", "last_updated": "2026-06-20"},
    }
    assert check_current_status_coherence(hot) == []


def test_z_suffixed_meta_instant_parsed():
    hot = {
        "meta": {"written_by_session": "S94", "last_updated_utc": "2026-06-20T23:59:00Z"},
        "current_status": {"session": "S94", "last_updated": "2026-06-21"},
    }
    f = check_current_status_coherence(hot)
    assert len(f) == 1  # 06-20 (UTC) != 06-21


# --------------------------------------------------------------------------
# no false positives — self-skip on absent / key-less / unparseable source
# --------------------------------------------------------------------------

def test_matching_facts_pass():
    hot = {
        "meta": {"written_by_session": "S94", "last_updated_utc": "2026-06-20T13:37:55+00:00"},
        "current_status": {"session": "S94", "last_updated": "2026-06-20"},
    }
    assert check_current_status_coherence(hot) == []


def test_absent_current_status_self_skips():
    assert check_current_status_coherence({"meta": {"written_by_session": "S94"}}) == []
    assert check_current_status_coherence({}) == []


def test_absent_meta_self_skips():
    assert check_current_status_coherence({"current_status": {"session": "S94"}}) == []


def test_keyless_current_status_self_skips():
    # this kernel's own RAG: current_status carries narrative fields but neither
    # ``session`` nor ``last_updated`` — the check must NOT fire.
    hot = {
        "meta": {"written_by_session": "S94", "last_updated_utc": "2026-06-20T13:37:55+00:00"},
        "current_status": {"rag_kernel_version": "v0.4.16 ...", "github_repo": "..."},
    }
    assert check_current_status_coherence(hot) == []


def test_empty_written_by_session_not_double_reported_here():
    # an empty written_by_session is check_written_by_session's concern; the
    # coherence check skips rather than flagging the same defect twice.
    hot = {
        "meta": {"written_by_session": "", "last_updated_utc": "2026-06-20T13:37:55+00:00"},
        "current_status": {"session": "S94", "last_updated": "2026-06-20"},
    }
    assert check_current_status_coherence(hot) == []


def test_unparseable_date_self_skips():
    hot = {
        "meta": {"written_by_session": "S94", "last_updated_utc": "not-a-date"},
        "current_status": {"session": "S94", "last_updated": "2026-06-20"},
    }
    assert check_current_status_coherence(hot) == []


# --------------------------------------------------------------------------
# the _coerce_utc_date helper
# --------------------------------------------------------------------------

def test_coerce_bare_day():
    assert _coerce_utc_date("2026-06-20") == date(2026, 6, 20)


def test_coerce_offset_instant_normalized_to_utc():
    # 01:30 at +02:00 is 23:30 UTC the PREVIOUS day
    assert _coerce_utc_date("2026-06-20T01:30:00+02:00") == date(2026, 6, 19)


def test_coerce_z_instant():
    assert _coerce_utc_date("2026-06-20T23:59:00Z") == date(2026, 6, 20)


def test_coerce_rejects_garbage():
    assert _coerce_utc_date("not-a-date") is None
    assert _coerce_utc_date("") is None
    assert _coerce_utc_date(None) is None
    assert _coerce_utc_date(12345) is None


# --------------------------------------------------------------------------
# composition into the boundary gate
# --------------------------------------------------------------------------

def test_fires_in_audit_hot():
    # a minimal otherwise-clean RAG with the eBay stale-session defect
    hot = {
        "meta": {"policy_version": "3.2.5", "written_by_session": "S94",
                 "last_updated_utc": "2026-06-20T13:00:00+00:00",
                 "last_checkpoint_seq": 1},
        "state_machine_status": "READY",
        "operating_protocol": {"tool_hierarchy": "..."},
        "sessions_recent": [{"id": "S94"}],
        "tracked_items": [],
        "current_status": {"session": "S0", "last_updated": "2026-06-16"},
    }
    report = drift_audit.audit_hot(hot)
    assert not report.ok
    assert "current_status_coherence" in _checks(report.errors)


def test_audit_hot_clean_when_coherent():
    hot = {
        "meta": {"policy_version": "3.2.5", "written_by_session": "S94",
                 "last_updated_utc": "2026-06-20T13:00:00+00:00",
                 "last_checkpoint_seq": 1},
        "state_machine_status": "READY",
        "operating_protocol": {"tool_hierarchy": "..."},
        "sessions_recent": [{"id": "S94"}],
        "tracked_items": [],
        "current_status": {"session": "S94", "last_updated": "2026-06-20"},
    }
    report = drift_audit.audit_hot(hot)
    assert "current_status_coherence" not in _checks(report.findings)
