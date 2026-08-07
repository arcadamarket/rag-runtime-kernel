"""REPORT-TESTS-GATE-UNMEASURED (S186 opened, S188 closed).

The S188 forensic audit of S178–S187 found the Tests cell of the canonical report was
never measured by the seal: S184 and S185 sealed ``n/a``, S186 sealed ``2294``, S187
sealed ``2,409`` — every one of them a number the AGENT typed into ``--tests``. The
count was true in S187's case; that it happened to be true is exactly the problem,
because nothing in the seal could tell a true one from a false one.

These tests pin the three properties that make the gate honest:

  1. the count comes from parsing a real run;
  2. the stamp records WHAT IT WAS MEASURED AGAINST, so it can go stale; and
  3. the verdict is TRI-STATE — unmeasured and stale both refuse to read as pass,
     without lying in the other direction and reading as fail.
"""

from __future__ import annotations

import json

import pytest

from rag_kernel import test_gate
from rag_kernel.test_gate import (
    TestGateError,
    parse_pytest_summary,
    read_stamp,
    resolve_repo_root,
    set_test_gate_file,
    verdict,
)


# --------------------------------------------------------------------------
# the parser — pure, and pinned against real pytest output shapes
# --------------------------------------------------------------------------

class TestParsePytestSummary:
    def test_all_green(self):
        r = parse_pytest_summary("2409 passed in 168.98s (0:02:48)")
        assert r["passed"] == 2409
        assert r["failed"] == 0
        assert r["collected"] == 2409
        assert r["duration_s"] == 168.98
        assert r["no_tests_ran"] is False

    def test_mixed_failures(self):
        """The exact S187 scrollback line that triggered the S188 audit."""
        r = parse_pytest_summary("4 failed, 2294 passed in 233.30s (0:03:53)")
        assert (r["passed"], r["failed"]) == (2294, 4)
        assert r["collected"] == 2298

    def test_skips_and_xfails_counted_into_collected(self):
        r = parse_pytest_summary(
            "1 failed, 2 passed, 3 skipped, 1 xfailed, 2 warnings in 4.20s"
        )
        assert (r["passed"], r["failed"], r["skipped"], r["xfailed"]) == (2, 1, 3, 1)
        assert r["collected"] == 7, "warnings are not tests and must not be counted"

    def test_errors_are_counted(self):
        r = parse_pytest_summary("2 errors in 1.00s")
        assert r["error"] == 2

    def test_reads_the_last_summary_not_the_progress_above_it(self):
        text = (
            "........................................ [ 50%]\n"
            "FAILED tests/test_x.py::test_y - AssertionError\n"
            "1 failed, 9 passed in 2.00s\n"
        )
        r = parse_pytest_summary(text)
        assert (r["passed"], r["failed"]) == (9, 1)

    def test_no_tests_ran_is_not_a_pass(self):
        """The RAG/ copy has no tests/ dir; this must never read as green."""
        r = parse_pytest_summary("no tests ran in 0.83s")
        assert r["no_tests_ran"] is True
        assert r["collected"] == 0

    def test_empty_output_fails_loud(self):
        with pytest.raises(TestGateError):
            parse_pytest_summary("")

    def test_unparseable_output_fails_loud(self):
        with pytest.raises(TestGateError):
            parse_pytest_summary("ImportError: cannot import name 'x'")


# --------------------------------------------------------------------------
# the verdict — tri-state
# --------------------------------------------------------------------------

def _stamp(**kw):
    base = {
        "passed": 2409, "failed": 0, "skipped": 0, "collected": 2409,
        "exit_code": 0, "session": "S188", "runtime": "0.4.54",
        "git_head": "baf7d7a1111", "measured_utc": "2026-08-07T16:00:00Z",
    }
    base.update(kw)
    return base


