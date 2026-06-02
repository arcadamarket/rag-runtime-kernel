"""Tests for the M-009 context-truncation policy.

Two layers:
1. Pure policy unit tests (context_policy) — determinism, threshold bands,
   the HOT source-of-truth guarantee, eviction ordering, ML reorder-within-tier.
2. Enforcement integration tests (api.KernelApp.enforce_context_policy) —
   guarded checkpoint, real COLD partition eviction, WAL logging, HALT/transfer,
   and the proposal -> commit pipeline path for `truncate_context`.
"""

import json

import pytest

from rag_kernel.context_policy import (
    MemoryRegion,
    PolicyAction,
    RegionAccount,
    TokenLedger,
    TruncationPolicy,
    EvictionStep,
    PolicyDecision,
    evaluate,
    default_policy,
)
from rag_kernel.api import KernelApp
from rag_kernel.state_machine import State


# ===== Helpers =====

def make_policy():
    """Small, explicit thresholds for deterministic band testing."""
    return TruncationPolicy(checkpoint_at=100, evict_at=200, halt_at=300)


def ledger(hot=0, cold=0, wal=0, conv=0, cold_items=()):
    return TokenLedger(accounts=(
        RegionAccount(MemoryRegion.HOT, hot, pinned=True),
        RegionAccount(MemoryRegion.COLD, cold, items=tuple(cold_items)),
        RegionAccount(MemoryRegion.WAL, wal),
        RegionAccount(MemoryRegion.CONVERSATION, conv),
    ))


# ===== Policy configuration =====

class TestPolicyConfig:
    def test_default_policy_fractions(self):
        p = default_policy(1000)
        assert p.checkpoint_at == 600
        assert p.evict_at == 750
        assert p.halt_at == 900
        assert p.max_budget == 1000

    def test_thresholds_must_be_increasing(self):
        with pytest.raises(ValueError):
            TruncationPolicy(checkpoint_at=200, evict_at=100, halt_at=300)

    def test_thresholds_must_be_positive(self):
        with pytest.raises(ValueError):
            TruncationPolicy(checkpoint_at=0, evict_at=100, halt_at=200)

    def test_hot_cannot_be_in_region_order(self):
        with pytest.raises(ValueError):
            TruncationPolicy(
                checkpoint_at=100, evict_at=200, halt_at=300,
                region_order=(MemoryRegion.HOT, MemoryRegion.COLD),
            )

    def test_region_order_no_duplicates(self):
        with pytest.raises(ValueError):
            TruncationPolicy(
                checkpoint_at=100, evict_at=200, halt_at=300,
                region_order=(MemoryRegion.COLD, MemoryRegion.COLD),
            )


# ===== Ledger =====

class TestLedger:
    def test_total(self):
        assert ledger(hot=10, cold=20, wal=5, conv=3).total == 38

    def test_hot_not_evictable(self):
        acct = RegionAccount(MemoryRegion.HOT, 100, pinned=True)
        assert acct.evictable is False

    def test_cold_evictable(self):
        assert RegionAccount(MemoryRegion.COLD, 100).evictable is True

    def test_pinned_region_not_evictable(self):
        assert RegionAccount(MemoryRegion.COLD, 100, pinned=True).evictable is False

    def test_by_region(self):
        lg = ledger(cold=20)
        assert lg.by_region(MemoryRegion.COLD).tokens == 20
        assert lg.by_region(MemoryRegion.HOT).tokens == 0


# ===== Decision bands =====

