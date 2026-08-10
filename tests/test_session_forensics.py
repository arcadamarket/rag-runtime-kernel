"""SELF-DIAGNOSIS-UNSOURCED (S187 committed it, S188 measures it).

S187 was asked why the seal was taking so long and answered that six illegal
``OPEN -> RESOLVED`` transitions were "the real delay and it's on me". Those six
calls span five seconds. The session ran four hours, 81 percent of it inside five
silent gaps. The explanation was wrong by three orders of magnitude and wrong in
the direction that sounds like taking responsibility.

You cannot mechanically catch a wrong explanation. You can make the true numbers
cost one command and print them at every close, which is what this module does.
These tests pin the arithmetic on a reconstruction of the S187 log — the exact
shape that produced the wrong story — so the numbers cannot quietly change.
"""

from __future__ import annotations

import json

import pytest

from rag_kernel.session_forensics import (
    BURST_MIN_REPEATS,
    CALLER_AGENT,
    CALLER_ENV,
    ForensicsError,
    analyze_file,
    analyze_log,
    render_text,
)


def _rec(seq, ts, event="tool_invocation", verb="audit", rc=0, sid="S187", ms=None,
         caller=None):
    data = {"command": verb, "exit_code": rc}
    if ms is not None:
        data["duration_ms"] = ms
    if caller is not None:
        data["caller"] = caller
    return {"seq": seq, "ts": ts, "sid": sid, "event": event,
            "msg": f"cli {verb}", "data": data}


def _s187_shape():
    """A faithful reduction of session_log_S187.jsonl's timing skeleton."""
    rows = [{"seq": 1, "ts": "2026-08-07T11:31:05+00:00", "sid": "S187",
             "event": "session_start", "msg": "start", "data": {}}]
    rows.append(_rec(2, "2026-08-07T11:33:48+00:00", verb="report"))
    rows.append(_rec(17, "2026-08-07T12:06:10+00:00", verb="render"))
    # the 84-minute silence
    rows.append(_rec(18, "2026-08-07T13:30:38+00:00", verb="prune-current-status"))
    rows.append(_rec(22, "2026-08-07T13:33:00+00:00", verb="audit"))
    # the 40-minute silence
    rows.append(_rec(23, "2026-08-07T14:13:06+00:00", verb="refresh-current-status"))
    # the six failed resolves — five seconds, total
    for i, sec in enumerate(range(18, 24)):
        rows.append(_rec(44 + i, f"2026-08-07T15:21:{sec:02d}+00:00",
                         verb="resolve", rc=1, ms=800))
    rows.append({"seq": 63, "ts": "2026-08-07T15:27:18+00:00", "sid": "S187",
                 "event": "session_end", "msg": "end", "data": {}})
    # ...and eight mutations AFTER that first seal
    for i, verb in enumerate(["un-add", "un-add", "add", "add", "note"]):
        rows.append(_rec(64 + i, f"2026-08-07T15:29:{21 + i:02d}+00:00", verb=verb))
    rows.append({"seq": 72, "ts": "2026-08-07T15:31:02+00:00", "sid": "S187",
                 "event": "session_end", "msg": "end", "data": {}})
    return rows


class TestS187Reconstruction:
    def test_the_stated_cause_is_seconds_and_the_session_is_hours(self):
        f = analyze_log(_s187_shape())
        assert len(f.failures) == 6
        assert all(x["verb"] == "resolve" for x in f.failures)
        assert f.failure_seconds < 10, "the blamed retries cost seconds"
        assert f.wall_seconds > 3 * 3600, "the session cost hours"

    def test_the_gaps_are_where_the_time_went(self):
        f = analyze_log(_s187_shape())
        assert len(f.gaps) >= 2
        biggest = max(g["seconds"] for g in f.gaps)
        assert biggest > 80 * 60, "the 84-minute silence must be visible"
        assert f.gap_share > 0.5

    def test_the_double_seal_is_detected(self):
        f = analyze_log(_s187_shape())
        assert f.double_sealed is True
        assert len(f.session_ends) == 2
        assert f.mutations_after_first_end == ["un-add", "un-add", "add", "add", "note"]

    def test_render_names_the_gaps_as_the_answer(self):
        out = render_text(analyze_log(_s187_shape()))
        assert "silent gaps" in out
        assert "THIS is where the time went" in out
        assert "DOUBLE SEAL" in out


