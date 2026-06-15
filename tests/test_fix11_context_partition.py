"""FIX-11 / U3 — sanctioned, non-loaded project-context partition.

Before FIX-11 the deploy guide told operators to drop a ``*_context.json`` into
the RAG dir and ``configure``-merge it, which the side-store auditor then flagged
as a redundant parallel store (the eBay ``ebay_context.json`` contradiction,
S80 deploy audit U3). FIX-11 gives project context a legitimate home:
``RAG_CONTEXT.json`` — a sanctioned, persistent, NON-LOADED, lazy-loaded
partitioned store (``rag_kernel.cold_manager.ProjectContextManager``), allowlisted
in ``persistence.SANCTIONED_CONTEXT_STORES`` so neither the live pre-write guard
nor ``audit`` flags it, while a *transient* ``ebay_context.json`` STILL is.

inc1 scope pinned here:
  * the persistence finder excludes the sanctioned store but keeps flagging
    transient inputs (single source of truth for guard + audit),
  * ``assert_no_side_stores`` passes with only the sanctioned store present and
    still raises on a transient ``*_context.json``,
  * the after-the-fact auditor (``check_context_side_stores``) agrees (DRY),
  * ``ProjectContextManager`` round-trips a partition through the sanctioned file
    with lazy loading, eviction, token budgeting, and atomic persistence,
  * the sanction is case-insensitive (platform-stable).
"""

from __future__ import annotations

import json

import pytest

from rag_kernel import persistence
from rag_kernel.persistence import (
    SANCTIONED_CONTEXT_STORES,
    SideStoreViolation,
    assert_no_side_stores,
    find_context_side_stores,
    find_side_stores,
)
from rag_kernel import drift_audit
from rag_kernel.cold_manager import (
    CONTEXT_FILENAME,
    PartitionNotFoundError,
    ProjectContextManager,
)


# ---------------------------------------------------------------------------
# the sanction list itself
# ---------------------------------------------------------------------------

def test_sanctioned_set_contains_rag_context_lowercased():
    # Stored lowercased so the case-insensitive compare is platform-stable.
    assert "rag_context.json" in SANCTIONED_CONTEXT_STORES
    assert CONTEXT_FILENAME == "RAG_CONTEXT.json"
    assert CONTEXT_FILENAME.lower() in SANCTIONED_CONTEXT_STORES


# ---------------------------------------------------------------------------
# finder: sanctioned excluded, transient still flagged
# ---------------------------------------------------------------------------

def test_finder_excludes_sanctioned_store(tmp_path):
    (tmp_path / CONTEXT_FILENAME).write_text("{}", encoding="utf-8")
    assert find_context_side_stores(tmp_path) == []


def test_finder_still_flags_transient_context_inputs(tmp_path):
    (tmp_path / CONTEXT_FILENAME).write_text("{}", encoding="utf-8")  # sanctioned
    (tmp_path / "ebay_context.json").write_text("{}", encoding="utf-8")  # transient
    hits = {p.name for p in find_context_side_stores(tmp_path)}
    assert hits == {"ebay_context.json"}


@pytest.mark.parametrize("name", ["RAG_CONTEXT.json", "rag_context.json", "Rag_Context.json"])
def test_sanction_is_case_insensitive(tmp_path, name):
    (tmp_path / name).write_text("{}", encoding="utf-8")
    # Whatever the casing of the canonical store name, it is never flagged.
    assert [p.name for p in find_context_side_stores(tmp_path)] == []


def test_find_side_stores_does_not_tag_sanctioned(tmp_path):
    (tmp_path / CONTEXT_FILENAME).write_text("{}", encoding="utf-8")
    assert find_side_stores(tmp_path, tmp_path) == []


# ---------------------------------------------------------------------------
# live pre-write guard
# ---------------------------------------------------------------------------

def test_guard_passes_with_only_sanctioned_store(tmp_path):
    (tmp_path / CONTEXT_FILENAME).write_text("{}", encoding="utf-8")
    rag = tmp_path / "RAG_MASTER.json"
    assert_no_side_stores(rag)  # sanctioned store present -> still silent


