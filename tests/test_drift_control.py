"""Unit tests for rag_kernel.drift_control (DRIFT-ELIM increment 1).

Covers the pure lifecycle core: the canonical status enum, the lifecycle
transition table + fail-loud guards, and the immutable TrackedItem with its
append-only history and serialization. Persistence over RAG_MASTER.json, the
CLI surface, and the session auditor are later increments and are out of scope.
"""

from __future__ import annotations

import dataclasses

import pytest

from rag_kernel.drift_control import (
    ItemKind,
    ItemStateError,
    ItemStatus,
    ItemValidationError,
    LIFECYCLE,
    StatusEvent,
    TERMINAL_STATUSES,
    TrackedItem,
    assert_status_transition,
    legal_status_transition,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _item(status=ItemStatus.OPEN, **kw):
    base = dict(id="INS-099", title="a tracked thing", status=status)
    base.update(kw)
    return TrackedItem(**base)


# ---------------------------------------------------------------------------
# enums
# ---------------------------------------------------------------------------

def test_status_members_are_strings():
    assert ItemStatus.OPEN == "OPEN"
    assert ItemStatus.RESOLVED.value == "RESOLVED"
    # usable directly as a dict key / json value
    assert {ItemStatus.OPEN: 1}["OPEN"] == 1


def test_status_has_exactly_six_members():
    assert {s.value for s in ItemStatus} == {
        "OPEN", "IN_PROGRESS", "RESOLVED", "DEFERRED", "SUPERSEDED", "DISCARDED",
    }


def test_kind_members():
    assert ItemKind.TASK == "TASK"
    assert {k.value for k in ItemKind} >= {"TASK", "INFERENCE", "ERROR"}


# ---------------------------------------------------------------------------
# lifecycle table structure
# ---------------------------------------------------------------------------

def test_lifecycle_defines_every_status_as_source():
    assert set(LIFECYCLE) == set(ItemStatus)


def test_lifecycle_targets_are_all_known_statuses():
    for src, targets in LIFECYCLE.items():
        assert set(targets) <= set(ItemStatus), src


def test_terminal_statuses_are_exactly_the_sinks():
    assert TERMINAL_STATUSES == {
        ItemStatus.RESOLVED, ItemStatus.SUPERSEDED, ItemStatus.DISCARDED,
    }
    for s in TERMINAL_STATUSES:
        assert LIFECYCLE[s] == frozenset()


def test_directive_edges_present():
    # OPEN -> IN_PROGRESS
    assert ItemStatus.IN_PROGRESS in LIFECYCLE[ItemStatus.OPEN]
    # IN_PROGRESS -> {RESOLVED, DEFERRED, SUPERSEDED, DISCARDED}
    assert LIFECYCLE[ItemStatus.IN_PROGRESS] == frozenset({
        ItemStatus.RESOLVED, ItemStatus.DEFERRED,
        ItemStatus.SUPERSEDED, ItemStatus.DISCARDED,
    })
    # DEFERRED <-> OPEN
    assert ItemStatus.DEFERRED in LIFECYCLE[ItemStatus.OPEN]
    assert ItemStatus.OPEN in LIFECYCLE[ItemStatus.DEFERRED]


# ---------------------------------------------------------------------------
# transition guards
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("src,dst", [
    (ItemStatus.OPEN, ItemStatus.IN_PROGRESS),
    (ItemStatus.OPEN, ItemStatus.DEFERRED),
    (ItemStatus.OPEN, ItemStatus.SUPERSEDED),
    (ItemStatus.OPEN, ItemStatus.DISCARDED),
    (ItemStatus.IN_PROGRESS, ItemStatus.RESOLVED),
    (ItemStatus.IN_PROGRESS, ItemStatus.DEFERRED),
    (ItemStatus.DEFERRED, ItemStatus.OPEN),
])
def test_legal_transitions_true(src, dst):
    assert legal_status_transition(src, dst) is True


@pytest.mark.parametrize("src,dst", [
    (ItemStatus.OPEN, ItemStatus.RESOLVED),        # must pass through IN_PROGRESS
    (ItemStatus.RESOLVED, ItemStatus.OPEN),        # terminal
    (ItemStatus.SUPERSEDED, ItemStatus.IN_PROGRESS),
    (ItemStatus.DISCARDED, ItemStatus.OPEN),
    (ItemStatus.DEFERRED, ItemStatus.RESOLVED),    # resume only via OPEN
    (ItemStatus.IN_PROGRESS, ItemStatus.OPEN),     # no direct un-start
])
def test_illegal_transitions_false(src, dst):
    assert legal_status_transition(src, dst) is False


def test_legal_transition_accepts_strings():
    assert legal_status_transition("OPEN", "IN_PROGRESS") is True
    assert legal_status_transition("OPEN", "RESOLVED") is False


def test_unknown_status_raises():
    with pytest.raises(ItemStateError):
        legal_status_transition("OPEN", "BOGUS")
    with pytest.raises(ItemStateError):
        assert_status_transition("NOPE", "OPEN")


def test_assert_passes_on_legal():
    assert assert_status_transition(ItemStatus.OPEN, ItemStatus.IN_PROGRESS) is None


def test_assert_raises_on_illegal_with_allowed_set():
    with pytest.raises(ItemStateError) as ei:
        assert_status_transition(ItemStatus.OPEN, ItemStatus.RESOLVED)
    msg = str(ei.value)
    assert "OPEN -> RESOLVED" in msg
    assert "IN_PROGRESS" in msg  # lists what IS allowed


def test_assert_terminal_reports_none_allowed():
    with pytest.raises(ItemStateError) as ei:
        assert_status_transition(ItemStatus.RESOLVED, ItemStatus.OPEN)
    assert "terminal" in str(ei.value)


# ---------------------------------------------------------------------------
# reachability sanity
# ---------------------------------------------------------------------------

def test_every_terminal_reachable_from_open():
    # BFS from OPEN over LIFECYCLE
    seen, frontier = set(), [ItemStatus.OPEN]
    while frontier:
        s = frontier.pop()
        if s in seen:
            continue
        seen.add(s)
        frontier.extend(LIFECYCLE[s])
    assert TERMINAL_STATUSES <= seen


# ---------------------------------------------------------------------------
# TrackedItem construction + validation
# ---------------------------------------------------------------------------

def test_construct_basic():
    it = _item()
    assert it.status is ItemStatus.OPEN
    assert it.kind is ItemKind.TASK
    assert it.is_terminal is False


def test_construct_coerces_string_status_and_kind():
    it = TrackedItem(id="X", title="t", status="OPEN", kind="ERROR")
    assert it.status is ItemStatus.OPEN
    assert it.kind is ItemKind.ERROR


def test_empty_id_rejected():
    with pytest.raises(ItemValidationError):
        TrackedItem(id="  ", title="t", status=ItemStatus.OPEN)


def test_empty_title_rejected():
    with pytest.raises(ItemValidationError):
        TrackedItem(id="X", title="", status=ItemStatus.OPEN)


def test_unknown_kind_rejected():
    with pytest.raises(ItemValidationError):
        TrackedItem(id="X", title="t", status=ItemStatus.OPEN, kind="WIDGET")


def test_superseded_requires_superseded_by():
    with pytest.raises(ItemValidationError):
        TrackedItem(id="X", title="t", status=ItemStatus.SUPERSEDED)


def test_superseded_by_on_non_superseded_rejected():
    with pytest.raises(ItemValidationError):
        TrackedItem(id="X", title="t", status=ItemStatus.OPEN,
                    superseded_by="INS-100")


def test_superseded_with_ref_ok():
    it = TrackedItem(id="X", title="t", status=ItemStatus.SUPERSEDED,
                     superseded_by="INS-100")
    assert it.superseded_by == "INS-100"
    assert it.is_terminal is True


def test_frozen_is_immutable():
    it = _item()
    with pytest.raises(dataclasses.FrozenInstanceError):
        it.status = ItemStatus.RESOLVED  # type: ignore[misc]


# ---------------------------------------------------------------------------
# with_status
# ---------------------------------------------------------------------------

def test_with_status_returns_new_item_and_keeps_original():
    it = _item()
    nxt = it.with_status(ItemStatus.IN_PROGRESS, session="S48")
    assert nxt is not it
    assert it.status is ItemStatus.OPEN          # original untouched
    assert nxt.status is ItemStatus.IN_PROGRESS
    assert nxt.session == "S48"


def test_with_status_appends_history():
    it = _item()
    nxt = it.with_status(ItemStatus.IN_PROGRESS, session="S48", reason="started")
    assert len(nxt.history) == 1
    ev = nxt.history[0]
    assert ev.from_status is ItemStatus.OPEN
    assert ev.to_status is ItemStatus.IN_PROGRESS
    assert ev.session == "S48"
    assert ev.reason == "started"


def test_with_status_illegal_raises():
    it = _item()
    with pytest.raises(ItemStateError):
        it.with_status(ItemStatus.RESOLVED, session="S48")


def test_with_status_to_superseded_requires_ref():
    it = _item(status=ItemStatus.IN_PROGRESS)
    with pytest.raises(ItemValidationError):
        it.with_status(ItemStatus.SUPERSEDED, session="S48")
    ok = it.with_status(ItemStatus.SUPERSEDED, session="S48",
                        superseded_by="INS-100")
    assert ok.superseded_by == "INS-100"


def test_with_status_clears_superseded_by_on_non_supersede():
    it = _item(status=ItemStatus.DEFERRED)
    nxt = it.with_status(ItemStatus.OPEN, session="S48")
    assert nxt.superseded_by is None


def test_resume_path_deferred_open_in_progress():
    it = _item().with_status(ItemStatus.DEFERRED, session="S1")
    it = it.with_status(ItemStatus.OPEN, session="S2")
    it = it.with_status(ItemStatus.IN_PROGRESS, session="S3")
    assert it.status is ItemStatus.IN_PROGRESS
    assert len(it.history) == 3
    assert [e.to_status.value for e in it.history] == [
        "DEFERRED", "OPEN", "IN_PROGRESS",
    ]


def test_full_lifecycle_to_resolved():
    it = _item()
    it = it.with_status(ItemStatus.IN_PROGRESS, session="S48")
    it = it.with_status(ItemStatus.RESOLVED, session="S48", reason="done")
    assert it.is_terminal is True
    with pytest.raises(ItemStateError):
        it.with_status(ItemStatus.OPEN, session="S49")


# ---------------------------------------------------------------------------
# serialization
# ---------------------------------------------------------------------------

def test_to_dict_has_canonical_status_string():
    it = _item(status=ItemStatus.OPEN, kind=ItemKind.INFERENCE, note="ctx")
    d = it.to_dict()
    assert d["status"] == "OPEN"
    assert d["kind"] == "INFERENCE"
    assert d["note"] == "ctx"
    assert d["history"] == []


def test_round_trip_preserves_everything():
    it = _item().with_status(ItemStatus.IN_PROGRESS, session="S48", reason="go")
    it = it.with_status(ItemStatus.SUPERSEDED, session="S49",
                        superseded_by="INS-100")
    back = TrackedItem.from_dict(it.to_dict())
    assert back == it
    assert back.superseded_by == "INS-100"
    assert len(back.history) == 2
    assert back.history[0].reason == "go"


def test_status_event_round_trip():
    ev = StatusEvent(ItemStatus.OPEN, ItemStatus.IN_PROGRESS, "S48", "r")
    assert StatusEvent.from_dict(ev.to_dict()) == ev