class TestBands:
    def test_none_below_checkpoint(self):
        d = evaluate(ledger(conv=50), make_policy())
        assert d.action == PolicyAction.NONE
        assert d.projected_after == 50

    def test_checkpoint_band(self):
        d = evaluate(ledger(conv=150), make_policy())
        assert d.action == PolicyAction.CHECKPOINT
        assert d.plan == ()  # no eviction in checkpoint band

    def test_evict_band(self):
        # total 250 in [200,300): evict toward checkpoint_at=100
        d = evaluate(ledger(conv=250), make_policy())
        assert d.action == PolicyAction.EVICT
        assert d.projected_after <= 100

    def test_halt_when_eviction_insufficient(self):
        # 350 total, but it's ALL hot (pinned). Eviction frees nothing.
        d = evaluate(ledger(hot=350), make_policy())
        assert d.action == PolicyAction.HALT
        assert d.projected_after == 350  # nothing evictable
        assert d.plan == ()

    def test_evict_rescues_above_halt(self):
        # 350 total over halt(300), but conv=300 is evictable -> drops to 50
        d = evaluate(ledger(hot=50, conv=300), make_policy())
        assert d.action == PolicyAction.EVICT
        assert d.projected_after < 300

    def test_hot_never_in_plan(self):
        d = evaluate(ledger(hot=280, conv=60), make_policy())
        for step in d.plan:
            assert step.region != MemoryRegion.HOT


# ===== Eviction ordering (determinism) =====

class TestEvictionOrdering:
    def test_region_priority_conversation_first(self):
        # Both COLD and CONVERSATION evictable; conversation must go first.
        d = evaluate(ledger(hot=0, cold=200, conv=200), make_policy())
        assert d.action == PolicyAction.EVICT
        assert d.plan[0].region == MemoryRegion.CONVERSATION

    def test_cold_items_largest_first_without_scores(self):
        items = [("small", 30), ("big", 120), ("mid", 60)]
        d = evaluate(ledger(cold=210, cold_items=items), make_policy())
        # target = 100, so must free >=110: big(120) alone suffices.
        assert d.plan[0].target == "big"

    def test_deterministic_repeatable(self):
        items = [("a", 50), ("b", 50), ("c", 50)]
        lg = ledger(cold=150, cold_items=items)
        p = make_policy()
        d1 = evaluate(lg, p)
        d2 = evaluate(lg, p)
        assert d1.to_dict() == d2.to_dict()

    def test_scores_reorder_within_tier(self):
        # Equal-size partitions; relevance decides eviction order. Total 240
        # lands in the evict band [200,300) with COLD as the evictable region.
        items = [("keep", 120), ("drop", 120)]
        scores = {"keep": 0.9, "drop": 0.1}
        d = evaluate(ledger(cold=240, cold_items=items), make_policy(),
                     candidate_scores=scores)
        assert d.action == PolicyAction.EVICT
        # Least relevant ("drop") is evicted before the more relevant ("keep").
        assert d.plan[0].target == "drop"

    def test_scores_cannot_make_hot_evictable(self):
        # Even with a (meaningless) HOT score, HOT stays pinned.
        d = evaluate(ledger(hot=350), make_policy(),
                     candidate_scores={"HOT": 0.0})
        assert d.action == PolicyAction.HALT
        assert all(s.region != MemoryRegion.HOT for s in d.plan)


# ===== Serialization =====

class TestSerialization:
    def test_decision_to_dict(self):
        d = evaluate(ledger(conv=250), make_policy())
        out = d.to_dict()
        assert out["action"] == "EVICT"
        assert "plan" in out and isinstance(out["plan"], list)
        assert out["total_before"] == 250

    def test_eviction_step_to_dict(self):
        s = EvictionStep(MemoryRegion.COLD, "part", 42)
        assert s.to_dict() == {"region": "COLD", "target": "part", "tokens_freed": 42}

    def test_ledger_to_dict_marks_hot_pinned(self):
        out = ledger(hot=10, cold=20).to_dict()
        assert out["regions"]["HOT"]["pinned"] is True
        assert out["regions"]["HOT"]["evictable"] is False
        assert out["regions"]["COLD"]["evictable"] is True


# ===== Enforcement integration (KernelApp) =====

@pytest.fixture
def project_dir(tmp_path):
    d = tmp_path / "RAG"
    d.mkdir()
    (d / "RAG_MASTER.json").write_text(
        json.dumps({"meta": {"session_id": "S-CTX", "state_hash": ""},
                    "current_status": {"phase": "idle"}}),
        encoding="utf-8",
    )
    # COLD with two sizeable partitions so eviction has something to free.
    (d / "RAG_COLD.json").write_text(
        json.dumps({
            "meta": {"type": "RAG_COLD"},
            "history": {"sessions": ["x" * 4000]},
            "inventory": {"files": ["y" * 4000]},
        }),
        encoding="utf-8",
    )
    return d


