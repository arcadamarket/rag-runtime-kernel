"""FIX-5 (P3 + P2) — guarded ``add-rule`` verb + RAG-dir context side-store scan.

Two halves of the eBay Session-Zero deploy backlog:

  * **P3 — add-operating-protocol-rule verb.** ``operating_protocol`` is the
    project's rule vault; new rules (e.g. STRICT-OBEY) were previously introduced
    by hand-editing RAG_MASTER.json — the manual-JSON drift the project forbids
    (E-037 / E-039). ``drift_store.add_operating_protocol_rule[_file]`` and the
    ``rag_kernel add-rule`` CLI verb make it a guarded, atomic, ``.bak``-mirroring
    mutation: fail-loud on an existing key, no silent overwrite.

  * **P2 — context side-store scan.** A ``*_context.json`` is a transient input to
    ``configure`` whose content is merged INTO the canonical RAG; a copy left
    beside RAG_MASTER.json is a redundant parallel artifact (the eBay
    ``ebay_context.json`` defect). ``drift_audit.check_context_side_stores`` flags
    it, scanning the RAG dir only and non-recursively.
"""
import json
from pathlib import Path

import pytest

from rag_kernel.drift_store import (
    DriftStoreError,
    DuplicateItemError,
    OPERATING_PROTOCOL_KEY,
    add_operating_protocol_rule,
    add_operating_protocol_rule_file,
)
from rag_kernel.drift_audit import (
    ERROR,
    check_context_side_stores,
    audit_file,
)
from rag_kernel.__main__ import main


def _bak(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".bak")


def _checks(findings) -> set[str]:
    return {f.check for f in findings}


def _make_rag(tmp_path, *, op=None, items=None) -> Path:
    rag = tmp_path / "RAG_MASTER.json"
    body = {
        "meta": {"last_checkpoint_seq": 1, "written_by_session": "T"},
        "operating_protocol": {} if op is None else dict(op),
        "tracked_items": items or [],
        "open_tasks": [],
        "deferred_items": [],
    }
    rag.write_text(json.dumps(body), encoding="utf-8")
    return rag


# ===========================================================================
# P3 — add_operating_protocol_rule (pure, on a dict)
# ===========================================================================

def test_add_rule_inserts_new_key():
    hot = {"operating_protocol": {"existing": "x"}}
    add_operating_protocol_rule(hot, "strict_obey", "Rule N. Obey exactly.")
    assert hot["operating_protocol"]["strict_obey"] == "Rule N. Obey exactly."
    assert hot["operating_protocol"]["existing"] == "x"  # neighbours untouched


def test_add_rule_fail_loud_on_existing_key():
    hot = {"operating_protocol": {"strict_obey": "old"}}
    with pytest.raises(DuplicateItemError):
        add_operating_protocol_rule(hot, "strict_obey", "new")
    assert hot["operating_protocol"]["strict_obey"] == "old"  # unchanged


def test_add_rule_allow_overwrite_replaces():
    hot = {"operating_protocol": {"strict_obey": "old"}}
    add_operating_protocol_rule(hot, "strict_obey", "new", allow_overwrite=True)
    assert hot["operating_protocol"]["strict_obey"] == "new"


def test_add_rule_absent_operating_protocol_raises():
    with pytest.raises(DriftStoreError):
        add_operating_protocol_rule({}, "k", "v")


def test_add_rule_non_dict_operating_protocol_raises():
    with pytest.raises(DriftStoreError):
        add_operating_protocol_rule({"operating_protocol": ["not", "a", "dict"]}, "k", "v")


@pytest.mark.parametrize("bad_key", ["", "   ", None, 7])
def test_add_rule_rejects_bad_key(bad_key):
    with pytest.raises(DriftStoreError):
        add_operating_protocol_rule({"operating_protocol": {}}, bad_key, "v")


@pytest.mark.parametrize("bad_value", ["", "   ", None, 7])
def test_add_rule_rejects_bad_value(bad_value):
    with pytest.raises(DriftStoreError):
        add_operating_protocol_rule({"operating_protocol": {}}, "k", bad_value)


# ===========================================================================
# P3 — add_operating_protocol_rule_file (atomic, .bak parity)
# ===========================================================================

def test_add_rule_file_writes_and_mirrors_bak(tmp_path):
    rag = _make_rag(tmp_path)
    add_operating_protocol_rule_file(rag, "strict_obey", "Obey exactly.")
    data = json.loads(rag.read_text(encoding="utf-8"))
    assert data["operating_protocol"]["strict_obey"] == "Obey exactly."
    # FIX-4 parity-mirror contract: .bak is byte-identical to the committed HOT.
    assert _bak(rag).read_bytes() == rag.read_bytes()


def test_add_rule_file_touches_meta(tmp_path):
    rag = _make_rag(tmp_path)
    add_operating_protocol_rule_file(rag, "k", "v")
    data = json.loads(rag.read_text(encoding="utf-8"))
    assert data["meta"].get("last_updated_utc")  # stamped


def test_add_rule_file_existing_key_fail_loud_no_write(tmp_path):
    rag = _make_rag(tmp_path, op={"strict_obey": "old"})
    before = rag.read_text(encoding="utf-8")
    with pytest.raises(DuplicateItemError):
        add_operating_protocol_rule_file(rag, "strict_obey", "new")
    assert rag.read_text(encoding="utf-8") == before  # nothing written


