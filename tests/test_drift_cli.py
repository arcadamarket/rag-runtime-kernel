"""Tests for the DRIFT-ELIM increment 3 item-lifecycle CLI.

Covers the top-level verbs (resolve / defer / reopen / start / discard /
supersede) and the read-only ``items`` renderer, all routed through the
drift_store mutation API over a RAG_MASTER.json ``tracked_items`` array.

Contract under test:
- legal transitions write atomically and append history (exit 0);
- illegal transitions, unknown ids, and missing files fail LOUD and write
  NOTHING (exit 1);
- --dry-run never writes;
- ``items`` is a pure read-only render (never mutates).
"""

import json

import pytest

from rag_kernel.__main__ import main


# ===== Fixtures =====

def _item(item_id, status, *, kind="TASK", title=None, superseded_by=None):
    return {
        "id": item_id,
        "title": title or f"item {item_id}",
        "status": status,
        "kind": kind,
        "session": "S49",
        "note": "",
        "superseded_by": superseded_by,
        "history": [],
    }


@pytest.fixture
def rag_path(tmp_path):
    """A RAG_MASTER.json with one item in each interesting starting status."""
    hot = {
        "meta": {"last_checkpoint_seq": 49, "last_updated_utc": "2026-06-06T00:00:00Z"},
        "tracked_items": [
            _item("OPEN-1", "OPEN"),
            _item("PROG-1", "IN_PROGRESS"),
            _item("DEF-1", "DEFERRED"),
            _item("OPEN-2", "OPEN", kind="MILESTONE"),
        ],
    }
    p = tmp_path / "RAG_MASTER.json"
    p.write_text(json.dumps(hot, indent=2), encoding="utf-8")
    return p


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _status_of(path, item_id):
    for it in _load(path)["tracked_items"]:
        if it["id"] == item_id:
            return it["status"]
    raise KeyError(item_id)


# ===== Legal transitions =====

class TestLegalTransitions:
    def test_resolve_in_progress(self, rag_path):
        rc = main(["resolve", "PROG-1", "--rag", str(rag_path), "--session", "S50"])
        assert rc == 0
        assert _status_of(rag_path, "PROG-1") == "RESOLVED"

    def test_defer_open(self, rag_path):
        rc = main(["defer", "OPEN-1", "--rag", str(rag_path), "--session", "S50"])
        assert rc == 0
        assert _status_of(rag_path, "OPEN-1") == "DEFERRED"

    def test_reopen_deferred(self, rag_path):
        rc = main(["reopen", "DEF-1", "--rag", str(rag_path), "--session", "S50"])
        assert rc == 0
        assert _status_of(rag_path, "DEF-1") == "OPEN"

    def test_start_open(self, rag_path):
        rc = main(["start", "OPEN-1", "--rag", str(rag_path), "--session", "S50"])
        assert rc == 0
        assert _status_of(rag_path, "OPEN-1") == "IN_PROGRESS"

    def test_discard_open(self, rag_path):
        rc = main(["discard", "OPEN-1", "--rag", str(rag_path), "--session", "S50"])
        assert rc == 0
        assert _status_of(rag_path, "OPEN-1") == "DISCARDED"

    def test_supersede_requires_by_and_sets_ref(self, rag_path):
        rc = main([
            "supersede", "OPEN-1", "--by", "OPEN-2",
            "--rag", str(rag_path), "--session", "S50",
        ])
        assert rc == 0
        item = next(it for it in _load(rag_path)["tracked_items"] if it["id"] == "OPEN-1")
        assert item["status"] == "SUPERSEDED"
        assert item["superseded_by"] == "OPEN-2"

    def test_transition_appends_history(self, rag_path):
        main(["resolve", "PROG-1", "--rag", str(rag_path), "--session", "S50", "--reason", "done"])
        item = next(it for it in _load(rag_path)["tracked_items"] if it["id"] == "PROG-1")
        assert len(item["history"]) == 1
        ev = item["history"][0]
        assert ev["from_status"] == "IN_PROGRESS"
        assert ev["to_status"] == "RESOLVED"
        assert ev["session"] == "S50"
        assert ev["reason"] == "done"


# ===== Fail-loud paths (must write nothing) =====

