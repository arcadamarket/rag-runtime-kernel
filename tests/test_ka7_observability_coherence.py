"""KA-7: fail-loud when meta advanced past the newest session-log trail.

The eBay governance freeze had a second silent signature beside KA-1: the
per-session logs stopped at S1 while ``meta.written_by_session`` kept advancing
across later sessions — sessions ran and checkpointed but opened no logger, so no
``session_log_S<N>.jsonl`` trail was ever written, and ``audit --strict`` still
reported 0 findings. ``check_observability_coherence`` (KA-7, part of the KA-10
GOVERNANCE-DETERMINISM arc) closes that gap.

Signal: among the session logs beside the RAG that hold at least one entry, the
greatest ordinal is ``max_logged``; the check fires iff ``meta.written_by_session``
is STRICTLY GREATER than ``max_logged`` (meta advanced beyond where logging
stopped). KA-1 fires only when a log is newer than the checkpoint and KA-7 only
when the checkpoint is newer than every log, so the two are mutually exclusive and
never double-report.

These tests pin the invariant, prove the eBay shape, prove an empty / entry-less
log does not count as a trail, prove the equal-ordinal and newer-log (KA-1's lane)
cases are clean, prove mutual exclusivity with KA-1, prove it self-skips a
BOOTING / un-stamped / malformed-id RAG and a no-logger project, and prove it
composes into the ``audit_file`` boundary gate.
"""

from __future__ import annotations

import json

from rag_kernel.drift_audit import (
    _session_log_has_entries,
    audit_file,
    check_observability_coherence,
    check_uncheckpointed_session,
)


def _checks(findings) -> set[str]:
    return {f.check for f in findings}


def _write_log(rag_dir, sid: str, *, extra_events=()) -> None:
    """Write a minimal session_log_<sid>.jsonl carrying >=1 entry (a real trail)."""
    lines = [{"seq": 1, "sid": sid, "event": "session_start"}]
    for i, ev in enumerate(extra_events, start=2):
        lines.append({"seq": i, "sid": sid, "event": ev})
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
# the KA-7 gap: the checkpoint advanced past the newest observability trail
# --------------------------------------------------------------------------

def test_meta_advanced_past_newest_log_is_error(tmp_path):
    _write_log(tmp_path, "S0")
    _write_log(tmp_path, "S1")  # logging stopped at S1
    f = check_observability_coherence(tmp_path, _hot("S5"))  # meta advanced to S5
    assert len(f) == 1
    assert _checks(f) == {"observability_coherence"}
    assert "meta advanced but" in f[0].detail
    assert "S5" in f[0].detail and "S1" in f[0].detail


def test_ebay_shape_logs_stop_at_s1_meta_advances(tmp_path):
    # the literal eBay signature: logs present through S1, checkpoint sits at S3.
    _write_log(tmp_path, "S0")
    _write_log(tmp_path, "S1")
    f = check_observability_coherence(tmp_path, _hot("S3"))
    assert len(f) == 1
    assert "session_log_S1.jsonl" in f[0].detail


def test_one_step_ahead_is_error(tmp_path):
    # written_by S2 but the trail's newest is S1 — the current checkpoint left no log.
    _write_log(tmp_path, "S1")
    f = check_observability_coherence(tmp_path, _hot("S2"))
    assert len(f) == 1
    assert _checks(f) == {"observability_coherence"}


def test_empty_current_log_does_not_count_as_trail(tmp_path):
    # S5's log exists but is empty (logger touched, recorded nothing): not a trail,
    # so the newest REAL trail is S1 and meta@S5 advanced past it → error.
    _write_log(tmp_path, "S1")
    (tmp_path / "session_log_S5.jsonl").write_text("   \n", encoding="utf-8")
    f = check_observability_coherence(tmp_path, _hot("S5"))
    assert len(f) == 1
    assert "session_log_S1.jsonl" in f[0].detail


# --------------------------------------------------------------------------
# no false positives — the trail kept pace, or it's KA-1's lane
# --------------------------------------------------------------------------

def test_equal_ordinal_is_clean(tmp_path):
    # the healthy case: the current checkpoint session left its own log.
    _write_log(tmp_path, "S5")
    assert check_observability_coherence(tmp_path, _hot("S5")) == []


def test_newer_log_than_checkpoint_is_clean(tmp_path):
    # a log newer than the checkpoint is KA-1's lane, not KA-7's.
    _write_log(tmp_path, "S6")
    assert check_observability_coherence(tmp_path, _hot("S5")) == []


def test_live_kernel_shape_is_clean(tmp_path):
    # S92..S100 all logged, checkpoint at S100 → equal/older only → clean.
    for n in range(92, 101):
        _write_log(tmp_path, f"S{n}")
    assert check_observability_coherence(tmp_path, _hot("S100")) == []


