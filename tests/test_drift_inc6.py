"""DRIFT-ELIM increment 6 (INS-039): record migration + auditor coverage + Rule 11.

Covers: inference_ledger disposition mapping, INFERENCE spec derivation, additive
guarded migration (add_items / add_items_file), kind-scoped renders, the ERROR/
INFERENCE record render, and the three new auditor checks (ledger consistency,
record coverage, Rule 11 published-doc reconciliation) — including the
false-positive guards (historical-line / CHANGELOG exemption) that keep the
doc reconciliation deterministic.
"""

from __future__ import annotations

import json

import pytest

from rag_kernel.drift_control import ItemKind, ItemStatus, TrackedItem
from rag_kernel import drift_store, drift_render, drift_audit
from rag_kernel.drift_store import (
    DriftStoreError,
    DuplicateItemError,
    ledger_disposition_to_status,
    inference_specs_from_hot,
    add_items,
    add_items_file,
)


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------

def _task(id, status, kind=ItemKind.TASK, note=""):
    return TrackedItem(id=id, title=f"{id} title", status=ItemStatus(status),
                       kind=kind, note=note).to_dict()


def _hot(items=None, ledger=None):
    return {
        "meta": {"last_updated_utc": "2026-06-07T00:00:00Z"},
        "tracked_items": items or [],
        "inference_ledger": ledger or [],
    }


# ---------------------------------------------------------------------------
# disposition mapping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("disp,expected", [
    ("OPEN", ItemStatus.OPEN),
    ("SCHEDULED", ItemStatus.RESOLVED),
    ("DONE", ItemStatus.RESOLVED),
    ("DEFERRED", ItemStatus.DEFERRED),
    ("SUPERSEDED", ItemStatus.SUPERSEDED),
    ("DISCARDED", ItemStatus.DISCARDED),
    ("scheduled", ItemStatus.RESOLVED),  # case-insensitive
])
def test_ledger_disposition_mapping(disp, expected):
    assert ledger_disposition_to_status(disp) == expected


def test_ledger_disposition_unknown_fails_loud():
    with pytest.raises(DriftStoreError):
        ledger_disposition_to_status("MAYBE")


# ---------------------------------------------------------------------------
# inference spec derivation
# ---------------------------------------------------------------------------

def test_inference_specs_from_hot_projects_ledger():
    hot = _hot(ledger=[
        {"id": "INS-100", "summary": "a useful idea " * 20, "disposition": "SCHEDULED",
         "scheduled_as": "ENH-1", "session": "S1"},
        {"id": "INS-101", "summary": "parked thing", "disposition": "DEFERRED", "session": "S2"},
    ])
    specs = inference_specs_from_hot(hot)
    assert [s["id"] for s in specs] == ["INS-100", "INS-101"]
    assert specs[0]["status"] == ItemStatus.RESOLVED
    assert specs[0]["kind"] == ItemKind.INFERENCE
    assert specs[0]["note"] == "ENH-1"
    assert len(specs[0]["title"]) <= 100
    assert specs[1]["status"] == ItemStatus.DEFERRED


def test_inference_specs_bad_ledger_type():
    with pytest.raises(DriftStoreError):
        inference_specs_from_hot({"inference_ledger": {"not": "a list"}})


# ---------------------------------------------------------------------------
# additive migration (guarded)
# ---------------------------------------------------------------------------

def test_add_items_appends_to_existing():
    hot = _hot(items=[_task("TASK-1", "OPEN")])
    add_items(hot, [
        {"id": "E-1", "title": "err one", "status": ItemStatus.RESOLVED, "kind": ItemKind.ERROR},
        {"id": "INS-1", "title": "inf one", "status": ItemStatus.DEFERRED, "kind": ItemKind.INFERENCE},
    ])
    ids = [i["id"] for i in hot["tracked_items"]]
    assert ids == ["E-1", "INS-1", "TASK-1"]  # id-sorted


def test_add_items_duplicate_fails_loud():
    hot = _hot(items=[_task("TASK-1", "OPEN")])
    with pytest.raises(DuplicateItemError):
        add_items(hot, [{"id": "TASK-1", "title": "dup", "status": ItemStatus.OPEN}])


def test_add_items_allow_existing_is_idempotent():
    hot = _hot(items=[_task("TASK-1", "OPEN")])
    add_items(hot, [{"id": "TASK-1", "title": "dup", "status": ItemStatus.OPEN}],
              allow_existing=True)
    assert len(hot["tracked_items"]) == 1