@pytest.fixture
def booted(project_dir):
    app = KernelApp(project_dir, session_id="S-CTX")
    app.boot()
    return app


class TestEnforcementIntegration:
    def test_none_action_no_checkpoint(self, booted):
        res = booted.enforce_context_policy(
            conversation_tokens=10, policy=default_policy(1_000_000)
        )
        assert res["action"] == "NONE"
        assert res["executed"]["checkpoint"] is None

    def test_build_ledger_pins_hot(self, booted):
        lg = booted.build_token_ledger(conversation_tokens=5)
        d = lg.to_dict()
        assert d["regions"]["HOT"]["pinned"] is True
        assert d["regions"]["HOT"]["tokens"] > 0

    def test_evict_frees_loaded_cold_partition(self, booted):
        # Load both COLD partitions so they're accounted + evictable.
        booted.cold.get("history")
        booted.cold.get("inventory")
        assert set(booted.cold.loaded_partitions) == {"history", "inventory"}

        # Tiny budget so we land in the EVICT band on conversation+cold.
        pol = TruncationPolicy(checkpoint_at=100, evict_at=200, halt_at=5000)
        res = booted.enforce_context_policy(conversation_tokens=300, policy=pol)

        assert res["action"] == "EVICT"
        assert res["executed"]["checkpoint"]["checkpointed"] is True
        # At least one COLD partition was actually evicted from the cache.
        assert len(res["executed"]["evicted"]) >= 1
        for name in res["executed"]["evicted"]:
            assert not booted.cold.is_loaded(name)
        # State returned to READY after the guarded checkpoint.
        assert booted.state_machine.current == State.READY

    def test_halt_sets_transfer_required(self, booted):
        # Force a huge conversation but a halt threshold below it, and make
        # HOT dominate so eviction can't rescue: use a policy where even after
        # evicting conversation we stay above halt.
        # hot ~ small; give conversation just under halt and pin nothing else.
        # Simplance: set thresholds so total>halt and evictable can't reach it.
        # Here HOT tokens alone are below halt, so evicting conversation works;
        # to force HALT we shrink halt below HOT size is impossible (hot small).
        # Instead, drive HALT via the pure layer is covered above; here verify
        # the enforcement plumbing returns transfer_required when action==HALT
        # by using a policy whose halt_at is below HOT token size.
        hot_tokens = booted.build_token_ledger().by_region(MemoryRegion.HOT).tokens
        pol = TruncationPolicy(
            checkpoint_at=max(1, hot_tokens // 4),
            evict_at=max(2, hot_tokens // 2),
            halt_at=max(3, hot_tokens),  # total (>=hot) will be at/over halt
        )
        res = booted.enforce_context_policy(conversation_tokens=0, policy=pol)
        assert res["action"] == "HALT"
        assert res.get("transfer_required") is True
        # A safe point was still persisted before declaring transfer.
        assert res["executed"]["checkpoint"]["checkpointed"] is True

    def test_context_truncation_logs_wal_event(self, booted):
        pol = TruncationPolicy(checkpoint_at=100, evict_at=200, halt_at=5000)
        booted.enforce_context_policy(conversation_tokens=300, policy=pol)
        events = [e.event for e in booted.wal.replay()]
        assert "CONTEXT_TRUNCATION" in events

    def test_proposal_commit_pipeline_routes_truncation(self, booted):
        # truncate_context flows through propose -> commit and does NOT merge
        # its payload into HOT.
        prop = booted.propose({
            "action": "truncate_context",
            "payload": {"conversation_tokens": 300, "max_budget": 500},
        })
        assert prop["valid"] is True
        res = booted.commit(prop["proposal_id"])
        assert res["committed"] is True
        assert "truncation" in res
        # payload keys must not have leaked into HOT
        assert "conversation_tokens" not in booted._hot
        assert "max_budget" not in booted._hot
