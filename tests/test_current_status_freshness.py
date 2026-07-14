"""E-043 / AUDIT-CS-FRESHNESS: the current_status freshness guard.

``current_status`` is a human-readable narrative block that denormalizes two
facts whose authorities live OUTSIDE the RAG — the kernel version
(``rag_kernel.__version__``) and the published git HEAD. They cannot be rendered
*from* the RAG, so the auditor GUARDS them: it extracts the stated fact and
asserts it still equals the live authority, failing loud on the stale-snapshot
drift E-043 caught (a current_status frozen at S62 while version/HEAD moved on).

These tests pin the guard's contract: it fires on a mismatch, self-skips when a
field or a canonical fact is absent, is tolerant of the ``v`` prefix and short/long
sha forms, and anchors on the leading version token so historical mentions in the
same field don't trip it.
"""

from __future__ import annotations

import json

from rag_kernel import drift_audit
from rag_kernel.drift_audit import check_current_status_freshness, audit_hot, ERROR


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _hot(version_field=None, github_field=None, *, with_cs=True):
    cs = {}
    if version_field is not None:
        cs["rag_kernel_version"] = version_field
    if github_field is not None:
        cs["github_repo"] = github_field
    hot = {"tracked_items": []}
    if with_cs:
        hot["current_status"] = cs
    return hot


# ---------------------------------------------------------------------------
# version sub-check
# ---------------------------------------------------------------------------

def test_version_match_is_clean():
    hot = _hot(version_field="v0.4.3 — 19 capability modules, 1,142 tests")
    assert check_current_status_freshness(hot, version="0.4.3") == []


def test_version_prefix_v_tolerant():
    # cs carries 'v0.4.3'; canonical is the bare '0.4.3' — must still match.
    hot = _hot(version_field="v0.4.3 — released")
    assert check_current_status_freshness(hot, version="v0.4.3") == []
    assert check_current_status_freshness(hot, version="0.4.3") == []


def test_version_mismatch_is_error():
    hot = _hot(version_field="v0.4.2 — stale snapshot from a prior session")
    out = check_current_status_freshness(hot, version="0.4.3")
    assert len(out) == 1
    assert out[0].severity == ERROR
    assert out[0].check == "current_status_freshness"
    assert "0.4.2" in out[0].detail and "0.4.3" in out[0].detail


def test_version_anchors_on_leading_token_ignoring_history():
    # The field leads with the CURRENT version; later historical mentions
    # (e.g. a version-history tail) must NOT cause a false positive.
    hot = _hot(
        version_field="v0.4.3 — 1,142 tests. History: 1,123 @ S60 (v0.4.1), "
                      "1,082 @ S53 (v0.4.0)."
    )
    assert check_current_status_freshness(hot, version="0.4.3") == []


def test_version_skipped_when_canonical_none():
    hot = _hot(version_field="v0.4.2 — anything")
    assert check_current_status_freshness(hot, version=None) == []


def test_version_skipped_when_field_absent():
    hot = _hot(github_field="LATEST COMMIT abc1234")  # no rag_kernel_version
    assert check_current_status_freshness(hot, version="0.4.3") == []


# ---------------------------------------------------------------------------
# git HEAD sub-check
# ---------------------------------------------------------------------------

def test_head_match_is_clean():
    hot = _hot(github_field="https://github.com/x/y — PUBLIC. LATEST COMMIT e109794 (S66)")
    assert check_current_status_freshness(hot, git_head="e109794") == []


def test_head_prefix_match_short_vs_long():
    # current_status short sha vs a longer live HEAD (prefix) — must match.
    hot = _hot(github_field="LATEST COMMIT e109794 ...")
    assert check_current_status_freshness(hot, git_head="e109794ae6c2") == []


def test_head_mismatch_is_error():
    hot = _hot(github_field="LATEST COMMIT deadbee (old)")
    out = check_current_status_freshness(hot, git_head="e109794")
    assert len(out) == 1
    assert out[0].severity == ERROR
    assert "deadbee" in out[0].detail and "e109794" in out[0].detail


def test_head_skipped_when_canonical_none():
    hot = _hot(github_field="LATEST COMMIT deadbee")
    assert check_current_status_freshness(hot, git_head=None) == []


def test_head_skipped_when_field_absent():
    hot = _hot(version_field="v0.4.3")  # no github_repo
    assert check_current_status_freshness(hot, git_head="e109794") == []


# ---------------------------------------------------------------------------
# structural self-skipping
# ---------------------------------------------------------------------------