def test_add_items_file_atomic(tmp_path):
    p = tmp_path / "RAG_MASTER.json"
    p.write_text(json.dumps(_hot(items=[_task("TASK-1", "OPEN")])), encoding="utf-8")
    add_items_file(p, [{"id": "E-9", "title": "e9", "status": ItemStatus.RESOLVED,
                        "kind": ItemKind.ERROR}])
    data = json.loads(p.read_text(encoding="utf-8"))
    assert {i["id"] for i in data["tracked_items"]} == {"TASK-1", "E-9"}
    assert (tmp_path / "RAG_MASTER.json.bak").exists()  # .bak refreshed


# ---------------------------------------------------------------------------
# kind-scoped renders
# ---------------------------------------------------------------------------

def test_renders_exclude_record_kinds():
    items = [
        _task("TASK-1", "OPEN"),
        _task("TASK-2", "DEFERRED"),
        _task("E-1", "OPEN", kind=ItemKind.ERROR),
        _task("INS-1", "DEFERRED", kind=ItemKind.INFERENCE),
    ]
    store = drift_store.TrackedItemStore.from_hot(_hot(items=items))
    open_lines = drift_render.render_open_tasks(store)
    deferred = drift_render.render_deferred_items(store)
    backlog = drift_render.render_backlog_section(store)
    assert any("TASK-1" in l for l in open_lines)
    assert not any("E-1" in l for l in open_lines)        # ERROR excluded
    assert [d["id"] for d in deferred] == ["TASK-2"]      # INFERENCE excluded
    assert backlog["open"] == ["TASK-1 — TASK-1 title"]
    assert backlog["deferred"] == ["TASK-2 — TASK-2 title"]


def test_render_records_by_kind():
    items = [
        _task("E-1", "RESOLVED", kind=ItemKind.ERROR),
        _task("INS-1", "DEFERRED", kind=ItemKind.INFERENCE),
        _task("TASK-1", "OPEN"),
    ]
    store = drift_store.TrackedItemStore.from_hot(_hot(items=items))
    errs = drift_render.render_records_by_kind(store, ItemKind.ERROR)
    infs = drift_render.render_records_by_kind(store, "INFERENCE")
    assert [e["id"] for e in errs] == ["E-1"]
    assert [i["id"] for i in infs] == ["INS-1"]


# ---------------------------------------------------------------------------
# auditor: ledger consistency
# ---------------------------------------------------------------------------

def test_ledger_consistency_clean():
    hot = _hot(
        items=[_task("INS-1", "RESOLVED", kind=ItemKind.INFERENCE)],
        ledger=[{"id": "INS-1", "summary": "x", "disposition": "SCHEDULED"}],
    )
    assert drift_audit.check_ledger_consistency(hot) == []


def test_ledger_consistency_status_mismatch():
    hot = _hot(
        items=[_task("INS-1", "DEFERRED", kind=ItemKind.INFERENCE)],
        ledger=[{"id": "INS-1", "summary": "x", "disposition": "SCHEDULED"}],  # ->RESOLVED
    )
    f = drift_audit.check_ledger_consistency(hot)
    assert len(f) == 1 and f[0].severity == drift_audit.ERROR


def test_ledger_consistency_missing_canonical():
    # Post-cutover (an INFERENCE item exists), a ledger entry with no canonical
    # item is flagged. (Pre-cutover dormancy is covered separately.)
    hot = _hot(
        items=[_task("INS-0", "RESOLVED", kind=ItemKind.INFERENCE)],
        ledger=[{"id": "INS-0", "summary": "x", "disposition": "SCHEDULED"},
                {"id": "INS-1", "summary": "x", "disposition": "DEFERRED"}],
    )
    f = drift_audit.check_ledger_consistency(hot)
    assert any(x.item_id == "INS-1" and "no canonical" in x.detail for x in f)


# ---------------------------------------------------------------------------
# auditor: record coverage
# ---------------------------------------------------------------------------

def test_record_coverage_flags_unmigrated_ledger():
    # Enforced once the cutover has happened (at least one INFERENCE item exists).
    hot = _hot(
        items=[_task("INS-9", "RESOLVED", kind=ItemKind.INFERENCE)],
        ledger=[{"id": "INS-9", "summary": "x", "disposition": "SCHEDULED"},
                {"id": "INS-7", "summary": "x", "disposition": "DEFERRED"}],
    )
    f = drift_audit.check_record_coverage(hot)
    assert any(x.item_id == "INS-7" for x in f)


