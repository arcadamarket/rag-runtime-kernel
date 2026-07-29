"""FLEET-INVENTORY — classify what exists, here and next door (S180).

Covers the design contract in rag_kernel.inventory:
  * classify: pure, total, and correctly ORDERED — a .bak mirror is governed and
    not scratch; the kernel package is never counted as self-made work
  * scan: prunes .git/__pycache__, ignores symlinks, is deterministic across runs
  * unregistered: the honest count of shipped capability reuse-check cannot see
  * fleet_scan: a sibling with NO registry partition still contributes its
    scripts — the eBay case, and the entire reason this is fleet-scoped
  * fleet_reuse_check: matches an unregistered sibling script by filename stem,
    because prior art with no purpose text is still prior art
  * CLI: scan is fail-loud (exit 1) while reusable work is unregistered;
    backfill demands a session; dry-run writes nothing
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rag_kernel import inventory as inv
from rag_kernel.__main__ import main


# --------------------------------------------------------------------------- #
# classify — ordering is the whole game
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("rel, expected", [
    ("rag_kernel/api.py", inv.KERNEL),
    ("RAG/rag_kernel/inventory.py", inv.KERNEL),
    ("tests/test_api.py", inv.TEST),
    ("scripts/preflight.py", inv.SCRIPT),
    ("run_intake.py", inv.SCRIPT),
    ("RUNBOOK.md", inv.DOC),
    ("notes.txt", inv.DOC),
    ("AUDIT_CANONICAL_REPORT_S161.md", inv.REPORT),
    ("RUNBOOK_CLONE_INIT_S179.md", inv.DOC),
    ("BLUEPRINT_ONLINE_BIZ_CLONE.md", inv.DOC),
    ("RAG_MASTER.json", inv.DATA),
    ("session_log_S1.jsonl", inv.DATA),
    ("RAG_MASTER.json.bak", inv.MIRROR),
    ("scripts/thing.py.bak", inv.MIRROR),
    ("build/thing.pyc", inv.SCRATCH),
    ("x.tmp", inv.SCRATCH),
    ("logo.svg", inv.OTHER),
])
def test_classify(rel, expected):
    assert inv.classify(rel) == expected


def test_bak_beats_suffix_rules():
    """A .bak is an atomic-write mirror — governed state, not waste.

    Classifying it as scratch would invite a hygiene sweep to delete the
    rollback path.
    """
    assert inv.classify("RAG_MASTER.json.bak") == inv.MIRROR
    assert inv.classify("RAG_MASTER.json.bak") != inv.SCRATCH


def test_kernel_beats_script():
    """The kernel travels by file copy and is never self-made work."""
    assert inv.classify("rag_kernel/transplant.py") == inv.KERNEL
    assert inv.classify("scripts/transplant.py") == inv.SCRIPT


def test_history_is_not_capability():
    """45 session audit reports must not drown the two scripts that matter.

    A per-session report records what happened once; it is never reused. An
    inventory that cannot tell capability from history produces a number nobody
    reads — which is precisely how this registry stayed empty for 178 sessions.
    """
    assert inv.classify("AUDIT_CANONICAL_REPORT_S161.md") == inv.REPORT
    assert inv.REPORT not in inv.REUSABLE


def test_session_stamped_instructions_stay_durable():
    """`RUNBOOK_CLONE_INIT_S179.md` is stamped but instructs every future
    session, so the stamp alone must not demote it to history."""
    assert inv.classify("RUNBOOK_CLONE_INIT_S179.md") == inv.DOC
    assert inv.classify("GUIDE_SPINUP_S12.md") == inv.DOC


def test_classify_is_total():
    for rel in ["", "x", "a/b/c", ".hidden", "no.suffix.here"]:
        assert inv.classify(rel) in inv.CLASSES


# --------------------------------------------------------------------------- #
# scan
# --------------------------------------------------------------------------- #
@pytest.fixture
def tree(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "preflight.py").write_text("x", encoding="utf-8")
    (tmp_path / "scripts" / "schedule_all.py").write_text("x", encoding="utf-8")
    (tmp_path / "RUNBOOK.md").write_text("x", encoding="utf-8")
    (tmp_path / "RAG_MASTER.json").write_text("{}", encoding="utf-8")
    (tmp_path / "RAG_MASTER.json.bak").write_text("{}", encoding="utf-8")
    (tmp_path / "rag_kernel").mkdir()
    (tmp_path / "rag_kernel" / "api.py").write_text("x", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "objects").write_text("junk", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "a.pyc").write_text("junk", encoding="utf-8")
    return tmp_path


def test_scan_prunes_noise_directories(tree):
    rels = {f.rel for f in inv.scan(tree).files}
    assert not any(r.startswith(".git/") for r in rels)
    assert not any(r.startswith("__pycache__/") for r in rels)


def test_scan_counts(tree):
    counts = inv.scan(tree).counts
    assert counts[inv.SCRIPT] == 2
    assert counts[inv.KERNEL] == 1
    assert counts[inv.DOC] == 1
    assert counts[inv.MIRROR] == 1


def test_scan_is_deterministic(tree):
    a = [f.to_dict() for f in inv.scan(tree).files]
    b = [f.to_dict() for f in inv.scan(tree).files]
    assert a == b, "two scans of an unchanged tree must be byte-identical"


def test_reusable_excludes_kernel_and_data(tree):
    rels = {f.rel for f in inv.scan(tree).reusable()}
    assert rels == {"RUNBOOK.md", "scripts/preflight.py", "scripts/schedule_all.py"}


def test_scan_missing_root_is_empty_not_a_crash(tmp_path):
    assert inv.scan(tmp_path / "nope").files == []


def test_render_is_bounded(tree):
    out = inv.scan(tree).render(limit=1)
    assert "more" in out
    assert len(out.splitlines()) < 10


# --------------------------------------------------------------------------- #
# unregistered / fleet
# --------------------------------------------------------------------------- #
def _write_ctx(rag_dir: Path, partitions: dict):
    rag_dir.mkdir(parents=True, exist_ok=True)
    (rag_dir / "RAG_CONTEXT.json").write_text(
        json.dumps(partitions), encoding="utf-8")


def test_unregistered_lists_everything_when_registry_absent(tree):
    pending = inv.unregistered(tree, tree)
    assert {f.rel for f in pending} == {
        "RUNBOOK.md", "scripts/preflight.py", "scripts/schedule_all.py"}


def test_sibling_without_a_registry_still_contributes(tmp_path):
    """The eBay case, and the reason this is fleet-scoped.

    47 scripts, no baked_assets partition, reuse-check CLEAR on all of them.
    A fleet view that only merged *registries* would see nothing at all —
    which would make it useless exactly where it is needed.
    """
    sib = tmp_path / "EBAY"
    (sib / "scripts").mkdir(parents=True)
    (sib / "scripts" / "preflight.py").write_text("x", encoding="utf-8")
    (sib / "scripts" / "capability_ledger.py").write_text("x", encoding="utf-8")
    _write_ctx(sib / "RAG", {"known_issues_registry": {}})  # no baked_assets

    entries = inv.fleet_scan([{"name": "ebay", "root": str(sib),
                               "rag_dir": str(sib / "RAG")}])

    assert {e.rel for e in entries} == {
        "scripts/preflight.py", "scripts/capability_ledger.py"}
    assert all(e.registered is False for e in entries)
    assert all(e.deployment == "ebay" for e in entries)


def test_fleet_marks_registered_entries(tmp_path):
    sib = tmp_path / "DEP"
    (sib / "scripts").mkdir(parents=True)
    (sib / "scripts" / "known.py").write_text("x", encoding="utf-8")
    _write_ctx(sib / "RAG", {"baked_assets": {"assets": [
        {"asset_id": "K", "path": "scripts/known.py", "purpose": "does a thing"}]}})

    entry = inv.fleet_scan([{"name": "dep", "root": str(sib),
                             "rag_dir": str(sib / "RAG")}])[0]
    assert entry.registered is True
    assert entry.purpose == "does a thing"


def test_fleet_reuse_check_matches_unregistered_script_by_stem(tmp_path):
    """An unregistered `preflight.py` next door has no purpose text.

    Matching only on purpose would miss every artifact in exactly the
    deployment this feature exists to surface.
    """
    rag_dir = tmp_path / "RAG"
    _write_ctx(rag_dir, {inv.FLEET_PARTITION: {"entries": [
        {"deployment": "ebay", "path": "scripts/preflight.py",
         "class": "script", "purpose": None, "registered": False}]}})

    hits = inv.fleet_reuse_check(rag_dir, "preflight gate for a run")
    assert len(hits) == 1
    assert hits[0].deployment == "ebay"
    assert hits[0].registered is False


def test_fleet_reuse_check_empty_when_no_partition(tmp_path):
    _write_ctx(tmp_path / "RAG", {})
    assert inv.fleet_reuse_check(tmp_path / "RAG", "anything") == []


def test_fleet_config_reads_declared_deployments(tmp_path):
    rag_dir = tmp_path / "RAG"
    _write_ctx(rag_dir, {"fleet": {"deployments": [
        {"name": "ebay", "root": "/somewhere"}]}})
    assert inv.fleet_config(rag_dir)[0]["name"] == "ebay"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_cli_scan_is_fail_loud_while_work_is_unregistered(tree, capsys):
    rc = main(["inventory", "scan", "--root", str(tree), "--rag-dir", str(tree)])
    out = capsys.readouterr().out
    assert rc == 1, "unregistered capability is invisible to reuse-check — say so loudly"
    assert "UNREGISTERED" in out


def test_cli_scan_clean_when_nothing_reusable(tmp_path, capsys):
    (tmp_path / "RAG_MASTER.json").write_text("{}", encoding="utf-8")
    rc = main(["inventory", "scan", "--root", str(tmp_path),
               "--rag-dir", str(tmp_path)])
    assert rc == 0
    assert "All reusable work is registered." in capsys.readouterr().out


def test_cli_backfill_requires_a_session(tree, capsys):
    rc = main(["inventory", "backfill", "--root", str(tree), "--rag-dir", str(tree)])
    assert rc == 2
    assert "--session is required" in capsys.readouterr().err


def test_cli_backfill_dry_run_writes_nothing(tree, capsys):
    before = sorted(p.name for p in tree.iterdir())
    rc = main(["inventory", "backfill", "--root", str(tree), "--rag-dir", str(tree),
               "--session", "S999", "--dry-run"])
    assert rc == 0
    assert "[DRY RUN]" in capsys.readouterr().out
    assert sorted(p.name for p in tree.iterdir()) == before


def test_cli_backfill_registers_and_then_scan_is_clean(tree, capsys):
    rc = main(["inventory", "backfill", "--root", str(tree), "--rag-dir", str(tree),
               "--session", "S999"])
    assert rc == 0
    capsys.readouterr()
    rc2 = main(["inventory", "scan", "--root", str(tree), "--rag-dir", str(tree)])
    assert rc2 == 0, "after backfill nothing reusable should remain unregistered"


def test_cli_fleet_refuses_without_deployments(tmp_path, capsys):
    rc = main(["inventory", "fleet", "--root", str(tmp_path),
               "--rag-dir", str(tmp_path)])
    assert rc == 2
    assert "no deployments" in capsys.readouterr().err


def test_cli_fleet_writes_the_partition(tmp_path, capsys):
    sib = tmp_path / "SIB"
    (sib / "scripts").mkdir(parents=True)
    (sib / "scripts" / "thing.py").write_text("x", encoding="utf-8")
    rag_dir = tmp_path / "RAG"
    rag_dir.mkdir()

    rc = main(["inventory", "fleet", "--rag-dir", str(rag_dir),
               "--deployment", f"sib={sib}"])
    assert rc == 0

    stored = json.loads((rag_dir / "RAG_CONTEXT.json").read_text(encoding="utf-8"))
    entries = stored[inv.FLEET_PARTITION]["entries"]
    assert [e["path"] for e in entries] == ["scripts/thing.py"]


def test_cli_fleet_rejects_malformed_deployment_spec(tmp_path, capsys):
    rc = main(["inventory", "fleet", "--rag-dir", str(tmp_path),
               "--deployment", "nopath"])
    assert rc == 2
    assert "NAME=ROOT" in capsys.readouterr().err
