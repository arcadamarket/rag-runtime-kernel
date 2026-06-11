"""Tests for the guarded `add` CLI verb (ENV-NORM increment 1).

The verb is the missing CLI path to introduce a BRAND-NEW canonical tracked item
(the lifecycle verbs only transition existing items; migrate_backlog refuses a
non-empty array). It wires drift_store.add_items_file: one validated spec ->
unique-id invariant -> atomic write (tmp -> verify -> .bak -> rename). A duplicate
id, an unknown status/kind, or a SUPERSEDED add without --by all fail LOUD and
write nothing. No new module — CLI-only, health stays 20/20.
"""
import json

import pytest

from rag_kernel.__main__ import main


def _make_rag(tmp_path, items=None):
    rag = tmp_path / "RAG_MASTER.json"
    rag.write_text(json.dumps({
        "meta": {"last_checkpoint_seq": 1, "written_by_session": "T"},
        "tracked_items": items or [],
    }), encoding="utf-8")
    return rag


def _items(rag):
    return json.loads(rag.read_text(encoding="utf-8"))["tracked_items"]


def test_add_new_item(tmp_path, capsys):
    rag = _make_rag(tmp_path)
    rc = main(["add", "ENV-NORM", "ENV normalization milestone",
               "--rag", str(rag), "--status", "IN_PROGRESS",
               "--kind", "MILESTONE", "--session", "S65"])
    assert rc == 0
    items = _items(rag)
    assert len(items) == 1
    assert items[0]["id"] == "ENV-NORM"
    assert items[0]["status"] == "IN_PROGRESS"
    assert items[0]["kind"] == "MILESTONE"


def test_add_duplicate_fails_loud(tmp_path, capsys):
    rag = _make_rag(tmp_path)
    main(["add", "X", "first", "--rag", str(rag), "--session", "S65"])
    rc = main(["add", "X", "second", "--rag", str(rag), "--session", "S65"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "already exists" in err
    # the original is untouched and nothing duplicated
    items = _items(rag)
    assert len(items) == 1
    assert items[0]["title"] == "first"


def test_add_dry_run_writes_nothing(tmp_path, capsys):
    rag = _make_rag(tmp_path)
    rc = main(["add", "Y", "title", "--rag", str(rag), "--session", "S65", "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "[DRY RUN]" in out
    assert _items(rag) == []


def test_add_unknown_status(tmp_path, capsys):
    rag = _make_rag(tmp_path)
    rc = main(["add", "Z", "t", "--rag", str(rag), "--session", "S65", "--status", "BOGUS"])
    assert rc == 1
    assert "unknown status" in capsys.readouterr().err
    assert _items(rag) == []


def test_add_unknown_kind(tmp_path, capsys):
    rag = _make_rag(tmp_path)
    rc = main(["add", "Z", "t", "--rag", str(rag), "--session", "S65", "--kind", "BOGUS"])
    assert rc == 1
    assert "unknown kind" in capsys.readouterr().err


def test_add_supersede_requires_by(tmp_path, capsys):
    rag = _make_rag(tmp_path)
    rc = main(["add", "Z", "t", "--rag", str(rag), "--session", "S65", "--status", "SUPERSEDED"])
    assert rc == 1
    assert "requires --by" in capsys.readouterr().err
    assert _items(rag) == []


def test_add_supersede_with_by(tmp_path, capsys):
    rag = _make_rag(tmp_path)
    rc = main(["add", "OLD", "t", "--rag", str(rag), "--session", "S65",
               "--status", "SUPERSEDED", "--by", "NEW"])
    assert rc == 0
    items = _items(rag)
    assert items[0]["superseded_by"] == "NEW"


def test_add_missing_rag(tmp_path, capsys):
    rc = main(["add", "Z", "t", "--rag", str(tmp_path / "nope.json"), "--session", "S65"])
    assert rc == 1
    assert "not found" in capsys.readouterr().err
