"""`doctor --recover` — the scripted RECOVERY ADVISOR (BOOT-PROSE-TO-SCRIPT).

One governed verb absorbs the Project Instructions' ".bak -> COLD + WAL ->
rebuild" recovery prose so the PI's RECOVERY EXCEPTION collapses to a pointer.

Contract pinned here:
  * healthy HOT -> action "none", exit 0, nothing written,
  * corrupt HOT + valid .bak, no --fix -> "bak_available", exit 1, HOT untouched,
  * corrupt HOT + valid .bak, --fix -> "bak_restored", exit 0, HOT == .bak,
  * corrupt HOT + no/invalid .bak -> "manual", exit 1, COLD/WAL/rebuild offered
    (never auto-reconstructed),
  * assessment is fail-safe: a corrupt file degrades to a recommendation, no raise.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import rag_kernel.__main__ as m
from rag_kernel.persistence import atomic_write_json


def _seed_healthy(tmp_path: Path) -> Path:
    """Write a hash-clean HOT + parity .bak via the kernel's own writer."""
    rag = {"meta": {"written_by_session": "S1"}, "operating_protocol": {}, "tracked_items": []}
    hot = tmp_path / "RAG_MASTER.json"
    atomic_write_json(hot, rag, mirror_bak=True)  # writes HOT + hash-clean .bak
    return hot


def _run(hot: Path, fix: bool = False) -> dict:
    return m._doctor_recover(hot, do_fix=fix)


def test_healthy_hot_needs_no_recovery(tmp_path):
    hot = _seed_healthy(tmp_path)
    r = _run(hot)
    assert r["hot_ok"] is True and r["action"] == "none"


def test_corrupt_hot_valid_bak_assess_only(tmp_path):
    hot = _seed_healthy(tmp_path)
    hot.write_text("{ corrupt", encoding="utf-8")
    r = _run(hot, fix=False)
    assert r["hot_ok"] is False
    assert r["bak"]["valid"] is True
    assert r["action"] == "bak_available"
    # read-only: HOT still corrupt
    assert hot.read_text(encoding="utf-8") == "{ corrupt"


def test_corrupt_hot_valid_bak_fix_restores(tmp_path):
    hot = _seed_healthy(tmp_path)
    bak = hot.with_suffix(hot.suffix + ".bak")
    good = bak.read_bytes()
    hot.write_text("{ corrupt", encoding="utf-8")
    r = _run(hot, fix=True)
    assert r["action"] == "bak_restored"
    assert hot.read_bytes() == good  # HOT restored byte-for-byte from .bak


def test_corrupt_hot_no_bak_offers_manual(tmp_path):
    hot = _seed_healthy(tmp_path)
    hot.with_suffix(hot.suffix + ".bak").unlink()
    hot.write_text("x", encoding="utf-8")
    r = _run(hot, fix=True)
    assert r["action"] == "manual"
    assert any("Stage 3" in s for s in r["recommended"])


def test_cli_recover_exit_codes(tmp_path, capsys):
    hot = _seed_healthy(tmp_path)
    ns = argparse.Namespace(
        path=tmp_path, rag=hot, emit_runner=None, recover=True,
        fix=False, stale_after=60.0, json_output=False,
    )
    assert m.cmd_doctor(ns) == 0  # healthy -> 0

    hot.write_text("{ corrupt", encoding="utf-8")
    assert m.cmd_doctor(ns) == 1  # corrupt, no fix -> 1

    ns.fix = True
    assert m.cmd_doctor(ns) == 0  # corrupt, fix from valid .bak -> 0
