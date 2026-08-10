"""FORENSICS-AS-GATE (S190, P1-A) — conduct facts must be able to refuse a seal.

``session_forensics`` has computed the right numbers since S188. It was called at
the close under the comment *"Advisory: forensics must never strand a seal"* and
``# observability, never a blocker``, AFTER the transfer marker was already
written. S188 therefore sealed GREEN while its own forensics printed two polling
bursts and 1h09 of silence, and S189 sealed with 3 bursts, 22 failed governed
calls and 5 silent gaps. The evidence was produced every session and discarded
every session — the single highest-value defect in the S189 grand audit.

This suite pins the repair:

* :func:`session_forensics.conduct_findings` names each blocking condition;
* the real S188/S189 logs, replayed, produce findings (the historical proof);
* a clean log produces none, so the gate cannot become an outage;
* ``--accept-conduct REASON`` exists on both close verbs and is recorded in the
  close marker, because an override without a reason is what advisory was;
* the close no longer carries the advisory exemption in its source.
"""

from __future__ import annotations

import inspect
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from rag_kernel import session_forensics as sf
from rag_kernel.__main__ import _build_close_marker, _drive_close, build_parser

T0 = datetime(2026, 8, 9, 12, 0, 0)


def _rec(seq, offset_s, verb, rc=0, event="tool_invocation", duration_ms=1000,
         error_type=None):
    data = {"command": verb, "rc": rc, "duration_ms": duration_ms}
    if error_type is not None:
        data["error_type"] = error_type
    return {
        "seq": seq,
        "sid": "STEST",
        "ts": (T0 + timedelta(seconds=offset_s)).isoformat(),
        "event": event,
        "data": data,
    }


def _clean_log():
    return [_rec(i, i * 30, v) for i, v in enumerate(("audit", "items", "report", "note"))]


class TestConductFindings:
    def test_a_clean_session_produces_no_findings(self):
        assert sf.conduct_findings(sf.analyze_log(_clean_log())) == []

    def test_a_polling_burst_is_a_finding(self):
        """Four `audit` calls inside BURST_SECONDS — the S189 profile exactly."""
        log = [_rec(i, i * 10, "audit") for i in range(sf.BURST_MIN_REPEATS)]
        found = sf.conduct_findings(sf.analyze_log(log))
        assert any("repeat burst" in f for f in found), found

    def test_a_failed_governed_call_is_a_finding(self):
        # S191 (E-118): a non-zero exit is only a FAILURE when something
        # actually broke. `audit` returning 1 because it found findings is a
        # guard working and no longer a conduct finding — so the call that
        # must still be caught here is one that raised.
        log = _clean_log() + [_rec(90, 200, "checkpoint", rc=1, error_type="OSError")]
        found = sf.conduct_findings(sf.analyze_log(log))
        assert any("failed governed call" in f for f in found), found

    def test_a_guard_refusing_is_no_longer_a_conduct_finding(self):
        log = _clean_log() + [_rec(90, 200, "resolve", rc=1)]
        found = sf.conduct_findings(sf.analyze_log(log))
        assert not any("failed governed call" in f for f in found), found

    def test_gaps_within_the_allowance_pass(self):
        log = [_rec(i, i * (sf.GAP_SECONDS + 60), "items") for i in range(sf.GAP_ALLOWANCE + 1)]
        # GAP_ALLOWANCE gaps between GAP_ALLOWANCE+1 records: at the limit, not over.
        assert not any("silent gaps" in f for f in sf.conduct_findings(sf.analyze_log(log)))

    def test_excess_silent_gaps_are_a_finding(self):
        log = [_rec(i, i * (sf.GAP_SECONDS + 60), "items") for i in range(sf.GAP_ALLOWANCE + 3)]
        found = sf.conduct_findings(sf.analyze_log(log))
        assert any("silent gaps" in f for f in found), found

    def test_a_double_seal_is_a_finding(self):
        log = _clean_log() + [
            _rec(80, 300, "session-end", event="session_end"),
            _rec(81, 400, "session-end", event="session_end"),
        ]
        found = sf.conduct_findings(sf.analyze_log(log))
        assert any("double seal" in f for f in found), found

    def test_findings_are_strings_a_human_can_act_on(self):
        log = [_rec(i, i * 10, "report", rc=2) for i in range(sf.BURST_MIN_REPEATS)]
        for f in sf.conduct_findings(sf.analyze_log(log)):
            assert isinstance(f, str) and len(f) > 20


