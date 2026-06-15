"""FIX-10 (U2) — the CLI ``configure`` verb must mirror ``.bak`` to byte-parity.

``cmd_configure`` merges project-specific context into an existing
RAG_MASTER.json and is therefore a *canonical RAG-state writer*. It used to
persist through ``SpecParser.write_rag``, which does its own tmp+replace atomic
write and never touches ``.bak`` — so after a configure the backup was left at
the pre-configure content, one write stale. That violates the FIX-4 / K6
parity-mirror contract (``check_bak_parity`` fails loud), the same family as
the FIX-8 / E-045 checkpoint gap. FIX-10 routes the configure write through
``atomic_write_json(..., mirror_bak=True)`` — matching api.checkpoint do_full,
the standalone ``checkpoint`` verb (FIX-8) and ``init --auto-ready`` (FIX-9) —
so the merge honors the parity contract on its own.
"""
import argparse
import json
from pathlib import Path

from rag_kernel.__main__ import cmd_configure
from rag_kernel.drift_audit import check_bak_parity


def _bak(p: Path) -> Path:
    return p.with_suffix(p.suffix + ".bak")


def _make_rag(tmp_path: Path) -> Path:
    """A structurally valid RAG so ``validate_rag`` is clean and cmd_configure
    returns 0 — isolating the test to the .bak-parity behavior, not validation."""
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


def _make_context(tmp_path: Path) -> Path:
    ctx = tmp_path / "project_context.json"
    ctx.write_text(json.dumps({
        "project_context": {"deployed_onto": "fix10-regression"},
    }), encoding="utf-8")
    return ctx


def _args(rag: Path, context: Path, **kw) -> argparse.Namespace:
    ns = argparse.Namespace(rag=rag, context=context, dry_run=False)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def test_cli_configure_mirrors_bak_to_byte_parity(tmp_path):
    """The whole point of FIX-10: a configure write, no follow-up, yet ``.bak``
    is byte-for-byte identical to the just-committed HOT."""
    rag = _make_rag(tmp_path)
    ctx = _make_context(tmp_path)
    assert cmd_configure(_args(rag, ctx)) == 0
    bak = _bak(rag)
    assert bak.exists()
    assert bak.read_bytes() == rag.read_bytes()


def test_cli_configure_passes_audit_bak_parity_with_no_followup(tmp_path):
    """``check_bak_parity`` (the K6 invariant) must be clean immediately after a
    configure — previously it required a later mirroring write to mask the gap."""
    rag = _make_rag(tmp_path)
    ctx = _make_context(tmp_path)
    assert cmd_configure(_args(rag, ctx)) == 0
    hot = json.loads(rag.read_text(encoding="utf-8"))
    assert check_bak_parity(rag, hot) == []


def test_cli_configure_merges_context_and_preserves_existing(tmp_path):
    """The parity fix must not alter merge semantics: new context is merged,
    pre-existing keys are preserved, and ``.bak`` tracks the merged result."""
    rag = _make_rag(tmp_path)
    ctx = _make_context(tmp_path)
    assert cmd_configure(_args(rag, ctx)) == 0
    hot = json.loads(rag.read_text(encoding="utf-8"))
    assert hot["project_context"]["deployed_onto"] == "fix10-regression"
    assert hot["project_context"]["existing"] == "keep-me"
    assert _bak(rag).read_bytes() == rag.read_bytes()


def test_cli_configure_dry_run_writes_nothing(tmp_path):
    """A dry-run configure must not write HOT or create a ``.bak`` — the fix is
    scoped to the real-write branch only."""
    rag = _make_rag(tmp_path)
    ctx = _make_context(tmp_path)
    before = rag.read_bytes()
    assert cmd_configure(_args(rag, ctx, dry_run=True)) == 0
    assert rag.read_bytes() == before
    assert not _bak(rag).exists()


def test_second_configure_keeps_bak_parity(tmp_path):
    """A second configure still leaves ``.bak`` byte-identical — the backup is
    never left one write behind."""
    rag = _make_rag(tmp_path)
    ctx = _make_context(tmp_path)
    assert cmd_configure(_args(rag, ctx)) == 0
    ctx2 = tmp_path / "more_context.json"
    ctx2.write_text(json.dumps({"project_context": {"round": "two"}}), encoding="utf-8")
    assert cmd_configure(_args(rag, ctx2)) == 0
    hot = json.loads(rag.read_text(encoding="utf-8"))
    assert hot["project_context"]["round"] == "two"
    assert _bak(rag).read_bytes() == rag.read_bytes()
    assert check_bak_parity(rag, hot) == []
