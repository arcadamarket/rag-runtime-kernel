"""Tests for KA-CUTOVER-GATE — the record-coverage cutover gate + governed un-add.

Two coupled defects made a mis-``add`` of a forensic-kind (ERROR/INFERENCE) item
UNRECOVERABLE:

  1. ``check_record_coverage`` counted a kind as "migrated" (gate ON) by ANY item
     of that kind regardless of status — so a single mis-kinded item latched the
     per-kind cutover gate ON, demanding full ERROR_LOG / ledger coverage.
  2. There was no governed un-add: the lifecycle verbs only transition, so the
     mis-kinded item could be discarded/superseded but never removed — and since
     discard/supersede leave ``kind`` intact, the status-blind gate stayed latched.

Fix: the gate now counts only NON-RETIRED members (drift_control.RETIRED_STATUSES
= {SUPERSEDED, DISCARDED}), and a guarded, atomic un-add (store.remove /
remove_item_file / the `un-add` CLI verb) removes a PRISTINE mis-add outright.
Either path breaks the deadlock; together they give clean recovery.
"""
from __future__ import annotations

import json

import pytest

from rag_kernel.drift_control import (
    ItemKind,
    ItemStatus,
    TrackedItem,
    RETIRED_STATUSES,
    TERMINAL_STATUSES,
)
from rag_kernel import drift_audit
from rag_kernel.drift_store import (
    DriftStoreError,
    UnknownItemError,
    TrackedItemStore,
    add_items_file,
    remove_item_file,
    load_hot,
)
from rag_kernel.__main__ import main


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------

def _task(id, status, kind=ItemKind.TASK, note=""):
    return TrackedItem(id=id, title=f"{id} title", status=ItemStatus(status),
                       kind=kind, note=note).to_dict()


def _historied(id, kind=ItemKind.ERROR):
    """A real item carrying one lifecycle event (OPEN -> DISCARDED)."""
    it = TrackedItem(id=id, title=f"{id} title", status=ItemStatus.OPEN, kind=kind)
    it = it.with_status(ItemStatus.DISCARDED, session="T", reason="x")
    return it.to_dict()


def _hot(items=None, ledger=None):
    return {
        "meta": {"last_updated_utc": "2026-07-04T00:00:00Z"},
        "tracked_items": items or [],
        "inference_ledger": ledger or [],
    }


def _make_rag(tmp_path, items=None):
    rag = tmp_path / "RAG_MASTER.json"
    rag.write_text(json.dumps({
        "meta": {"last_checkpoint_seq": 1, "written_by_session": "T"},
        "tracked_items": items or [],
        "inference_ledger": [],
    }), encoding="utf-8")
    return rag


def _items(rag):
    return json.loads(rag.read_text(encoding="utf-8"))["tracked_items"]


# ---------------------------------------------------------------------------
# RETIRED_STATUSES constant
# ---------------------------------------------------------------------------

def test_retired_statuses_constant():
    assert RETIRED_STATUSES == frozenset({ItemStatus.SUPERSEDED, ItemStatus.DISCARDED})
    # retired is a strict subset of terminal — RESOLVED is terminal but NOT retired
    assert RETIRED_STATUSES < TERMINAL_STATUSES
    assert ItemStatus.RESOLVED not in RETIRED_STATUSES


# ---------------------------------------------------------------------------
# gate fix: check_record_coverage counts only NON-retired members
# ---------------------------------------------------------------------------

def test_gate_discarded_error_does_not_latch(tmp_path):
    # Only a DISCARDED (retired) ERROR item exists => the ERROR gate stays dormant,
    # so an un-migrated E-### heading is NOT flagged (pre-migration state restored).
    el = tmp_path / "ERROR_LOG.md"
    el.write_text("### E-001: boom\n", encoding="utf-8")
    hot = _hot(items=[_task("BOGUS", "DISCARDED", kind=ItemKind.ERROR)])
    assert drift_audit.check_record_coverage(hot, error_log_path=el) == []


def test_gate_discarded_inference_does_not_latch():
    # Only a DISCARDED (retired) INFERENCE item => the ledger gate stays dormant.
    hot = _hot(
        items=[_task("BOGUS", "DISCARDED", kind=ItemKind.INFERENCE)],
        ledger=[{"id": "INS-7", "summary": "x", "disposition": "DEFERRED"}],
    )
    assert drift_audit.check_record_coverage(hot) == []


def test_gate_resolved_error_still_counts(tmp_path):
    # RESOLVED is terminal but NOT retired: a completed record is still canonical,
    # so the gate is active and an uncovered E-### heading IS flagged.
    el = tmp_path / "ERROR_LOG.md"
    el.write_text("### E-001: a\n\n### E-002: b\n", encoding="utf-8")
    hot = _hot(items=[_task("E-001", "RESOLVED", kind=ItemKind.ERROR)])
    f = drift_audit.check_record_coverage(hot, error_log_path=el)
    assert {x.item_id for x in f} == {"E-002"}


def test_gate_active_open_error_still_latches(tmp_path):
    # A NON-retired (OPEN) ERROR item keeps the gate ON — the retirement carve-out
    # must not accidentally disable coverage for live migrated records.
    el = tmp_path / "ERROR_LOG.md"
    el.write_text("### E-001: a\n\n### E-002: b\n", encoding="utf-8")
    hot = _hot(items=[_task("E-001", "OPEN", kind=ItemKind.ERROR)])
    f = drift_audit.check_record_coverage(hot, error_log_path=el)
    assert {x.item_id for x in f} == {"E-002"}


# ---------------------------------------------------------------------------
# store-level un-add: TrackedItemStore.remove
# ---------------------------------------------------------------------------