def _repo_log(sid: str) -> Path | None:
    """Locate a real session log without hard-coding the checkout layout."""
    for base in Path(__file__).resolve().parents:
        for cand in (base / "RAG" / f"session_log_{sid}.jsonl",
                     base / f"session_log_{sid}.jsonl"):
            if cand.exists():
                return cand
    return None


class TestHistoricalReplay:
    """The sessions that sealed green are the regression corpus."""

    @pytest.mark.parametrize("sid", ("S188", "S189"))
    def test_the_sessions_that_sealed_green_would_now_be_refused(self, sid):
        log = _repo_log(sid)
        if log is None:
            pytest.skip(f"{sid} log not present in this checkout")
        found = sf.conduct_findings(sf.analyze_file(log))
        assert found, (
            f"{sid} sealed GREEN with bursts/failures/gaps in its own forensics; "
            "the gate must refuse it"
        )


class TestGateWiring:
    """Existence is not wiring (grand audit axis 10). These assert the wiring."""

    def test_accept_conduct_is_a_close_flag_on_both_verbs(self):
        parser = build_parser()
        cases = {
            "session-end": ["--rag", "x.json", "--session", "S190", "--summary", "s"],
            "session-resume": ["--rag", "x.json"],
        }
        for verb, base in cases.items():
            ns = parser.parse_args([verb, *base, "--accept-conduct", "why"])
            assert ns.accept_conduct == "why", verb
            assert parser.parse_args([verb, *base]).accept_conduct is None, verb

    def test_accept_conduct_requires_a_reason(self):
        """No boolean form: an override without a reason is what advisory was."""
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["session-end", "--rag", "x.json", "--session", "S190",
                               "--summary", "s", "--accept-conduct"])

    def test_the_close_marker_carries_the_conduct_record(self):
        m = _build_close_marker(
            "S190", "COMPLETE", {}, "t0", "t1", transfer_ready=True,
            conduct=["3 repeat burst(s)"], conduct_measured=True,
            conduct_accepted="operator accepted",
        )
        assert m["conduct"]["measured"] is True
        assert m["conduct"]["findings"] == ["3 repeat burst(s)"]
        assert m["conduct"]["accepted_reason"] == "operator accepted"

    def test_a_refusal_marker_is_resumable_not_complete(self):
        m = _build_close_marker("S190", "SURFACE_PENDING", {}, "t0", None,
                                conduct=["x"], conduct_measured=True)
        assert m["transfer_ready"] is False
        assert m["conduct"]["accepted_reason"] is None

    def test_the_advisory_exemption_is_gone_from_the_close(self):
        """The historical phrasing may survive as a citation; the CODE may not.

        Pinned on the exception handler that made it true: forensics used to be
        caught with ``# noqa: BLE001 - observability, never a blocker`` and the
        close carried on regardless.
        """
        src = inspect.getsource(_drive_close)
        assert "BLE001 — observability, never a blocker" not in src
        assert "BLE001 - observability, never a blocker" not in src
        assert "FORENSICS-AS-GATE" in src
        assert "_conduct and not _accept_conduct" in src, "the refusal branch itself"

    def test_the_bootmap_reseal_is_no_longer_advisory(self):
        """BOOTMAP-ADVISORY-ENDED (S190): the last advisory-only governance step.

        A close whose domain map did not reseal hands the successor a coverage
        gap over every file the session touched — the exact defect the step
        exists to prevent. It refuses now, resumably.
        """
        src = inspect.getsource(_drive_close)
        # The old sentence may survive as a CITATION (it explains the defect);
        # what may not survive is the branch that acted on it.
        assert "WARN: could not reseal domain boot-map" not in src
        assert "SEAL-BOOTMAP-ORDER-GAP — could not reseal" in src
        assert "transfer_ready WITHDRAWN" in src

    def test_the_gate_runs_before_the_transfer_marker(self):
        """Order is the whole defect: S188's forensics printed after the seal."""
        src = inspect.getsource(_drive_close)
        gate = src.index("FORENSICS-AS-GATE")
        complete = src.index('"COMPLETE", steps, started, _utcnow_iso()')
        assert gate < complete, "conduct must be judged before transfer_ready is set"
