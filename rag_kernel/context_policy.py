"""Context-truncation policy for the RAG Runtime Kernel (M-009).

Kernel-enforced, deterministic context-window management. The LLM may
*propose* eviction candidates (relevance scores), but ORDERING, ATOMICITY,
and the source-of-truth guarantee are owned by this policy.

    LLM proposes. System decides. State persists.

Design goals:
- Token accounting per memory region (HOT fields, COLD partitions, WAL,
  conversation).
- Deterministic eviction order: a fixed region priority, refined only
  *within* the evictable tier by optional relevance scores. HOT is the
  source of truth and is structurally NEVER evictable.
- Threshold actions: CHECKPOINT (persist a safe point) -> EVICT-to-COLD
  (free evictable regions) -> HALT (cannot reduce without evicting HOT).
- The pure decision (`evaluate`) does no I/O. Enforcement lives in
  `api.KernelApp.enforce_context_policy`, which routes the decision
  through the guarded CHECKPOINTING transition and a WAL-logged event.

CS lens: this is a deterministic function over a token ledger — same
ledger + same policy + same scores always yields the same decision and
the same ordered plan. No hidden state, no float nondeterminism in
ordering (ties broken by a total order).

ML lens: `candidate_scores` is the relevance signal. It can only reorder
candidates that are already evictable; it can never make HOT evictable
nor change which thresholds fire.

@rag-kernel-manifest
{
  "module": "rag_kernel.context_policy",
  "capability": "context_truncation",
  "description": "Deterministic, kernel-enforced context-window truncation policy: per-region token accounting, pinned/evictable ordering (HOT never evicted), checkpoint/evict/halt threshold actions",
  "exports": ["MemoryRegion", "PolicyAction", "RegionAccount", "TokenLedger", "TruncationPolicy", "EvictionStep", "PolicyDecision", "evaluate", "default_policy"],
  "use_when": "Deciding whether to checkpoint, evict-to-COLD, or halt as token usage approaches the context budget",
  "never_bypass": true
}
Satisfies: M-009 (kernel-enforced context-truncation policy)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Optional, Tuple


# ---------------------------------------------------------------------------
# Regions and actions
# ---------------------------------------------------------------------------

class MemoryRegion(Enum):
    """The accountable memory regions that consume context budget.

    HOT is the single source of truth (RAG_MASTER.json). It is pinned and
    structurally never evictable — see ``_NEVER_EVICTABLE``.
    """

    HOT = "HOT"
    COLD = "COLD"
    WAL = "WAL"
    CONVERSATION = "CONVERSATION"


class PolicyAction(Enum):
    """The action the kernel must take given the current ledger.

    NONE       — under the checkpoint threshold; nothing to do.
    CHECKPOINT — persist a safe point (delta/full) but no eviction yet.
    EVICT      — checkpoint, then evict evictable regions per the plan.
    HALT       — cannot get below the hard ceiling without evicting the
                 source-of-truth HOT state; the session must transfer.
    """

    NONE = "NONE"
    CHECKPOINT = "CHECKPOINT"
    EVICT = "EVICT"
    HALT = "HALT"


# HOT is the source of truth. This is the non-negotiable invariant of the
# whole kernel: the policy may free anything EXCEPT HOT.
_NEVER_EVICTABLE = frozenset({MemoryRegion.HOT})

# Default deterministic eviction priority — cheapest-to-lose first.
# Conversation scratch is most disposable; COLD is archival and re-loadable;
# WAL is freed only via checkpoint+truncate so it is ordered last.
DEFAULT_REGION_ORDER: Tuple[MemoryRegion, ...] = (
    MemoryRegion.CONVERSATION,
    MemoryRegion.COLD,
    MemoryRegion.WAL,
)


# ---------------------------------------------------------------------------
# Token ledger
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RegionAccount:
    """Token accounting for one memory region.

    ``items`` optionally subdivides the region into named sub-units
    (e.g., COLD partitions: ``(("session_history", 1200), ...)``) so the
    eviction plan can name exactly what to free. If ``items`` is empty the
    region is evicted as a whole.
    """

    region: MemoryRegion
    tokens: int
    pinned: bool = False
    items: Tuple[Tuple[str, int], ...] = ()

    @property
    def evictable(self) -> bool:
        """A region is evictable iff it is not pinned and not source-of-truth."""
        return (not self.pinned) and (self.region not in _NEVER_EVICTABLE)


@dataclass(frozen=True)
class TokenLedger:
    """Immutable snapshot of per-region token consumption."""

    accounts: Tuple[RegionAccount, ...]

    @property
    def total(self) -> int:
        return sum(a.tokens for a in self.accounts)

    def by_region(self, region: MemoryRegion) -> Optional[RegionAccount]:
        for a in self.accounts:
            if a.region == region:
                return a
        return None

    def to_dict(self) -> dict:
        return {
            "total_tokens": self.total,
            "regions": {
                a.region.value: {
                    "tokens": a.tokens,
                    "pinned": a.pinned,
                    "evictable": a.evictable,
                    "items": list(a.items),
                }
                for a in self.accounts
            },
        }


# ---------------------------------------------------------------------------
# Policy configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TruncationPolicy:
    """Threshold configuration for the truncation policy.

    Three strictly-increasing token thresholds define the action bands:

        total < checkpoint_at                  -> NONE
        checkpoint_at <= total < evict_at      -> CHECKPOINT
        evict_at <= total < halt_at            -> EVICT (toward checkpoint_at)
        total >= halt_at                       -> EVICT if eviction can drop
                                                  below halt_at, else HALT

    ``region_order`` is the deterministic eviction priority.
    ``max_budget`` is informational (the context window size the thresholds
    were derived from) and is not used in the decision itself.
    """

    checkpoint_at: int
    evict_at: int
    halt_at: int
    region_order: Tuple[MemoryRegion, ...] = DEFAULT_REGION_ORDER
    max_budget: int = 0

    def __post_init__(self) -> None:
        if not (0 < self.checkpoint_at < self.evict_at < self.halt_at):
            raise ValueError(
                "thresholds must satisfy 0 < checkpoint_at < evict_at < "
                f"halt_at (got {self.checkpoint_at}, {self.evict_at}, "
                f"{self.halt_at})"
            )
        # HOT must never appear in the eviction order.
        bad = [r for r in self.region_order if r in _NEVER_EVICTABLE]
        if bad:
            raise ValueError(
                f"region_order must not contain source-of-truth regions: "
                f"{[r.value for r in bad]}"
            )
        if len(set(self.region_order)) != len(self.region_order):
            raise ValueError("region_order must not contain duplicates")


def default_policy(max_budget: int = 200_000) -> TruncationPolicy:
    """Construct a policy from a context-window budget.

    Defaults: checkpoint at 60%, evict at 75%, halt at 90% of budget.
    These mirror the project's "halt before compaction" discipline —
    act well before the hard ceiling.
    """
    return TruncationPolicy(
        checkpoint_at=int(max_budget * 0.60),
        evict_at=int(max_budget * 0.75),
        halt_at=int(max_budget * 0.90),
        max_budget=max_budget,
    )


# ---------------------------------------------------------------------------
# Decision output
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EvictionStep:
    """One ordered unit of eviction. ``target`` names a sub-item
    (e.g., a COLD partition) or is None to evict the whole region."""

    region: MemoryRegion
    target: Optional[str]
    tokens_freed: int

    def to_dict(self) -> dict:
        return {
            "region": self.region.value,
            "target": self.target,
            "tokens_freed": self.tokens_freed,
        }


@dataclass(frozen=True)
class PolicyDecision:
    """The deterministic decision: an action plus an ordered eviction plan."""

    action: PolicyAction
    reason: str
    total_before: int
    projected_after: int
    plan: Tuple[EvictionStep, ...] = ()

    def to_dict(self) -> dict:
        return {
            "action": self.action.value,
            "reason": self.reason,
            "total_before": self.total_before,
            "projected_after": self.projected_after,
            "plan": [s.to_dict() for s in self.plan],
        }


# ---------------------------------------------------------------------------
# Eviction planning (pure, deterministic)
# ---------------------------------------------------------------------------

def _order_items(
    items: Tuple[Tuple[str, int], ...],
    scores: Optional[Mapping[str, float]],
) -> Tuple[Tuple[str, int], ...]:
    """Order sub-items for eviction: least-relevant first.

    Determinism contract:
    - With no scores: evict largest first (free the most, fewest evictions),
      ties broken by name ascending.
    - With scores: evict lowest relevance first (missing score == 0.0, i.e.
      treated as least relevant). Ties broken by (-tokens, name) so the
      result is a total order regardless of float equality.

    The ML relevance signal only REORDERS candidates that are already
    evictable; it can never add HOT or change thresholds.
    """
    if scores is None:
        return tuple(sorted(items, key=lambda it: (-it[1], it[0])))
    return tuple(
        sorted(items, key=lambda it: (scores.get(it[0], 0.0), -it[1], it[0]))
    )


def _plan_eviction(
    ledger: TokenLedger,
    policy: TruncationPolicy,
    target: int,
    scores: Optional[Mapping[str, float]],
) -> Tuple[Tuple[EvictionStep, ...], int]:
    """Build the minimal ordered plan to bring ``total`` down to ``target``.

    Walks regions in policy order; within a region, evicts named sub-items
    (ordered by ``_order_items``) or the whole region. Stops as soon as the
    projection reaches ``target``. HOT (and any pinned region) is skipped.

    Returns (plan, projected_total_after).
    """
    projected = ledger.total
    plan: list[EvictionStep] = []

    for region in policy.region_order:
        if projected <= target:
            break
        acct = ledger.by_region(region)
        if acct is None or not acct.evictable or acct.tokens <= 0:
            continue

        if acct.items:
            for name, tok in _order_items(acct.items, scores):
                if projected <= target:
                    break
                if tok <= 0:
                    continue
                plan.append(EvictionStep(region, name, tok))
                projected -= tok
        else:
            plan.append(EvictionStep(region, None, acct.tokens))
            projected -= acct.tokens

    return tuple(plan), projected


# ---------------------------------------------------------------------------
# The decision function (pure, no I/O)
# ---------------------------------------------------------------------------

def evaluate(
    ledger: TokenLedger,
    policy: TruncationPolicy,
    candidate_scores: Optional[Mapping[str, float]] = None,
) -> PolicyDecision:
    """Decide the truncation action for a ledger under a policy.

    Pure and deterministic: identical inputs always yield an identical
    decision and an identical ordered plan.
    """
    total = ledger.total

    # Band 1: under the checkpoint threshold — nothing to do.
    if total < policy.checkpoint_at:
        return PolicyDecision(
            action=PolicyAction.NONE,
            reason=f"total {total} below checkpoint threshold {policy.checkpoint_at}",
            total_before=total,
            projected_after=total,
        )

    # Band 2: checkpoint band — persist a safe point, no eviction yet.
    if total < policy.evict_at:
        return PolicyDecision(
            action=PolicyAction.CHECKPOINT,
            reason=(
                f"total {total} in checkpoint band "
                f"[{policy.checkpoint_at}, {policy.evict_at}) — persist safe point"
            ),
            total_before=total,
            projected_after=total,
        )

    # Bands 3 & 4: plan eviction toward the checkpoint threshold.
    plan, projected = _plan_eviction(
        ledger, policy, target=policy.checkpoint_at, scores=candidate_scores
    )

    # Band 3: evict band — checkpoint + evict-to-COLD per plan.
    if total < policy.halt_at:
        return PolicyDecision(
            action=PolicyAction.EVICT,
            reason=(
                f"total {total} in evict band "
                f"[{policy.evict_at}, {policy.halt_at}) — evict to COLD"
            ),
            total_before=total,
            projected_after=projected,
            plan=plan,
        )

    # Band 4: at/over the hard ceiling.
    if projected < policy.halt_at:
        # Eviction of evictable regions rescues us below the ceiling.
        return PolicyDecision(
            action=PolicyAction.EVICT,
            reason=(
                f"total {total} at/over halt threshold {policy.halt_at}; "
                f"eviction reduces to {projected} (below ceiling)"
            ),
            total_before=total,
            projected_after=projected,
            plan=plan,
        )

    # Eviction cannot drop us below the ceiling without touching HOT.
    # The source-of-truth guarantee forbids that: HALT and transfer.
    return PolicyDecision(
        action=PolicyAction.HALT,
        reason=(
            f"total {total} at/over halt threshold {policy.halt_at} and "
            f"eviction of all evictable regions only reaches {projected}; "
            f"cannot reduce further without evicting source-of-truth HOT state — "
            f"checkpoint and transfer session"
        ),
        total_before=total,
        projected_after=projected,
        plan=plan,
    )
