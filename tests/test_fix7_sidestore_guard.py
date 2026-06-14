"""FIX-7 / T1 — live pre-write side-store guard.

The Rule 13 / E-039 side-store invariant (no parallel rule/state store: a
Cowork-memory ``MEMORY.md`` / ``feedback_*.md`` / ``project_*.md``, or a stray
``*_context.json`` beside the RAG) used to be enforced only after the fact by
``drift_audit``. FIX-7 turns it into a WRITE-TIME guard: a canonical RAG-state
write is REFUSED (fail-loud :class:`SideStoreViolation`) while such a store is
live, so the divergence can never commit.

These tests pin:
  * the persistence finders (single source of truth) + their scope,
  * ``assert_no_side_stores`` raising / passing,
  * ``atomic_write(_json)(guard_side_stores=True)`` refusing the write atomically,
  * the guard firing through the real canonical writers (mutate / render / add),
  * the DRY contract: ``drift_audit`` still reports identically (it delegates).
"""

from __future__ import annotations

import json

import pytest

from rag_kernel import persistence
from rag_kernel.persistence import (
    SideStoreViolation,
    assert_no_side_stores,
    atomic_write,
    atomic_write_json,
    find_context_side_stores,
    find_forbidden_rule_stores,
    find_side_stores,
)
from rag_kernel import drift_audit
from rag_kernel import drift_store


# ---------------------------------------------------------------------------
# finders (single source of truth)
# ---------------------------------------------------------------------------

def test_find_forbidden_rule_stores_matches_all_shapes(tmp_path):
    (tmp_path / "MEMORY.md").write_text("x", encoding="utf-8")
    (tmp_path / "feedback_report.md").write_text("x", encoding="utf-8")
    (tmp_path / "project_state.md").write_text("x", encoding="utf-8")
    (tmp_path / "README.md").write_text("ok", encoding="utf-8")  # not forbidden
    hits = {p.name for p in find_forbidden_rule_stores(tmp_path)}
    assert hits == {"MEMORY.md", "feedback_report.md", "project_state.md"}


def test_find_forbidden_rule_stores_skips_vcs_and_build_dirs(tmp_path):
    for d in (".git", "__pycache__", ".pytest_cache"):
        sub = tmp_path / d
        sub.mkdir()
        (sub / "MEMORY.md").write_text("x", encoding="utf-8")
    assert find_forbidden_rule_stores(tmp_path) == []


def test_find_forbidden_rule_stores_missing_root_is_empty(tmp_path):
    assert find_forbidden_rule_stores(tmp_path / "nope") == []


def test_find_context_side_stores_non_recursive(tmp_path):
    (tmp_path / "ebay_context.json").write_text("{}", encoding="utf-8")
    (tmp_path / "other_context.json").write_text("{}", encoding="utf-8")
    nested = tmp_path / "sub"
    nested.mkdir()
    (nested / "deep_context.json").write_text("{}", encoding="utf-8")  # not top-level
    hits = {p.name for p in find_context_side_stores(tmp_path)}
    assert hits == {"ebay_context.json", "other_context.json"}


def test_find_side_stores_tags_kinds(tmp_path):
    (tmp_path / "MEMORY.md").write_text("x", encoding="utf-8")
    (tmp_path / "foo_context.json").write_text("{}", encoding="utf-8")
    kinds = sorted(kind for kind, _ in find_side_stores(tmp_path, tmp_path))
    assert kinds == ["context_store", "rule_store"]


# ---------------------------------------------------------------------------
# assert_no_side_stores
# ---------------------------------------------------------------------------

def test_assert_passes_on_clean_dir(tmp_path):
    rag = tmp_path / "RAG_MASTER.json"
    assert_no_side_stores(rag)  # no offenders -> returns silently


@pytest.mark.parametrize("name", ["MEMORY.md", "feedback_x.md", "project_y.md"])
def test_assert_raises_on_rule_store_in_rag_dir(tmp_path, name):
    (tmp_path / name).write_text("x", encoding="utf-8")
    rag = tmp_path / "RAG_MASTER.json"
    with pytest.raises(SideStoreViolation) as exc:
        assert_no_side_stores(rag)
    assert name in str(exc.value)


def test_assert_raises_on_context_side_store(tmp_path):
    (tmp_path / "ebay_context.json").write_text("{}", encoding="utf-8")
    rag = tmp_path / "RAG_MASTER.json"
    with pytest.raises(SideStoreViolation) as exc:
        assert_no_side_stores(rag)
    assert "ebay_context.json" in str(exc.value)