def test_guard_still_raises_on_transient_beside_sanctioned(tmp_path):
    (tmp_path / CONTEXT_FILENAME).write_text("{}", encoding="utf-8")
    (tmp_path / "ebay_context.json").write_text("{}", encoding="utf-8")
    rag = tmp_path / "RAG_MASTER.json"
    with pytest.raises(SideStoreViolation) as exc:
        assert_no_side_stores(rag)
    assert "ebay_context.json" in str(exc.value)
    assert CONTEXT_FILENAME not in str(exc.value)


# ---------------------------------------------------------------------------
# after-the-fact auditor agrees (DRY delegation)
# ---------------------------------------------------------------------------

def test_audit_does_not_flag_sanctioned_store(tmp_path):
    (tmp_path / CONTEXT_FILENAME).write_text("{}", encoding="utf-8")
    assert drift_audit.check_context_side_stores(tmp_path) == []


def test_audit_still_flags_transient(tmp_path):
    (tmp_path / "ebay_context.json").write_text("{}", encoding="utf-8")
    findings = drift_audit.check_context_side_stores(tmp_path)
    assert len(findings) == 1
    assert "ebay_context.json" in findings[0].detail


# ---------------------------------------------------------------------------
# ProjectContextManager — non-loaded, lazy, atomic, token-budgeted
# ---------------------------------------------------------------------------

def test_default_points_at_canonical_filename(tmp_path):
    mgr = ProjectContextManager.default(tmp_path)
    assert mgr.path == tmp_path / CONTEXT_FILENAME
    assert ProjectContextManager.FILENAME == CONTEXT_FILENAME


def test_missing_file_is_empty_not_an_error(tmp_path):
    mgr = ProjectContextManager.default(tmp_path)
    assert mgr.partitions == []
    assert mgr.has_partition("anything") is False


def test_update_partition_persists_and_lazy_loads(tmp_path):
    mgr = ProjectContextManager.default(tmp_path)
    mgr.update_partition("comps", {"sku-1": {"median": 42.0}})

    # File exists and is valid JSON with the partition as a top-level key.
    raw = json.loads((tmp_path / CONTEXT_FILENAME).read_text(encoding="utf-8"))
    assert raw["comps"]["sku-1"]["median"] == 42.0

    # A FRESH manager indexes the partition without loading its value...
    fresh = ProjectContextManager.default(tmp_path)
    assert fresh.has_partition("comps") is True
    assert fresh.is_loaded("comps") is False
    # ...and lazy-loads on first get().
    assert fresh.get("comps")["sku-1"]["median"] == 42.0
    assert fresh.is_loaded("comps") is True


def test_unloaded_partition_costs_zero_tokens(tmp_path):
    mgr = ProjectContextManager.default(tmp_path)
    mgr.update_partition("blob", {"x": "y" * 400})
    fresh = ProjectContextManager.default(tmp_path)
    # Not loaded => 0 tokens consumed (the whole point of a non-loaded store).
    assert fresh.token_estimate("blob") == 0
    fresh.get("blob")
    assert fresh.token_estimate("blob") > 0


def test_evict_frees_then_reloads(tmp_path):
    mgr = ProjectContextManager.default(tmp_path)
    mgr.update_partition("p", {"a": 1})
    assert mgr.is_loaded("p") is True
    assert mgr.evict("p") is True
    assert mgr.is_loaded("p") is False
    assert mgr.get("p") == {"a": 1}


def test_missing_partition_raises(tmp_path):
    mgr = ProjectContextManager.default(tmp_path)
    mgr.update_partition("present", {"a": 1})
    with pytest.raises(PartitionNotFoundError):
        mgr.get("absent")


def test_repr_identifies_project_context_manager(tmp_path):
    mgr = ProjectContextManager.default(tmp_path)
    assert "ProjectContextManager" in repr(mgr)