def test_precutover_gate_ledger_consistency_dormant():
    # No INFERENCE items yet => ledger not migrated => consistency dormant (clean).
    hot = _hot(items=[_task("TASK-1", "OPEN")],
               ledger=[{"id": "INS-7", "summary": "x", "disposition": "DEFERRED"}])
    assert drift_audit.check_ledger_consistency(hot) == []


def test_precutover_gate_record_coverage_dormant(tmp_path):
    # No INFERENCE/ERROR items yet => coverage dormant for both stores (clean).
    el = tmp_path / "ERROR_LOG.md"
    el.write_text("### E-001: boom\n", encoding="utf-8")
    hot = _hot(items=[_task("TASK-1", "OPEN")],
               ledger=[{"id": "INS-7", "summary": "x", "disposition": "DEFERRED"}])
    assert drift_audit.check_record_coverage(hot, error_log_path=el) == []


def test_record_coverage_error_log_headings(tmp_path):
    el = tmp_path / "ERROR_LOG.md"
    el.write_text("## S1\n\n### E-001: first\nbody\n\n### E-002: second\n", encoding="utf-8")
    hot = _hot(items=[_task("E-001", "RESOLVED", kind=ItemKind.ERROR)])
    f = drift_audit.check_record_coverage(hot, error_log_path=el)
    ids = {x.item_id for x in f}
    assert ids == {"E-002"}  # E-001 covered, E-002 missing


# ---------------------------------------------------------------------------
# auditor: Rule 11 reconciliation
# ---------------------------------------------------------------------------

