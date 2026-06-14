"""FIX-6 (K9) — layout-aware ``--rag`` default (no doubled ``RAG/RAG``).

The historical CLI default ``RAG/RAG_MASTER.json`` assumes a run-from-root cwd. In
the eBay Session-Zero nested deploy (``rag_kernel/`` under ``RAG/``), running from
inside the RAG dir made that default resolve to ``RAG/RAG/RAG_MASTER.json`` — a
doubled path that simply errors "not found" (K9).

``_default_rag_path()`` resolves the RAG whether invoked from the project root or
from inside the RAG dir, returning the first existing candidate and never
prepending ``RAG/`` to a path already in the RAG dir.
"""
import json
from pathlib import Path

import pytest

from rag_kernel.__main__ import _default_rag_path, build_parser, main


def _write_rag(path: Path) -> Path:
    path.write_text(json.dumps({
        "meta": {"last_checkpoint_seq": 1, "written_by_session": "T"},
        "operating_protocol": {},
        "tracked_items": [],
        "open_tasks": [],
        "deferred_items": [],
    }), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# _default_rag_path — resolution
# ---------------------------------------------------------------------------

def test_default_from_project_root(tmp_path, monkeypatch):
    rag_dir = tmp_path / "RAG"
    rag_dir.mkdir()
    _write_rag(rag_dir / "RAG_MASTER.json")
    monkeypatch.chdir(tmp_path)               # project root
    assert _default_rag_path() == Path("RAG") / "RAG_MASTER.json"
    assert _default_rag_path().exists()


def test_default_from_inside_rag_dir_no_doubling(tmp_path, monkeypatch):
    # cwd IS the RAG dir: RAG_MASTER.json present, no RAG/ subdir -> must NOT
    # resolve to RAG/RAG_MASTER.json (the K9 doubled path).
    _write_rag(tmp_path / "RAG_MASTER.json")
    monkeypatch.chdir(tmp_path)
    resolved = _default_rag_path()
    assert resolved == Path("RAG_MASTER.json")
    assert resolved.exists()
    assert resolved != Path("RAG") / "RAG_MASTER.json"


def test_default_prefers_root_layout_when_both_present(tmp_path, monkeypatch):
    # If both RAG/RAG_MASTER.json and ./RAG_MASTER.json exist, the canonical
    # root layout wins (deterministic candidate order).
    rag_dir = tmp_path / "RAG"
    rag_dir.mkdir()
    _write_rag(rag_dir / "RAG_MASTER.json")
    _write_rag(tmp_path / "RAG_MASTER.json")
    monkeypatch.chdir(tmp_path)
    assert _default_rag_path() == Path("RAG") / "RAG_MASTER.json"


def test_default_fallback_when_absent(tmp_path, monkeypatch):
    # Neither candidate exists -> canonical root-layout path (sensible not-found).
    monkeypatch.chdir(tmp_path)
    assert _default_rag_path() == Path("RAG") / "RAG_MASTER.json"
    assert not _default_rag_path().exists()


# ---------------------------------------------------------------------------
# wiring — the parsed --rag default is layout-aware
# ---------------------------------------------------------------------------

def test_audit_parser_default_is_layout_aware(tmp_path, monkeypatch):
    _write_rag(tmp_path / "RAG_MASTER.json")
    monkeypatch.chdir(tmp_path)               # inside the RAG dir
    args = build_parser().parse_args(["audit"])
    assert args.rag == Path("RAG_MASTER.json")
    assert args.rag.exists()                  # NOT the doubled RAG/RAG path


@pytest.mark.parametrize("cmd", ["audit", "items", "render", "verify", "note", "add"])
def test_all_rag_commands_share_layout_aware_default(cmd, tmp_path, monkeypatch):
    _write_rag(tmp_path / "RAG_MASTER.json")
    monkeypatch.chdir(tmp_path)
    # Parse just enough to read the resolved --rag default for each command.
    base = {
        "audit": ["audit"],
        "items": ["items"],
        "render": ["render"],
        "verify": ["verify"],
        "note": ["note", "FIX-1", "n", "--session", "S"],
        "add": ["add", "NEW-1", "title", "--session", "S"],
    }[cmd]
    args = build_parser().parse_args(base)
    assert args.rag == Path("RAG_MASTER.json")


# ---------------------------------------------------------------------------
# end-to-end — the K9 repro is fixed (no "not found" from the doubled path)
# ---------------------------------------------------------------------------

def test_cli_items_from_rag_dir_finds_file(tmp_path, monkeypatch, capsys):
    # Reproduces the K9 invocation: run a RAG command from inside the RAG dir
    # with no --rag. Pre-fix this errored "RAG file not found: .../RAG/RAG/...".
    _write_rag(tmp_path / "RAG_MASTER.json")
    monkeypatch.chdir(tmp_path)
    rc = main(["items"])
    err = capsys.readouterr().err
    assert "not found" not in err.lower()
    assert rc == 0