class TestExitCodeShapes:
    """The logger has written exit codes two ways; both must be legible.

    Reading only the int form is how the first cut of this module reported
    "failed calls: none" against the real S187 log, whose bootstrap records carry
    ``status: "exit 1"``. A forensics tool that cannot see failures produces a
    confident wrong answer — the exact defect it exists to catch.
    """

    def test_int_exit_code(self):
        rows = [{"seq": 1, "ts": "2026-01-01T00:00:00+00:00", "sid": "S1",
                 "event": "tool_invocation", "msg": "cli resolve",
                 "data": {"command": "resolve", "exit_code": 1}}]
        assert len(analyze_log(rows).failures) == 1

    def test_rendered_status_string(self):
        rows = [{"seq": 1, "ts": "2026-01-01T00:00:00+00:00", "sid": "S1",
                 "event": "tool_invocation", "msg": "cli resolve",
                 "data": {"command": "cli", "status": "exit 1"}}]
        f = analyze_log(rows)
        assert len(f.failures) == 1
        assert f.failures[0]["verb"] == "resolve", "verb falls back to the message"

    def test_rendered_status_zero_is_not_a_failure(self):
        rows = [{"seq": 1, "ts": "2026-01-01T00:00:00+00:00", "sid": "S1",
                 "event": "tool_invocation", "msg": "cli audit",
                 "data": {"command": "cli", "status": "exit 0"}}]
        assert analyze_log(rows).failures == []

    def test_unknown_shape_is_not_guessed_into_a_failure(self):
        rows = [{"seq": 1, "ts": "2026-01-01T00:00:00+00:00", "sid": "S1",
                 "event": "tool_invocation", "msg": "cli audit",
                 "data": {"command": "audit", "status": "probably fine"}}]
        assert analyze_log(rows).failures == []


class TestGaps:
    def test_short_pauses_are_not_gaps(self):
        rows = [_rec(1, "2026-01-01T00:00:00+00:00"),
                _rec(2, "2026-01-01T00:05:00+00:00")]
        assert analyze_log(rows).gaps == []

    def test_gap_share_is_zero_when_wall_time_is_unknown(self):
        assert analyze_log([]).gap_share == 0.0


class TestBursts:
    def test_repeated_verb_in_a_tight_window_is_a_burst(self):
        rows = [_rec(i, f"2026-01-01T00:00:{i:02d}+00:00", verb="items")
                for i in range(1, BURST_MIN_REPEATS + 2)]
        f = analyze_log(rows)
        assert f.bursts and f.bursts[0]["verb"] == "items"

    def test_wait_for_is_exempt_because_it_is_the_anti_poll_primitive(self):
        rows = [_rec(i, f"2026-01-01T00:00:{i:02d}+00:00", verb="wait-for")
                for i in range(1, BURST_MIN_REPEATS + 3)]
        assert analyze_log(rows).bursts == []

    def test_spaced_out_repeats_are_not_a_burst(self):
        rows = [_rec(i, f"2026-01-01T{i:02d}:00:00+00:00", verb="items")
                for i in range(1, BURST_MIN_REPEATS + 2)]
        assert analyze_log(rows).bursts == []