def _docs(tmp_path, readme="", changelog="", roadmap=""):
    (tmp_path / "README.md").write_text(readme, encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    (tmp_path / "docs").mkdir(exist_ok=True)
    (tmp_path / "docs" / "ROADMAP.md").write_text(roadmap, encoding="utf-8")
    return [tmp_path / "README.md", tmp_path / "CHANGELOG.md", tmp_path / "docs" / "ROADMAP.md"]


def test_repo_claim_clean(tmp_path):
    docs = _docs(tmp_path,
                 readme="Runtime v0.4.0 — 19 modules. sha `268149294421`.\nGRAPH-ORCH shipped.",
                 roadmap="GRAPH-ORCH — Released in v0.4.0. 19 modules.")
    store = [_task("GRAPH-ORCH", "RESOLVED", kind=ItemKind.MILESTONE)]
    f = drift_audit.check_repo_claim_reconciliation(
        docs, [TrackedItem.from_dict(d) for d in store],
        version="0.4.0", module_count=19, drift_sha="268149294421")
    assert [x for x in f if x.severity == drift_audit.ERROR] == []


def test_repo_claim_module_count_mismatch(tmp_path):
    docs = _docs(tmp_path, readme="Runtime v0.4.0 — 12 modules.")
    f = drift_audit.check_repo_claim_reconciliation(
        docs, [], version="0.4.0", module_count=19)
    assert any(x.check == "repo_claim_headline" and x.severity == drift_audit.ERROR for x in f)


def test_repo_claim_sha_mismatch(tmp_path):
    docs = _docs(tmp_path, readme="drift gate sha `deadbeefcafe` is current.")
    f = drift_audit.check_repo_claim_reconciliation(
        docs, [], drift_sha="268149294421")
    assert any("sha" in x.detail and x.severity == drift_audit.ERROR for x in f)


def test_repo_claim_id_anchored_pending_contradiction(tmp_path):
    docs = _docs(tmp_path, roadmap="DRIFT-ELIM is still deferred to a later release.")
    store = [TrackedItem(id="DRIFT-ELIM", title="t", status=ItemStatus.RESOLVED,
                         kind=ItemKind.MILESTONE)]
    f = drift_audit.check_repo_claim_reconciliation(docs, store)
    assert any(x.check == "repo_claim_status" and x.item_id == "DRIFT-ELIM"
               and x.severity == drift_audit.ERROR for x in f)


def test_repo_claim_changelog_history_exempt_from_status(tmp_path):
    # The SAME pending claim in CHANGELOG must NOT fire (append-only history).
    docs = _docs(tmp_path, changelog="DRIFT-ELIM increment 1 ... Unreleased.")
    store = [TrackedItem(id="DRIFT-ELIM", title="t", status=ItemStatus.RESOLVED,
                         kind=ItemKind.MILESTONE)]
    f = drift_audit.check_repo_claim_reconciliation(docs, store)
    assert not any(x.check == "repo_claim_status" for x in f)


def test_repo_claim_past_version_line_exempt(tmp_path):
    # A line naming a past version is historical: pending word must not fire,
    # and an old module count on it must not fire either.
    docs = _docs(tmp_path,
                 roadmap="Runtime v0.2.7 — 12 modules. GRAPH-ORCH was planned then.")
    store = [TrackedItem(id="GRAPH-ORCH", title="t", status=ItemStatus.RESOLVED,
                         kind=ItemKind.MILESTONE)]
    f = drift_audit.check_repo_claim_reconciliation(
        docs, store, version="0.4.0", module_count=19)
    assert [x for x in f if x.severity == drift_audit.ERROR] == []


def test_repo_claim_unreleased_near_resolved_is_not_flagged(tmp_path):
    # "unreleased" is orthogonal to resolution: a RESOLVED item can be correctly
    # unreleased-on-main. Must NOT fire (this project's normal vocabulary).
    docs = _docs(tmp_path, roadmap="DRIFT-ELIM-INC6 done — unreleased on main (post-v0.4.0).")
    store = [TrackedItem(id="DRIFT-ELIM-INC6", title="t", status=ItemStatus.RESOLVED,
                         kind=ItemKind.MILESTONE)]
    f = drift_audit.check_repo_claim_reconciliation(docs, store)
    assert not any(x.check == "repo_claim_status" for x in f)


def test_repo_claim_word_boundary_no_identifier_false_positive(tmp_path):
    # "deferred" inside the identifier "deferred_items" must NOT fire.
    docs = _docs(tmp_path, readme="DRIFT-ELIM renders open_tasks/deferred_items from canonical.")
    store = [TrackedItem(id="DRIFT-ELIM", title="t", status=ItemStatus.RESOLVED,
                         kind=ItemKind.MILESTONE)]
    f = drift_audit.check_repo_claim_reconciliation(docs, store)
    assert not any(x.check == "repo_claim_status" for x in f)


def test_repo_claim_todo_near_resolved_is_flagged(tmp_path):
    docs = _docs(tmp_path, roadmap="DRIFT-ELIM is still a TODO for next quarter.")
    store = [TrackedItem(id="DRIFT-ELIM", title="t", status=ItemStatus.RESOLVED,
                         kind=ItemKind.MILESTONE)]
    f = drift_audit.check_repo_claim_reconciliation(docs, store)
    assert any(x.check == "repo_claim_status" for x in f)


def test_canonical_facts_live():
    import rag_kernel
    version, module_count, drift_sha = drift_audit.canonical_facts()
    # De-drift (E-041 follow-up): assert against the single source of truth
    # (rag_kernel.__version__), never a frozen literal that silently rots on the
    # next version bump and re-reddens the suite (the original S60→S61 E-041).
    assert version == rag_kernel.__version__
    assert module_count == 19
    assert drift_sha and len(drift_sha) == 12


# ---------------------------------------------------------------------------
# integration: full migration round-trip on a temp RAG
# ---------------------------------------------------------------------------

def test_migration_roundtrip(tmp_path):
    hot = _hot(
        items=[_task("TASK-1", "OPEN"), _task("REL-1", "RESOLVED", kind=ItemKind.RELEASE)],
        ledger=[
            {"id": "INS-1", "summary": "did a thing", "disposition": "SCHEDULED"},
            {"id": "INS-2", "summary": "parked", "disposition": "DEFERRED"},
        ],
    )
    p = tmp_path / "RAG_MASTER.json"
    p.write_text(json.dumps(hot), encoding="utf-8")
    el = tmp_path / "ERROR_LOG.md"
    el.write_text("### E-001: boom\n", encoding="utf-8")

    # migrate inference + one error via the guarded path
    base = drift_store.load_hot(p)
    specs = inference_specs_from_hot(base) + [
        {"id": "E-001", "title": "boom", "status": ItemStatus.RESOLVED, "kind": ItemKind.ERROR},
    ]
    add_items_file(p, specs)
    drift_render.apply_renders_file(p)

    report = drift_audit.audit_file(p, scan_root=False, error_log_path=el)
    assert report.is_clean(strict=True), report.summary()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert {i["id"] for i in data["tracked_items"]} == {"TASK-1", "REL-1", "INS-1", "INS-2", "E-001"}
    # task backlog renders stayed scoped (records did not leak in)
    assert data["open_tasks"] == ["TASK-1 [OPEN · —]: TASK-1 title"]
    assert data["deferred_items"] == []
