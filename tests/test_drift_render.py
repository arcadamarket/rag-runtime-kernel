"""Unit tests for rag_kernel.drift_render (DRIFT-ELIM increment 4).

Covers the deterministic renderers that project the canonical ``tracked_items``
array into the legacy ``open_tasks`` / ``deferred_items`` arrays, the Rule 12
status-report backlog, and the ERROR_LOG backlog summary — plus the atomic
``apply_*`` writers that make the legacy arrays projections. Properties asserted:
determinism (id-sorted, stable), idempotence (rendering a render is a no-op),
purity (the canonical array is never mutated), and status-bucket correctness.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rag_kernel.drift_control import ItemKind, ItemStatus, TrackedItem
from rag_kernel.drift_store import TRACKED_ITEMS_KEY, TrackedItemStore
from rag_kernel.drift_render import (
    ACTIVE_STATUSES,
    DRIFT_RENDER_VERSION,
    apply_renders,
    apply_renders_file,
    default_gated,
    render_all,
    render_backlog_markdown,
    render_backlog_section,
    render_deferred_items,
    render_error_log_backlog,
    render_open_tasks,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _item(item_id, status, **kw):
    base = dict(id=item_id, title=f"title for {item_id}", status=status)
    base.update(kw)
    return TrackedItem(**base)


def _mixed_store():
    """A store with one item in every non-supersede status, plus a gated one."""
    return TrackedItemStore([
        _item("B-OPEN", ItemStatus.OPEN, session="S40"),
        _item("A-PROG", ItemStatus.IN_PROGRESS, session="S49", note="building"),
        _item("D-DEF", ItemStatus.DEFERRED, session="S46", note="parked"),
        _item("C-DONE", ItemStatus.RESOLVED, session="S37"),
        _item("E-GATE", ItemStatus.OPEN, session="S25", note="blocked on user PAT rotation"),
        _item("F-DISC", ItemStatus.DISCARDED, session="S30"),
    ])


def _hot_from(store):
    return {"meta": {"session_id": "S51"}, TRACKED_ITEMS_KEY: store.to_list()}


# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

def test_version_and_active_statuses():
    assert DRIFT_RENDER_VERSION == "1.0.0"
    assert ACTIVE_STATUSES == frozenset({ItemStatus.OPEN, ItemStatus.IN_PROGRESS})


# ---------------------------------------------------------------------------
# render_open_tasks
# ---------------------------------------------------------------------------

def test_open_tasks_only_active_and_id_sorted():
    lines = render_open_tasks(_mixed_store())
    # only OPEN / IN_PROGRESS items (incl. the gated one — it's still active)
    assert len(lines) == 3
    ids = [ln.split(" ")[0] for ln in lines]
    assert ids == sorted(ids)  # id-sorted determinism
    assert ids == ["A-PROG", "B-OPEN", "E-GATE"]
    # terminal items never appear
    joined = "\n".join(lines)
    assert "C-DONE" not in joined and "F-DISC" not in joined and "D-DEF" not in joined


def test_open_tasks_line_format_includes_status_session_note():
    lines = render_open_tasks(_mixed_store())
    prog = next(ln for ln in lines if ln.startswith("A-PROG"))
    assert "[IN_PROGRESS · S49]" in prog
    assert prog.endswith("building")  # note appended


def test_open_tasks_missing_session_renders_dash():
    store = TrackedItemStore([_item("X", ItemStatus.OPEN)])
    assert "· —]" in render_open_tasks(store)[0]


def test_open_tasks_deterministic_repeatable():
    s = _mixed_store()
    assert render_open_tasks(s) == render_open_tasks(s)


# ---------------------------------------------------------------------------
# render_deferred_items
# ---------------------------------------------------------------------------

def test_deferred_items_only_deferred():
    objs = render_deferred_items(_mixed_store())
    assert [o["id"] for o in objs] == ["D-DEF"]
    o = objs[0]
    assert o["status"] == "DEFERRED"
    assert o["kind"] == "TASK"
    assert o["note"] == "parked"
    assert set(o) == {"id", "title", "status", "kind", "session", "note"}


# ---------------------------------------------------------------------------
# backlog section + markdown
# ---------------------------------------------------------------------------

def test_backlog_buckets_partition_by_status_and_gate():
    section = render_backlog_section(_mixed_store())
    assert section["open"] == ["A-PROG — title for A-PROG", "B-OPEN — title for B-OPEN"]
    assert section["blocked_or_user_gated"] == ["E-GATE — title for E-GATE"]
    assert section["deferred"] == ["D-DEF — title for D-DEF"]


def test_default_gated_predicate():
    assert default_gated(_item("g", ItemStatus.OPEN, note="BLOCKED on x")) is True
    assert default_gated(_item("g", ItemStatus.OPEN, note="ordinary note")) is False


def test_backlog_custom_gate_predicate():
    section = render_backlog_section(_mixed_store(), gated=lambda it: False)
    assert section["blocked_or_user_gated"] == []
    assert "E-GATE — title for E-GATE" in section["open"]


def test_backlog_markdown_contains_all_buckets():
    md = render_backlog_markdown(_mixed_store())
    assert "**Open:**" in md and "**Blocked / user-gated:**" in md and "**Deferred:**" in md
    assert "E-GATE" in md


def test_backlog_markdown_empty_buckets_say_none():
    store = TrackedItemStore([_item("only", ItemStatus.RESOLVED)])
    md = render_backlog_markdown(store)
    assert "(none)" in md


# ---------------------------------------------------------------------------
# error_log render
# ---------------------------------------------------------------------------

def test_error_log_backlog_lists_open_and_deferred():
    md = render_error_log_backlog(_mixed_store())
    assert "do NOT hand-edit" in md
    assert "### Open" in md and "### Deferred" in md
    assert "B-OPEN" in md and "D-DEF" in md
    assert "1 deferred" in md  # count summary


# ---------------------------------------------------------------------------
# render_all + apply_renders (purity / idempotence)
# ---------------------------------------------------------------------------

def test_render_all_shape():
    out = render_all(_mixed_store())
    # inc6 expanded render_all to also surface the INFERENCE/ERROR record renders.
    assert set(out) == {
        "open_tasks", "deferred_items", "backlog",
        "inference_records", "error_records",
    }


def test_apply_renders_overwrites_legacy_arrays_not_canonical():
    store = _mixed_store()
    canonical = store.to_list()
    hot = {"meta": {}, TRACKED_ITEMS_KEY: canonical,
           "open_tasks": ["STALE hand-authored line"],
           "deferred_items": [{"id": "STALE"}]}
    apply_renders(hot)
    # legacy arrays replaced by renders
    assert hot["open_tasks"] == render_open_tasks(store)
    assert hot["deferred_items"] == render_deferred_items(store)
    assert "STALE" not in json.dumps(hot["open_tasks"])
    # canonical array untouched
    assert hot[TRACKED_ITEMS_KEY] == canonical


def test_apply_renders_is_idempotent():
    hot = _hot_from(_mixed_store())
    once = apply_renders(dict(hot))
    twice = apply_renders(apply_renders(dict(hot)))
    assert once["open_tasks"] == twice["open_tasks"]
    assert once["deferred_items"] == twice["deferred_items"]


def test_apply_renders_requires_tracked_items():
    with pytest.raises(KeyError):
        apply_renders({"meta": {}})


def test_apply_renders_rejects_non_dict():
    with pytest.raises(TypeError):
        apply_renders(["not", "a", "dict"])


# ---------------------------------------------------------------------------
# apply_renders_file (atomic, .bak refresh)
# ---------------------------------------------------------------------------

def test_apply_renders_file_writes_and_refreshes_bak(tmp_path):
    store = _mixed_store()
    hot = _hot_from(store)
    hot["open_tasks"] = ["stale"]
    p = tmp_path / "RAG_MASTER.json"
    p.write_text(json.dumps(hot, indent=2), encoding="utf-8")

    new_hot = apply_renders_file(p)
    on_disk = json.loads(p.read_text(encoding="utf-8"))

    assert on_disk["open_tasks"] == render_open_tasks(store)
    assert on_disk["deferred_items"] == render_deferred_items(store)
    assert on_disk[TRACKED_ITEMS_KEY] == store.to_list()  # canonical preserved
    assert new_hot["open_tasks"] == on_disk["open_tasks"]
    # atomic_write_json refreshes the sibling .bak
    assert (tmp_path / "RAG_MASTER.json.bak").exists()


def test_apply_renders_file_touches_meta_timestamp(tmp_path):
    hot = _hot_from(_mixed_store())
    hot["meta"]["last_updated_utc"] = "1970-01-01T00:00:00Z"
    p = tmp_path / "RAG_MASTER.json"
    p.write_text(json.dumps(hot, indent=2), encoding="utf-8")
    out = apply_renders_file(p)
    assert out["meta"]["last_updated_utc"] != "1970-01-01T00:00:00Z"


def test_apply_renders_file_no_touch_meta(tmp_path):
    hot = _hot_from(_mixed_store())
    hot["meta"]["last_updated_utc"] = "1970-01-01T00:00:00Z"
    p = tmp_path / "RAG_MASTER.json"
    p.write_text(json.dumps(hot, indent=2), encoding="utf-8")
    out = apply_renders_file(p, touch_meta=False)
    assert out["meta"]["last_updated_utc"] == "1970-01-01T00:00:00Z"