def test_add_rule_file_overwrite_ok(tmp_path):
    rag = _make_rag(tmp_path, op={"strict_obey": "old"})
    add_operating_protocol_rule_file(rag, "strict_obey", "new",
                                     allow_overwrite=True)
    data = json.loads(rag.read_text(encoding="utf-8"))
    assert data["operating_protocol"]["strict_obey"] == "new"


# ===========================================================================
# P2 — check_context_side_stores (RAG-dir scan, non-recursive)
# ===========================================================================

def test_context_scan_flags_stray_context_json(tmp_path):
    (tmp_path / "ebay_context.json").write_text("{}", encoding="utf-8")
    findings = check_context_side_stores(tmp_path)
    assert len(findings) == 1
    assert findings[0].check == "context_side_stores"
    assert findings[0].severity == ERROR
    assert "ebay_context.json" in findings[0].detail


def test_context_scan_clean_when_none(tmp_path):
    (tmp_path / "RAG_MASTER.json").write_text("{}", encoding="utf-8")
    assert check_context_side_stores(tmp_path) == []


def test_context_scan_multiple_hits(tmp_path):
    (tmp_path / "ebay_context.json").write_text("{}", encoding="utf-8")
    (tmp_path / "project_context.json").write_text("{}", encoding="utf-8")
    assert len(check_context_side_stores(tmp_path)) == 2


def test_context_scan_ignores_non_context_json(tmp_path):
    (tmp_path / "RAG_MASTER.json").write_text("{}", encoding="utf-8")
    (tmp_path / "RAG_COLD.json").write_text("{}", encoding="utf-8")
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
    assert check_context_side_stores(tmp_path) == []


def test_context_scan_is_non_recursive(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "ebay_context.json").write_text("{}", encoding="utf-8")
    assert check_context_side_stores(tmp_path) == []  # only top-level RAG dir


def test_context_scan_missing_dir_is_clean(tmp_path):
    assert check_context_side_stores(tmp_path / "nope") == []


# ===========================================================================
# P2 — audit_file integration (RAG dir = parent of RAG_MASTER.json)
# ===========================================================================

def test_audit_file_flags_context_side_store(tmp_path):
    rag = _make_rag(tmp_path)
    (tmp_path / "ebay_context.json").write_text("{}", encoding="utf-8")
    report = audit_file(rag)
    assert "context_side_stores" in _checks(report.findings)
    assert not report.is_clean()  # ERROR fails even without --strict


def test_audit_file_clean_without_context_side_store(tmp_path):
    rag = _make_rag(tmp_path)
    report = audit_file(rag)
    assert "context_side_stores" not in _checks(report.findings)


def test_audit_file_no_scan_root_skips_context_scan(tmp_path):
    rag = _make_rag(tmp_path)
    (tmp_path / "ebay_context.json").write_text("{}", encoding="utf-8")
    report = audit_file(rag, scan_root=False)
    assert "context_side_stores" not in _checks(report.findings)


# ===========================================================================
# P3 — CLI: rag_kernel add-rule
# ===========================================================================

def test_cli_add_rule_dry_run_no_write(tmp_path, capsys):
    rag = _make_rag(tmp_path)
    before = rag.read_text(encoding="utf-8")
    rc = main(["add-rule", "strict_obey", "Obey exactly.",
               "--rag", str(rag), "--session", "S75", "--dry-run"])
    assert rc == 0
    assert "[DRY RUN]" in capsys.readouterr().out
    assert rag.read_text(encoding="utf-8") == before  # untouched


def test_cli_add_rule_writes(tmp_path, capsys):
    rag = _make_rag(tmp_path)
    rc = main(["add-rule", "strict_obey", "Obey exactly.",
               "--rag", str(rag), "--session", "S75"])
    assert rc == 0
    data = json.loads(rag.read_text(encoding="utf-8"))
    assert data["operating_protocol"]["strict_obey"] == "Obey exactly."
    assert _bak(rag).read_bytes() == rag.read_bytes()


def test_cli_add_rule_duplicate_fails(tmp_path, capsys):
    rag = _make_rag(tmp_path, op={"strict_obey": "old"})
    rc = main(["add-rule", "strict_obey", "new",
               "--rag", str(rag), "--session", "S75"])
    assert rc == 1
    assert "already has rule" in capsys.readouterr().err


def test_cli_add_rule_overwrite(tmp_path, capsys):
    rag = _make_rag(tmp_path, op={"strict_obey": "old"})
    rc = main(["add-rule", "strict_obey", "new", "--allow-overwrite",
               "--rag", str(rag), "--session", "S75"])
    assert rc == 0
    data = json.loads(rag.read_text(encoding="utf-8"))
    assert data["operating_protocol"]["strict_obey"] == "new"


def test_cli_add_rule_value_file(tmp_path, capsys):
    rag = _make_rag(tmp_path)
    vf = tmp_path / "rule.txt"
    long_rule = "Rule 16. " + ("STRICT-OBEY: do exactly what the operator says. " * 20)
    vf.write_text(long_rule, encoding="utf-8")
    rc = main(["add-rule", "strict_obey", "--value-file", str(vf),
               "--rag", str(rag), "--session", "S75"])
    assert rc == 0
    data = json.loads(rag.read_text(encoding="utf-8"))
    assert data["operating_protocol"]["strict_obey"] == long_rule.strip()


def test_cli_add_rule_missing_value_errors(tmp_path, capsys):
    rag = _make_rag(tmp_path)
    rc = main(["add-rule", "strict_obey", "--rag", str(rag), "--session", "S75"])
    assert rc == 1
    assert "rule text" in capsys.readouterr().err
