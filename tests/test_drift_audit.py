"""Unit + CLI tests for rag_kernel.drift_audit and the guarded note verb (DRIFT-ELIM increment 5).

Covers:
- the fail-loud session auditor: render parity (the E-040 regression), supersede
  referential integrity, note/status contradiction (INS-038), and the Rule 13
  side-store scan — plus ``assert_clean`` (errors always raise; warnings raise only
  under ``strict``);
- the guarded note-update path: ``TrackedItem.with_note`` (core), ``set_note`` /
  ``set_note_in_file`` (store), and the ``note`` / ``audit`` CLI commands.

Properties asserted: the auditor is a pure predicate over persisted state (a
hand-edited legacy array is detected, a clean rendered RAG is clean), note updates
never change status or append history, and the CLI fails loud (exit 1) on a dirty
audit or an unknown id.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rag_kernel.drift_control import (
    ItemKind,
    ItemStatus,
    ItemValidationError,
    TrackedItem,
)
from rag_kernel.drift_store import (
    TRACKED_ITEMS_KEY,
    TrackedItemStore,
    UnknownItemError,
    load_hot,
    set_note_in_file,
)
from rag_kernel import drift_render
from rag_kernel.drift_audit import (
    DRIFT_AUDIT_VERSION,
    ERROR,
    WARNING,
    AuditReport,
    DriftAuditError,
    assert_clean,
    audit_file,
    audit_hot,
    check_manifest_version_binding,
    check_note_status_contradiction,
    check_render_parity,
    check_secrets_boundary,
    collect_declared_secret_values,
    check_side_rule_stores,
    check_supersede_refs,
)
from rag_kernel.__main__ import main


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _item(item_id, status, **kw):
    base = dict(id=item_id, title=f"title for {item_id}", status=status)
    base.update(kw)
    return TrackedItem(**base)


def _clean_store():
    """A store with assorted statuses and ONE valid supersede ref (REPL exists)."""
    return TrackedItemStore([
        _item("A-OPEN", ItemStatus.OPEN, session="S40"),
        _item("B-PROG", ItemStatus.IN_PROGRESS, session="S49", note="building"),
        _item("C-DEF", ItemStatus.DEFERRED, session="S46", note="parked"),
        _item("D-DONE", ItemStatus.RESOLVED, session="S37"),
        _item("E-SUP", ItemStatus.SUPERSEDED, session="S30", superseded_by="REPL"),
        _item("REPL", ItemStatus.OPEN, session="S30"),
    ])


def _rendered_hot(store):
    """A HOT dict whose legacy arrays are rendered from the canonical store."""
    hot = {"meta": {"session_id": "S52"}, TRACKED_ITEMS_KEY: store.to_list()}
    drift_render.apply_renders(hot)  # open_tasks + deferred_items now match
    return hot


# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

def test_version_and_severities():
    assert DRIFT_AUDIT_VERSION == "1.14.0"
    assert ERROR == "error" and WARNING == "warning"


# ---------------------------------------------------------------------------
# manifest version single-source binding (KA-5 / E-046)
# ---------------------------------------------------------------------------

class TestManifestVersionBinding:
    """check_manifest_version_binding asserts the @rag-kernel-manifest version
    fields are single-sourced from rag_kernel.__version__ / __spec_version__."""

    def test_live_package_binds_clean(self):
        """The real kernel package is correctly single-sourced — no findings."""
        assert check_manifest_version_binding() == []

    def test_docstring_carries_no_version_literal(self):
        """The single-source contract: the raw manifest docstring must NOT hardcode
        version / spec_version (discover injects them)."""
        import rag_kernel
        raw = rag_kernel._extract_manifest(rag_kernel.__doc__) or {}
        assert "version" not in raw
        assert "spec_version" not in raw

    def test_discover_injects_authorities(self):
        """discover() surfaces the live authorities in the package manifest."""
        import rag_kernel
        pkg = rag_kernel.discover()["package"]
        assert pkg["version"] == rag_kernel.__version__
        assert pkg["spec_version"] == rag_kernel.__spec_version__

    def test_reintroduced_stale_literal_is_caught(self, monkeypatch):
        """If a stale version literal is re-introduced into the docstring, the
        binding check fails loud (the exact E-046 regression)."""
        import rag_kernel
        stale_doc = rag_kernel.__doc__.replace(
            '"package": "rag_kernel",',
            '"package": "rag_kernel",\n  "version": "0.0.1",', 1)
        monkeypatch.setattr(rag_kernel, "__doc__", stale_doc)
        findings = check_manifest_version_binding()
        assert any(f.check == "manifest_version_binding" and f.severity == ERROR
                   and "0.0.1" in f.detail for f in findings)

    def test_missing_authority_is_caught(self, monkeypatch):
        """A missing single-source authority is itself the defect."""
        import rag_kernel
        monkeypatch.delattr(rag_kernel, "__spec_version__", raising=False)
        findings = check_manifest_version_binding()
        assert any(f.check == "manifest_version_binding" and f.severity == ERROR
                   and "__spec_version__" in f.detail for f in findings)

    def test_wired_into_audit_hot(self, monkeypatch):
        """The binding check is always-on in audit_hot — a stale docstring literal
        surfaces through the aggregate report even on an otherwise-clean HOT."""
        import rag_kernel
        clean_hot = _rendered_hot(_clean_store())
        assert audit_hot(clean_hot).errors == ()  # baseline clean
        stale_doc = rag_kernel.__doc__.replace(
            '"package": "rag_kernel",',
            '"package": "rag_kernel",\n  "spec_version": "0.0.0",', 1)
        monkeypatch.setattr(rag_kernel, "__doc__", stale_doc)
        report = audit_hot(clean_hot)
        assert any(f.check == "manifest_version_binding" for f in report.errors)


# ---------------------------------------------------------------------------
# render parity (the E-040 regression assertion)
# ---------------------------------------------------------------------------

def test_clean_rendered_rag_is_clean():
    hot = _rendered_hot(_clean_store())
    report = audit_hot(hot)
    assert report.ok
    assert report.errors == ()


def test_render_parity_detects_open_tasks_hand_edit():
    hot = _rendered_hot(_clean_store())
    hot["open_tasks"].append("X-GHOST [OPEN · S99]: hand-typed line")
    findings = check_render_parity(hot)
    assert any(f.check == "render_parity" and f.severity == ERROR for f in findings)


def test_render_parity_detects_deferred_items_hand_edit():
    hot = _rendered_hot(_clean_store())
    hot["deferred_items"] = []  # someone wiped the rendered array
    findings = check_render_parity(hot)
    assert any(f.check == "render_parity" and f.severity == ERROR for f in findings)


def test_absent_legacy_arrays_are_not_a_parity_error():
    # A HOT with only the canonical array (nothing rendered yet) is not "drift".
    hot = {"meta": {}, TRACKED_ITEMS_KEY: _clean_store().to_list()}
    assert check_render_parity(hot) == []


# ---------------------------------------------------------------------------
# supersede referential integrity
# ---------------------------------------------------------------------------

def test_supersede_ref_valid_is_clean():
    assert check_supersede_refs(_clean_store()) == []


def test_supersede_ref_dangling_is_error():
    store = TrackedItemStore([
        _item("A-OPEN", ItemStatus.OPEN),
        _item("B-SUP", ItemStatus.SUPERSEDED, superseded_by="NOPE"),
    ])
    findings = check_supersede_refs(store)
    assert len(findings) == 1
    assert findings[0].severity == ERROR
    assert findings[0].item_id == "B-SUP"


# ---------------------------------------------------------------------------
# note / status contradiction (INS-038, heuristic warning)
# ---------------------------------------------------------------------------

def test_active_item_note_claiming_done_warns():
    store = TrackedItemStore([_item("A", ItemStatus.OPEN, note="incs 1-4 done; this stays open")])
    findings = check_note_status_contradiction(store)
    assert len(findings) == 1
    assert findings[0].severity == WARNING
    assert findings[0].item_id == "A"


def test_terminal_item_note_claiming_done_is_fine():
    store = TrackedItemStore([_item("A", ItemStatus.RESOLVED, note="done and shipped")])
    assert check_note_status_contradiction(store) == []


def test_note_without_completion_word_no_false_positive():
    # "fix the bug" must not trip (only the claim "fixed" does, on a word boundary).
    store = TrackedItemStore([_item("A", ItemStatus.IN_PROGRESS, note="fix the parser bug")])
    assert check_note_status_contradiction(store) == []


# ---------------------------------------------------------------------------
# Rule 13 side-store scan
# ---------------------------------------------------------------------------

def test_side_rule_stores_detected(tmp_path):
    (tmp_path / "MEMORY.md").write_text("x", encoding="utf-8")
    (tmp_path / "feedback_report.md").write_text("x", encoding="utf-8")
    (tmp_path / "project_state.md").write_text("x", encoding="utf-8")
    (tmp_path / "README.md").write_text("legit", encoding="utf-8")
    findings = check_side_rule_stores(tmp_path)
    names = sorted(Path(f.detail.split("root: ")[1].split(" ")[0]).name for f in findings)
    assert names == ["MEMORY.md", "feedback_report.md", "project_state.md"]
    assert all(f.severity == ERROR for f in findings)


def test_side_rule_stores_skips_vcs_and_cache(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "MEMORY.md").write_text("x", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "project_x.md").write_text("x", encoding="utf-8")
    assert check_side_rule_stores(tmp_path) == []


def test_side_rule_stores_missing_root_is_empty(tmp_path):
    assert check_side_rule_stores(tmp_path / "does-not-exist") == []


# ---------------------------------------------------------------------------
# secrets boundary (KA-SECRETS-BOUNDARY / P1 G2)
# ---------------------------------------------------------------------------

import hashlib


def _hot_with(*values):
    """A HOT dict that embeds each given string somewhere in its content."""
    hot = _rendered_hot(_clean_store())
    hot["meta"]["_test_blob"] = list(values)
    return hot


def test_secrets_boundary_none_root_self_skips():
    hot = _hot_with("SUPERSECRETTOKEN-abcdef123456")
    assert check_secrets_boundary(hot, None) == []


def test_secrets_boundary_no_secret_files_is_clean(tmp_path):
    # A project with no config/ tree and no declared secret files audits clean.
    (tmp_path / "README.md").write_text("nothing secret here", encoding="utf-8")
    assert check_secrets_boundary(_rendered_hot(_clean_store()), tmp_path) == []


def test_secrets_boundary_detects_leaked_json_value(tmp_path):
    secret = "AKIA-live-1234567890-EXAMPLE"
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "creds.json").write_text(
        json.dumps({"api_key": secret, "note": "prod"}), encoding="utf-8")
    hot = _hot_with(secret)  # the secret leaked into the RAG
    findings = check_secrets_boundary(hot, tmp_path)
    assert len(findings) == 1
    f = findings[0]
    assert f.check == "secrets_boundary" and f.severity == ERROR
    # redaction: the secret NEVER appears; only its fingerprint does
    assert secret not in f.detail
    assert hashlib.sha256(secret.encode()).hexdigest()[:12] in f.detail
    assert "config/creds.json" in f.detail


def test_collect_declared_secret_values_shared_source(tmp_path):
    # The shared collector (used by BOTH the audit-time boundary check and the
    # ingest-time guard) returns the declared-secret value keyed to its source.
    secret = "FAKE-shared-collector-credential-0192837465"
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "creds.json").write_text(
        json.dumps({"api_key": secret}), encoding="utf-8")
    cands = collect_declared_secret_values(_rendered_hot(_clean_store()), tmp_path)
    assert secret in cands
    rel_posix, key_label = cands[secret]
    assert rel_posix == "config/creds.json"


def test_collect_declared_secret_values_none_root_is_empty():
    assert collect_declared_secret_values({"meta": {}}, None) == {}


def test_secrets_boundary_clean_when_secret_not_in_rag(tmp_path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "creds.json").write_text(
        json.dumps({"api_key": "value-that-stays-put-987654"}), encoding="utf-8")
    hot = _rendered_hot(_clean_store())  # secret NOT embedded
    assert check_secrets_boundary(hot, tmp_path) == []


def test_secrets_boundary_ignores_non_secret_keys(tmp_path):
    # A long value under a NON-secret key is not a credential — even if it happens
    # to appear in the RAG, it must not trip the guard.
    shared = "the-quarterly-roadmap-headline-2026"
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "app.json").write_text(
        json.dumps({"description": shared}), encoding="utf-8")
    hot = _hot_with(shared)
    assert check_secrets_boundary(hot, tmp_path) == []


def test_secrets_boundary_detects_env_file(tmp_path):
    secret = "pk_live_env_secret_0987654321"
    (tmp_path / ".env").write_text(
        f"HOST=example.com\nAPI_TOKEN={secret}\n", encoding="utf-8")
    hot = _hot_with(secret)
    findings = check_secrets_boundary(hot, tmp_path)
    assert len(findings) == 1 and findings[0].severity == ERROR
    assert secret not in findings[0].detail


def test_secrets_boundary_detects_pem_body(tmp_path):
    body = "MIIBVwIBADANBgkqhkiG9w0BAQEFAASCAT8wggE7AgEAAkEA-EXAMPLE-KEYMATERIAL"
    (tmp_path / "server.pem").write_text(
        f"-----BEGIN PRIVATE KEY-----\n{body}\n-----END PRIVATE KEY-----\n",
        encoding="utf-8")
    hot = _hot_with(body)
    findings = check_secrets_boundary(hot, tmp_path)
    assert len(findings) == 1 and findings[0].severity == ERROR


def test_secrets_boundary_min_len_suppresses_trivial(tmp_path):
    # A short value (< default 12) under a secret key is not flagged even if present.
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "creds.json").write_text(
        json.dumps({"token": "abc"}), encoding="utf-8")
    hot = _hot_with("abc")
    assert check_secrets_boundary(hot, tmp_path) == []


def test_secrets_boundary_meta_secret_paths_widens(tmp_path):
    # A project can WIDEN the boundary to a custom dir via meta.secret_paths.
    secret = "custom-keydir-secret-abcdef123456"
    keys = tmp_path / "vault"
    keys.mkdir()
    (keys / "prod.json").write_text(
        json.dumps({"secret": secret}), encoding="utf-8")
    hot = _hot_with(secret)
    # default globs do NOT cover vault/ → clean
    assert check_secrets_boundary(hot, tmp_path) == []
    # declaring it widens the boundary → caught
    hot["meta"]["secret_paths"] = ["vault/**"]
    findings = check_secrets_boundary(hot, tmp_path)
    assert len(findings) == 1 and findings[0].severity == ERROR


def test_secrets_boundary_skips_vcs_and_rag_dirs(tmp_path):
    secret = "should-not-be-scanned-abcdef123456"
    for d in (".git", "RAG"):
        sub = tmp_path / d / "config"
        sub.mkdir(parents=True)
        (sub / "creds.json").write_text(
            json.dumps({"api_key": secret}), encoding="utf-8")
    hot = _hot_with(secret)
    assert check_secrets_boundary(hot, tmp_path) == []


def test_secrets_boundary_wired_into_audit_hot(tmp_path):
    secret = "wired-in-secret-value-abcdef123456"
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "creds.json").write_text(
        json.dumps({"password": secret}), encoding="utf-8")
    clean_hot = _rendered_hot(_clean_store())
    # baseline: no root passed → pure in-memory audit stays clean
    assert audit_hot(clean_hot).errors == ()
    # leaked secret + root → surfaces through the aggregate report
    hot = _hot_with(secret)
    report = audit_hot(hot, root=tmp_path)
    assert any(f.check == "secrets_boundary" and f.severity == ERROR
               for f in report.errors)


def test_secrets_boundary_self_skips_without_root_in_audit_hot(tmp_path):
    # audit_hot with root=None must never run the filesystem secrets scan.
    secret = "no-root-no-scan-abcdef123456"
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "creds.json").write_text(
        json.dumps({"api_key": secret}), encoding="utf-8")
    hot = _hot_with(secret)
    report = audit_hot(hot)  # root omitted
    assert not any(f.check == "secrets_boundary" for f in report.findings)


# ---------------------------------------------------------------------------
# assert_clean contract
# ---------------------------------------------------------------------------

def test_assert_clean_passes_when_clean():
    assert_clean(audit_hot(_rendered_hot(_clean_store())))  # no raise


def test_assert_clean_raises_on_error():
    hot = _rendered_hot(_clean_store())
    hot["open_tasks"] = ["bogus"]
    with pytest.raises(DriftAuditError):
        assert_clean(audit_hot(hot))


def test_assert_clean_warning_only_raises_under_strict():
    store = TrackedItemStore([_item("A", ItemStatus.OPEN, note="this is done")])
    hot = _rendered_hot(store)
    report = audit_hot(hot)
    assert report.ok  # warning does not break ok
    assert_clean(report)  # non-strict: no raise
    with pytest.raises(DriftAuditError):
        assert_clean(report, strict=True)


def test_report_to_dict_shape():
    hot = _rendered_hot(_clean_store())
    d = audit_hot(hot).to_dict()
    assert d["ok"] is True
    assert d["errors"] == 0
    assert "findings" in d


# ---------------------------------------------------------------------------
# audit_file (with the default project-root side-store scan)
# ---------------------------------------------------------------------------

def test_audit_file_clean(tmp_path):
    rag_dir = tmp_path / "RAG"
    rag_dir.mkdir()
    p = rag_dir / "RAG_MASTER.json"
    p.write_text(json.dumps(_rendered_hot(_clean_store()), indent=2), encoding="utf-8")
    report = audit_file(p)  # root defaults to p.parent.parent == tmp_path
    assert report.ok


def test_audit_file_flags_side_store_in_root(tmp_path):
    rag_dir = tmp_path / "RAG"
    rag_dir.mkdir()
    p = rag_dir / "RAG_MASTER.json"
    p.write_text(json.dumps(_rendered_hot(_clean_store()), indent=2), encoding="utf-8")
    (tmp_path / "MEMORY.md").write_text("forbidden", encoding="utf-8")
    report = audit_file(p)
    assert not report.ok
    assert any(f.check == "side_rule_stores" for f in report.errors)


# ===========================================================================
# Guarded note verb — core + store + file
# ===========================================================================

def test_with_note_updates_note_keeps_status_and_history():
    it = _item("A", ItemStatus.OPEN, session="S40")
    out = it.with_note("fresh context", session="S52")
    assert out.note == "fresh context"
    assert out.status == ItemStatus.OPEN
    assert out.session == "S52"
    assert out.history == it.history  # note edits are not status events


def test_with_note_rejects_non_string():
    with pytest.raises(ItemValidationError):
        _item("A", ItemStatus.OPEN).with_note(123, session="S52")


def test_store_set_note_unknown_id_raises():
    store = _clean_store()
    with pytest.raises(UnknownItemError):
        store.set_note("NOPE", "x", session="S52")


def test_set_note_in_file_atomic_and_status_untouched(tmp_path):
    p = tmp_path / "RAG_MASTER.json"
    hot = {"meta": {}, TRACKED_ITEMS_KEY: _clean_store().to_list()}
    p.write_text(json.dumps(hot, indent=2), encoding="utf-8")

    set_note_in_file(p, "A-OPEN", "refreshed note", session="S52")

    reread = TrackedItemStore.from_hot(load_hot(p))
    item = reread.get("A-OPEN")
    assert item.note == "refreshed note"
    assert item.status == ItemStatus.OPEN
    assert (tmp_path / "RAG_MASTER.json.bak").exists()  # .bak refreshed


# ===========================================================================
# CLI: note + audit
# ===========================================================================

@pytest.fixture
def rag_file(tmp_path):
    rag_dir = tmp_path / "RAG"
    rag_dir.mkdir()
    p = rag_dir / "RAG_MASTER.json"
    p.write_text(json.dumps(_rendered_hot(_clean_store()), indent=2), encoding="utf-8")
    return p


def test_cli_note_updates(rag_file):
    rc = main(["note", "A-OPEN", "new note", "--rag", str(rag_file), "--session", "S52"])
    assert rc == 0
    store = TrackedItemStore.from_hot(load_hot(rag_file))
    assert store.get("A-OPEN").note == "new note"
    assert store.get("A-OPEN").status == ItemStatus.OPEN


def test_cli_note_unknown_id_fails_loud(rag_file):
    before = rag_file.read_text(encoding="utf-8")
    rc = main(["note", "GHOST", "x", "--rag", str(rag_file), "--session", "S52"])
    assert rc == 1
    assert rag_file.read_text(encoding="utf-8") == before  # nothing written


def test_cli_note_dry_run_writes_nothing(rag_file):
    before = rag_file.read_text(encoding="utf-8")
    rc = main(["note", "A-OPEN", "x", "--rag", str(rag_file), "--session", "S52", "--dry-run"])
    assert rc == 0
    assert rag_file.read_text(encoding="utf-8") == before


def test_cli_audit_clean_exit_zero(rag_file):
    assert main(["audit", "--rag", str(rag_file)]) == 0


def test_cli_audit_detects_hand_edit_exit_one(rag_file):
    hot = json.loads(rag_file.read_text(encoding="utf-8"))
    hot["open_tasks"].append("Z-GHOST [OPEN · S99]: hand-typed")
    rag_file.write_text(json.dumps(hot, indent=2), encoding="utf-8")
    assert main(["audit", "--rag", str(rag_file)]) == 1


def test_cli_audit_strict_fails_on_warning(tmp_path):
    rag_dir = tmp_path / "RAG"
    rag_dir.mkdir()
    p = rag_dir / "RAG_MASTER.json"
    store = TrackedItemStore([_item("A", ItemStatus.OPEN, note="this is done")])
    p.write_text(json.dumps(_rendered_hot(store), indent=2), encoding="utf-8")
    assert main(["audit", "--rag", str(p)]) == 0           # warning -> still 0
    assert main(["audit", "--rag", str(p), "--strict"]) == 1  # strict -> 1


def test_cli_audit_json_output(rag_file, capsys):
    rc = main(["audit", "--rag", str(rag_file), "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["errors"] == 0
