"""FIX-8 (E-045) — the CLI ``checkpoint`` verb must mirror ``.bak`` to byte-parity.

``api.KernelApp.checkpoint`` (do_full) already passes ``mirror_bak=True``, but the
standalone CLI ``checkpoint`` verb (``cmd_checkpoint``) wrote with a plain
``atomic_write_json(rag_path, rag)`` — leaving ``RAG_MASTER.json.bak`` one seq
behind. A session closed on the CLI ``checkpoint`` alone (no follow-up
``render --apply``) therefore left a stale ``.bak`` that ``check_bak_parity``
correctly fails loud on (the FIX-4 / K6 parity-mirror contract). FIX-8 wires
``mirror_bak=True`` into ``cmd_checkpoint`` so the CLI close honors the contract
on its own — without depending on a later mirroring write to mask the gap.
"""
import argparse
import json
from pathlib import Path

from rag_kernel.__main__ import cmd_checkpoint
from rag_kernel.drift_audit import check_bak_parity


def _bak(p: Path) -> Path:
    return p.with_suffix(p.suffix + ".bak")


def _make_rag(tmp_path: Path) -> Path:
    rag = tmp_path / "RAG_MASTER.json"
    rag.write_text(json.dumps({
        "meta": {"last_checkpoint_seq": 1, "written_by_session": "T0"},
        "sessions_recent": [],
        "tracked_items": [],
    }), encoding="utf-8")
    return rag


def _args(rag: Path, **kw) -> argparse.Namespace:
    ns = argparse.Namespace(
        rag=rag, session="S99", summary="fix8 regression",
        status=None, tasks=None, dry_run=False,
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def test_cli_checkpoint_mirrors_bak_to_byte_parity(tmp_path):
    """The whole point of FIX-8: a standalone CLI checkpoint, no follow-up write,
    yet ``.bak`` is byte-for-byte identical to the just-committed HOT."""
    rag = _make_rag(tmp_path)
    assert cmd_checkpoint(_args(rag)) == 0
    bak = _bak(rag)
    assert bak.exists()
    assert bak.read_bytes() == rag.read_bytes()


def test_cli_checkpoint_passes_audit_bak_parity_with_no_followup(tmp_path):
    """``check_bak_parity`` (the K6 invariant) must be clean immediately after a
    CLI checkpoint — previously it required a render --apply to follow (E-045)."""
    rag = _make_rag(tmp_path)
    assert cmd_checkpoint(_args(rag)) == 0
    hot = json.loads(rag.read_text(encoding="utf-8"))
    assert check_bak_parity(rag, hot) == []


def test_cli_checkpoint_seq_bumped_and_bak_tracks_it(tmp_path):
    """A second CLI checkpoint bumps the seq and the parity-mirror still holds —
    the ``.bak`` is never left one seq behind."""
    rag = _make_rag(tmp_path)
    assert cmd_checkpoint(_args(rag)) == 0
    assert cmd_checkpoint(_args(rag, session="S100")) == 0
    hot = json.loads(rag.read_text(encoding="utf-8"))
    assert hot["meta"]["last_checkpoint_seq"] == 3  # 1 -> 2 -> 3
    assert _bak(rag).read_bytes() == rag.read_bytes()
    assert check_bak_parity(rag, hot) == []