def test_store_remove_pristine_item():
    store = TrackedItemStore()
    store.add(TrackedItem(id="BOGUS", title="t", status=ItemStatus.OPEN, kind=ItemKind.ERROR))
    removed = store.remove("BOGUS")
    assert removed.id == "BOGUS"
    assert "BOGUS" not in store
    assert len(store) == 0


def test_store_remove_unknown_fails_loud():
    store = TrackedItemStore()
    with pytest.raises(UnknownItemError):
        store.remove("NOPE")


def test_store_remove_historied_is_protected():
    store = TrackedItemStore()
    store.add(TrackedItem(id="REAL", title="t", status=ItemStatus.OPEN, kind=ItemKind.TASK))
    store.start("REAL", session="T")  # appends a lifecycle event => non-pristine
    with pytest.raises(DriftStoreError):
        store.remove("REAL")
    assert "REAL" in store  # untouched


# ---------------------------------------------------------------------------
# file-level un-add: remove_item_file (guarded + atomic)
# ---------------------------------------------------------------------------

def test_remove_item_file_atomic(tmp_path):
    p = tmp_path / "RAG_MASTER.json"
    p.write_text(json.dumps(_hot(items=[
        _task("BOGUS", "OPEN", kind=ItemKind.ERROR),
        _task("KEEP", "OPEN"),
    ])), encoding="utf-8")
    remove_item_file(p, "BOGUS")
    ids = {i["id"] for i in json.loads(p.read_text(encoding="utf-8"))["tracked_items"]}
    assert ids == {"KEEP"}
    assert (tmp_path / "RAG_MASTER.json.bak").exists()  # .bak parity-mirrored


def test_remove_item_file_historied_writes_nothing(tmp_path):
    p = tmp_path / "RAG_MASTER.json"
    p.write_text(json.dumps(_hot(items=[_historied("REAL")])), encoding="utf-8")
    before = p.read_text(encoding="utf-8")
    with pytest.raises(DriftStoreError):
        remove_item_file(p, "REAL")
    assert p.read_text(encoding="utf-8") == before  # prior file intact


def test_remove_item_file_unknown_writes_nothing(tmp_path):
    p = tmp_path / "RAG_MASTER.json"
    p.write_text(json.dumps(_hot(items=[_task("KEEP", "OPEN")])), encoding="utf-8")
    before = p.read_text(encoding="utf-8")
    with pytest.raises(UnknownItemError):
        remove_item_file(p, "NOPE")
    assert p.read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# end-to-end: the KA-CUTOVER-GATE deadlock and its recovery
# ---------------------------------------------------------------------------

def test_cutover_gate_deadlock_recovered_via_unadd(tmp_path):
    el = tmp_path / "ERROR_LOG.md"
    el.write_text("### E-001: a real, un-migrated error\n", encoding="utf-8")
    p = tmp_path / "RAG_MASTER.json"
    p.write_text(json.dumps(_hot(items=[_task("TASK-1", "OPEN")])), encoding="utf-8")

    # 1) mis-add an ERROR-kind item (pristine, OPEN) -> latches the per-kind gate ON
    add_items_file(p, [{
        "id": "OOPS", "title": "mis-kinded", "status": ItemStatus.OPEN,
        "kind": ItemKind.ERROR, "session": "S123",
    }])
    flagged = drift_audit.check_record_coverage(load_hot(p), error_log_path=el)
    assert any(x.item_id == "E-001" for x in flagged)  # deadlock: E-001 uncovered

    # 2) un-add recovers it -> gate falls back to dormant -> clean
    remove_item_file(p, "OOPS")
    assert drift_audit.check_record_coverage(load_hot(p), error_log_path=el) == []


# ---------------------------------------------------------------------------
# CLI: the `un-add` verb
# ---------------------------------------------------------------------------

def test_cli_unadd_pristine(tmp_path, capsys):
    rag = _make_rag(tmp_path)
    main(["add", "OOPS", "mis", "--rag", str(rag), "--kind", "ERROR", "--session", "S123"])
    rc = main(["un-add", "OOPS", "--rag", str(rag), "--session", "S123"])
    assert rc == 0
    assert _items(rag) == []
    assert "un-added OOPS" in capsys.readouterr().out


def test_cli_unadd_unknown_id(tmp_path, capsys):
    rag = _make_rag(tmp_path)
    rc = main(["un-add", "NOPE", "--rag", str(rag), "--session", "S123"])
    assert rc == 1
    assert "no tracked item" in capsys.readouterr().err


def test_cli_unadd_historied_protected(tmp_path, capsys):
    rag = _make_rag(tmp_path)
    main(["add", "REAL", "t", "--rag", str(rag), "--session", "S123"])
    main(["start", "REAL", "--rag", str(rag), "--session", "S123"])  # gives it history
    rc = main(["un-add", "REAL", "--rag", str(rag), "--session", "S123"])
    assert rc == 1
    assert "pristine" in capsys.readouterr().err
    assert len(_items(rag)) == 1  # untouched


def test_cli_unadd_dry_run_writes_nothing(tmp_path, capsys):
    rag = _make_rag(tmp_path)
    main(["add", "OOPS", "t", "--rag", str(rag), "--kind", "ERROR", "--session", "S123"])
    rc = main(["un-add", "OOPS", "--rag", str(rag), "--session", "S123", "--dry-run"])
    assert rc == 0
    assert "[DRY RUN]" in capsys.readouterr().out
    assert len(_items(rag)) == 1


def test_cli_unadd_missing_rag(tmp_path, capsys):
    rc = main(["un-add", "X", "--rag", str(tmp_path / "nope.json"), "--session", "S123"])
    assert rc == 1
    assert "not found" in capsys.readouterr().err
