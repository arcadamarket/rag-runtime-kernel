"""RULE19-PIN-REFRESH (F3) — the governance_runtime pin must not drift silently.

Rule 19 declares that governance runs on the deployment's OWN pinned runtime, named
in the rule as a version + release commit. Nothing bound that claim to reality, so
the pin sat at `v0.4.23/c140137` while the code had moved to `v0.4.45` — twenty-two
releases of silent divergence with a clean audit every session. This is the E-043 /
KA-CS-PROSE-DRIFT class one layer up: the rule that declares WHICH runtime enforces
the rules was itself unenforced.

`check_governance_pin_provenance` binds the declared pin to the live
`rag_kernel.__version__` authority.
"""
from __future__ import annotations

import rag_kernel
from rag_kernel.drift_audit import (
    ERROR,
    WARNING,
    audit_hot,
    check_governance_pin_provenance,
)

PINNED = (
    "Rule 19 (SELF-HOSTED RUNTIME). This project's GOVERNANCE runs on its OWN "
    "deployed, pinned v{v} runtime, NOT the dev worktree. DEPLOYED BACKBONE: "
    "RAG/rag_kernel/ -- a byte-identical copy of the runtime-v{v} / commit {sha} "
    "package (20 modules + __init__ + __main__ + generated_guards)."
)


def _hot(rule=None):
    hot = {"meta": {}, "operating_protocol": {}}
    if rule is not None:
        hot["operating_protocol"]["governance_runtime"] = rule
    return hot


# --- clean ------------------------------------------------------------------

def test_pin_matching_the_authority_is_clean():
    hot = _hot(PINNED.format(v="0.4.47", sha="abc1234"))
    assert check_governance_pin_provenance(hot, version="0.4.47") == []


def test_leading_v_on_the_authority_is_tolerated():
    """`0.4.47` and `v0.4.47` are the same claim."""
    hot = _hot(PINNED.format(v="0.4.47", sha="abc1234"))
    assert check_governance_pin_provenance(hot, version="v0.4.47") == []


def test_history_tokens_are_not_flagged():
    """The rule records where the pin CAME FROM; that is not drift."""
    rule = (PINNED.format(v="0.4.47", sha="abc1234")
            + " (pin refreshed S173->v0.4.46@8af6ed6, S112 MIGRATION-V0423)")
    assert check_governance_pin_provenance(_hot(rule), version="0.4.47") == []


# --- the drift this exists to catch ----------------------------------------

def test_stale_pin_is_an_error():
    """The exact F3 state: rule says v0.4.23, code says v0.4.45."""
    rule = PINNED.format(v="0.4.23", sha="c140137")
    findings = check_governance_pin_provenance(_hot(rule), version="0.4.45")
    assert findings, "a pin behind the live authority must fail loud"
    assert all(f.severity == ERROR for f in findings)
    assert all(f.check == "governance_pin_provenance" for f in findings)
    joined = " ".join(f.detail for f in findings)
    assert "0.4.23" in joined and "0.4.45" in joined


def test_half_refreshed_pin_is_an_error():
    """Refreshing the prose pin but not the backbone line is still drift."""
    rule = (
        "This project's GOVERNANCE runs on its OWN deployed, pinned v0.4.47 runtime. "
        "DEPLOYED BACKBONE: a byte-identical copy of the runtime-v0.4.46 / commit "
        "8af6ed6 package."
    )
    findings = check_governance_pin_provenance(_hot(rule), version="0.4.47")
    assert [f.severity for f in findings] == [ERROR]
    assert "0.4.46" in findings[0].detail


# --- unverifiable pins warn rather than pass silently ----------------------

def test_pin_without_release_provenance_warns():
    rule = ("GOVERNANCE runs on its OWN deployed, pinned v0.4.47 runtime; "
            "DEPLOYED BACKBONE: RAG/rag_kernel/.")
    findings = check_governance_pin_provenance(_hot(rule), version="0.4.47")
    assert [f.severity for f in findings] == [WARNING]
    assert "provenance" in findings[0].detail


def test_unparseable_pin_claim_warns():
    rule = "GOVERNANCE runs on its OWN pinned, deployed backbone runtime."
    findings = check_governance_pin_provenance(_hot(rule), version="0.4.47")
    assert [f.severity for f in findings] == [WARNING]
    assert "unverifiable" in findings[0].detail


# --- self-skips -------------------------------------------------------------

def test_no_governance_rule_self_skips():
    assert check_governance_pin_provenance(_hot(), version="0.4.47") == []


def test_unrelated_rule_text_self_skips():
    """A deployment that does not self-host declares no pin and is out of scope."""
    rule = "Governance is performed by the operator's own tooling."
    assert check_governance_pin_provenance(_hot(rule), version="0.4.47") == []


def test_dict_shaped_rule_is_searched():
    """`update-rule` accepts dict rules; the guard must not depend on the shape."""
    rule = {
        "doctrine": "GOVERNANCE runs on its OWN deployed, pinned v0.4.23 runtime.",
        "backbone": "a byte-identical copy of the runtime-v0.4.23 / commit c140137 package",
    }
    findings = check_governance_pin_provenance(_hot(rule), version="0.4.47")
    assert len(findings) == 2
    assert all(f.severity == ERROR for f in findings)


# --- wiring -----------------------------------------------------------------

def test_check_runs_inside_audit_hot():
    """The guard is always-on in the standard audit, not an opt-in helper."""
    hot = {
        "meta": {},
        "operating_protocol": {
            "governance_runtime": PINNED.format(v="0.4.23", sha="c140137"),
        },
        "tracked_items": [],
        "open_tasks": [],
        "deferred_items": [],
    }
    report = audit_hot(hot, version="0.4.45")
    assert any(f.check == "governance_pin_provenance" for f in report.errors)


def test_live_authority_is_the_default_comparand():
    """With no explicit version the guard reads rag_kernel.__version__ itself."""
    stale = "0.0.1"
    assert rag_kernel.__version__ != stale
    hot = _hot(PINNED.format(v=stale, sha="c140137"))
    findings = check_governance_pin_provenance(hot)
    assert findings and all(f.severity == ERROR for f in findings)
    assert rag_kernel.__version__ in findings[0].detail