def test_in_flight_session_pre_checkpoint_is_clean(tmp_path):
    # S100 logger open (has entries) but checkpoint still at S99 → 99 <= 100 → clean.
    _write_log(tmp_path, "S99")
    _write_log(tmp_path, "S100")
    assert check_observability_coherence(tmp_path, _hot("S99")) == []


# --------------------------------------------------------------------------
# mutual exclusivity with KA-1 (the two never double-report)
# --------------------------------------------------------------------------

def test_ka7_fires_ka1_silent_on_meta_ahead(tmp_path):
    _write_log(tmp_path, "S1")
    hot = _hot("S4")
    assert len(check_observability_coherence(tmp_path, hot)) == 1
    assert check_uncheckpointed_session(tmp_path, hot) == []


def test_ka1_fires_ka7_silent_on_log_ahead(tmp_path):
    # a completed log newer than the checkpoint: KA-1's signature, KA-7 stays quiet.
    _write_log(tmp_path, "S2", extra_events=("session_end",))  # completed close
    hot = _hot("S1")
    assert len(check_uncheckpointed_session(tmp_path, hot)) == 1
    assert check_observability_coherence(tmp_path, hot) == []


# --------------------------------------------------------------------------
# self-skip: no logger / BOOTING / un-stamped / malformed / empty
# --------------------------------------------------------------------------

def test_no_logs_self_skips(tmp_path):
    # a project not using the session logger: nothing to be coherent with.
    assert check_observability_coherence(tmp_path, _hot("S5")) == []


def test_only_entryless_logs_self_skips(tmp_path):
    (tmp_path / "session_log_S1.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "session_log_S2.jsonl").write_text("  \n\n", encoding="utf-8")
    assert check_observability_coherence(tmp_path, _hot("S5")) == []


def test_booting_self_skips(tmp_path):
    _write_log(tmp_path, "S1")
    assert check_observability_coherence(tmp_path, _hot("S5", status="BOOTING")) == []


def test_missing_written_by_self_skips(tmp_path):
    _write_log(tmp_path, "S1")
    assert check_observability_coherence(tmp_path, _hot(None)) == []


def test_malformed_written_by_self_skips(tmp_path):
    _write_log(tmp_path, "S1")
    assert check_observability_coherence(tmp_path, _hot("S-12488-1781260490")) == []


def test_malformed_log_filename_id_skipped(tmp_path):
    # session_log_Sxyz.jsonl has no parseable ordinal → not a counted trail; with no
    # other logs the dir has no usable trail → self-skip.
    (tmp_path / "session_log_Sxyz.jsonl").write_text(
        json.dumps({"event": "session_start"}) + "\n", encoding="utf-8")
    assert check_observability_coherence(tmp_path, _hot("S5")) == []


def test_nonexistent_dir_self_skips(tmp_path):
    assert check_observability_coherence(tmp_path / "nope", _hot("S5")) == []


def test_non_dict_hot_self_skips(tmp_path):
    assert check_observability_coherence(tmp_path, "not-a-dict") == []
    assert check_observability_coherence(tmp_path, {"meta": "x"}) == []


# --------------------------------------------------------------------------
# the helper
# --------------------------------------------------------------------------

def test_session_log_has_entries_true(tmp_path):
    _write_log(tmp_path, "S5")
    assert _session_log_has_entries(tmp_path / "session_log_S5.jsonl") is True


def test_session_log_has_entries_false_when_empty(tmp_path):
    p = tmp_path / "session_log_S5.jsonl"
    p.write_text("   \n\n", encoding="utf-8")
    assert _session_log_has_entries(p) is False


def test_session_log_has_entries_tolerates_malformed_lines(tmp_path):
    p = tmp_path / "session_log_S5.jsonl"
    p.write_text("not json\n" + json.dumps({"event": "x"}) + "\n", encoding="utf-8")
    assert _session_log_has_entries(p) is True


def test_session_log_has_entries_missing_file_is_false(tmp_path):
    assert _session_log_has_entries(tmp_path / "nope.jsonl") is False


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
    p = _write_min_rag(rag_dir, "S5")
    _write_log(rag_dir, "S1")  # logging stopped at S1, checkpoint advanced to S5
    report = audit_file(p)
    assert not report.ok
    assert "observability_coherence" in _checks(report.errors)


def test_audit_file_clean_when_current_session_logged(tmp_path):
    rag_dir = tmp_path / "RAG"
    p = _write_min_rag(rag_dir, "S2")
    _write_log(rag_dir, "S1")  # historical
    _write_log(rag_dir, "S2")  # the checkpointing session left its trail
    report = audit_file(p)
    assert "observability_coherence" not in _checks(report.findings)


def test_audit_file_clean_when_no_logger(tmp_path):
    rag_dir = tmp_path / "RAG"
    p = _write_min_rag(rag_dir, "S5")  # no session logs at all → self-skip
    report = audit_file(p)
    assert "observability_coherence" not in _checks(report.findings)
