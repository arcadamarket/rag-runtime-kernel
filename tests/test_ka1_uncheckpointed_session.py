"""KA-1: fail-loud when a *completed* session ran but was never checkpointed.

The eBay governance freeze (S88 headline: "deployed auditor passed clean while
governance frozen at S0/seq1") happened because an agent ended sessions on
``configure``/``audit`` without ever running ``checkpoint`` — so
``meta.written_by_session`` stayed behind while later sessions ran. The KA-4 close
gate stops the LIVE session from closing un-checkpointed, but the AUDITOR itself
never asserted it — the exact blind spot that let an already-frozen RAG report
clean. ``check_uncheckpointed_session`` (KA-1, part of the KA-10
GOVERNANCE-DETERMINISM arc) closes that gap.

Signal: a session log beside the RAG (``session_log_<sid>.jsonl``) that both
carries a ``session_end`` marker (ran to a clean close) AND has a numeric session
ordinal greater than ``meta.written_by_session``'s ordinal.

These tests pin the invariant, prove it keys on ``session_end`` so the in-flight
current session (still-open / detached / crashed — no end marker) is never
false-positived, prove an ordinal ``<= written_by_session`` (a historical
checkpointed session whose log persists) is clean, prove it self-skips a
BOOTING / un-stamped / malformed-id RAG and an empty RAG dir, and prove it
composes into the ``audit_file`` boundary gate.
"""

from __future__ import annotations

import json

from rag_kernel.drift_audit import (
    _session_id_int,
    _session_log_completed,
    audit_file,
    check_uncheckpointed_session,
)


def _checks(findings) -> set[str]:
    return {f.check for f in findings}


def _write_log(rag_dir, sid: str, *, completed: bool = True, extra_events=()) -> None:
    """Write a minimal session_log_<sid>.jsonl. completed => has a session_end."""
    lines = [{"seq": 1, "sid": sid, "event": "session_start"}]
    for i, ev in enumerate(extra_events, start=2):
        lines.append({"seq": i, "sid": sid, "event": ev})
    if completed:
        lines.append({"seq": len(lines) + 1, "sid": sid, "event": "session_end"})
    path = rag_dir / f"session_log_{sid}.jsonl"
    path.write_text(
        "\n".join(json.dumps(d, separators=(",", ":")) for d in lines) + "\n",
        encoding="utf-8",
    )


def _hot(written_by: str | None = "S1", *, status: str = "READY") -> dict:
    meta: dict = {"policy_version": "3.2.5", "last_checkpoint_seq": 1}
    if written_by is not None:
        meta["written_by_session"] = written_by
    return {"meta": meta, "state_machine_status": status}


# --------------------------------------------------------------------------
# the KA-1 gap: a completed session newer than the last checkpoint
# --------------------------------------------------------------------------

def test_completed_log_newer_than_checkpoint_is_error(tmp_path):
    _write_log(tmp_path, "S2", completed=True)  # S2 ran to a clean close
    f = check_uncheckpointed_session(tmp_path, _hot("S1"))  # checkpoint frozen at S1
    assert len(f) == 1
    assert _checks(f) == {"uncheckpointed_session"}
    assert "ran-but-never-checkpointed" in f[0].detail
    assert "S2" in f[0].detail and "S1" in f[0].detail


def test_multiple_newer_completed_logs_each_flagged(tmp_path):
    _write_log(tmp_path, "S2", completed=True)
    _write_log(tmp_path, "S3", completed=True)
    f = check_uncheckpointed_session(tmp_path, _hot("S1"))
    assert len(f) == 2
    assert _checks(f) == {"uncheckpointed_session"}


def test_eday_shape_s0_frozen_s1_completed(tmp_path):
    # the literal eBay signature: checkpoint frozen at S0, S1 ran and closed.
    _write_log(tmp_path, "S0", completed=True)
    _write_log(tmp_path, "S1", completed=True)
    f = check_uncheckpointed_session(tmp_path, _hot("S0"))
    assert len(f) == 1
    assert "S1" in f[0].detail


# --------------------------------------------------------------------------
# no false positives — the in-flight current session is NOT flagged
# --------------------------------------------------------------------------

def test_in_flight_log_no_session_end_self_skips(tmp_path):
    # current session S2 is still running (no session_end): legitimately newer
    # than the last checkpoint until it closes.
    _write_log(tmp_path, "S2", completed=False)
    assert check_uncheckpointed_session(tmp_path, _hot("S1")) == []


def test_crashed_log_with_activity_but_no_end_self_skips(tmp_path):
    _write_log(tmp_path, "S2", completed=False,
               extra_events=("rag_mutation", "checkpoint"))
    assert check_uncheckpointed_session(tmp_path, _hot("S1")) == []


def test_equal_ordinal_is_clean(tmp_path):
    # the healthy case: the newest completed log IS written_by_session.
    _write_log(tmp_path, "S1", completed=True)
    assert check_uncheckpointed_session(tmp_path, _hot("S1")) == []


def test_older_completed_logs_are_clean(tmp_path):
    # historical checkpointed sessions whose logs persist (ordinal < written_by).
    _write_log(tmp_path, "S0", completed=True)
    _write_log(tmp_path, "S1", completed=True)
    assert check_uncheckpointed_session(tmp_path, _hot("S2")) == []