def test_no_current_status_is_clean():
    hot = {"tracked_items": []}
    assert check_current_status_freshness(hot, version="0.4.3", git_head="e109794") == []


def test_current_status_not_a_dict_is_clean():
    hot = {"current_status": "some prose string"}
    assert check_current_status_freshness(hot, version="0.4.3", git_head="e109794") == []


def test_both_subchecks_fire_together():
    hot = _hot(
        version_field="v0.4.1 — wrong",
        github_field="LATEST COMMIT badc0de — wrong",
    )
    out = check_current_status_freshness(hot, version="0.4.3", git_head="e109794")
    assert len(out) == 2
    assert all(f.severity == ERROR for f in out)


# ---------------------------------------------------------------------------
# integration through the aggregate runner
# ---------------------------------------------------------------------------

def test_audit_hot_includes_freshness_error():
    hot = _hot(version_field="v0.4.2 — stale")
    report = audit_hot(hot, version="0.4.3")
    assert not report.ok
    assert any(f.check == "current_status_freshness" for f in report.errors)


def test_audit_hot_clean_when_fresh():
    hot = _hot(
        version_field="v0.4.3 — current",
        github_field="LATEST COMMIT e109794",
    )
    report = audit_hot(hot, version="0.4.3", git_head="e109794")
    assert report.ok
    assert not any(f.check == "current_status_freshness" for f in report.findings)


def test_audit_file_threads_git_head(tmp_path):
    # audit_file computes the live version itself (canonical_facts) and accepts an
    # explicit git_head; a deliberately-stale HEAD in github_repo must fail loud.
    hot = _hot(github_field="LATEST COMMIT 0000000 (stale)")
    p = tmp_path / "RAG_MASTER.json"
    p.write_text(json.dumps(hot), encoding="utf-8")
    report = drift_audit.audit_file(p, scan_root=False, git_head="e109794")
    assert not report.ok
    assert any(f.check == "current_status_freshness" for f in report.errors)


# ---------------------------------------------------------------------------
# labeled RELEASE sub-check (KA-CS-PROSE-DRIFT)
# ---------------------------------------------------------------------------

def test_release_stale_secondary_narrative_is_error():
    # The LEADING version token is fresh (v0.4.31) — sub-check 1 passes — but an
    # embedded labeled "RUNTIME RELEASE" / "runtime-v" claim is frozen at an old
    # release. That secondary-narrative drift is exactly what KA-CS-PROSE-DRIFT
    # caught escaping the leading-token-only guard; the release sub-check fires.
    hot = _hot(
        version_field="v0.4.31 — deployed; byte-identical to runtime-v0.4.27 worktree."
    )
    out = check_current_status_freshness(hot, version="0.4.31")
    assert len(out) == 1
    assert out[0].severity == ERROR
    assert "0.4.27" in out[0].detail and "0.4.31" in out[0].detail
    assert "KA-CS-PROSE-DRIFT" in out[0].detail


def test_release_match_is_clean():
    hot = _hot(github_field="RUNTIME RELEASE v0.4.31 @ abc1234 (tag runtime-v0.4.31).")
    assert check_current_status_freshness(hot, version="0.4.31") == []


def test_release_unlabeled_prior_is_ignored():
    # Historical releases written UNLABELED ("Prior: vX") — the project convention —
    # must NOT be matched; only labeled current-release tokens are governed.
    hot = _hot(
        github_field="RUNTIME RELEASE v0.4.31 (marked Latest). Prior: v0.4.27 @ 7b8bca1."
    )
    assert check_current_status_freshness(hot, version="0.4.31") == []


def test_release_spec_and_component_versions_untouched():
    # The spec version (3.2.6) and a sub-component version (1.5.0) carry no release
    # label, so they never trip the release sub-check even when != __version__.
    hot = _hot(
        version_field="v0.4.31 — spec 3.2.6; DRIFT_STORE 1.4.0->1.5.0; runtime-v0.4.31."
    )
    assert check_current_status_freshness(hot, version="0.4.31") == []


def test_release_all_labeled_occurrences_reported():
    # Two labeled tokens, both stale at different versions — both surface.
    hot = _hot(
        github_field="RUNTIME RELEASE v0.4.27 (tag runtime-v0.4.26)."
    )
    out = check_current_status_freshness(hot, version="0.4.31")
    assert len(out) == 1  # one finding per field, enumerating the stale tokens
    assert "0.4.26" in out[0].detail and "0.4.27" in out[0].detail


def test_release_skipped_when_canonical_none():
    hot = _hot(github_field="RUNTIME RELEASE v0.4.27.")
    assert check_current_status_freshness(hot, version=None) == []