class TestRobustness:
    def test_empty_log_is_total_not_fatal(self):
        f = analyze_log([])
        assert f.invocations == 0 and f.wall_seconds == 0.0

    def test_unparseable_timestamps_are_skipped(self):
        rows = [_rec(1, "not-a-time"), _rec(2, "2026-01-01T00:00:00+00:00")]
        assert analyze_log(rows).records == 2

    def test_torn_last_line_does_not_lose_the_session(self, tmp_path):
        p = tmp_path / "session_log_S1.jsonl"
        p.write_text(
            json.dumps(_rec(1, "2026-01-01T00:00:00+00:00")) + "\n{\"seq\": 2, ",
            encoding="utf-8",
        )
        assert analyze_file(p).records == 1

    def test_missing_file_fails_loud(self, tmp_path):
        with pytest.raises(ForensicsError):
            analyze_file(tmp_path / "nope.jsonl")

    def test_file_with_no_parseable_records_fails_loud(self, tmp_path):
        p = tmp_path / "session_log_S1.jsonl"
        p.write_text("garbage\nmore garbage\n", encoding="utf-8")
        with pytest.raises(ForensicsError):
            analyze_file(p)


class TestCleanSession:
    def test_a_clean_session_reports_no_signal_rather_than_inventing_one(self):
        rows = [
            {"seq": 1, "ts": "2026-01-01T00:00:00+00:00", "sid": "S1",
             "event": "session_start", "msg": "s", "data": {}},
            _rec(2, "2026-01-01T00:01:00+00:00", verb="audit"),
            {"seq": 3, "ts": "2026-01-01T00:02:00+00:00", "sid": "S1",
             "event": "session_end", "msg": "e", "data": {}},
        ]
        f = analyze_log(rows)
        assert (f.failures, f.gaps, f.bursts) == ([], [], [])
        assert f.double_sealed is False
        out = render_text(f)
        assert "failed calls     : none" in out
        assert "SEALS            : 1 (clean)" in out


class TestCallerAttribution:
    """FORENSICS-CALLER-ATTRIBUTION (S191, E-111).

    The grand auditor drives the kernel through the same CLI the agent uses. In
    S190 its own probes — gc x4, audit x8 — were logged indistinguishably from
    agent calls, so a session whose agent had done nothing wrong closed with two
    polling bursts and eight failures against its name. A conduct gate fed that
    input does not teach discipline; it teaches the agent to declare its way
    past findings that were never its own.
    """

    def _burst(self, verb, caller=None, base=0):
        n = BURST_MIN_REPEATS + 1
        return [_rec(base + i, "2026-01-01T00:00:%02d+00:00" % i,
                     verb=verb, caller=caller) for i in range(n)]

    def test_auditor_burst_is_not_charged_to_the_agent(self):
        f = analyze_log(self._burst("audit", caller="auditor"))
        assert f.bursts == []
        assert f.machine_invocations == BURST_MIN_REPEATS + 1
        assert f.machine_callers == {"auditor": BURST_MIN_REPEATS + 1}

    def test_the_agents_own_burst_is_still_caught(self):
        # The exemption is by declared caller, never by verb name: the same
        # verb, unstamped, remains a violation.
        f = analyze_log(self._burst("audit"))
        assert len(f.bursts) == 1
        assert f.machine_invocations == 0

    def test_auditor_failures_are_reported_but_not_agent_failures(self):
        rows = [_rec(1, "2026-01-01T00:00:00+00:00", verb="gc", rc=2,
                     caller="auditor"),
                _rec(2, "2026-01-01T00:00:05+00:00", verb="resolve", rc=1)]
        f = analyze_log(rows)
        assert [x["verb"] for x in f.failures] == ["resolve"]
        assert [x["verb"] for x in f.machine_failures] == ["gc"]
        out = render_text(f)
        assert "of which machine : 1" in out

    def test_an_unstamped_record_defaults_to_the_agent(self):
        # A forensics tool that defaulted the other way could be silenced by
        # simply not writing the field.
        f = analyze_log([_rec(1, "2026-01-01T00:00:00+00:00", verb="gc", rc=3)])
        assert len(f.failures) == 1
        assert f.machine_invocations == 0

    def test_the_env_var_the_kernel_stamps_from_is_the_one_tools_set(self):
        assert (CALLER_ENV, CALLER_AGENT) == ("RAG_KERNEL_CALLER", "agent")
