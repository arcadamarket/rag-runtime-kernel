"""KA-CKPT-PARITY-GATE (E-049) — checkpoint must not seal a stale render.

``tracked_items`` is the sole status authority (DRIFT-ELIM inc4); the legacy
``open_tasks`` / ``deferred_items`` arrays are a pure render of it. Any
tracked_item-mutating verb (note/resolve/defer/…) run between the last
``render --apply`` and a ``checkpoint`` invalidates those renders. Before this
gate, ``cmd_checkpoint`` sealed whatever legacy arrays were on disk, so a
verb→checkpoint sequence with no interposed render sealed a STALE render that
post-seal ``audit --strict`` flagged as an E-040-family ``render_parity`` ERROR
(this is exactly the E-049 incident, seq 141/142, S114).

The gate re-renders the legacy arrays from canonical ``tracked_items`` at seal,
so render-parity holds BY CONSTRUCTION at every checkpoint — collapsing the
fragile verb→render→checkpoint sequence to verb→checkpoint. It:
  * corrects a stale ``open_tasks`` / ``deferred_items`` before the atomic write,
  * prints a VISIBLE note when it does (never a silent mutation),
  * is a no-op (and prints nothing) when the arrays are already parity-clean,
  * leaves an un-migrated RAG (no ``tracked_items``) untouched — its legacy
    arrays are authored, not rendered, and must never be wiped,
  * previews the correction under ``--dry-run`` without writing.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from rag_kernel import drift_render
from rag_kernel.__main__ import cmd_checkpoint
from rag_kernel.drift_audit import check_render_parity
from rag_kernel.drift_store import TrackedItemStore


def _bak(p: Path) -> Path:
    return p.with_suffix(p.suffix + ".bak")


def _item(id_, title, status, kind="TASK", session="", note=""):
    return {
        "id": id_, "title": title, "status": status, "kind": kind,
        "session": session, "note": note, "superseded_by": None, "history": [],
    }


# Two canonical items: one OPEN (belongs in open_tasks) and one DEFERRED
# (belongs in deferred_items).
_TRACKED = [
    _item("ALPHA", "an open task", "OPEN", note="wip"),
    _item("BETA", "a parked task", "DEFERRED"),
]


def _rendered_open(hot):
    return drift_render.render_open_tasks(TrackedItemStore.from_hot(hot))


def _rendered_deferred(hot):
    return drift_render.render_deferred_items(TrackedItemStore.from_hot(hot))


def _make_rag(tmp_path: Path, *, stale_open, stale_deferred, tracked=_TRACKED) -> Path:
    rag = tmp_path / "RAG_MASTER.json"
    hot = {
        "meta": {"last_checkpoint_seq": 10, "written_by_session": "T0"},
        "sessions_recent": [],
        "tracked_items": tracked,
        "open_tasks": stale_open,
        "deferred_items": stale_deferred,
    }
    rag.write_text(json.dumps(hot), encoding="utf-8")
    return rag


def _args(rag: Path, **kw) -> argparse.Namespace:
    ns = argparse.Namespace(
        rag=rag, session="S126", summary="ka-ckpt-parity regression",
        status=None, tasks=None, dry_run=False,
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _load(rag: Path) -> dict:
    return json.loads(rag.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# core: a stale render is corrected at seal
# ---------------------------------------------------------------------------

def test_stale_open_tasks_is_rerendered_at_seal(tmp_path, capsys):
    # open_tasks deliberately emptied — simulates a mutating verb that added an
    # OPEN item to tracked_items without a follow-up `render --apply`.
    rag = _make_rag(tmp_path, stale_open=[], stale_deferred=None)
    assert cmd_checkpoint(_args(rag)) == 0
    hot = _load(rag)
    assert hot["open_tasks"] == _rendered_open(hot)
    assert hot["open_tasks"]  # non-empty: ALPHA rendered back in
    assert check_render_parity(hot) == []
    assert "render-parity" in capsys.readouterr().out


def test_stale_deferred_items_is_rerendered_at_seal(tmp_path, capsys):
    rag = _make_rag(tmp_path, stale_open=None, stale_deferred=[])
    assert cmd_checkpoint(_args(rag)) == 0
    hot = _load(rag)
    assert hot["deferred_items"] == _rendered_deferred(hot)
    assert check_render_parity(hot) == []
    assert "render-parity" in capsys.readouterr().out


def test_post_seal_audit_render_parity_is_clean(tmp_path):
    # The whole point of E-049: audit's render_parity check must be clean
    # immediately after a checkpoint, with NO interposed `render --apply`.
    rag = _make_rag(tmp_path, stale_open=["GARBAGE stale line"], stale_deferred=[])
    assert cmd_checkpoint(_args(rag)) == 0
    assert check_render_parity(_load(rag)) == []


# ---------------------------------------------------------------------------
# no-op when already clean: no spurious note, arrays unchanged
# ---------------------------------------------------------------------------

def test_already_clean_arrays_are_untouched_and_silent(tmp_path, capsys):
    rag = tmp_path / "RAG_MASTER.json"
    hot = {
        "meta": {"last_checkpoint_seq": 10, "written_by_session": "T0"},
        "sessions_recent": [],
        "tracked_items": _TRACKED,
    }
    # Pre-render the legacy arrays correctly.
    hot["open_tasks"] = drift_render.render_open_tasks(TrackedItemStore.from_hot(hot))
    hot["deferred_items"] = drift_render.render_deferred_items(TrackedItemStore.from_hot(hot))
    rag.write_text(json.dumps(hot), encoding="utf-8")
    before_open = list(hot["open_tasks"])

    assert cmd_checkpoint(_args(rag)) == 0
    out = capsys.readouterr().out
    assert "render-parity" not in out  # nothing to correct => no note
    assert _load(rag)["open_tasks"] == before_open


# ---------------------------------------------------------------------------
# backward safety: an un-migrated RAG (no tracked_items) is never wiped
# ---------------------------------------------------------------------------

def test_unmigrated_rag_legacy_arrays_left_intact(tmp_path, capsys):
    rag = tmp_path / "RAG_MASTER.json"
    authored = ["hand-authored legacy task that is NOT a render"]
    rag.write_text(json.dumps({
        "meta": {"last_checkpoint_seq": 10, "written_by_session": "T0"},
        "sessions_recent": [],
        "open_tasks": authored,           # no tracked_items key at all
    }), encoding="utf-8")
    assert cmd_checkpoint(_args(rag)) == 0
    hot = _load(rag)
    assert hot["open_tasks"] == authored   # untouched — not rendered away
    assert "render-parity" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# dry-run previews the correction without writing
# ---------------------------------------------------------------------------

def test_dry_run_previews_stale_render_without_writing(tmp_path, capsys):
    rag = _make_rag(tmp_path, stale_open=[], stale_deferred=[])
    before = rag.read_bytes()
    assert cmd_checkpoint(_args(rag, dry_run=True)) == 0
    out = capsys.readouterr().out
    assert "[DRY RUN]" in out
    assert "render-parity" in out and "would re-render" in out
    assert rag.read_bytes() == before  # nothing written
