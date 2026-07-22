"""Tests for the deterministic domain boot-map (rag_kernel.bootmap).

Covers every obligation of DESIGN_DOMAIN_MANIFEST_S166.md: the excluded-set walk,
the {path,sha256,size,mtime,class,owner} entry shape, deterministic ordering, the
content-hash diff, the boot line, .bak-parity persistence, the one-line meta
pointer (idempotent), owner=operator classification, and the fail-loud
check_map_coverage invariant (self-skip / phantom / coverage-gap / churn-exempt).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from rag_kernel import bootmap as bm


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def _make_root(tmp_path: Path) -> Path:
    """A miniature project root exercising each class, owner, and exclude rule."""
    root = tmp_path / "proj"
    (root / "RAG").mkdir(parents=True)
    (root / "RAG" / "rag_kernel").mkdir()
    # governed, mapped
    (root / "RAG" / "RAG_MASTER.json").write_text('{"a": 1}', encoding="utf-8")
    (root / "RAG" / "RAG_COLD.json").write_text("{}", encoding="utf-8")
    (root / "RAG" / "ERROR_LOG.md").write_text("log", encoding="utf-8")
    (root / "RAG" / "AUDIT_CANONICAL_REPORT_S1.md").write_text("r", encoding="utf-8")
    (root / "RAG" / "session_log_S1.jsonl").write_text("{}", encoding="utf-8")
    (root / "RAG" / "INIT_UNIVERSAL_RUNTIME_KERNEL_v1.md").write_text("i", encoding="utf-8")
    (root / "RAG" / "DESIGN_X_S1.md").write_text("d", encoding="utf-8")
    (root / "RAG" / "rag_kernel" / "__main__.py").write_text("x=1", encoding="utf-8")
    (root / "CLEANUP.ps1").write_text("Write-Host hi", encoding="utf-8")
    (root / "README.md").write_text("readme", encoding="utf-8")
    # excluded: dirs and suffixes
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref", encoding="utf-8")
    (root / "RAG" / "rag_kernel" / "__pycache__").mkdir()
    (root / "RAG" / "rag_kernel" / "__pycache__" / "m.pyc").write_text("x", encoding="utf-8")
    (root / "RAG" / "RAG_MASTER.json.bak").write_text('{"a": 1}', encoding="utf-8")
    (root / "RAG" / "x.tmp").write_text("t", encoding="utf-8")
    (root / "GIT WORKTREES").mkdir()
    (root / "GIT WORKTREES" / "dev.py").write_text("y=2", encoding="utf-8")
    return root


# --------------------------------------------------------------------------- #
# walk / exclude / entry shape / determinism
# --------------------------------------------------------------------------- #
def test_walk_excludes_dirs_suffixes_and_sidecar(tmp_path):
    root = _make_root(tmp_path)
    # pre-seed a sidecar to prove it is never mapped
    (root / "RAG" / bm.MANIFEST_NAME).write_text("{}", encoding="utf-8")
    paths = {e.path for e in bm.walk_domain(root)}
    assert "RAG/RAG_MASTER.json" in paths
    assert "CLEANUP.ps1" in paths
    # excluded
    assert not any(p.startswith(".git/") for p in paths)
    assert not any("__pycache__" in p for p in paths)
    assert not any(p.endswith((".pyc", ".bak", ".tmp")) for p in paths)
    assert not any(p.startswith("GIT WORKTREES/") for p in paths)
    assert f"RAG/{bm.MANIFEST_NAME}" not in paths


def test_entry_shape_and_types(tmp_path):
    root = _make_root(tmp_path)
    e = next(e for e in bm.walk_domain(root) if e.path == "RAG/RAG_MASTER.json")
    d = e.to_dict()
    assert set(d) == {"path", "sha256", "size", "mtime", "class", "owner"}
    assert len(d["sha256"]) == 64
    assert isinstance(d["size"], int) and isinstance(d["mtime"], int)
    assert d["class"] == "rag_state" and d["owner"] == "kernel"


def test_walk_is_sorted_deterministic(tmp_path):
    root = _make_root(tmp_path)
    a = [e.path for e in bm.walk_domain(root)]
    b = [e.path for e in bm.walk_domain(root)]
    assert a == b == sorted(a)


def test_unreadable_file_fails_loud(tmp_path, monkeypatch):
    root = _make_root(tmp_path)

    def boom(_p):
        raise OSError("unreadable")

    monkeypatch.setattr(bm, "_sha256_file", boom)
    with pytest.raises(OSError):
        bm.walk_domain(root)


# --------------------------------------------------------------------------- #
# classification / ownership
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("rel,expected", [
    ("RAG/RAG_MASTER.json", "rag_state"),
    ("RAG/RAG_COLD.json", "rag_state"),
    ("RAG/rag_kernel/__main__.py", "kernel"),
    ("RAG/AUDIT_CANONICAL_REPORT_S1.md", "audit_report"),
    ("RAG/session_log_S1.jsonl", "session_log"),
    ("RAG/ERROR_LOG.md", "error_log"),
    ("RAG/INIT_UNIVERSAL_RUNTIME_KERNEL_v1.md", "init_prompt"),
    ("RAG/DESIGN_X_S1.md", "design"),
    ("CLEANUP.ps1", "script"),
    ("tool.py", "code"),
    ("README.md", "doc"),
    ("data.json", "data"),
    ("weird.xyz", "other"),
])
def test_classify(rel, expected):
    assert bm.classify(rel) == expected


@pytest.mark.parametrize("rel,owner", [
    ("CLEANUP.ps1", "operator"),
    ("run.bat", "operator"),
    ("go.cmd", "operator"),
    ("RAG/RAG_MASTER.json", "kernel"),
    ("RAG/rag_kernel/api.py", "kernel"),
])
def test_owner_of(rel, owner):
    assert bm.owner_of(rel) == owner


# --------------------------------------------------------------------------- #
# manifest build / persist / .bak parity / round-trip
# --------------------------------------------------------------------------- #
def test_build_manifest_shape(tmp_path):
    root = _make_root(tmp_path)
    m = bm.build_manifest(root, "S168")
    assert m["schema_version"] == bm.SCHEMA_VERSION
    assert m["session"] == "S168"
    assert m["count"] == len(m["files"]) > 0


def test_write_read_roundtrip_and_bak_parity(tmp_path):
    root = _make_root(tmp_path)
    rag_dir = root / "RAG"
    m = bm.build_manifest(root, "S168")
    p = bm.write_manifest(rag_dir, m)
    bak = p.with_suffix(p.suffix + ".bak")
    assert p.exists() and bak.exists()
    assert p.read_bytes() == bak.read_bytes()  # FIX-4 parity mirror
    assert bm.read_manifest(rag_dir)["count"] == m["count"]


def test_read_manifest_absent_is_none(tmp_path):
    assert bm.read_manifest(tmp_path) is None


# --------------------------------------------------------------------------- #
# diff / boot line
# --------------------------------------------------------------------------- #
def test_diff_new_changed_deleted_by_hash(tmp_path):
    root = _make_root(tmp_path)
    prior = bm.build_manifest(root, "S1")
    # mutate content (changed), add (new), remove (deleted)
    (root / "RAG" / "RAG_MASTER.json").write_text('{"a": 2}', encoding="utf-8")
    (root / "NEWFILE.md").write_text("new", encoding="utf-8")
    (root / "README.md").unlink()
    current = bm.build_manifest(root, "S2")
    d = bm.diff_maps(prior, current)
    assert "RAG/RAG_MASTER.json" in d["changed"]
    assert "NEWFILE.md" in d["new"]
    assert "README.md" in d["deleted"]


def test_diff_ignores_mtime_only_change(tmp_path):
    root = _make_root(tmp_path)
    prior = bm.build_manifest(root, "S1")
    import os
    f = root / "README.md"
    os.utime(f, (10 ** 9, 10 ** 9))  # touch mtime, same bytes
    current = bm.build_manifest(root, "S2")
    d = bm.diff_maps(prior, current)
    assert "README.md" not in d["changed"]


def test_boot_line_first_run(tmp_path):
    root = _make_root(tmp_path)
    m = bm.build_manifest(root, "S168")
    line = bm.boot_line(None, m)
    assert "no prior baseline" in line and f"{m['count']} files" in line


def test_boot_line_since_last(tmp_path):
    root = _make_root(tmp_path)
    prior = bm.build_manifest(root, "S1")
    (root / "NEWFILE.md").write_text("n", encoding="utf-8")
    current = bm.build_manifest(root, "S2")
    line = bm.boot_line(prior, current)
    assert "since S1:" in line and "+1 new" in line


def test_session_start_line_is_read_only(tmp_path):
    root = _make_root(tmp_path)
    rag_dir = root / "RAG"
    bm.session_start_line(root, rag_dir)
    assert not bm.manifest_path(rag_dir).exists()  # boot never writes the baseline


def test_refresh_baseline_writes_with_parity(tmp_path):
    root = _make_root(tmp_path)
    rag_dir = root / "RAG"
    p = bm.refresh_baseline(root, rag_dir, "S168")
    assert p.exists() and p.with_suffix(p.suffix + ".bak").exists()
    assert bm.read_manifest(rag_dir)["session"] == "S168"


# --------------------------------------------------------------------------- #
# meta pointer (idempotent)
# --------------------------------------------------------------------------- #
def test_ensure_meta_pointer_idempotent(tmp_path):
    rag = tmp_path / "RAG_MASTER.json"
    rag.write_text(json.dumps({"meta": {"rag_files": {"hot": "RAG_MASTER.json"}}}),
                   encoding="utf-8")
    assert bm.ensure_meta_pointer(rag) is True           # first write
    assert bm.ensure_meta_pointer(rag) is False          # no-op second
    hot = json.loads(rag.read_text(encoding="utf-8"))
    assert hot["meta"]["rag_files"][bm.META_POINTER_KEY] == bm.MANIFEST_NAME
    assert hot["meta"]["rag_files"]["hot"] == "RAG_MASTER.json"  # preserved
    assert rag.with_suffix(".json.bak").read_bytes() == rag.read_bytes()  # parity


# --------------------------------------------------------------------------- #
# check_map_coverage invariant
# --------------------------------------------------------------------------- #
def test_coverage_self_skips_without_baseline(tmp_path):
    root = _make_root(tmp_path)
    assert bm.check_map_coverage({}, root, root / "RAG") == []


def test_coverage_clean_when_sealed(tmp_path):
    root = _make_root(tmp_path)
    rag_dir = root / "RAG"
    bm.refresh_baseline(root, rag_dir, "S168")
    assert bm.check_map_coverage({}, root, rag_dir) == []


def test_coverage_flags_phantom(tmp_path):
    root = _make_root(tmp_path)
    rag_dir = root / "RAG"
    bm.refresh_baseline(root, rag_dir, "S168")
    (root / "README.md").unlink()  # mapped file vanishes
    findings = bm.check_map_coverage({}, root, rag_dir)
    assert any("stale map entry" in f.detail and "README.md" in f.detail for f in findings)
    assert all(f.severity == "error" for f in findings)


def test_coverage_flags_uncovered_governed_file(tmp_path):
    root = _make_root(tmp_path)
    rag_dir = root / "RAG"
    bm.refresh_baseline(root, rag_dir, "S168")
    (root / "NEWSCRIPT.ps1").write_text("x", encoding="utf-8")  # non-churn class
    findings = bm.check_map_coverage({}, root, rag_dir)
    assert any("coverage gap" in f.detail and "NEWSCRIPT.ps1" in f.detail for f in findings)


def test_coverage_exempts_churn_classes(tmp_path):
    root = _make_root(tmp_path)
    rag_dir = root / "RAG"
    bm.refresh_baseline(root, rag_dir, "S168")
    # a NEW session log and audit report appear after the seal — normal cadence
    (root / "RAG" / "session_log_S169.jsonl").write_text("{}", encoding="utf-8")
    (root / "RAG" / "AUDIT_CANONICAL_REPORT_S169.md").write_text("r", encoding="utf-8")
    assert bm.check_map_coverage({}, root, rag_dir) == []
