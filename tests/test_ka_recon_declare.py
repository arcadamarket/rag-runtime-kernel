"""KA-RECON-DECLARE — governed writer for ``meta.reconciliation_docs_root``.

KA-13 wired the close-time Rule 11 reconciliation to resolve its published-doc
surface root from ``meta.reconciliation_docs_root``, but there was no governed way
to *set* that key: declaring it meant a hand-edit of RAG_MASTER.json — exactly the
drift the project forbids — which blocked dogfooding KA-13 here.

KA-RECON-DECLARE adds a ``--reconciliation-docs-root`` flag to the ``configure``
verb. It rides configure's existing ``deep_merge`` + ``atomic_write_json(
mirror_bak=True)`` path, so the declaration is atomic and keeps HOT<->.bak parity by
construction. The flag may be used alone (``--context`` is now optional) or together
with a context overlay; an explicit flag wins over any value a context file carries.
"""
import argparse
import json
from pathlib import Path

from rag_kernel.__main__ import cmd_configure
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


def _args(rag: Path, **kw) -> argparse.Namespace:
    ns = argparse.Namespace(
        rag=rag, context=None, dry_run=False,
        reconciliation_docs_root=None, consume=False,
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


# --- the declaration itself, used alone (no --context) --------------------------

def test_flag_alone_declares_meta_key(tmp_path):
    rag = _make_rag(tmp_path)
    assert cmd_configure(_args(rag, reconciliation_docs_root="docs/published")) == 0
    hot = json.loads(rag.read_text(encoding="utf-8"))
    assert hot["meta"]["reconciliation_docs_root"] == "docs/published"


def test_flag_alone_keeps_bak_byte_parity(tmp_path):
    rag = _make_rag(tmp_path)
    assert cmd_configure(_args(rag, reconciliation_docs_root="docs")) == 0
    assert _bak(rag).exists()
    assert _bak(rag).read_bytes() == rag.read_bytes()
    hot = json.loads(rag.read_text(encoding="utf-8"))
    assert check_bak_parity(rag, hot) == []


def test_flag_preserves_existing_state(tmp_path):
    rag = _make_rag(tmp_path)
    assert cmd_configure(_args(rag, reconciliation_docs_root="docs")) == 0
    hot = json.loads(rag.read_text(encoding="utf-8"))
    assert hot["project_context"]["existing"] == "keep-me"
    assert hot["meta"]["schema_version"] == "5.4"


# --- interaction with --context and --dry-run -----------------------------------

def test_flag_wins_over_context_value(tmp_path):
    rag = _make_rag(tmp_path)
    ctx = tmp_path / "ctx.json"
    ctx.write_text(json.dumps({"meta": {"reconciliation_docs_root": "from-context"}}),
                   encoding="utf-8")
    assert cmd_configure(
        _args(rag, context=ctx, reconciliation_docs_root="from-flag")) == 0
    hot = json.loads(rag.read_text(encoding="utf-8"))
    assert hot["meta"]["reconciliation_docs_root"] == "from-flag"


def test_dry_run_with_flag_writes_nothing(tmp_path):
    rag = _make_rag(tmp_path)
    before = rag.read_bytes()
    assert cmd_configure(
        _args(rag, reconciliation_docs_root="docs", dry_run=True)) == 0
    assert rag.read_bytes() == before
    assert not _bak(rag).exists()


# --- guardrails -----------------------------------------------------------------

def test_neither_context_nor_flag_is_an_error(tmp_path):
    rag = _make_rag(tmp_path)
    assert cmd_configure(_args(rag)) == 1


def test_consume_without_context_is_an_error(tmp_path):
    # --consume needs a context file to consume; with only the flag it must fail
    # loud rather than reach an unlink on a None path.
    rag = _make_rag(tmp_path)
    assert cmd_configure(
        _args(rag, reconciliation_docs_root="docs", consume=True)) == 1
