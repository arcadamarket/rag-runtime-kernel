"""AUDIT-SPEC-COVERAGE — canonical-vs-SPEC coverage (S180).

Every other invariant in drift_audit compares a RENDER against the CANONICAL
store. None compared the CANONICAL store against the SPEC that declares what it
must contain, and that blind spot let **eight spec-universal rules stay absent
from this kernel's own RAG across 178 clean audits** (S179 §0.3). This suite is
what stops the class reopening.

Covers:
  * a missing spec-universal key is an ERROR, one per key, naming the repair
  * a complete operating_protocol audits clean
  * an UNRESOLVABLE spec self-skips clean, and resolution never reaches outside
    the deployment being audited (both learned the hard way in S180)
  * spec-vs-meta version disagreement is a WARNING, and coverage is still
    computed
  * spec resolution: beside the RAG, beside the project root, and by bounded
    glob (this project keeps its spec in a git worktree, not next to the RAG)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rag_kernel import drift_audit
from rag_kernel.drift_audit import ERROR, WARNING, check_spec_completeness

SPEC_NAME = "INIT_UNIVERSAL_RUNTIME_KERNEL_v3.2.8.md"


@pytest.fixture
def hot():
    return {
        "meta": {"policy_version": "3.2.8", "rag_files": {"init_prompt": SPEC_NAME}},
        "operating_protocol": {"alpha": "a", "beta": "b", "project_only": "p"},
    }


@pytest.fixture
def spec(tmp_path):
    p = tmp_path / SPEC_NAME
    p.write_text("# spec\n", encoding="utf-8")
    return p


def _patch_keys(monkeypatch, keys, version="3.2.8"):
    import rag_kernel.transplant as t
    monkeypatch.setattr(t, "universal_keys_from_spec",
                        lambda _p: (set(keys), version))


def _severities(findings):
    return {f.severity for f in findings}


# --------------------------------------------------------------------------- #
# the invariant itself
# --------------------------------------------------------------------------- #
def test_missing_universal_key_is_an_error(hot, spec, monkeypatch):
    _patch_keys(monkeypatch, ["alpha", "beta", "gamma"])

    findings = check_spec_completeness(hot, spec.parent, spec.parent)

    assert [f.severity for f in findings] == [ERROR]
    assert "gamma" in findings[0].detail


def test_one_finding_per_missing_key_and_they_are_named(hot, spec, monkeypatch):
    """Counts are useless for repair; the eight missing rules had to be listed
    by name before anyone could author them."""
    _patch_keys(monkeypatch, ["alpha", "beta", "gamma", "delta", "epsilon"])

    findings = check_spec_completeness(hot, spec.parent, spec.parent)

    assert len(findings) == 3
    named = " ".join(f.detail for f in findings)
    for key in ("gamma", "delta", "epsilon"):
        assert key in named


def test_finding_names_the_repair_verb(hot, spec, monkeypatch):
    _patch_keys(monkeypatch, ["alpha", "beta", "gamma"])
    detail = check_spec_completeness(hot, spec.parent, spec.parent)[0].detail
    assert "add-rule" in detail


def test_complete_operating_protocol_audits_clean(hot, spec, monkeypatch):
    _patch_keys(monkeypatch, ["alpha", "beta"])
    assert check_spec_completeness(hot, spec.parent, spec.parent) == []


def test_project_specific_keys_are_never_flagged(hot, spec, monkeypatch):
    """22 project-specific rules must not travel and must not be audited
    against a spec that says nothing about them."""
    _patch_keys(monkeypatch, ["alpha", "beta"])
    findings = check_spec_completeness(hot, spec.parent, spec.parent)
    assert not any("project_only" in f.detail for f in findings)


# --------------------------------------------------------------------------- #
# unresolvable spec — a WARNING, never a silent skip
# --------------------------------------------------------------------------- #
def test_missing_spec_self_skips_clean(hot, tmp_path):
    """A deployment with no spec beside it is measured against none.

    The first S180 draft raised a WARNING here, reasoning that a silent skip is
    how this class hid for 178 sessions. That reasoning was wrong twice over:
    the class hid because there was NO CHECK, and the warning broke the governed
    FIX-9 contract that a fresh `init --auto-ready` audits --strict clean. The
    residual — `init` does not leave the spec discoverable from the RAG it
    writes — is tracked as SPEC-DISCOVERABILITY-AT-INIT rather than papered
    over with a finding the auditor cannot act on.
    """
    assert check_spec_completeness(hot, tmp_path, tmp_path) == []


def test_unparseable_spec_self_skips_clean(hot, spec, monkeypatch):
    import rag_kernel.transplant as t

    def boom(_p):
        raise ValueError("malformed section 32")

    monkeypatch.setattr(t, "universal_keys_from_spec", boom)
    assert check_spec_completeness(hot, spec.parent, spec.parent) == []


def test_a_foreign_spec_is_never_borrowed(hot, tmp_path, monkeypatch):
    """Resolution must not reach outside the deployment being audited.

    S180 briefly added the auditing kernel's own directory as a search base.
    That made every synthetic RAG in the suite get measured against the repo's
    spec and produced 25 false ERRORs on a toy fixture. A deployment is
    measured against ITS spec or against none.
    """
    _patch_keys(monkeypatch, ["alpha", "beta", "gamma"])
    assert check_spec_completeness(hot, tmp_path / "RAG", tmp_path) == []


def test_absent_operating_protocol_is_not_this_checks_problem(spec):
    assert check_spec_completeness({"meta": {}}, spec.parent, spec.parent) == []


# --------------------------------------------------------------------------- #
# spec resolution
# --------------------------------------------------------------------------- #
def test_resolves_spec_by_bounded_glob_when_not_beside_the_rag(hot, tmp_path,
                                                               monkeypatch):
    """This project keeps its spec in `GIT WORKTREES/<repo>/`, not in RAG/.

    A check that only works in one directory layout is a check that silently
    stops working — the exact failure mode being fixed here.
    """
    nested = tmp_path / "GIT WORKTREES" / "repo"
    nested.mkdir(parents=True)
    (nested / SPEC_NAME).write_text("# spec\n", encoding="utf-8")
    _patch_keys(monkeypatch, ["alpha", "beta", "gamma"])

    findings = check_spec_completeness(hot, tmp_path / "RAG", tmp_path)

    assert [f.severity for f in findings] == [ERROR]


def test_explicit_spec_path_wins(hot, tmp_path, monkeypatch):
    explicit = tmp_path / "elsewhere.md"
    explicit.write_text("# spec\n", encoding="utf-8")
    _patch_keys(monkeypatch, ["alpha", "beta", "zeta"])

    findings = check_spec_completeness(hot, tmp_path, tmp_path, spec_path=explicit)

    assert "zeta" in findings[0].detail


def test_version_disagreement_warns_but_still_computes_coverage(hot, spec,
                                                                monkeypatch):
    _patch_keys(monkeypatch, ["alpha", "beta", "gamma"], version="3.2.7")

    findings = check_spec_completeness(hot, spec.parent, spec.parent)

    assert _severities(findings) == {WARNING, ERROR}
    assert any("disagree" in f.detail for f in findings)
    assert any("gamma" in f.detail for f in findings)


# --------------------------------------------------------------------------- #
# wiring
# --------------------------------------------------------------------------- #
def test_check_is_wired_into_the_audit_runner():
    """A check nobody calls is a hope. Assert the runner calls this one."""
    import inspect
    src = inspect.getsource(drift_audit.audit_file)
    assert "check_spec_completeness" in src
