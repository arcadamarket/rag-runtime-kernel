"""SPEC-PROMOTION-DRIFT — promote the boot-integrity spine (KA-20 + KA-21) into
the universal INIT spec (v3.2.8).

BOOT-GUARD-FIRST-ACTION (KA-20) and CLOSE-SEAL-ENFORCE (KA-21) shipped in runtime
v0.4.45 and were applied to THIS project's live RAG, but the *universal* spec
never carried them — so a fresh `init --spec` deploy did not inherit either guard
and had to re-author its boot boundary by hand (E-063, the spec-promotion half of
the bidirectional spec<->RAG governance drift). v3.2.8 seeds them:

  - §50 gains a BOOT-GUARD subsection (session-start is the FIRST action; never
    read the canonical RAG directly to load/report boot state; two-phase
    token-attested start; zero-read boot from v0.4.46).
  - §50 gains a CLOSE-SEAL-ENFORCE subsection (the carry-forward gate refuses to
    open over an unsealed predecessor; named recoveries; legacy RAGs untouched).
  - The session-end ritual gains the matching explicit seal step (5).
  - `session_start_protocol` / `session_end_protocol` rag-config strings encode
    both guards so a fresh deploy inherits them mechanically.

These tests dogfood the REAL v3.2.8 spec the same way test_ka8/test_ka15 do:
parse it with the production SpecParser and assert the seeded contract.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from rag_kernel.spec_parser import SpecParser

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_328 = REPO_ROOT / "INIT_UNIVERSAL_RUNTIME_KERNEL_v3.2.8.md"


def _parse():
    if not SPEC_328.exists():
        pytest.skip("v3.2.8 spec not present in repo root")
    return SpecParser().parse_file(SPEC_328)


def test_spec_version_is_328():
    """The v3.2.8 bump must carry the self-version through to the parsed RAG."""
    res = _parse()
    assert res.spec_version == "3.2.8"
    meta = res.merged["meta"]
    assert meta["policy_version"] == "3.2.8"
    assert meta["rag_files"]["init_prompt"] == "INIT_UNIVERSAL_RUNTIME_KERNEL_v3.2.8.md"


def test_session_start_protocol_seeds_boot_guard_first_action():
    """KA-20 must be inherited by a fresh deploy, not re-authored per project."""
    ssp = _parse().merged["operating_protocol"]["session_start_protocol"]
    assert isinstance(ssp, str)
    assert "BOOT-GUARD-FIRST-ACTION" in ssp
    assert "KA-20" in ssp
    # the load-bearing clauses
    assert "VERY FIRST action" in ssp
    assert "NEVER read the canonical RAG directly" in ssp
    assert "BOOT-STATE BRIEFING" in ssp
    # two-phase token attestation (KA-14) and the proof-of-order marker
    assert "--attest" in ssp
    assert "RULES_LOADED" in ssp
    assert "boot_guard" in ssp
    # zero-read boot
    assert "AUTO-SID-DERIVE" in ssp


def test_session_start_protocol_seeds_close_seal_enforce():
    """KA-21 belongs to the carry-forward gate and must name its recoveries."""
    ssp = _parse().merged["operating_protocol"]["session_start_protocol"]
    assert "CLOSE-SEAL-ENFORCE" in ssp
    assert "KA-21" in ssp
    assert "UNSEALED predecessor" in ssp
    assert "transfer_ready" in ssp
    assert "AUDIT_CANONICAL_REPORT_" in ssp
    # recovery is named, not guessed
    assert "session-resume" in ssp
    assert "session-end" in ssp
    # legacy deploys are not retro-broken, and the override stays sanctioned
    assert "legacy RAGs" in ssp
    assert "--force" in ssp


def test_session_start_protocol_preserves_the_v325_ritual():
    """Promotion is ADDITIVE: the GC-first carry-forward ritual must survive."""
    ssp = _parse().merged["operating_protocol"]["session_start_protocol"]
    assert "CARRY-FORWARD GATE" in ssp
    assert "GC-FIRST" in ssp
    assert "OPEN the session logger" in ssp


def test_session_end_protocol_seeds_the_seal_step():
    """The close must end in an explicit seal — the state KA-21 gates on."""
    sep = _parse().merged["operating_protocol"]["session_end_protocol"]
    assert isinstance(sep, str)
    assert "SEAL THE TRANSFER" in sep
    assert "AUDIT_CANONICAL_REPORT_" in sep
    assert "transfer_ready" in sep
    assert "session-resume" in sep
    assert "CLOSE-SEAL-ENFORCE" in sep
    # ordered ritual renumbered to five, prior steps preserved (v3.2.6 pass first)
    assert "CLAIM-RECONCILIATION PASS" in sep
    assert "CHECKPOINT" in sep
    assert "SESSION-CLOSE AUDIT" in sep
    assert "all five steps" in sep


def test_promotion_did_not_drop_prior_universal_rules():
    """A spec bump is append-only: the inherited rule set must not shrink."""
    op = _parse().merged["operating_protocol"]
    for key in (
        "token_economy",            # v3.2.7
        "session_start_protocol",   # v3.2.5
        "session_end_protocol",     # v3.2.5/v3.2.6
        "strict_obey",              # v3.2.4
        "web_access_protocol",      # v3.2.3
        "known_issues_registry",    # v3.2.1
    ):
        assert key in op, f"universal rule {key} lost in the v3.2.8 promotion"
