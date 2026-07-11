"""Unit tests for the REPORT-VERB deterministic status report (S136).

Covers the pure renderer ``drift_render.render_status_report`` and the
``report`` CLI verb. Rule 12 requires the transfer/close status report to be a
DETERMINISTIC RENDER of the RAG canonical fields, never hand-authored. Properties
asserted here:

- all 7 canonical sections render, in order, under the mandated heading;
- objective R/A/G thresholds (GREEN only when released AND every gate green;
  AMBER when unreleased or a gate is unknown; RED when a hard gate fails) — an
  unknown fact can NEVER yield a false GREEN (increment-status-honesty, Rule 14);
- the backlog is FULLY ENUMERATED line-by-line (STRICT-OBEY), never a bare count;
- unknown external scalars render ``n/a``, never blank or invented;
- determinism: identical inputs -> byte-identical output;
- the CLI verb renders read-only from a RAG file and never mutates it.
"""

from __future__ import annotations

import json

import pytest

from rag_kernel.drift_control import ItemKind, ItemStatus, TrackedItem
from rag_kernel.drift_store import TRACKED_ITEMS_KEY, TrackedItemStore
from rag_kernel.drift_render import (
    DRIFT_RENDER_VERSION,
    _overall_rag,
    render_status_report,
)
from rag_kernel.__main__ import main


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _item(item_id, status, **kw):
    base = dict(id=item_id, title=f"title for {item_id}", status=status)
    base.update(kw)
    return TrackedItem(**base)


def _store():
    return TrackedItemStore([
        _item("A-OPEN", ItemStatus.OPEN, session="S40"),
        _item("B-PROG", ItemStatus.IN_PROGRESS, session="S136", note="building"),
        _item("C-DEF", ItemStatus.DEFERRED, session="S46"),
        _item("D-DONE", ItemStatus.RESOLVED, session="S37"),
        _item("E-GATE", ItemStatus.OPEN, session="S25", note="blocked on user PAT rotation"),
        _item("M-MILE", ItemStatus.RESOLVED, kind=ItemKind.MILESTONE, session="S53"),
        _item("R-REL", ItemStatus.RESOLVED, kind=ItemKind.RELEASE, session="S38"),
        _item("X-ERR", ItemStatus.OPEN, kind=ItemKind.ERROR, session="S70"),
    ])


_META = {"last_checkpoint_seq": 201, "written_by_session": "S135", "rag_version": "1.8.0"}
_LEDGER = [
    {"id": "INS-1", "disposition": "RESOLVED", "summary": "done"},
    {"id": "INS-2", "disposition": "OPEN", "summary": "still open"},
]

_ALL_GREEN = dict(
    version="0.4.30", tests="1,700 green", tests_ok=True, health="20/20",
    health_ok=True, drift_sha="268149294412", drift_ok=True, released=True,
    release_ref="runtime-v0.4.30", claims_ok=True, context_pct="30%",
    git_head="abc1234",
)


# ---------------------------------------------------------------------------
# _overall_rag thresholds (objective, not subjective)
# ---------------------------------------------------------------------------

class TestOverallRag:
    def test_all_green_released(self):
        assert _overall_rag(tests_ok=True, health_ok=True, drift_ok=True,
                            claims_ok=True, released=True) == "GREEN"

    def test_unreleased_is_amber(self):
        assert _overall_rag(tests_ok=True, health_ok=True, drift_ok=True,
                            claims_ok=True, released=False) == "AMBER"

    @pytest.mark.parametrize("gate", ["tests_ok", "health_ok", "drift_ok", "claims_ok"])
    def test_any_failing_gate_is_red(self, gate):
        kw = dict(tests_ok=True, health_ok=True, drift_ok=True, claims_ok=True, released=True)
        kw[gate] = False
        assert _overall_rag(**kw) == "RED"

    def test_unknown_gate_never_false_green(self):
        # every gate unknown -> AMBER, never GREEN (Rule 14 honesty)
        assert _overall_rag(tests_ok=None, health_ok=None, drift_ok=None,
                            claims_ok=None, released=None) == "AMBER"

    def test_failing_gate_beats_unreleased(self):
        assert _overall_rag(tests_ok=False, health_ok=True, drift_ok=True,
                            claims_ok=True, released=False) == "RED"


# ---------------------------------------------------------------------------
# pure renderer — structure
# ---------------------------------------------------------------------------

class TestRenderStructure:
    def test_version_bumped(self):
        assert DRIFT_RENDER_VERSION == "1.1.0"

    def test_heading_and_seven_sections_in_order(self):
        out = render_status_report(_store(), session="S136", meta=_META, ledger=_LEDGER)
        assert out.startswith("## RAG Runtime Kernel — Status Report (S136 close)")
        markers = [
            "### 1 · At a glance",
            "### 2 · Build",
            "### 3 · This session (S136)",
            "### 4 · Backlog",
            "### 5 · Risks & deviations",
            "### 6 · Ledger & errors",
            "### 7 · Verification & handoff",
        ]
        positions = [out.find(m) for m in markers]
        assert all(p != -1 for p in positions), positions
        assert positions == sorted(positions)  # in order

    def test_seq_and_written_by_from_meta(self):
        out = render_status_report(_store(), session="S136", meta=_META)
        assert "201" in out
        assert "S135" in out

    def test_determinism(self):
        a = render_status_report(_store(), session="S136", meta=_META, ledger=_LEDGER, **_ALL_GREEN)
        b = render_status_report(_store(), session="S136", meta=_META, ledger=_LEDGER, **_ALL_GREEN)
        assert a == b


