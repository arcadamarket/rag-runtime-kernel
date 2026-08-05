"""S186 -- SEAL-REPORT-STALE-SURFACE + SEAL-BOOTMAP-ORDER-GAP.

Two deployments sealed a false transfer surface on the same day: the parent S185
report named seq 284 while the live RAG was at 285, and the child S6 report named
a HEAD that was already superseded. Same defect, no shared code path, so it is a
defect in the ritual ORDER rather than a local slip.
"""
import json

from rag_kernel.__main__ import _report_state_drift


def _rag(tmp_path, seq):
    p = tmp_path / "RAG_MASTER.json"
    p.write_text(json.dumps({"meta": {"last_checkpoint_seq": seq}}), encoding="utf-8")
    return p


def test_report_matching_live_seq_is_not_drift(tmp_path):
    p = _rag(tmp_path, 285)
    assert _report_state_drift(p, "- RAG: seq 285 mid-line noise") is None


def test_the_s185_surface_is_caught(tmp_path):
    """The exact artifact S185 sealed: report says 284, live RAG is 285."""
    p = _rag(tmp_path, 285)
    msg = _report_state_drift(p, "- RAG: seq 284 written_by S185")
    assert msg and "284" in msg and "285" in msg


def test_absent_report_is_not_drift(tmp_path):
    """No surface to contradict is not the same as a contradicted surface."""
    assert _report_state_drift(_rag(tmp_path, 1), None) is None


def test_unparseable_rag_does_not_manufacture_drift(tmp_path):
    bad = tmp_path / "RAG_MASTER.json"
    bad.write_text("{not json", encoding="utf-8")
    assert _report_state_drift(bad, "seq 7") is None
