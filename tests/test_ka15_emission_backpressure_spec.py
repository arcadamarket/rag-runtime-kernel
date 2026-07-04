"""KA-15 — bake the context-emission backpressure + malformed-emission catalog
into the universal INIT spec (v3.2.7).

The operational core of KA-15 (the `token_economy` rule) was applied to this
project's live RAG in S108, but the *universal* spec never carried it — so a
fresh `init --spec` deploy did not inherit the discipline. v3.2.7 seeds it:

  - §15 Token Economy gains an "Emission backpressure" subsection and the
    `token_economy` rag-config string is expanded to a three-in-one
    (loading + bounded tool-output + malformed-emission strike).
  - §21 circuit breaker gains a malformed-emission strike clause.
  - §41 known-issues registry gains a `malformed_emission` entry (human table +
    machine rag-config, kept in sync).

These tests dogfood the REAL v3.2.7 spec the same way test_fix2/test_ka8 do:
parse it with the production SpecParser and assert the seeded contract.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from rag_kernel.spec_parser import SpecParser

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_327 = REPO_ROOT / "INIT_UNIVERSAL_RUNTIME_KERNEL_v3.2.7.md"


def _parse():
    if not SPEC_327.exists():
        pytest.skip("v3.2.7 spec not present in repo root")
    return SpecParser().parse_file(SPEC_327)


def test_spec_version_is_327():
    """The v3.2.7 bump must carry the self-version through to the parsed RAG."""
    res = _parse()
    assert res.spec_version == "3.2.7"
    meta = res.merged["meta"]
    assert meta["policy_version"] == "3.2.7"
    assert meta["rag_files"]["init_prompt"] == "INIT_UNIVERSAL_RUNTIME_KERNEL_v3.2.7.md"


def test_token_economy_is_three_in_one_backpressure():
    """token_economy must carry all three lobes: loading + bounded output + strike."""
    te = _parse().merged["operating_protocol"]["token_economy"]
    assert isinstance(te, str)
    # (1) loading discipline retained (not lost in the expansion)
    assert "COLD sections" in te
    assert "HOT" in te
    # (2) bounded tool-output
    assert "BOUNDED TOOL-OUTPUT" in te
    assert "bounded slice" in te
    # (3) malformed-emission is a circuit-breaker strike
    assert "MALFORMED-EMISSION" in te
    assert "STRIKE" in te.upper()


def test_malformed_emission_seeded_into_known_issues_registry():
    """The §41 registry (machine block) must gain the malformed_emission key."""
    kir = _parse().merged["operating_protocol"]["known_issues_registry"]
    assert isinstance(kir, dict)
    assert "malformed_emission" in kir
    entry = kir["malformed_emission"]
    assert "circuit-breaker strike" in entry
    # pre-existing universal entries are preserved (append-only registry)
    assert "python314_pip" in kir
    assert "sandbox_mount_truncation" in kir
