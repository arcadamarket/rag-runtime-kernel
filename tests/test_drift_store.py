"""Unit tests for rag_kernel.drift_store (DRIFT-ELIM increment 2).

Covers the deterministic mutation API over the RAG: the TrackedItemStore
(unique-id invariant, guarded transitions, deterministic serialization), the
file-level atomic persistence (load -> guarded mutate -> atomic write + .bak,
fail-closed so a tripped guard writes nothing), and the one-time backlog
migration (legacy prose stores -> normalized tracked_items). The pure lifecycle
core lives in test_drift_control; registration/CLI/renders/auditor are later
increments and are out of scope here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rag_kernel.drift_control import (
    ItemKind,
    ItemStateError,
    ItemStatus,
    TrackedItem,
)
from rag_kernel.drift_store import (
    DRIFT_STORE_VERSION,
    TRACKED_ITEMS_KEY,
    DriftStoreError,
    DuplicateItemError,
    TrackedItemStore,
    UnknownItemError,
    load_hot,
    migrate_backlog,
    migrate_backlog_file,
    mutate_hot,
    seed_items,
    transition_in_file,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _item(item_id="T-1", status=ItemStatus.OPEN, **kw):
    base = dict(id=item_id, title="a tracked thing", status=status)
    base.update(kw)
    return TrackedItem(**base)


def _hot(items=None, **meta):
    h = {"meta": {"session_id": "S49", **meta}}
    if items is not None:
        h[TRACKED_ITEMS_KEY] = items
    return h


def _write_hot(path: Path, hot: dict) -> Path:
    path.write_text(json.dumps(hot, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# store: construction + queries
# ---------------------------------------------------------------------------

def test_version_and_key_constants():
    assert DRIFT_STORE_VERSION == "1.3.0"  # KA-CUTOVER-GATE: added the un-add path
    assert TRACKED_ITEMS_KEY == "tracked_items"


def test_empty_store():
    store = TrackedItemStore()
    assert len(store) == 0
    assert store.ids() == []
    assert list(store) == []


def test_add_and_get():
    store = TrackedItemStore()
    store.add(_item("T-1"))
    assert "T-1" in store
    assert store.get("T-1").id == "T-1"
    assert len(store) == 1


def test_add_duplicate_id_fails_loud():
    store = TrackedItemStore([_item("T-1")])
    with pytest.raises(DuplicateItemError):
        store.add(_item("T-1"))


def test_add_non_trackeditem_fails():
    store = TrackedItemStore()
    with pytest.raises(DriftStoreError):
        store.add({"id": "T-1"})  # type: ignore[arg-type]


def test_get_unknown_raises():
    store = TrackedItemStore()
    with pytest.raises(UnknownItemError):
        store.get("missing")


def test_iteration_is_id_sorted():
    store = TrackedItemStore([_item("T-3"), _item("T-1"), _item("T-2")])
    assert [it.id for it in store] == ["T-1", "T-2", "T-3"]
    assert store.ids() == ["T-1", "T-2", "T-3"]


def test_by_status_and_by_kind():
    store = TrackedItemStore([
        _item("T-1", status=ItemStatus.OPEN, kind=ItemKind.TASK),
        _item("T-2", status=ItemStatus.DEFERRED, kind=ItemKind.TASK),
        _item("M-1", status=ItemStatus.OPEN, kind=ItemKind.MILESTONE),
    ])
    assert {it.id for it in store.by_status(ItemStatus.OPEN)} == {"T-1", "M-1"}
    assert {it.id for it in store.by_status("DEFERRED")} == {"T-2"}
    assert {it.id for it in store.by_kind(ItemKind.MILESTONE)} == {"M-1"}
    assert {it.id for it in store.by_kind("TASK")} == {"T-1", "T-2"}


# ---------------------------------------------------------------------------
# store: round-trip serialization
# ---------------------------------------------------------------------------

def test_from_hot_round_trip():
    items = [_item("T-2"), _item("T-1")]
    hot = _hot(items=[it.to_dict() for it in items])
    store = TrackedItemStore.from_hot(hot)
    assert store.ids() == ["T-1", "T-2"]


def test_from_hot_missing_key_is_empty():
    store = TrackedItemStore.from_hot(_hot())
    assert len(store) == 0


def test_from_hot_non_list_fails():
    with pytest.raises(DriftStoreError):
        TrackedItemStore.from_hot({TRACKED_ITEMS_KEY: {"not": "a list"}})


def test_to_list_is_deterministic_and_sorted():
    store = TrackedItemStore([_item("T-3"), _item("T-1"), _item("T-2")])
    out = store.to_list()
    assert [d["id"] for d in out] == ["T-1", "T-2", "T-3"]
    # stable across calls
    assert store.to_list() == out


def test_write_into_preserves_other_keys():
    hot = _hot(rag_version="1.7.0")
    hot["unrelated"] = {"keep": 1}
    TrackedItemStore([_item("T-1")]).write_into(hot)
    assert hot["unrelated"] == {"keep": 1}
    assert hot["meta"]["session_id"] == "S49"
    assert [d["id"] for d in hot[TRACKED_ITEMS_KEY]] == ["T-1"]


# ---------------------------------------------------------------------------
# store: guarded transitions
# ---------------------------------------------------------------------------

def test_transition_legal_records_history():
    store = TrackedItemStore([_item("T-1", status=ItemStatus.OPEN)])
    updated = store.transition("T-1", ItemStatus.IN_PROGRESS, session="S49", reason="go")
    assert updated.status == ItemStatus.IN_PROGRESS
    assert store.get("T-1").status == ItemStatus.IN_PROGRESS
    assert len(store.get("T-1").history) == 1
    assert store.get("T-1").history[0].reason == "go"


def test_transition_illegal_raises_and_does_not_mutate():
    store = TrackedItemStore([_item("T-1", status=ItemStatus.OPEN)])
    with pytest.raises(ItemStateError):
        store.transition("T-1", ItemStatus.RESOLVED, session="S49")  # OPEN->RESOLVED illegal
    assert store.get("T-1").status == ItemStatus.OPEN  # unchanged


def test_transition_unknown_id_raises():
    store = TrackedItemStore()
    with pytest.raises(UnknownItemError):
        store.transition("nope", ItemStatus.IN_PROGRESS, session="S49")


def test_named_shortcuts():
    store = TrackedItemStore([_item("T-1", status=ItemStatus.OPEN)])
    store.start("T-1", session="S49")
    assert store.get("T-1").status == ItemStatus.IN_PROGRESS
    store.resolve("T-1", session="S49")
    assert store.get("T-1").status == ItemStatus.RESOLVED


def test_defer_reopen_cycle():
    store = TrackedItemStore([_item("T-1", status=ItemStatus.OPEN)])
    store.defer("T-1", session="S49", reason="parked")
    assert store.get("T-1").status == ItemStatus.DEFERRED
    store.reopen("T-1", session="S50")
    assert store.get("T-1").status == ItemStatus.OPEN
    assert [e.to_status.value for e in store.get("T-1").history] == ["DEFERRED", "OPEN"]


def test_supersede_requires_by():
    store = TrackedItemStore([_item("T-1", status=ItemStatus.OPEN)])
    store.supersede("T-1", by="T-2", session="S49")
    assert store.get("T-1").status == ItemStatus.SUPERSEDED
    assert store.get("T-1").superseded_by == "T-2"


def test_discard_is_terminal():
    store = TrackedItemStore([_item("T-1", status=ItemStatus.OPEN)])
    store.discard("T-1", session="S49")
    assert store.get("T-1").is_terminal
    with pytest.raises(ItemStateError):
        store.reopen("T-1", session="S50")


# ---------------------------------------------------------------------------
# file persistence: mutate_hot / transition_in_file
# ---------------------------------------------------------------------------

def test_mutate_hot_writes_atomically_and_refreshes_bak(tmp_path):
    p = _write_hot(tmp_path / "RAG_MASTER.json",
                   _hot(items=[_item("T-1", status=ItemStatus.OPEN).to_dict()]))
    mutate_hot(p, lambda s: s.start("T-1", session="S49"), now="2026-06-06T08:00:00Z")
    on_disk = json.loads(p.read_text(encoding="utf-8"))
    assert on_disk[TRACKED_ITEMS_KEY][0]["status"] == "IN_PROGRESS"
    assert on_disk["meta"]["last_updated_utc"] == "2026-06-06T08:00:00Z"
    # .bak was created by the atomic write
    assert (tmp_path / "RAG_MASTER.json.bak").exists()


def test_mutate_hot_failed_guard_leaves_file_intact(tmp_path):
    original = _hot(items=[_item("T-1", status=ItemStatus.OPEN).to_dict()])
    p = _write_hot(tmp_path / "RAG_MASTER.json", original)
    before = p.read_text(encoding="utf-8")
    with pytest.raises(ItemStateError):
        mutate_hot(p, lambda s: s.transition("T-1", ItemStatus.RESOLVED, session="S49"))
    assert p.read_text(encoding="utf-8") == before  # nothing written


def test_transition_in_file(tmp_path):
    p = _write_hot(tmp_path / "RAG_MASTER.json",
                   _hot(items=[_item("T-1", status=ItemStatus.OPEN).to_dict()]))
    transition_in_file(p, "T-1", ItemStatus.DEFERRED, session="S49", reason="later")
    on_disk = json.loads(p.read_text(encoding="utf-8"))
    assert on_disk[TRACKED_ITEMS_KEY][0]["status"] == "DEFERRED"
    assert on_disk[TRACKED_ITEMS_KEY][0]["history"][0]["reason"] == "later"


def test_load_hot_rejects_non_object(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(DriftStoreError):
        load_hot(p)


# ---------------------------------------------------------------------------
# migration
# ---------------------------------------------------------------------------

_SPECS = [
    {"id": "M-1", "title": "shipped milestone", "status": "RESOLVED", "kind": ItemKind.MILESTONE, "session": "S35"},
    {"id": "T-1", "title": "open task", "status": "OPEN", "kind": ItemKind.TASK},
    {"id": "T-2", "title": "parked task", "status": "DEFERRED", "kind": ItemKind.TASK, "note": "after stars"},
]


def test_seed_items_builds_validated_items():
    items = seed_items(_SPECS)
    assert [it.id for it in items] == ["M-1", "T-1", "T-2"]
    by_id = {it.id: it for it in items}
    assert by_id["M-1"].status == ItemStatus.RESOLVED
    assert by_id["M-1"].is_terminal
    assert by_id["T-2"].note == "after stars"


def test_seed_items_duplicate_fails():
    with pytest.raises(DuplicateItemError):
        seed_items([
            {"id": "X", "title": "a", "status": "OPEN"},
            {"id": "X", "title": "b", "status": "OPEN"},
        ])


def test_seed_items_invalid_status_fails():
    with pytest.raises(ValueError):
        seed_items([{"id": "X", "title": "a", "status": "BOGUS"}])


def test_migrate_backlog_seeds_array():
    hot = _hot()
    migrate_backlog(hot, _SPECS)
    assert [d["id"] for d in hot[TRACKED_ITEMS_KEY]] == ["M-1", "T-1", "T-2"]


def test_migrate_backlog_refuses_to_clobber():
    hot = _hot(items=[_item("EXISTING").to_dict()])
    with pytest.raises(DriftStoreError):
        migrate_backlog(hot, _SPECS)
    # original preserved
    assert hot[TRACKED_ITEMS_KEY][0]["id"] == "EXISTING"


def test_migrate_backlog_allow_overwrite():
    hot = _hot(items=[_item("EXISTING").to_dict()])
    migrate_backlog(hot, _SPECS, allow_overwrite=True)
    assert [d["id"] for d in hot[TRACKED_ITEMS_KEY]] == ["M-1", "T-1", "T-2"]


def test_migrate_backlog_file_atomic(tmp_path):
    p = _write_hot(tmp_path / "RAG_MASTER.json", _hot())
    migrate_backlog_file(p, _SPECS, now="2026-06-06T08:00:00Z")
    on_disk = json.loads(p.read_text(encoding="utf-8"))
    assert [d["id"] for d in on_disk[TRACKED_ITEMS_KEY]] == ["M-1", "T-1", "T-2"]
    assert on_disk["meta"]["last_updated_utc"] == "2026-06-06T08:00:00Z"
    assert (tmp_path / "RAG_MASTER.json.bak").exists()


def test_migrated_items_then_transition_in_file(tmp_path):
    """End-to-end: migrate, then drive a migrated OPEN item through its lifecycle."""
    p = _write_hot(tmp_path / "RAG_MASTER.json", _hot())
    migrate_backlog_file(p, _SPECS)
    transition_in_file(p, "T-1", ItemStatus.IN_PROGRESS, session="S49")
    transition_in_file(p, "T-1", ItemStatus.RESOLVED, session="S49", reason="done")
    on_disk = json.loads(p.read_text(encoding="utf-8"))
    t1 = next(d for d in on_disk[TRACKED_ITEMS_KEY] if d["id"] == "T-1")
    assert t1["status"] == "RESOLVED"
    assert [e["to_status"] for e in t1["history"]] == ["IN_PROGRESS", "RESOLVED"]
