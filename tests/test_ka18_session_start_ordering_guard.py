"""KA-18 (E-044/E-045) — checkpoint must refuse before session-start opens the log.

E-044 (S108) and E-045 (S109) were the same recurring slip: a manual
carry-forward + ``checkpoint`` run BEFORE the mechanized ``session-start`` opened
the session log. That banked state with no observability record — tripping the
KA-7 observability-coherence auditor — and left ``current_status`` stale
post-commit. KA-18 is the permanent guard: ``cmd_checkpoint`` refuses fail-loud
when no session log exists for ``--session``, forcing the mechanized
``session-start`` to run first.

Contract pinned here:
  * with the guard ON and NO session log, checkpoint refuses (rc 1) and banks
    NOTHING (seq unchanged, no .bak written),
  * with the guard ON and the session log present (session-start ran), it seals
    normally,
  * the guard is OFF for a programmatic Namespace that does not opt in (the
    session-end ritual and legacy unit tests keep working unchanged),
  * ``--no-require-session-log`` is an explicit bypass,
  * ``--dry-run`` previews the refusal without writing rather than hard-failing,
  * the CLI (``main([...])``) defaults the guard ON — the real E-044/E-045 path.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from rag_kernel.__main__ import cmd_checkpoint, main


def _bak(p: Path) -> Path:
    return p.with_suffix(p.suffix + ".bak")


def _make_rag(tmp_path: Path) -> Path:
    rag = tmp_path / "RAG_MASTER.json"
    rag.write_text(json.dumps({
        "meta": {"last_checkpoint_seq": 10, "written_by_session": "T0"},
        "sessions_recent": [],
        "tracked_items": [],
        "open_tasks": [],
        "deferred_items": [],
    }), encoding="utf-8")
    return rag


def _open_log(tmp_path: Path, sid: str = "S126") -> Path:
    """Simulate session-start having opened the session log."""
    log = tmp_path / f"session_log_{sid}.jsonl"
    log.write_text('{"event":"session_start"}\n', encoding="utf-8")
    return log


def _args(rag: Path, **kw) -> argparse.Namespace:
    ns = argparse.Namespace(
        rag=rag, session="S126", summary="ka-18 regression",
        status=None, tasks=None, dry_run=False, require_session_log=True,
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _seq(rag: Path) -> int:
    return json.loads(rag.read_text(encoding="utf-8"))["meta"]["last_checkpoint_seq"]


# ---------------------------------------------------------------------------
# guard ON
# ---------------------------------------------------------------------------

def test_refuses_and_banks_nothing_when_no_session_log(tmp_path, capsys):
    rag = _make_rag(tmp_path)
    rc = cmd_checkpoint(_args(rag))
    assert rc == 1
    assert "session-start" in capsys.readouterr().err
    assert _seq(rag) == 10           # seq NOT bumped — nothing banked
    assert not _bak(rag).exists()    # no seal happened


def test_seals_when_session_log_present(tmp_path):
    rag = _make_rag(tmp_path)
    _open_log(tmp_path)
    rc = cmd_checkpoint(_args(rag))
    assert rc == 0
    assert _seq(rag) == 11
    assert _bak(rag).exists()


# ---------------------------------------------------------------------------
# guard OFF: programmatic default + explicit bypass
# ---------------------------------------------------------------------------

def test_absent_flag_defaults_guard_off_for_programmatic_callers(tmp_path):
    # A Namespace that never sets require_session_log (the session-end ritual and
    # legacy tests) must NOT be gated — getattr default is False.
    rag = _make_rag(tmp_path)
    ns = argparse.Namespace(
        rag=rag, session="S126", summary="no flag",
        status=None, tasks=None, dry_run=False,
    )
    assert cmd_checkpoint(ns) == 0
    assert _seq(rag) == 11


def test_explicit_bypass_flag_allows_no_log(tmp_path):
    rag = _make_rag(tmp_path)
    assert cmd_checkpoint(_args(rag, require_session_log=False)) == 0
    assert _seq(rag) == 11


# ---------------------------------------------------------------------------
# dry-run previews the refusal without writing
# ---------------------------------------------------------------------------

def test_dry_run_previews_refusal_without_failing(tmp_path, capsys):
    rag = _make_rag(tmp_path)
    before = rag.read_bytes()
    rc = cmd_checkpoint(_args(rag, dry_run=True))
    assert rc == 0
    out = capsys.readouterr().out
    assert "would refuse" in out and "session-start" in out
    assert rag.read_bytes() == before


# ---------------------------------------------------------------------------
# CLI defaults the guard ON (the real E-044/E-045 path)
# ---------------------------------------------------------------------------

def test_cli_checkpoint_defaults_guard_on(tmp_path, capsys):
    rag = _make_rag(tmp_path)
    rc = main(["checkpoint", "--rag", str(rag), "--session", "S126",
               "--summary", "cli default"])
    assert rc == 1
    assert "session-start" in capsys.readouterr().err
    assert _seq(rag) == 10


def test_cli_checkpoint_bypass_flag_seals(tmp_path):
    rag = _make_rag(tmp_path)
    rc = main(["checkpoint", "--rag", str(rag), "--session", "S126",
               "--summary", "cli bypass", "--no-require-session-log"])
    assert rc == 0
    assert _seq(rag) == 11
