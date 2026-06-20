"""KA-2: fail-loud when the ``sessions_recent`` ledger is not internally coherent.

``sessions_recent`` is the append-only ledger of per-session checkpoints. The
eBay Session-Zero deploy carried duplicate *bootstrap* rows — S0 and S1 minted at
the SAME instant, one never actually run — while ``audit --strict`` reported 0
findings, and there was no governed way to repair them.
``check_sessions_recent_coherence`` (KA-2, part of the KA-10 GOVERNANCE-DETERMINISM
arc) closes that gap with one order-agnostic fail-loud invariant: no two rows may
share a checkpoint timestamp ``d`` (the duplicate-bootstrap signature).

These tests pin the invariant, prove instant-granularity comparison (a Z-suffixed
instant equals its offset twin; sub-second-distinct rows are NOT duplicates), prove
it is order-agnostic — accepting both this kernel's oldest-first live RAG and a
fresh ``init --auto-ready`` RAG's newest-first ``[S1, S0]`` shape, plus the
legitimate S95/S95 multi-checkpoint pair — prove it self-skips an absent / short /
field-less ledger (no false positives — this kernel's own RAG audits clean), and
prove it composes into the ``audit_hot`` boundary gate.
"""

from __future__ import annotations

from datetime import datetime, timezone

from rag_kernel import drift_audit
from rag_kernel.drift_audit import (
    _coerce_utc_instant,
    check_sessions_recent_coherence,
)


def _checks(findings) -> set[str]:
    return {f.check for f in findings}


# --------------------------------------------------------------------------
# the KA-2 gap: duplicate-bootstrap rows (shared timestamp)
# --------------------------------------------------------------------------

def test_duplicate_bootstrap_timestamp_distinct_ids_is_error():
    # the exact eBay shape: S0 and S1 minted at the SAME instant.
    hot = {"sessions_recent": [
        {"id": "S0", "d": "2026-06-16T10:00:00+00:00"},
        {"id": "S1", "d": "2026-06-16T10:00:00+00:00"},
    ]}
    f = check_sessions_recent_coherence(hot)
    assert len(f) == 1
    assert _checks(f) == {"sessions_recent_coherence"}
    assert "duplicate-bootstrap" in f[0].detail


def test_duplicate_timestamp_same_id_is_error():
    hot = {"sessions_recent": [
        {"id": "S5", "d": "2026-06-16T10:00:00+00:00"},
        {"id": "S5", "d": "2026-06-16T10:00:00+00:00"},
    ]}
    f = check_sessions_recent_coherence(hot)
    assert len(f) == 1
    assert _checks(f) == {"sessions_recent_coherence"}


def test_z_suffixed_equals_offset_twin_is_duplicate():
    # 14:00Z and 16:00+02:00 are the SAME instant → duplicate.
    hot = {"sessions_recent": [
        {"id": "S0", "d": "2026-06-16T14:00:00Z"},
        {"id": "S1", "d": "2026-06-16T16:00:00+02:00"},
    ]}
    f = check_sessions_recent_coherence(hot)
    assert len(f) == 1
    assert _checks(f) == {"sessions_recent_coherence"}


def test_identical_unparseable_literal_d_is_duplicate():
    # two rows carrying the same unparseable literal timestamp are still dups.
    hot = {"sessions_recent": [
        {"id": "S0", "d": "bootstrap"},
        {"id": "S1", "d": "bootstrap"},
    ]}
    f = check_sessions_recent_coherence(hot)
    assert len(f) == 1
    assert "literal" in f[0].detail


# --------------------------------------------------------------------------
# order-agnostic by design — directional monotonicity is NOT enforced
# --------------------------------------------------------------------------

def test_descending_order_is_clean():
    # a fresh `init --auto-ready` RAG ships sessions_recent NEWEST-first: an "S1"
    # row at a LATER instant precedes the "S0" bootstrap row — distinct timestamps,
    # so coherent. Directional monotonicity would wrongly flag this clean deploy.
    hot = {"sessions_recent": [
        {"id": "S1", "d": "2026-06-20T19:02:03.160933+00:00"},
        {"id": "S0", "d": "2026-06-20T19:02:03+00:00"},
    ]}
    assert check_sessions_recent_coherence(hot) == []


def test_descending_session_ids_distinct_times_is_clean():
    hot = {"sessions_recent": [
        {"id": "S5", "d": "2026-06-16T12:00:00+00:00"},
        {"id": "S3", "d": "2026-06-16T09:00:00+00:00"},
    ]}
    assert check_sessions_recent_coherence(hot) == []


# --------------------------------------------------------------------------
# no false positives — legitimate shapes pass
# --------------------------------------------------------------------------

def test_legit_multi_checkpoint_same_id_different_times_is_clean():
    # this kernel's own S95/S95 pattern: a repeated id at DIFFERENT instants.
    hot = {"sessions_recent": [
        {"id": "S95", "d": "2026-06-20T14:12:52+00:00"},
        {"id": "S95", "d": "2026-06-20T14:13:22+00:00"},
    ]}
    assert check_sessions_recent_coherence(hot) == []


