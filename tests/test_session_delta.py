"""SESSION-DELTA-RITUAL — the measured debit/credit report every close emits.

Covers the contract in rag_kernel.session_delta:
  * movements derived from tracked_items history, attributed by session id
  * OPENED as a set difference against a persisted baseline — NOT inferred from
    the item, because an added item and a re-noted item are byte-identical there
  * the no-baseline run reports "undetermined" instead of a confident wrong count
  * counters: measured values, None meaning "not measured" and never zero
  * priority_actions parsed as the RENDERED STRINGS they actually are
  * render is total: an empty delta still produces a report
  * CLI: session-delta prints, writes --out, and seeds the baseline
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rag_kernel.__main__ import main
from rag_kernel.session_delta import (
    PARTITION_NAME,
    ItemMove,
    SessionDelta,
    collect_counters,
    compute,
    load_baseline,
    render,
    save_baseline,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _item(iid, status="OPEN", kind="TASK", session="S1", history=None, note=""):
    return {"id": iid, "title": f"title of {iid}", "status": status, "kind": kind,
            "session": session, "note": note, "superseded_by": None,
            "history": history or []}


def _hot(items, **extra):
    hot = {
        "meta": {"written_by_session": "S2", "test_gate": {"count": 10, "git_head": "abc1234"}},
        "tracked_items": items,
        "open_tasks": [], "deferred_items": [], "priority_actions": [],
        "inference_ledger": [],
    }
    hot.update(extra)
    return hot


def _write_rag(tmp_path: Path, hot: dict) -> Path:
    rag = tmp_path / "RAG_MASTER.json"
    rag.write_text(json.dumps(hot), encoding="utf-8")
    return rag


# --------------------------------------------------------------------------- #
# Movements
# --------------------------------------------------------------------------- #
def test_closed_is_read_from_history_not_from_status():
    # Status alone cannot say WHO closed it. The history entry carries the session.
    items = [
        _item("A", status="RESOLVED", session="S2", history=[
            {"from_status": "OPEN", "to_status": "IN_PROGRESS", "session": "S2", "reason": "r"},
            {"from_status": "IN_PROGRESS", "to_status": "RESOLVED", "session": "S2", "reason": "r"},
        ]),
        _item("B", status="RESOLVED", session="S1", history=[
            {"from_status": "OPEN", "to_status": "RESOLVED", "session": "S1", "reason": "prior"},
        ]),
    ]
    d = compute(_hot(items), "S2")
    assert [m.item_id for m in d.closed] == ["A"]      # B was closed by S1
    assert [m.item_id for m in d.other_moves] == ["A"]  # OPEN -> IN_PROGRESS


def test_reopened_is_distinguished_from_a_fresh_transition():
    items = [_item("A", status="OPEN", session="S2", history=[
        {"from_status": "RESOLVED", "to_status": "OPEN", "session": "S2", "reason": "wrong"},
    ])]
    d = compute(_hot(items), "S2")
    assert [m.item_id for m in d.reopened] == ["A"]
    assert d.closed == ()


# --------------------------------------------------------------------------- #
# OPENED — the defect this module was born with
# --------------------------------------------------------------------------- #
def test_without_baseline_added_and_renoted_are_reported_together():
    # THE FIRST CUT GOT THIS WRONG on live data: it called every history-less
    # item stamped with the session "opened", and so reported two items S199 had
    # merely re-noted (RESIDENT-SUPERVISOR, ACTIVATION-GAP-S197) as items S199
    # had opened. An honest "undetermined" is the only defensible output here.
    items = [_item("ADDED_NOW", session="S2"), _item("RENOTED", session="S2")]
    d = compute(_hot(items), "S2", baseline=None)
    assert d.opened == ()
    assert d.origin_undetermined == ("ADDED_NOW", "RENOTED")
    assert "undetermined" in render(d)


def test_with_baseline_opened_is_an_exact_set_difference():
    items = [_item("OLD", session="S2"), _item("NEW", session="S2")]
    baseline = {"session": "S1", "counters": {}, "item_ids": ["OLD"]}
    d = compute(_hot(items), "S2", baseline=baseline)
    assert d.opened == ("NEW",)
    assert d.origin_undetermined == ()          # ambiguity is gone once seeded
    assert "OLD" in d.touched_notes             # touched, not opened


def test_opened_and_closed_in_one_session_is_called_out():
    items = [_item("SHORT", status="RESOLVED", session="S2", history=[
        {"from_status": "OPEN", "to_status": "RESOLVED", "session": "S2", "reason": "r"},
    ])]
    baseline = {"session": "S1", "counters": {}, "item_ids": ["OTHER"]}
    d = compute(_hot(items), "S2", baseline=baseline)
    assert d.opened == ("SHORT",) and d.opened_and_closed == ("SHORT",)
    assert "and closed here" in render(d)


# --------------------------------------------------------------------------- #
# Lingering / P1 parsing
# --------------------------------------------------------------------------- #
def test_priority_actions_are_rendered_strings_not_objects():
    # MEASURED on the live RAG: priority_actions holds "ID [P1 · OPEN · S199]: …".
    # Reading them as dicts yields an empty id set, which printed "no untouched
    # P1 items" on a board carrying eleven of them.
    items = [_item("P1ITEM", session="S1"), _item("OTHER", session="S1")]
    hot = _hot(items, priority_actions=["P1ITEM [P1 · OPEN · S1]: some title text"])
    d = compute(hot, "S2")
    assert d.lingering_p1 == ("P1ITEM",)
    assert "P1ITEM" in render(d)


def test_touched_items_are_not_reported_as_lingering():
    items = [_item("P1ITEM", session="S2")]   # touched by the reporting session
    hot = _hot(items, priority_actions=["P1ITEM [P1 · OPEN · S2]: t"])
    d = compute(hot, "S2", baseline={"session": "S1", "counters": {}, "item_ids": ["P1ITEM"]})
    assert d.lingering_p1 == ()
    assert "P1ITEM" not in d.untouched_live


# --------------------------------------------------------------------------- #
# Counters
# --------------------------------------------------------------------------- #
def test_unmeasured_counters_are_none_not_zero(tmp_path):
    # "0 errors" from a run that never audited is the exact failure mode this
    # project keeps banking. Absent must not render as a measured zero.
    c = collect_counters(_hot([_item("A")]), rag_dir=None, project_root=None,
                         repo_root=None)
    assert c["audit_errors"] is None and c["audit_warnings"] is None
    assert c["tla_specs"] is None and c["git_head"] is None
    assert c["tracked_items"] == 1
    text = render(compute(_hot([_item("A")]), "S2", counters_after=c))
    assert "not measured" in text


def test_counters_count_live_and_ledger(tmp_path):
    items = [_item("A", status="OPEN"), _item("B", status="IN_PROGRESS"),
             _item("C", status="RESOLVED")]
    hot = _hot(items, inference_ledger=[{"status": "OPEN"}, {"status": "CLOSED"}])
    c = collect_counters(hot)
    assert c["tracked_items"] == 3 and c["live_items"] == 2
    assert c["ledger_open"] == 1 and c["ledger_total"] == 2
    assert c["tests"] == 10 and c["tests_commit"] == "abc1234"


def test_formal_counts_come_from_disk(tmp_path):
    formal = tmp_path / "formal"
    formal.mkdir()
    (formal / "A.tla").write_text("---- MODULE A ----\n====\n", encoding="utf-8")
    (formal / "A.cfg").write_text("SPEC Spec\n", encoding="utf-8")
    (formal / "A_naive.cfg").write_text("SPEC Spec\n", encoding="utf-8")
    c = collect_counters(_hot([]), project_root=tmp_path)
    assert c["tla_specs"] == 1 and c["tlc_configs"] == 2


# --------------------------------------------------------------------------- #
# Baseline round trip
# --------------------------------------------------------------------------- #
def test_baseline_round_trips_and_self_seeds(tmp_path):
    assert load_baseline(tmp_path) is None            # first ever run
    save_baseline(tmp_path, "S2", {"tracked_items": 3}, item_ids=["A", "B"])
    got = load_baseline(tmp_path)
    assert got["session"] == "S2"
    assert got["counters"]["tracked_items"] == 3
    assert got["item_ids"] == ["A", "B"]
    # Lands in the NON-LOADED context store, never in RAG_MASTER.json.
    ctx = json.loads((tmp_path / "RAG_CONTEXT.json").read_text(encoding="utf-8"))
    assert PARTITION_NAME in json.dumps(ctx)
    assert not (tmp_path / "RAG_MASTER.json").exists()


def test_delta_reports_before_after_and_arrow():
    d = SessionDelta(
        session="S2",
        counters_before={"tracked_items": 3, "tests": 100},
        counters_after={"tracked_items": 5, "tests": 100},
    )
    text = render(d)
    assert "+2" in text          # tracked items moved
    assert "| 100 | 100 | 0 |" in text or "0 |" in text


# --------------------------------------------------------------------------- #
# Render totality
# --------------------------------------------------------------------------- #
def test_render_is_total_over_an_empty_delta():
    text = render(SessionDelta(session="S2"))
    assert "SESSION DELTA — S2" in text
    assert "No untouched P1 items" in text


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_cli_prints_writes_and_seeds(tmp_path, capsys):
    hot = _hot([_item("A", session="S2")])
    rag = _write_rag(tmp_path, hot)
    out = tmp_path / "SESSION_DELTA_S2.md"
    rc = main(["session-delta", "--rag", str(rag), "--session", "S2",
               "--out", str(out), "--save-baseline",
               "--audit-errors", "0", "--audit-warnings", "1"])
    assert rc == 0
    printed = capsys.readouterr().out
    assert "SESSION DELTA — S2" in printed
    assert out.is_file() and "SESSION DELTA" in out.read_text(encoding="utf-8")
    base = load_baseline(tmp_path)
    assert base["session"] == "S2" and base["item_ids"] == ["A"]
    assert base["counters"]["audit_errors"] == 0


def test_cli_second_run_has_a_measured_before(tmp_path, capsys):
    rag = _write_rag(tmp_path, _hot([_item("A", session="S2")]))
    main(["session-delta", "--rag", str(rag), "--session", "S2", "--save-baseline"])
    capsys.readouterr()
    # S3 adds an item; the diff must now be exact rather than undetermined.
    _write_rag(tmp_path, _hot([_item("A", session="S2"), _item("B", session="S3")]))
    rc = main(["session-delta", "--rag", str(rag), "--session", "S3"])
    assert rc == 0
    printed = capsys.readouterr().out
    assert "- Opened: 1" in printed
    assert "undetermined" not in printed.split("## Counters")[0]


def test_cli_defaults_session_to_written_by_session(tmp_path, capsys):
    rag = _write_rag(tmp_path, _hot([_item("A", session="S2")]))
    assert main(["session-delta", "--rag", str(rag)]) == 0
    assert "SESSION DELTA — S2" in capsys.readouterr().out