def test_live_kernel_shape_is_clean(tmp_path):
    # S92..S98 all completed, checkpoint at S98 → equal/older only → clean.
    for n in range(92, 99):
        _write_log(tmp_path, f"S{n}", completed=True)
    assert check_uncheckpointed_session(tmp_path, _hot("S98")) == []


# --------------------------------------------------------------------------
# self-skip: BOOTING / un-stamped / malformed / empty
# --------------------------------------------------------------------------

def test_booting_self_skips(tmp_path):
    _write_log(tmp_path, "S2", completed=True)
    assert check_uncheckpointed_session(tmp_path, _hot("S1", status="BOOTING")) == []


def test_missing_written_by_self_skips(tmp_path):
    _write_log(tmp_path, "S2", completed=True)
    assert check_uncheckpointed_session(tmp_path, _hot(None)) == []


def test_malformed_written_by_self_skips(tmp_path):
    # a negative/machine-minted id parses to None — check_session_id_coherence's job.
    _write_log(tmp_path, "S2", completed=True)
    assert check_uncheckpointed_session(tmp_path, _hot("S-12488-1781260490")) == []


def test_malformed_log_filename_id_skipped(tmp_path):
    # session_log_Sxyz.jsonl has no parseable ordinal → skipped, not crashed.
    (tmp_path / "session_log_Sxyz.jsonl").write_text(
        json.dumps({"event": "session_end"}) + "\n", encoding="utf-8")
    assert check_uncheckpointed_session(tmp_path, _hot("S1")) == []


def test_empty_dir_self_skips(tmp_path):
    assert check_uncheckpointed_session(tmp_path, _hot("S1")) == []


def test_nonexistent_dir_self_skips(tmp_path):
    assert check_uncheckpointed_session(tmp_path / "nope", _hot("S1")) == []


def test_non_dict_hot_self_skips(tmp_path):
    assert check_uncheckpointed_session(tmp_path, "not-a-dict") == []
    assert check_uncheckpointed_session(tmp_path, {"meta": "x"}) == []


# --------------------------------------------------------------------------
# the helpers
# --------------------------------------------------------------------------

def test_session_id_int_parses_canonical_ids():
    assert _session_id_int("S98") == 98
    assert _session_id_int("S0") == 0
    assert _session_id_int(" S7 ") == 7


def test_session_id_int_rejects_non_canonical():
    assert _session_id_int("S-12488-1781260490") is None
    assert _session_id_int("Sxyz") is None
    assert _session_id_int("98") is None
    assert _session_id_int("") is None
    assert _session_id_int(None) is None
    assert _session_id_int(98) is None


def test_session_log_completed_detects_end(tmp_path):
    _write_log(tmp_path, "S5", completed=True)
    assert _session_log_completed(tmp_path / "session_log_S5.jsonl") is True


def test_session_log_completed_false_without_end(tmp_path):
    _write_log(tmp_path, "S5", completed=False)
    assert _session_log_completed(tmp_path / "session_log_S5.jsonl") is False


def test_session_log_completed_tolerates_malformed_lines(tmp_path):
    path = tmp_path / "session_log_S5.jsonl"
    path.write_text(
        "not json\n" + json.dumps({"event": "session_end"}) + "\n", encoding="utf-8")
    assert _session_log_completed(path) is True


def test_session_log_completed_missing_file_is_false(tmp_path):
    assert _session_log_completed(tmp_path / "nope.jsonl") is False


# --------------------------------------------------------------------------
# composition into the audit_file boundary gate
# --------------------------------------------------------------------------

def _write_min_rag(rag_dir, written_by: str) -> "object":
    rag_dir.mkdir(parents=True, exist_ok=True)
    p = rag_dir / "RAG_MASTER.json"
    hot = {
        "meta": {"policy_version": "3.2.5", "written_by_session": written_by,
                 "last_checkpoint_seq": 1, "last_updated_utc": "2026-06-21T00:00:00+00:00"},
        "state_machine_status": "READY",
        "operating_protocol": {"tool_hierarchy": "..."},
        "tracked_items": [],
        "open_tasks": [],
        "deferred_items": [],
        "inference_ledger": [],
    }
    p.write_text(json.dumps(hot, indent=2), encoding="utf-8")
    return p


def test_fires_in_audit_file(tmp_path):
    rag_dir = tmp_path / "RAG"
    p = _write_min_rag(rag_dir, "S1")
    _write_log(rag_dir, "S2", completed=True)  # S2 closed but checkpoint frozen at S1
    report = audit_file(p)
    assert not report.ok
    assert "uncheckpointed_session" in _checks(report.errors)


def test_audit_file_clean_when_current_session_checkpointed(tmp_path):
    rag_dir = tmp_path / "RAG"
    p = _write_min_rag(rag_dir, "S2")
    _write_log(rag_dir, "S1", completed=True)  # historical
    _write_log(rag_dir, "S2", completed=True)  # the checkpointing session
    report = audit_file(p)
    assert "uncheckpointed_session" not in _checks(report.findings)


def test_audit_file_clean_when_newer_session_still_running(tmp_path):
    rag_dir = tmp_path / "RAG"
    p = _write_min_rag(rag_dir, "S1")
    _write_log(rag_dir, "S2", completed=False)  # in-flight current session
    report = audit_file(p)
    assert "uncheckpointed_session" not in _checks(report.findings)