class TestVerdict:
    def test_unmeasured_is_none_not_false(self):
        ok, cell, _ = verdict(None)
        assert ok is None
        assert "UNMEASURED" in cell

    def test_fresh_green_is_true(self):
        ok, cell, _ = verdict(_stamp(), live_head="baf7d7a1111", live_runtime="0.4.54")
        assert ok is True
        assert "2,409 green" in cell and "S188" in cell

    def test_red_is_false(self):
        ok, cell, _ = verdict(
            _stamp(passed=2405, failed=4), live_head="baf7d7a1111", live_runtime="0.4.54"
        )
        assert ok is False
        assert "FAILED" in cell

    def test_moved_head_makes_a_green_stamp_stale(self):
        """The whole point: a cached pass must decay when the code moves."""
        ok, cell, _ = verdict(_stamp(), live_head="deadbeef999", live_runtime="0.4.54")
        assert ok is None
        assert "STALE" in cell

    def test_moved_runtime_makes_a_green_stamp_stale(self):
        ok, cell, _ = verdict(_stamp(), live_head="baf7d7a1111", live_runtime="0.4.99")
        assert ok is None
        assert "STALE" in cell

    def test_red_stamp_stays_red_even_when_stale(self):
        ok, _, _ = verdict(
            _stamp(failed=4), live_head="deadbeef999", live_runtime="0.4.54"
        )
        assert ok is False, "a known failure must not be softened into 'unknown'"

    def test_zero_collected_is_not_green(self):
        ok, cell, _ = verdict(
            _stamp(passed=0, collected=0), live_head="baf7d7a1111", live_runtime="0.4.54"
        )
        assert ok is None
        assert "NO TESTS COLLECTED" in cell

    def test_no_live_facts_cannot_prove_staleness(self):
        ok, _, _ = verdict(_stamp())
        assert ok is True


# --------------------------------------------------------------------------
# the stamp
# --------------------------------------------------------------------------

@pytest.fixture()
def rag(tmp_path):
    p = tmp_path / "RAG_MASTER.json"
    p.write_text(json.dumps({
        "tracked_items": [],
        "meta": {"last_updated_utc": "2020-01-01T00:00:00Z",
                 "reconciliation_docs_root": "repo"},
    }), encoding="utf-8")
    return p


class TestStamp:
    def test_round_trip(self, rag):
        result = parse_pytest_summary("2409 passed in 168.98s")
        result["exit_code"] = 0
        stamp, wrote = set_test_gate_file(
            rag, result, session="S188", runtime="0.4.54", git_head="baf7d7a1111"
        )
        assert wrote is True
        stored = read_stamp(json.loads(rag.read_text()))
        assert stored["passed"] == 2409
        assert stored["git_head"] == "baf7d7a1111"
        assert stored["runtime"] == "0.4.54"
        assert stored["session"] == "S188"

    def test_stamp_records_provenance_not_just_a_number(self, rag):
        """A count without provenance is the defect, not the fix."""
        result = parse_pytest_summary("10 passed in 1.0s")
        result["exit_code"] = 0
        stamp, _ = set_test_gate_file(
            rag, result, session="S188", runtime="0.4.54", git_head="abc1234"
        )
        for key in ("measured_utc", "session", "runtime", "git_head"):
            assert stamp.get(key), f"stamp must carry {key}"

    def test_dry_run_writes_nothing(self, rag):
        before = rag.read_bytes()
        result = parse_pytest_summary("10 passed in 1.0s")
        result["exit_code"] = 0
        _, wrote = set_test_gate_file(rag, result, session="S188", dry_run=True)
        assert wrote is False
        assert rag.read_bytes() == before

    def test_session_required(self, rag):
        result = parse_pytest_summary("10 passed in 1.0s")
        result["exit_code"] = 0
        with pytest.raises(TestGateError):
            set_test_gate_file(rag, result, session="")

    def test_read_stamp_absent(self):
        assert read_stamp({"meta": {}}) is None
        assert read_stamp({}) is None


class TestResolveRepoRoot:
    def test_uses_declared_reconciliation_docs_root(self, tmp_path):
        proj = tmp_path / "proj"
        (proj / "RAG").mkdir(parents=True)
        repo = proj / "repo"
        (repo / "tests").mkdir(parents=True)
        (repo / "rag_kernel").mkdir()
        rag = proj / "RAG" / "RAG_MASTER.json"
        rag.write_text(json.dumps({
            "meta": {"reconciliation_docs_root": "repo"}, "tracked_items": []
        }), encoding="utf-8")
        assert resolve_repo_root(rag) == repo.resolve()

    def test_fails_loud_when_no_suite_anywhere(self, tmp_path):
        proj = tmp_path / "proj"
        (proj / "RAG").mkdir(parents=True)
        rag = proj / "RAG" / "RAG_MASTER.json"
        rag.write_text(json.dumps({
            "meta": {"reconciliation_docs_root": "nope"}, "tracked_items": []
        }), encoding="utf-8")
        with pytest.raises(TestGateError):
            resolve_repo_root(rag)