def test_default_scope_is_rag_dir_not_parent(tmp_path):
    """A rule store in the PARENT of the RAG dir is out of the live guard's
    default scope (that wider sweep is the audit's job)."""
    rag_dir = tmp_path / "RAG"
    rag_dir.mkdir()
    (tmp_path / "MEMORY.md").write_text("x", encoding="utf-8")  # parent, not RAG dir
    rag = rag_dir / "RAG_MASTER.json"
    assert_no_side_stores(rag)  # does not raise — parent is out of default scope


def test_explicit_root_widens_scope_to_parent(tmp_path):
    rag_dir = tmp_path / "RAG"
    rag_dir.mkdir()
    (tmp_path / "MEMORY.md").write_text("x", encoding="utf-8")
    rag = rag_dir / "RAG_MASTER.json"
    with pytest.raises(SideStoreViolation):
        assert_no_side_stores(rag, root=tmp_path)


# ---------------------------------------------------------------------------
# atomic_write(_json) guard — write is refused ATOMICALLY
# ---------------------------------------------------------------------------

def test_guarded_write_succeeds_when_clean(tmp_path):
    rag = tmp_path / "RAG_MASTER.json"
    atomic_write_json(rag, {"meta": {"x": 1}}, guard_side_stores=True)
    assert json.loads(rag.read_text(encoding="utf-8"))["meta"]["x"] == 1


def test_guarded_write_refused_and_atomic(tmp_path):
    rag = tmp_path / "RAG_MASTER.json"
    rag.write_text(json.dumps({"v": "original"}), encoding="utf-8")
    (tmp_path / "MEMORY.md").write_text("x", encoding="utf-8")
    with pytest.raises(SideStoreViolation):
        atomic_write_json(rag, {"v": "new"}, guard_side_stores=True)
    # original untouched, no partial .tmp left behind
    assert json.loads(rag.read_text(encoding="utf-8"))["v"] == "original"
    assert not (tmp_path / "RAG_MASTER.json.tmp").exists()


def test_unguarded_write_ignores_side_stores(tmp_path):
    rag = tmp_path / "RAG_MASTER.json"
    (tmp_path / "MEMORY.md").write_text("x", encoding="utf-8")
    atomic_write(rag, b"{}", guard_side_stores=False)  # default off -> writes
    assert rag.read_bytes() == b"{}"


# ---------------------------------------------------------------------------
# integration: the guard fires through the real canonical writers
# ---------------------------------------------------------------------------

def _seed_rag(rag_dir):
    rag_dir.mkdir(parents=True, exist_ok=True)
    rag = rag_dir / "RAG_MASTER.json"
    rag.write_text(
        json.dumps({"meta": {}, "tracked_items": []}, indent=2), encoding="utf-8"
    )
    return rag


def test_mutate_hot_blocked_by_live_side_store(tmp_path):
    rag = _seed_rag(tmp_path / "RAG")
    (rag.parent / "feedback_note.md").write_text("x", encoding="utf-8")
    with pytest.raises(SideStoreViolation):
        drift_store.mutate_hot(rag, lambda store: None)


def test_mutate_hot_succeeds_when_clean(tmp_path):
    rag = _seed_rag(tmp_path / "RAG")
    drift_store.mutate_hot(rag, lambda store: None)  # no side store -> commits
    assert rag.exists()


# ---------------------------------------------------------------------------
# DRY: drift_audit still reports identically (it now delegates to persistence)
# ---------------------------------------------------------------------------

def test_audit_side_rule_stores_still_reports(tmp_path):
    (tmp_path / "MEMORY.md").write_text("x", encoding="utf-8")
    findings = drift_audit.check_side_rule_stores(tmp_path)
    assert len(findings) == 1
    assert findings[0].check == "side_rule_stores"
    assert findings[0].severity == drift_audit.ERROR


def test_audit_context_side_stores_still_reports(tmp_path):
    (tmp_path / "x_context.json").write_text("{}", encoding="utf-8")
    findings = drift_audit.check_context_side_stores(tmp_path)
    assert len(findings) == 1
    assert findings[0].check == "context_side_stores"


def test_audit_delegates_to_persistence_finders(monkeypatch, tmp_path):
    """Prove the single-source contract: stub the persistence finder and the
    audit check reflects it (no second, divergent scan in drift_audit)."""
    sentinel = tmp_path / "MEMORY.md"
    sentinel.write_text("x", encoding="utf-8")
    called = {}

    def fake(root):
        called["root"] = root
        return [sentinel]

    monkeypatch.setattr(persistence, "find_forbidden_rule_stores", fake)
    findings = drift_audit.check_side_rule_stores(tmp_path)
    assert called["root"] == tmp_path
    assert len(findings) == 1