def test_live_kernel_shape_is_clean():
    # the live S92..S95,S95 ledger (distinct, increasing timestamps).
    hot = {"sessions_recent": [
        {"id": "S92", "d": "2026-06-20T08:40:26+00:00"},
        {"id": "S93", "d": "2026-06-20T13:07:34+00:00"},
        {"id": "S94", "d": "2026-06-20T13:37:55+00:00"},
        {"id": "S95", "d": "2026-06-20T14:12:52+00:00"},
        {"id": "S95", "d": "2026-06-20T14:13:22+00:00"},
    ]}
    assert check_sessions_recent_coherence(hot) == []


def test_equal_timestamp_distinct_ids_is_duplicate():
    # two DIFFERENT sessions sharing one instant is the duplicate-bootstrap defect.
    hot = {"sessions_recent": [
        {"id": "S1", "d": "2026-06-16T10:00:00+00:00"},
        {"id": "S2", "d": "2026-06-16T10:00:00+00:00"},
    ]}
    assert len(check_sessions_recent_coherence(hot)) == 1


# --------------------------------------------------------------------------
# self-skip: absent / short / malformed ledger
# --------------------------------------------------------------------------

def test_absent_sessions_recent_self_skips():
    assert check_sessions_recent_coherence({}) == []
    assert check_sessions_recent_coherence({"meta": {}}) == []


def test_single_row_self_skips():
    assert check_sessions_recent_coherence(
        {"sessions_recent": [{"id": "S1", "d": "2026-06-16T10:00:00+00:00"}]}) == []


def test_non_list_self_skips():
    assert check_sessions_recent_coherence({"sessions_recent": {"id": "S1"}}) == []
    assert check_sessions_recent_coherence({"sessions_recent": "S1"}) == []


def test_non_dict_rows_skipped():
    hot = {"sessions_recent": [
        "garbage",
        {"id": "S1", "d": "2026-06-16T09:00:00+00:00"},
        {"id": "S2", "d": "2026-06-16T10:00:00+00:00"},
    ]}
    assert check_sessions_recent_coherence(hot) == []


def test_missing_or_unparseable_d_does_not_false_positive():
    # rows without a parseable d are skipped for the timestamp checks; distinct
    # unparseable literals are not duplicates, and ids stay monotonic → clean.
    hot = {"sessions_recent": [
        {"id": "S1"},
        {"id": "S2", "d": "not-a-date"},
        {"id": "S3", "d": "2026-06-16T10:00:00+00:00"},
    ]}
    assert check_sessions_recent_coherence(hot) == []


# --------------------------------------------------------------------------
# the _coerce_utc_instant helper
# --------------------------------------------------------------------------

def test_coerce_offset_instant_normalized_to_utc():
    assert _coerce_utc_instant("2026-06-20T01:30:00+02:00") == datetime(
        2026, 6, 19, 23, 30, tzinfo=timezone.utc)


def test_coerce_z_instant():
    assert _coerce_utc_instant("2026-06-20T23:59:00Z") == datetime(
        2026, 6, 20, 23, 59, tzinfo=timezone.utc)


def test_coerce_naive_read_as_utc():
    assert _coerce_utc_instant("2026-06-20T12:00:00") == datetime(
        2026, 6, 20, 12, 0, tzinfo=timezone.utc)


def test_coerce_bare_day_is_midnight_utc():
    assert _coerce_utc_instant("2026-06-20") == datetime(
        2026, 6, 20, 0, 0, tzinfo=timezone.utc)


def test_coerce_rejects_garbage():
    assert _coerce_utc_instant("not-a-date") is None
    assert _coerce_utc_instant("") is None
    assert _coerce_utc_instant(None) is None
    assert _coerce_utc_instant(12345) is None


# --------------------------------------------------------------------------
# composition into the boundary gate
# --------------------------------------------------------------------------

def test_fires_in_audit_hot():
    hot = {
        "meta": {"policy_version": "3.2.5", "written_by_session": "S1",
                 "last_updated_utc": "2026-06-16T10:00:00+00:00",
                 "last_checkpoint_seq": 1},
        "state_machine_status": "READY",
        "operating_protocol": {"tool_hierarchy": "..."},
        "sessions_recent": [
            {"id": "S0", "d": "2026-06-16T10:00:00+00:00"},
            {"id": "S1", "d": "2026-06-16T10:00:00+00:00"},
        ],
        "tracked_items": [],
    }
    report = drift_audit.audit_hot(hot)
    assert not report.ok
    assert "sessions_recent_coherence" in _checks(report.errors)


def test_audit_hot_clean_when_coherent():
    hot = {
        "meta": {"policy_version": "3.2.5", "written_by_session": "S2",
                 "last_updated_utc": "2026-06-16T10:00:00+00:00",
                 "last_checkpoint_seq": 1},
        "state_machine_status": "READY",
        "operating_protocol": {"tool_hierarchy": "..."},
        "sessions_recent": [
            {"id": "S1", "d": "2026-06-16T09:00:00+00:00"},
            {"id": "S2", "d": "2026-06-16T10:00:00+00:00"},
        ],
        "tracked_items": [],
    }
    report = drift_audit.audit_hot(hot)
    assert "sessions_recent_coherence" not in _checks(report.findings)
