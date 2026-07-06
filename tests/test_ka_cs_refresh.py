"""KA-CS-REFRESH: the governed current_status refresh verb.

``current_status`` denormalizes two machine-facts whose authorities live OUTSIDE
the RAG — ``rag_kernel.__version__`` and the published git HEAD.
``drift_audit.check_current_status_freshness`` FAILS LOUD (E-043) when the stated
fact drifts from the live authority, but until now there was no governed way to
REPAIR that drift: a mid-session dev commit left current_status stale and the only
fix was a forbidden hand-edit (the S116 + S127 field incidents).

These tests pin the repair verb's contract:
  * the planner replaces ONLY the leading machine-fact token, preserving all
    surrounding narrative, and is idempotent (unchanged when already fresh);
  * missing target fields self-skip unless ``--strict``;
  * the file writer is atomic + ``.bak``-mirrored and a true no-op when fresh;
  * detection and repair share the IDENTICAL token constants (the DRY invariant);
  * a refresh clears the freshness guard BY CONSTRUCTION;
  * the CLI verb wires through with correct exit codes and --dry-run safety.
"""

from __future__ import annotations

import json

import pytest

from rag_kernel import drift_audit, drift_store
from rag_kernel.drift_store import (
    CURRENT_STATUS_KEY,
    CurrentStatusRefreshError,
    compute_current_status_refresh,
    refresh_current_status_file,
)
from rag_kernel.drift_audit import check_current_status_freshness, audit_hot
from rag_kernel.__main__ import main


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _hot(version_field=None, github_field=None, tests_field=None, *,
         with_cs=True, meta=True):
    cs = {}
    if version_field is not None:
        cs["rag_kernel_version"] = version_field
    if github_field is not None:
        cs["github_repo"] = github_field
    if tests_field is not None:
        cs["unit_tests"] = tests_field
    hot = {"tracked_items": []}
    if meta:
        hot["meta"] = {"last_updated_utc": "2020-01-01T00:00:00Z",
                       "written_by_session": "S1"}
    if with_cs:
        hot["current_status"] = cs
    return hot


def _actions(changes):
    return {c["field"]: c["action"] for c in changes}


# ---------------------------------------------------------------------------
# DRY invariant: detection and repair share ONE set of token constants
# ---------------------------------------------------------------------------

def test_guard_and_repair_share_the_same_constants():
    # The re-exported names in drift_audit must be the very same objects defined
    # in drift_store — so the guard that detects staleness and the verb that
    # repairs it can never disagree on what a token is.
    assert drift_audit._CS_VERSION_FIELD is drift_store._CS_VERSION_FIELD
    assert drift_audit._CS_HEAD_FIELDS is drift_store._CS_HEAD_FIELDS
    assert drift_audit._CS_VERSION_TOKEN_RE is drift_store._CS_VERSION_TOKEN_RE
    assert drift_audit._CS_HEAD_RE is drift_store._CS_HEAD_RE


# ---------------------------------------------------------------------------
# planner — version token
# ---------------------------------------------------------------------------

def test_version_updated_when_stale():
    hot = _hot(version_field="v0.4.26 — 19 modules, 1,639 tests")
    new_cs, changes = compute_current_status_refresh(hot, version="0.4.27")
    assert _actions(changes)["rag_kernel_version"] == "updated"
    # only the numeric token is replaced — the stylistic 'v' prefix is preserved.
    assert new_cs["rag_kernel_version"].startswith("v0.4.27 — 19 modules")


def test_version_unchanged_when_fresh():
    hot = _hot(version_field="v0.4.27 — current")
    # canonical carries the bare form; the field the 'v' prefix — the leading token
    # 0.4.27 already equals the want, so it is a no-op.
    new_cs, changes = compute_current_status_refresh(hot, version="0.4.27")
    assert _actions(changes)["rag_kernel_version"] == "unchanged"


def test_version_replaces_only_leading_token_preserving_history():
    field = ("v0.4.26 — 1,639 tests. History: 1,623 @ S126 (v0.4.25), "
             "1,372 @ S87 (v0.4.12).")
    hot = _hot(version_field=field)
    new_cs, _ = compute_current_status_refresh(hot, version="0.4.27")
    got = new_cs["rag_kernel_version"]
    assert got.startswith("v0.4.27 — 1,639 tests.")
    # historical mentions untouched
    assert "v0.4.25" in got and "v0.4.12" in got


