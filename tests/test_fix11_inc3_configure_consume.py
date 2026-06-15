"""FIX-11 inc3 / U3 — `configure --consume` deletes the transient merge-input.

The eBay Session-Zero guide had operators drop a ``*_context.json`` into the RAG
dir and ``configure``-merge it; the side-store auditor (FIX-5 P2) then flagged
that very file on every clean deploy (the U3 contradiction). inc1+inc2 gave
NON-loaded project context a sanctioned home (``RAG_CONTEXT.json`` + the
``context`` CLI). inc3 closes the remaining HOT-merge path: when a merge-input
legitimately belongs in HOT, ``--consume`` deletes it after a *verified* merge —
one atomic, auditor-clean operation — so nothing lingers in the RAG dir.

Pinned here:
  * ``--consume`` deletes the input only AFTER a successful merge; HOT + .bak are
    committed first and .bak parity still holds,
  * without ``--consume`` the input is left untouched (control),
  * ``--consume --dry-run`` writes nothing and deletes nothing,
  * ``--consume`` refuses (fail-loud, exit 1, no delete, no merge) when the
    --context path is a canonical/sanctioned file (RAG_MASTER/.bak, RAG_COLD,
    RAG_CONTEXT.json),
  * merge semantics are unchanged by consuming.

CLI-only increment: no new module, health stays 20/20.
"""

from __future__ import annotations

import json
from pathlib import Path

from rag_kernel.__main__ import main
from rag_kernel.cold_manager import CONTEXT_FILENAME
from rag_kernel.drift_audit import check_bak_parity


def _bak(p: Path) -> Path:
    return p.with_suffix(p.suffix + ".bak")


def _make_rag(tmp_path: Path) -> Path:
    rag = tmp_path / "RAG_MASTER.json"
    rag.write_text(json.dumps({
        "meta": {
            "schema_version": "5.4",
            "policy_version": "3.2.3",
            "rag_type": "HOT",
            "root_project": str(tmp_path),
            "root_deliverables": str(tmp_path),
            "root_rag": str(tmp_path),
            "rag_files": {"hot": "RAG_MASTER.json", "backup": "RAG_MASTER.json.bak"},
            "last_checkpoint_seq": 7,
            "written_by_session": "T0",
        },
        "execution_mode": "autonomous",
        "state_machine_status": "READY",
        "policy_flags": {
            "atomic_writes_required": True,
            "hash_validation_required": True,
            "load_cold_on_demand_only": True,
            "session_close_audit_required": True,
            "proposal_validation_commit_required": True,
        },
        "operating_protocol": {},
        "pov_mandate": {"count": 2, "mode": "strict"},
        "project_context": {"existing": "keep-me"},
        "priority_actions": [],
        "open_tasks": [],
        "sessions_recent": [],
        "tracked_items": [],
    }, indent=2), encoding="utf-8")
    return rag


def _make_context(tmp_path: Path, name: str = "project_context.json", payload=None) -> Path:
    ctx = tmp_path / name
    ctx.write_text(json.dumps(payload or {
        "project_context": {"deployed_onto": "fix11-inc3"},
    }), encoding="utf-8")
    return ctx


# ---------------------------------------------------------------------------
# consume happy path
# ---------------------------------------------------------------------------

def test_consume_deletes_input_after_verified_merge(tmp_path):
    rag = _make_rag(tmp_path)
    ctx = _make_context(tmp_path)
    rc = main(["configure", "--rag", str(rag), "--context", str(ctx), "--consume"])
    assert rc == 0
    # the transient input is gone...
    assert not ctx.exists()
    # ...but the merge landed and .bak parity holds.
    hot = json.loads(rag.read_text(encoding="utf-8"))
    assert hot["project_context"]["deployed_onto"] == "fix11-inc3"
    assert hot["project_context"]["existing"] == "keep-me"
    assert _bak(rag).read_bytes() == rag.read_bytes()
    assert check_bak_parity(rag, hot) == []


def test_no_consume_leaves_input(tmp_path):
    rag = _make_rag(tmp_path)
    ctx = _make_context(tmp_path)
    rc = main(["configure", "--rag", str(rag), "--context", str(ctx)])
    assert rc == 0
    # control: without --consume the input file is left in place (the U3 hazard).
    assert ctx.exists()


def test_consumed_input_is_not_a_side_store(tmp_path):
    """After consume, the only files in the RAG dir are RAG_MASTER + .bak —
    the side-store finder has nothing transient to flag."""
    from rag_kernel.persistence import find_context_side_stores
    rag = _make_rag(tmp_path)
    ctx = _make_context(tmp_path, name="ebay_context.json")
    assert main(["configure", "--rag", str(rag), "--context", str(ctx), "--consume"]) == 0
    assert not ctx.exists()
    assert find_context_side_stores(tmp_path) == []


# ---------------------------------------------------------------------------
# dry-run
# ---------------------------------------------------------------------------

def test_consume_dry_run_writes_and_deletes_nothing(tmp_path, capsys):
    rag = _make_rag(tmp_path)
    ctx = _make_context(tmp_path)
    before = rag.read_bytes()
    rc = main(["configure", "--rag", str(rag), "--context", str(ctx),
               "--consume", "--dry-run"])
    assert rc == 0
    assert rag.read_bytes() == before          # HOT untouched
    assert not _bak(rag).exists()              # no backup written
    assert ctx.exists()                        # input NOT deleted
    assert "Would consume" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# fail-loud: refuse to consume a canonical / sanctioned file
# ---------------------------------------------------------------------------

def test_consume_refuses_rag_master(tmp_path, capsys):
    rag = _make_rag(tmp_path)
    before = rag.read_bytes()
    # point --context at the RAG itself with --consume
    rc = main(["configure", "--rag", str(rag), "--context", str(rag), "--consume"])
    assert rc == 1
    assert "refusing to --consume" in capsys.readouterr().err
    # RAG neither merged nor deleted
    assert rag.exists()
    assert rag.read_bytes() == before


def test_consume_refuses_sanctioned_context_store(tmp_path, capsys):
    rag = _make_rag(tmp_path)
    store = tmp_path / CONTEXT_FILENAME
    store.write_text(json.dumps({"project_context": {"x": 1}}), encoding="utf-8")
    rc = main(["configure", "--rag", str(rag), "--context", str(store), "--consume"])
    assert rc == 1
    assert "refusing to --consume" in capsys.readouterr().err
    assert store.exists()  # sanctioned store untouched


def test_consume_refuses_rag_cold(tmp_path, capsys):
    rag = _make_rag(tmp_path)
    cold = tmp_path / "RAG_COLD.json"
    cold.write_text(json.dumps({"project_context": {"x": 1}}), encoding="utf-8")
    rc = main(["configure", "--rag", str(rag), "--context", str(cold), "--consume"])
    assert rc == 1
    assert cold.exists()
