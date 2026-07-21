"""REUSE-REGISTRY-GUARD — baked-asset registry + reuse-before-rewrite guard.

Covers the design contract in rag_kernel.asset_registry:
  * register: additive, idempotent (same id+content), fail-loud on rebound id,
    fail-loud on duplicate path, fail-loud on a missing file
  * lean-RAG storage: records land in the RAG_CONTEXT.json `baked_assets` partition
    (non-loaded), never in RAG_MASTER.json; no .bak mirror
  * portable path storage: a file under project_root is stored relative
  * reuse-check: path hit, purpose containment (either direction), miss; never writes
  * auditor (drift_audit.check_asset_registry): clean when empty, ERROR on a missing
    file, ERROR on a diverged hash, ERROR on one path under two ids; self-skips clean
  * CLI: register-asset + reuse-check registration, exit codes, dry-run writes nothing
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rag_kernel.__main__ import main
from rag_kernel.asset_registry import (
    PARTITION_NAME,
    AssetFileNotFoundError,
    AssetPathCollisionError,
    AssetRecord,
    DuplicateAssetError,
    compute_sha256,
    list_assets,
    load_registry,
    normalize_path,
    register_asset,
    reuse_check,
)
from rag_kernel.cold_manager import ProjectContextManager
from rag_kernel.drift_audit import ERROR, check_asset_registry


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _asset(root: Path, rel: str, body: str = "print('hi')\n") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def _context_path(rag_dir: Path) -> Path:
    return rag_dir / "RAG_CONTEXT.json"


# --------------------------------------------------------------------------- #
# register — additive, idempotent, fail-loud
# --------------------------------------------------------------------------- #
def test_register_creates_record_in_context_partition(tmp_path):
    _asset(tmp_path, "scripts/thumb.py")
    rec, action = register_asset(
        tmp_path, asset_id="thumb", path="scripts/thumb.py",
        purpose="generate a video thumbnail", session="S165", project_root=tmp_path,
    )
    assert action == "created"
    assert rec.path == "scripts/thumb.py"          # stored relative to project_root
    assert rec.sha256 == compute_sha256(tmp_path / "scripts/thumb.py")
    # Landed in the sanctioned NON-LOADED store, not RAG_MASTER.json, and no .bak.
    assert _context_path(tmp_path).is_file()
    assert not (tmp_path / "RAG_CONTEXT.json.bak").exists()
    reg = load_registry(tmp_path)
    assert reg["assets"][0]["asset_id"] == "thumb"
    assert reg["_protocol"]  # protocol prose encapsulated with the bulk (lean-RAG)


def test_register_is_idempotent_on_identical_reregistration(tmp_path):
    _asset(tmp_path, "a.py")
    register_asset(tmp_path, asset_id="a", path="a.py", purpose="do a",
                   session="S165", project_root=tmp_path)
    rec, action = register_asset(tmp_path, asset_id="a", path="a.py", purpose="do a",
                                 session="S200", project_root=tmp_path)
    assert action == "idempotent"
    assert len(list_assets(tmp_path)) == 1          # no duplicate row appended


def test_register_rebinding_id_with_different_content_fails_loud(tmp_path):
    _asset(tmp_path, "a.py")
    _asset(tmp_path, "b.py", body="print('different')\n")
    register_asset(tmp_path, asset_id="a", path="a.py", purpose="do a",
                   session="S165", project_root=tmp_path)
    with pytest.raises(DuplicateAssetError):
        register_asset(tmp_path, asset_id="a", path="b.py", purpose="do a",
                       session="S165", project_root=tmp_path)
    assert len(list_assets(tmp_path)) == 1          # nothing written on the raise


def test_register_same_path_under_second_id_is_collision(tmp_path):
    _asset(tmp_path, "a.py")
    register_asset(tmp_path, asset_id="first", path="a.py", purpose="do a",
                   session="S165", project_root=tmp_path)
    with pytest.raises(AssetPathCollisionError):
        register_asset(tmp_path, asset_id="second", path="a.py", purpose="do a again",
                       session="S165", project_root=tmp_path)
    assert len(list_assets(tmp_path)) == 1


def test_register_missing_file_fails_loud(tmp_path):
    with pytest.raises(AssetFileNotFoundError):
        register_asset(tmp_path, asset_id="ghost", path="nope.py", purpose="x",
                       session="S165", project_root=tmp_path)
    assert not _context_path(tmp_path).exists()


def test_register_dry_run_writes_nothing(tmp_path):
    _asset(tmp_path, "a.py")
    rec, action = register_asset(tmp_path, asset_id="a", path="a.py", purpose="do a",
                                 session="S165", project_root=tmp_path, dry_run=True)
    assert action == "created"
    assert not _context_path(tmp_path).exists()     # dry-run never touches the store


# --------------------------------------------------------------------------- #
# reuse-check — the pre-write guard
# --------------------------------------------------------------------------- #
def test_reuse_check_hits_by_path_and_purpose(tmp_path):
    _asset(tmp_path, "scripts/thumb.py")
    register_asset(tmp_path, asset_id="thumb", path="scripts/thumb.py",
                   purpose="generate a video thumbnail", session="S165",
                   project_root=tmp_path)
    # exact path
    assert [r.asset_id for r in reuse_check(tmp_path, path="scripts/thumb.py",
                                            project_root=tmp_path)] == ["thumb"]
    # purpose containment, either direction ("thumbnail" is inside the stored purpose)
    assert reuse_check(tmp_path, purpose="thumbnail")
    assert reuse_check(tmp_path, purpose="please generate a video thumbnail for me")


def test_reuse_check_miss_returns_empty(tmp_path):
    _asset(tmp_path, "a.py")
    register_asset(tmp_path, asset_id="a", path="a.py", purpose="do a",
                   session="S165", project_root=tmp_path)
    assert reuse_check(tmp_path, purpose="something entirely unrelated") == []
    assert reuse_check(tmp_path, path="other.py", project_root=tmp_path) == []


def test_reuse_check_requires_a_criterion(tmp_path):
    with pytest.raises(Exception):
        reuse_check(tmp_path)


# --------------------------------------------------------------------------- #
# auditor
# --------------------------------------------------------------------------- #
def test_audit_clean_when_no_registry(tmp_path):
    assert check_asset_registry(tmp_path, tmp_path) == []


def test_audit_clean_when_registered_and_intact(tmp_path):
    _asset(tmp_path, "a.py")
    register_asset(tmp_path, asset_id="a", path="a.py", purpose="do a",
                   session="S165", project_root=tmp_path)
    assert check_asset_registry(tmp_path, tmp_path) == []


def test_audit_flags_missing_file(tmp_path):
    p = _asset(tmp_path, "a.py")
    register_asset(tmp_path, asset_id="a", path="a.py", purpose="do a",
                   session="S165", project_root=tmp_path)
    p.unlink()
    findings = check_asset_registry(tmp_path, tmp_path)
    assert len(findings) == 1
    assert findings[0].check == "asset_registry" and findings[0].severity == ERROR
    assert "missing" in findings[0].detail


def test_audit_flags_diverged_hash(tmp_path):
    p = _asset(tmp_path, "a.py")
    register_asset(tmp_path, asset_id="a", path="a.py", purpose="do a",
                   session="S165", project_root=tmp_path)
    p.write_text("print('mutated')\n", encoding="utf-8")   # content changed post-register
    findings = check_asset_registry(tmp_path, tmp_path)
    assert len(findings) == 1 and "diverged" in findings[0].detail


def test_audit_flags_one_path_under_two_ids(tmp_path):
    # Hand-craft the corruption the register guard would refuse, to prove the auditor
    # still catches a hand-edited registry.
    _asset(tmp_path, "a.py")
    sha = compute_sha256(tmp_path / "a.py")
    mgr = ProjectContextManager.default(tmp_path)
    mgr.update_partition(PARTITION_NAME, {"_protocol": "x", "assets": [
        {"asset_id": "one", "path": "a.py", "purpose": "p", "sha256": sha,
         "session": "S1", "registered_utc": "t"},
        {"asset_id": "two", "path": "a.py", "purpose": "p", "sha256": sha,
         "session": "S1", "registered_utc": "t"},
    ]})
    findings = check_asset_registry(tmp_path, tmp_path)
    assert any("two ids" in f.detail for f in findings)


# --------------------------------------------------------------------------- #
# path portability
# --------------------------------------------------------------------------- #
def test_normalize_path_stores_relative_under_root_absolute_outside(tmp_path):
    inside = _asset(tmp_path, "sub/x.py")
    assert normalize_path(inside, tmp_path) == "sub/x.py"
    assert normalize_path("sub/x.py", tmp_path) == "sub/x.py"
    outside = tmp_path.parent / "outside_x.py"
    assert Path(normalize_path(outside, tmp_path)).is_absolute()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_cli_register_then_reuse_check_roundtrip(tmp_path, capsys):
    _asset(tmp_path, "scripts/thumb.py")
    rc = main(["register-asset", str(tmp_path / "scripts/thumb.py"),
               "--purpose", "generate a video thumbnail", "--id", "thumb",
               "--session", "S165", "--rag-dir", str(tmp_path),
               "--project-root", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "registered" in out and "thumb" in out

    # reuse-check on a covered purpose is a fail-loud hit (exit 1).
    rc = main(["reuse-check", "--purpose", "video thumbnail",
               "--rag-dir", str(tmp_path), "--project-root", str(tmp_path)])
    assert rc == 1
    assert "REUSE" in capsys.readouterr().out

    # reuse-check on an unrelated purpose is clear (exit 0).
    rc = main(["reuse-check", "--purpose", "totally unrelated task",
               "--rag-dir", str(tmp_path), "--project-root", str(tmp_path)])
    assert rc == 0
    assert "CLEAR" in capsys.readouterr().out


def test_cli_register_duplicate_path_exits_one(tmp_path, capsys):
    _asset(tmp_path, "a.py")
    main(["register-asset", str(tmp_path / "a.py"), "--purpose", "do a",
          "--id", "first", "--session", "S165",
          "--rag-dir", str(tmp_path), "--project-root", str(tmp_path)])
    capsys.readouterr()
    rc = main(["register-asset", str(tmp_path / "a.py"), "--purpose", "do a",
               "--id", "second", "--session", "S165",
               "--rag-dir", str(tmp_path), "--project-root", str(tmp_path)])
    assert rc == 1
    assert "Error" in capsys.readouterr().err


def test_cli_register_dry_run_writes_nothing(tmp_path, capsys):
    _asset(tmp_path, "a.py")
    rc = main(["register-asset", str(tmp_path / "a.py"), "--purpose", "do a",
               "--session", "S165", "--rag-dir", str(tmp_path),
               "--project-root", str(tmp_path), "--dry-run"])
    assert rc == 0
    assert "[DRY RUN]" in capsys.readouterr().out
    assert not _context_path(tmp_path).exists()
