"""KA-13 — wire the Rule 11 published-doc reconciliation into the session close.

The governed close (``session-end`` / ``session-resume``) ran its step-3 audit with
``docs_root=None``, so the Rule 11 reconciliation of published docs against the
canonical records never ran at close — the exact recurring pass RECONCILE-PASS-
RECURRING wanted mechanized. KA-13 resolves a ``docs_root`` for the close audit with
a back-compatible precedence:

    --no-reconcile  >  --docs-root PATH  >  meta.reconciliation_docs_root  >  (skip)

An un-migrated RAG (no declaration, no flag) still closes byte-for-byte as before
(docs_root=None => reconciliation dormant). These tests pin the resolver precedence
and prove the resolved value is threaded into the close-time audit call.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import rag_kernel.__main__ as m
from rag_kernel.__main__ import main, _resolve_close_docs_root
from rag_kernel.session_logger import LOG_FILE_PREFIX, LOG_FILE_EXT


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _write_rag(tmp_path: Path, *, reconcile_docs_root=None,
               written_by="S0", seq=1, under_rag_dir=False) -> Path:
    meta = {"written_by_session": written_by, "last_checkpoint_seq": seq}
    if reconcile_docs_root is not None:
        meta["reconciliation_docs_root"] = reconcile_docs_root
    root = tmp_path
    if under_rag_dir:
        root = tmp_path / "RAG"
        root.mkdir()
    rag_path = root / "RAG_MASTER.json"
    rag_path.write_text(
        json.dumps({"meta": meta, "sessions_recent": []}), encoding="utf-8")
    return rag_path


def _ns(rag_path, *, docs_root=None, no_reconcile=False) -> argparse.Namespace:
    return argparse.Namespace(rag=rag_path, docs_root=docs_root,
                              no_reconcile=no_reconcile)


def _start_logger(tmp_path: Path, sid: str) -> None:
    assert main(["session", "start", sid, "--rag-dir", str(tmp_path)]) == 0


def _capturing_audit(seen: dict):
    """A cmd_audit stand-in that records docs_root and always reports clean (0)."""
    def _audit(args):
        seen["docs_root"] = args.docs_root
        return 0
    return _audit


# ---------------------------------------------------------------------------
# resolver precedence
# ---------------------------------------------------------------------------

def test_undeclared_resolves_to_none_backcompat(tmp_path):
    rag = _write_rag(tmp_path)
    assert _resolve_close_docs_root(rag, _ns(rag)) is None


def test_no_reconcile_forces_none_even_when_declared(tmp_path):
    rag = _write_rag(tmp_path, reconcile_docs_root="/some/where")
    assert _resolve_close_docs_root(
        rag, _ns(rag, docs_root="/other", no_reconcile=True)) is None


def test_docs_root_flag_overrides_declared(tmp_path):
    rag = _write_rag(tmp_path, reconcile_docs_root="/declared/root")
    got = _resolve_close_docs_root(rag, _ns(rag, docs_root="/override/root"))
    assert Path(got) == Path("/override/root")


def test_declared_meta_used_when_no_flag(tmp_path):
    rag = _write_rag(tmp_path, reconcile_docs_root="/declared/root")
    got = _resolve_close_docs_root(rag, _ns(rag))
    assert Path(got) == Path("/declared/root")


def test_relative_declared_resolves_against_project_root(tmp_path):
    # RAG/RAG_MASTER.json => project root is the grandparent (tmp_path).
    rag = _write_rag(tmp_path, reconcile_docs_root="worktree/docs",
                     under_rag_dir=True)
    got = _resolve_close_docs_root(rag, _ns(rag))
    assert Path(got) == (tmp_path / "worktree" / "docs")


def test_blank_declaration_is_ignored(tmp_path):
    rag = _write_rag(tmp_path, reconcile_docs_root="   ")
    assert _resolve_close_docs_root(rag, _ns(rag)) is None


# ---------------------------------------------------------------------------
# threading into the close-time audit (session-end)
# ---------------------------------------------------------------------------

def test_session_end_threads_declared_docs_root_into_audit(tmp_path, monkeypatch):
    rag = _write_rag(tmp_path, reconcile_docs_root=str(tmp_path / "wt"))
    _start_logger(tmp_path, "S1")
    seen = {}
    monkeypatch.setattr(m, "cmd_audit", _capturing_audit(seen))

    rc = main(["session-end", "--rag", str(rag), "--session", "S1", "--summary", "x"])
    assert rc == 0
    assert Path(seen["docs_root"]) == (tmp_path / "wt")


def test_session_end_docs_root_flag_beats_declaration(tmp_path, monkeypatch):
    rag = _write_rag(tmp_path, reconcile_docs_root=str(tmp_path / "declared"))
    _start_logger(tmp_path, "S1")
    seen = {}
    monkeypatch.setattr(m, "cmd_audit", _capturing_audit(seen))

    rc = main(["session-end", "--rag", str(rag), "--session", "S1", "--summary", "x",
               "--docs-root", str(tmp_path / "override")])
    assert rc == 0
    assert Path(seen["docs_root"]) == (tmp_path / "override")


def test_session_end_no_reconcile_passes_none(tmp_path, monkeypatch):
    rag = _write_rag(tmp_path, reconcile_docs_root=str(tmp_path / "wt"))
    _start_logger(tmp_path, "S1")
    seen = {}
    monkeypatch.setattr(m, "cmd_audit", _capturing_audit(seen))

    rc = main(["session-end", "--rag", str(rag), "--session", "S1", "--summary", "x",
               "--no-reconcile"])
    assert rc == 0
    assert seen["docs_root"] is None


def test_session_end_undeclared_passes_none_backcompat(tmp_path, monkeypatch):
    rag = _write_rag(tmp_path)  # no declaration
    _start_logger(tmp_path, "S1")
    seen = {}
    monkeypatch.setattr(m, "cmd_audit", _capturing_audit(seen))

    rc = main(["session-end", "--rag", str(rag), "--session", "S1", "--summary", "x"])
    assert rc == 0
    assert "docs_root" in seen and seen["docs_root"] is None  # audit got None