# ---------------------------------------------------------------------------
# pure renderer — verdict / R-A-G glyphs
# ---------------------------------------------------------------------------

class TestVerdict:
    def test_green_when_all_gates_and_released(self):
        out = render_status_report(_store(), session="S136", meta=_META, **_ALL_GREEN)
        assert "🟢 GREEN" in out
        assert "GREEN — released" in out

    def test_amber_and_unreleased_wording(self):
        kw = dict(_ALL_GREEN); kw["released"] = False
        out = render_status_report(_store(), session="S136", meta=_META, **kw)
        assert "🟡 AMBER" in out
        assert "UNRELEASED" in out

    def test_red_when_tests_fail(self):
        kw = dict(_ALL_GREEN); kw["tests_ok"] = False
        out = render_status_report(_store(), session="S136", meta=_META, **kw)
        assert "🔴 RED" in out
        assert "tests failing" in out

    def test_unknown_gates_amber(self):
        out = render_status_report(_store(), session="S136", meta=_META)
        assert "🟡 AMBER" in out


# ---------------------------------------------------------------------------
# pure renderer — backlog full enumeration + n/a discipline
# ---------------------------------------------------------------------------

class TestBacklogAndNa:
    def test_backlog_enumerates_every_open_item_by_id(self):
        out = render_status_report(_store(), session="S136", meta=_META)
        # A-OPEN is plain-open, E-GATE is user-gated -> different buckets, both listed
        assert "A-OPEN" in out
        assert "B-PROG" in out
        assert "E-GATE" in out
        assert "C-DEF" in out  # deferred bucket

    def test_gated_item_in_blocked_bucket(self):
        out = render_status_report(_store(), session="S136", meta=_META)
        blocked_idx = out.find("Blocked / user-gated")
        deferred_idx = out.find("**Deferred")
        gate_idx = out.find("E-GATE")
        # E-GATE renders after the blocked header and before the deferred header
        assert blocked_idx < gate_idx < deferred_idx

    def test_unknown_scalars_render_na_not_blank(self):
        out = render_status_report(_store(), session="S136", meta=_META)
        assert "n/a" in out
        # no empty context/tests cell leaking through as blank pipes
        assert "|  |" not in out

    def test_backlog_counts_match_enumeration(self):
        out = render_status_report(_store(), session="S136", meta=_META)
        # 3 active (A-OPEN, B-PROG, E-GATE) split open/gated; 1 deferred;
        # 3 resolved backlog items (D-DONE task, M-MILE milestone, R-REL release)
        assert "Backlog: 3 open · 1 deferred · 3 resolved" in out


# ---------------------------------------------------------------------------
# pure renderer — sections 3 / 6
# ---------------------------------------------------------------------------

class TestSessionAndLedger:
    def test_this_session_filters_by_session(self):
        out = render_status_report(_store(), session="S136", meta=_META)
        three = out[out.find("### 3"):out.find("### 4")]
        assert "B-PROG" in three          # session S136
        assert "A-OPEN" not in three      # session S40

    def test_ledger_open_count_and_error_items(self):
        out = render_status_report(_store(), session="S136", meta=_META, ledger=_LEDGER)
        assert "2 entries · 1 OPEN" in out
        assert "Open error items: 1" in out
        assert "X-ERR" in out


# ---------------------------------------------------------------------------
# CLI verb — read-only render from a RAG file
# ---------------------------------------------------------------------------

@pytest.fixture
def rag_file(tmp_path):
    hot = {
        "meta": {"last_checkpoint_seq": 201, "written_by_session": "S135",
                 "rag_version": "1.8.0", "last_updated_utc": "2026-07-11T00:00:00Z"},
        "inference_ledger": [{"id": "INS-2", "disposition": "OPEN", "summary": "x"}],
        "tracked_items": [
            {"id": "OPEN-1", "title": "open one", "status": "OPEN", "kind": "TASK",
             "session": "S40", "note": "", "superseded_by": None, "history": []},
            {"id": "MILE-1", "title": "a milestone", "status": "RESOLVED",
             "kind": "MILESTONE", "session": "S53", "note": "", "superseded_by": None,
             "history": []},
        ],
    }
    p = tmp_path / "RAG_MASTER.json"
    p.write_text(json.dumps(hot, indent=2), encoding="utf-8")
    return p


class TestReportCli:
    def test_report_renders_and_exits_zero(self, rag_file, capsys):
        rc = main(["report", "--session", "S136", "--rag", str(rag_file), "--no-live"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Status Report (S136 close)" in out
        assert "201" in out and "OPEN-1" in out

    def test_report_is_read_only(self, rag_file):
        before = rag_file.read_bytes()
        main(["report", "--session", "S136", "--rag", str(rag_file), "--no-live"])
        assert rag_file.read_bytes() == before

    def test_missing_rag_fails_loud(self, tmp_path, capsys):
        rc = main(["report", "--session", "S1", "--rag", str(tmp_path / "nope.json"), "--no-live"])
        assert rc == 1

    def test_explicit_args_flow_through(self, rag_file, capsys):
        rc = main([
            "report", "--session", "S136", "--rag", str(rag_file), "--no-live",
            "--context-pct", "42%", "--tests", "1,700 green", "--released",
            "--release-ref", "runtime-v0.4.30", "--claims-ok",
        ])
        assert rc == 0
        out = capsys.readouterr().out
        assert "42%" in out
        assert "1,700 green" in out
        # under --no-live health + drift are unknown, so the honest verdict is
        # AMBER (never a false GREEN) even with released + tests + claims provided.
        assert "🟡 AMBER" in out
        assert "runtime-v0.4.30" in out