def test_version_skipped_when_field_absent_nonstrict():
    hot = _hot(github_field="LATEST COMMIT abc1234")
    new_cs, changes = compute_current_status_refresh(hot, version="0.4.27")
    assert _actions(changes)["rag_kernel_version"] == "skipped"


def test_version_strict_raises_when_field_absent():
    hot = _hot(github_field="LATEST COMMIT abc1234")
    with pytest.raises(CurrentStatusRefreshError):
        compute_current_status_refresh(hot, version="0.4.27", strict=True)


def test_version_not_planned_when_authority_none():
    hot = _hot(version_field="v0.4.26 — anything")
    _, changes = compute_current_status_refresh(hot, version=None)
    assert "rag_kernel_version" not in _actions(changes)


# ---------------------------------------------------------------------------
# planner — git HEAD token
# ---------------------------------------------------------------------------

def test_head_updated_when_stale():
    hot = _hot(github_field="https://github.com/x/y — PUBLIC. LATEST COMMIT deadbee (old)")
    new_cs, changes = compute_current_status_refresh(hot, git_head="abc1234")
    assert _actions(changes)["github_repo"] == "updated"
    assert "LATEST COMMIT abc1234 (old)" in new_cs["github_repo"]


def test_head_unchanged_when_exact():
    hot = _hot(github_field="LATEST COMMIT abc1234 (S128)")
    _, changes = compute_current_status_refresh(hot, git_head="abc1234")
    assert _actions(changes)["github_repo"] == "unchanged"


def test_head_unchanged_when_prefix_equal():
    # current_status carries a short sha; live HEAD is the longer form (prefix) —
    # already fresh, no rewrite, the existing (short) sha is left as-is.
    hot = _hot(github_field="LATEST COMMIT abc1234 ...")
    new_cs, changes = compute_current_status_refresh(hot, git_head="abc1234def567")
    assert _actions(changes)["github_repo"] == "unchanged"
    assert "abc1234 ..." in new_cs["github_repo"]  # untouched


def test_head_skipped_when_field_absent_nonstrict():
    hot = _hot(version_field="v0.4.27")
    _, changes = compute_current_status_refresh(hot, git_head="abc1234")
    assert _actions(changes)["github_repo"] == "skipped"


def test_head_strict_raises_when_absent():
    hot = _hot(version_field="v0.4.27")
    with pytest.raises(CurrentStatusRefreshError):
        compute_current_status_refresh(hot, git_head="abc1234", strict=True)


# ---------------------------------------------------------------------------
# planner — optional unit_tests count
# ---------------------------------------------------------------------------

def test_tests_updated_when_supplied():
    hot = _hot(tests_field="1,639 tests, all passing (verified live S127)")
    new_cs, changes = compute_current_status_refresh(hot, tests="1,700")
    assert _actions(changes)["unit_tests"] == "updated"
    assert new_cs["unit_tests"].startswith("1,700 tests, all passing")


def test_tests_not_planned_when_none():
    hot = _hot(tests_field="1,639 tests")
    _, changes = compute_current_status_refresh(hot, tests=None)
    assert "unit_tests" not in _actions(changes)


# ---------------------------------------------------------------------------
# planner — structural guards
# ---------------------------------------------------------------------------

def test_raises_when_current_status_absent():
    hot = {"tracked_items": []}
    with pytest.raises(CurrentStatusRefreshError):
        compute_current_status_refresh(hot, version="0.4.27")


def test_raises_when_current_status_not_a_dict():
    hot = {"current_status": "prose"}
    with pytest.raises(CurrentStatusRefreshError):
        compute_current_status_refresh(hot, version="0.4.27")


def test_all_three_facts_plan_together():
    hot = _hot(
        version_field="v0.4.26 — stale",
        github_field="LATEST COMMIT deadbee",
        tests_field="1,639 tests",
    )
    _, changes = compute_current_status_refresh(
        hot, version="0.4.27", git_head="abc1234", tests="1,700")
    acts = _actions(changes)
    assert acts == {"rag_kernel_version": "updated",
                    "github_repo": "updated",
                    "unit_tests": "updated"}


# ---------------------------------------------------------------------------
# file writer — atomicity, .bak parity, idempotence, meta stamp
# ---------------------------------------------------------------------------

def _write(tmp_path, hot):
    p = tmp_path / "RAG_MASTER.json"
    p.write_text(json.dumps(hot, indent=2), encoding="utf-8")
    return p


