"""KA-8 — bake the GC-first carry-forward gate + session-end ritual into the
universal INIT spec (KA-10 GOVERNANCE-DETERMINISM arc, TierB).

KA-6 shipped the *runtime* commands (`session-start` / `session-end`). But the
spec never told a deploy to RUN them: the session-start/session-end steps lived
scattered across §17/§19/§20/§45 and a deploying agent had to hand-assemble the
ritual — which is exactly how the first external deploy skipped its checkpoint
and froze its governance lineage. KA-8 adds §50 + two `rag-config` blocks so a
fresh `init --spec` SEEDS `session_start_protocol` and `session_end_protocol`
into every RAG's `operating_protocol` deterministically (no per-project
re-authoring).

These tests dogfood the REAL v3.2.5 spec the same way test_fix2/test_fix3 do:
parse it with the production SpecParser and assert the seeded contract.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from rag_kernel.spec_parser import SpecParser

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_325 = REPO_ROOT / "INIT_UNIVERSAL_RUNTIME_KERNEL_v3.2.5.md"


def _parse():
    if not SPEC_325.exists():
        pytest.skip("v3.2.5 spec not present in repo root")
    return SpecParser().parse_file(SPEC_325)


def test_spec_version_is_325():
    """The §50 bump must carry the self-version through to the parsed RAG."""
    res = _parse()
    assert res.spec_version == "3.2.5"
    meta = res.merged["meta"]
    assert meta["policy_version"] == "3.2.5"
    assert meta["rag_files"]["init_prompt"] == "INIT_UNIVERSAL_RUNTIME_KERNEL_v3.2.5.md"


def test_both_ritual_rules_seeded_into_operating_protocol():
    """The headline KA-8 contract: a fresh init seeds BOTH ritual rules."""
    op = _parse().merged["operating_protocol"]
    assert "session_start_protocol" in op
    assert "session_end_protocol" in op
    assert isinstance(op["session_start_protocol"], str)
    assert isinstance(op["session_end_protocol"], str)


def test_session_start_protocol_encodes_gc_first_carry_forward_order():
    """session-start = carry-forward gate -> gc dry-run -> open logger (in order)."""
    s = _parse().merged["operating_protocol"]["session_start_protocol"]
    # carry-forward gate is FIRST and fail-loud into RECOVERY
    assert "CARRY-FORWARD GATE" in s
    assert "RECOVERY" in s
    # GC-first dry-run, scoped to root_project
    assert "GC-FIRST" in s
    assert "DRY-RUN" in s
    assert "root_project" in s
    # governed one-command form + autonomous fallback
    assert "session-start" in s
    assert "AUTONOMOUS" in s
    # ordering: gate (1) precedes gc (2) precedes logger (3). Anchor on the
    # numbered step labels — "GC-FIRST" also appears in the rule's title phrase.
    assert s.index("(1) CARRY-FORWARD GATE") < s.index("(2) GC-FIRST") < s.index("(3) OPEN the session logger")


def test_session_end_protocol_encodes_checkpoint_close_audit_order():
    """session-end = checkpoint -> close (KA-4 gate) -> audit, fail-loud."""
    s = _parse().merged["operating_protocol"]["session_end_protocol"]
    assert "CHECKPOINT" in s
    assert "CLOSE the logger" in s
    assert "KA-4" in s            # the checkpoint-to-close gate
    assert "AUDIT" in s
    assert "session-end" in s
    # ordering: checkpoint precedes close precedes audit
    assert s.index("CHECKPOINT") < s.index("CLOSE the logger") < s.index("SESSION-CLOSE AUDIT")


def test_spec_parses_clean_no_errors():
    """v3.2.5 must be born clean: zero parse errors, no surviving version token."""
    res = _parse()
    assert res.errors == [], [repr(e) for e in res.errors]
    # validate_rag has no structural complaints
    assert SpecParser.validate_rag(res.merged) == []
    # self-version coherence holds across HOT + COLD
    assert SpecParser.verify_coherence(
        res.merged, res.cold_template, res.spec_version
    ) == []


def test_pre_existing_rules_still_seeded():
    """No regression: §45 garbage_collector and §49 strict_obey still seed."""
    op = _parse().merged["operating_protocol"]
    assert "garbage_collector" in op
    assert "strict_obey" in op