class TestFailLoud:
    def test_illegal_transition_exits_1_no_write(self, rag_path):
        before = rag_path.read_text(encoding="utf-8")
        # RESOLVED is only reachable from IN_PROGRESS; OPEN-1 is OPEN.
        rc = main(["resolve", "OPEN-1", "--rag", str(rag_path), "--session", "S50"])
        assert rc == 1
        assert rag_path.read_text(encoding="utf-8") == before

    def test_unknown_id_exits_1_no_write(self, rag_path):
        before = rag_path.read_text(encoding="utf-8")
        rc = main(["resolve", "NOPE-9", "--rag", str(rag_path), "--session", "S50"])
        assert rc == 1
        assert rag_path.read_text(encoding="utf-8") == before

    def test_missing_rag_exits_1(self, tmp_path):
        rc = main(["resolve", "X", "--rag", str(tmp_path / "absent.json"), "--session", "S50"])
        assert rc == 1

    def test_supersede_without_by_is_parser_error(self, rag_path):
        with pytest.raises(SystemExit):
            main(["supersede", "OPEN-1", "--rag", str(rag_path), "--session", "S50"])

    def test_session_is_required(self, rag_path):
        with pytest.raises(SystemExit):
            main(["resolve", "PROG-1", "--rag", str(rag_path)])


# ===== Dry run =====

class TestDryRun:
    def test_dry_run_legal_no_write(self, rag_path):
        before = rag_path.read_text(encoding="utf-8")
        rc = main(["resolve", "PROG-1", "--rag", str(rag_path), "--session", "S50", "--dry-run"])
        assert rc == 0
        assert rag_path.read_text(encoding="utf-8") == before

    def test_dry_run_illegal_exits_1_no_write(self, rag_path):
        before = rag_path.read_text(encoding="utf-8")
        rc = main(["resolve", "OPEN-1", "--rag", str(rag_path), "--session", "S50", "--dry-run"])
        assert rc == 1
        assert rag_path.read_text(encoding="utf-8") == before


# ===== items renderer (read-only) =====

class TestItemsRender:
    def test_items_lists_all(self, rag_path, capsys):
        rc = main(["items", "--rag", str(rag_path)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "OPEN-1" in out and "PROG-1" in out and "DEF-1" in out

    def test_items_status_filter(self, rag_path, capsys):
        rc = main(["items", "--rag", str(rag_path), "--status", "OPEN"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "OPEN-1" in out and "OPEN-2" in out
        assert "PROG-1" not in out and "DEF-1" not in out

    def test_items_kind_filter(self, rag_path, capsys):
        rc = main(["items", "--rag", str(rag_path), "--kind", "MILESTONE"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "OPEN-2" in out and "OPEN-1" not in out

    def test_items_json_output_parses(self, rag_path, capsys):
        rc = main(["items", "--rag", str(rag_path), "--json"])
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert {it["id"] for it in data} == {"OPEN-1", "PROG-1", "DEF-1", "OPEN-2"}

    def test_items_never_mutates(self, rag_path):
        before = rag_path.read_text(encoding="utf-8")
        main(["items", "--rag", str(rag_path)])
        assert rag_path.read_text(encoding="utf-8") == before


# ===== render (DRIFT-ELIM increment 4) =====

class TestRenderCommand:
    def test_render_dry_run_prints_and_does_not_write(self, rag_path, capsys):
        before = rag_path.read_text(encoding="utf-8")
        rc = main(["render", "--rag", str(rag_path)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "open_tasks (render)" in out
        assert rag_path.read_text(encoding="utf-8") == before  # dry-run never writes

    def test_render_json_emits_all_sections(self, rag_path, capsys):
        rc = main(["render", "--rag", str(rag_path), "--json"])
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert set(data) == {"open_tasks", "deferred_items", "backlog"}

    def test_render_apply_rewrites_legacy_arrays(self, rag_path):
        # seed a stale hand-authored open_tasks to prove it gets overwritten
        hot = _load(rag_path)
        hot["open_tasks"] = ["STALE hand-authored entry"]
        rag_path.write_text(json.dumps(hot, indent=2), encoding="utf-8")

        rc = main(["render", "--rag", str(rag_path), "--apply"])
        assert rc == 0
        after = _load(rag_path)
        # only OPEN / IN_PROGRESS items render into open_tasks (DEF-1 excluded)
        joined = json.dumps(after["open_tasks"])
        assert "STALE" not in joined
        assert "OPEN-1" in joined and "PROG-1" in joined and "OPEN-2" in joined
        assert "DEF-1" not in joined
        # deferred_items holds exactly the DEFERRED item
        assert [o["id"] for o in after["deferred_items"]] == ["DEF-1"]
        # canonical array preserved
        assert len(after["tracked_items"]) == 4

    def test_render_apply_is_idempotent(self, rag_path):
        main(["render", "--rag", str(rag_path), "--apply"])
        first = _load(rag_path)
        main(["render", "--rag", str(rag_path), "--apply"])
        second = _load(rag_path)
        assert first["open_tasks"] == second["open_tasks"]
        assert first["deferred_items"] == second["deferred_items"]

    def test_render_missing_rag_exits_1(self, tmp_path):
        rc = main(["render", "--rag", str(tmp_path / "absent.json")])
        assert rc == 1

    def test_render_error_log_what(self, rag_path, capsys):
        rc = main(["render", "--rag", str(rag_path), "--what", "error_log"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Backlog status" in out and "### Deferred" in out