def test_file_writes_and_mirrors_bak_when_changed(tmp_path):
    p = _write(tmp_path, _hot(version_field="v0.4.26 — stale",
                              github_field="LATEST COMMIT deadbee"))
    changes, wrote = refresh_current_status_file(
        p, version="0.4.27", git_head="abc1234")
    assert wrote is True
    on_disk = json.loads(p.read_text(encoding="utf-8"))
    assert on_disk["current_status"]["rag_kernel_version"].startswith("v0.4.27")
    assert "LATEST COMMIT abc1234" in on_disk["current_status"]["github_repo"]
    # .bak mirrored to byte-parity
    bak = p.with_name(p.name + ".bak")
    assert bak.exists()
    assert bak.read_bytes() == p.read_bytes()


def test_file_stamps_meta_last_updated(tmp_path):
    p = _write(tmp_path, _hot(version_field="v0.4.26 — stale"))
    refresh_current_status_file(p, version="0.4.27")
    on_disk = json.loads(p.read_text(encoding="utf-8"))
    assert on_disk["meta"]["last_updated_utc"] != "2020-01-01T00:00:00Z"
    # written_by_session is NOT touched (checkpoint owns it) — coherence preserved
    assert on_disk["meta"]["written_by_session"] == "S1"


def test_file_noop_when_already_fresh(tmp_path):
    p = _write(tmp_path, _hot(version_field="v0.4.27 — current",
                              github_field="LATEST COMMIT abc1234"))
    before = p.read_bytes()
    changes, wrote = refresh_current_status_file(
        p, version="0.4.27", git_head="abc1234")
    assert wrote is False
    assert p.read_bytes() == before  # not rewritten
    assert all(c["action"] in ("unchanged", "skipped") for c in changes)


def test_file_dry_run_never_writes(tmp_path):
    p = _write(tmp_path, _hot(version_field="v0.4.26 — stale"))
    before = p.read_bytes()
    changes, wrote = refresh_current_status_file(
        p, version="0.4.27", dry_run=True)
    assert wrote is False
    assert p.read_bytes() == before
    assert _actions(changes)["rag_kernel_version"] == "updated"  # planned, not applied


# ---------------------------------------------------------------------------
# by-construction: a refresh clears the freshness guard
# ---------------------------------------------------------------------------

def test_refresh_clears_freshness_guard(tmp_path):
    hot = _hot(version_field="v0.4.26 — stale",
               github_field="LATEST COMMIT deadbee")
    # stale => guard fires on both sub-checks
    assert len(check_current_status_freshness(
        hot, version="0.4.27", git_head="abc1234")) == 2
    p = _write(tmp_path, hot)
    refresh_current_status_file(p, version="0.4.27", git_head="abc1234")
    refreshed = json.loads(p.read_text(encoding="utf-8"))
    # after the governed refresh the guard is clean by construction
    assert check_current_status_freshness(
        refreshed, version="0.4.27", git_head="abc1234") == []
    report = audit_hot(refreshed, version="0.4.27", git_head="abc1234")
    assert not any(f.check == "current_status_freshness" for f in report.findings)


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------

def test_cli_refresh_writes_and_exits_zero(tmp_path):
    p = _write(tmp_path, _hot(version_field="v0.4.26 — stale",
                              github_field="LATEST COMMIT deadbee"))
    rc = main(["refresh-current-status", "--rag", str(p), "--session", "S128",
               "--version", "0.4.27", "--git-head", "abc1234"])
    assert rc == 0
    on_disk = json.loads(p.read_text(encoding="utf-8"))
    assert on_disk["current_status"]["rag_kernel_version"].startswith("v0.4.27")


def test_cli_dry_run_does_not_write(tmp_path):
    p = _write(tmp_path, _hot(version_field="v0.4.26 — stale"))
    before = p.read_bytes()
    rc = main(["refresh-current-status", "--rag", str(p), "--session", "S128",
               "--version", "0.4.27", "--dry-run"])
    assert rc == 0
    assert p.read_bytes() == before


def test_cli_strict_missing_token_exits_one(tmp_path):
    # current_status present but no github_repo field; --strict + a git-head must fail.
    p = _write(tmp_path, _hot(version_field="v0.4.27 — current"))
    rc = main(["refresh-current-status", "--rag", str(p), "--session", "S128",
               "--version", "0.4.27", "--git-head", "abc1234", "--strict"])
    assert rc == 1
