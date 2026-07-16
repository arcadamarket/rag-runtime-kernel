"""Validation schemas for the RAG Runtime Kernel.

Provides pure-data validation for:
- Proposals (LLM -> kernel mutation requests)
- Events (WAL entries)
- HOT structure (RAG_MASTER.json)
- COLD structure (RAG_COLD.json)

All validation is stdlib-only (no jsonschema, pydantic, etc.).
Validation functions return (valid: bool, errors: list[str]).

Design doc reference: v3.2_ARCHITECTURE_DESIGN.md §10
Satisfies: M-023-P8 (proposal contract)

@rag-kernel-manifest
{
  "module": "rag_kernel.schemas",
  "capability": "validation",
  "description": "Pure-data validation for proposals, events, HOT/COLD structures",
  "exports": ["validate_proposal", "validate_event", "validate_hot", "validate_cold", "validate_next_session_directive", "normalize_directive_text", "directive_matches", "audit_plan_against_directive"],
  "use_when": "Before committing any proposal or writing any RAG structure",
  "never_bypass": true
}
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

ValidationResult = tuple[bool, list[str]]


def _ok() -> ValidationResult:
    return True, []


def _fail(errors: list[str]) -> ValidationResult:
    return False, errors


# ---------------------------------------------------------------------------
# Proposal validation
# ---------------------------------------------------------------------------

VALID_ACTIONS = frozenset({
    "update_status",
    "add_session",
    "update_meta",
    "add_conflict",
    "resolve_conflict",
    "update_inventory",
    "update_pov_mode",
    "truncate_context",
    "custom",
})

VALID_RISK_LEVELS = frozenset({"low", "medium", "high"})

# ---------------------------------------------------------------------------
# POV mode validation
# ---------------------------------------------------------------------------

VALID_POV_MODES = frozenset({"strict", "advisory", "silent", "disabled"})

# Operations that force auto-escalation to strict mode
AUTO_ESCALATE_OPERATIONS = frozenset({
    "state_machine_change",
    "persistence_change",
    "concurrency_change",
    "formal_verification",
    "schema_change",
    "security_decision",
})


def validate_pov_mode(mode: Any) -> ValidationResult:
    """Validate a POV mode value.

    Valid modes: strict, advisory, silent, disabled.
    """
    if not isinstance(mode, str):
        return _fail([f"pov_mode must be a string, got {type(mode).__name__}"])
    if mode not in VALID_POV_MODES:
        return _fail([f"pov_mode must be one of {sorted(VALID_POV_MODES)}, got '{mode}'"])
    return _ok()


def should_auto_escalate(operation_type: str) -> bool:
    """Check if an operation type requires auto-escalation to strict POV mode.

    Returns True if the operation is high-risk and requires full dual-POV analysis
    regardless of the configured pov_mandate.mode.
    """
    return operation_type in AUTO_ESCALATE_OPERATIONS


def validate_proposal(proposal: Any) -> ValidationResult:
    """Validate a mutation proposal.

    Required fields:
    - action: str (must be in VALID_ACTIONS or prefixed with 'custom:')
    - payload: dict

    Optional fields:
    - risk: str (low/medium/high, default: low)
    - reasoning: str
    - state_before: str
    - state_after: str
    """
    errors: list[str] = []

    if not isinstance(proposal, dict):
        return _fail([f"Proposal must be a dict, got {type(proposal).__name__}"])

    # Required: action
    action = proposal.get("action")
    if action is None:
        errors.append("Missing required field: 'action'")
    elif not isinstance(action, str):
        errors.append(f"'action' must be a string, got {type(action).__name__}")
    elif action not in VALID_ACTIONS and not action.startswith("custom:"):
        errors.append(
            f"Unknown action '{action}'. "
            f"Valid: {sorted(VALID_ACTIONS)} or 'custom:*'"
        )

    # Required: payload
    payload = proposal.get("payload")
    if payload is None:
        errors.append("Missing required field: 'payload'")
    elif not isinstance(payload, dict):
        errors.append(f"'payload' must be a dict, got {type(payload).__name__}")

    # Optional: risk
    risk = proposal.get("risk")
    if risk is not None:
        if not isinstance(risk, str):
            errors.append(f"'risk' must be a string, got {type(risk).__name__}")
        elif risk not in VALID_RISK_LEVELS:
            errors.append(f"'risk' must be one of {sorted(VALID_RISK_LEVELS)}, got '{risk}'")

    # Optional: reasoning
    reasoning = proposal.get("reasoning")
    if reasoning is not None and not isinstance(reasoning, str):
        errors.append(f"'reasoning' must be a string, got {type(reasoning).__name__}")

    return (len(errors) == 0, errors)


# ---------------------------------------------------------------------------
# Event validation
# ---------------------------------------------------------------------------

VALID_EVENT_TYPES = frozenset({
    "TRANSITION",
    "INVALID_TRANSITION",
    "GUARD_FAILURE",
    "STATE_MACHINE_CREATED",
    "BOOT_COMPLETE",
    "BOOT_RECOVERY",
    "PROPOSAL_CREATED",
    "PROPOSAL_COMMITTED",
    "PROPOSAL_REJECTED",
    "CHECKPOINT",
    "SESSION_CLOSED",
    "RECOVERY_BAK_RESTORED",
    "CONTEXT_TRUNCATION",
    "GRAPH_NODE_EXECUTED",
    "GRAPH_ROLLBACK",
})


def validate_event(event: Any) -> ValidationResult:
    """Validate a WAL event dict.

    Required fields:
    - seq: int (positive)
    - ts: str (ISO timestamp)
    - event: str (must be in VALID_EVENT_TYPES)
    """
    errors: list[str] = []

    if not isinstance(event, dict):
        return _fail([f"Event must be a dict, got {type(event).__name__}"])

    # seq
    seq = event.get("seq")
    if seq is None:
        errors.append("Missing required field: 'seq'")
    elif not isinstance(seq, int):
        errors.append(f"'seq' must be an int, got {type(seq).__name__}")
    elif seq < 1:
        errors.append(f"'seq' must be positive, got {seq}")

    # ts
    ts = event.get("ts")
    if ts is None:
        errors.append("Missing required field: 'ts'")
    elif not isinstance(ts, str):
        errors.append(f"'ts' must be a string, got {type(ts).__name__}")

    # event type
    event_type = event.get("event")
    if event_type is None:
        errors.append("Missing required field: 'event'")
    elif not isinstance(event_type, str):
        errors.append(f"'event' must be a string, got {type(event_type).__name__}")
    elif event_type not in VALID_EVENT_TYPES:
        errors.append(f"Unknown event type '{event_type}'")

    return (len(errors) == 0, errors)


# ---------------------------------------------------------------------------
# HOT structure validation
# ---------------------------------------------------------------------------

REQUIRED_HOT_META_KEYS = frozenset({
    "session_id",
})

OPTIONAL_HOT_META_KEYS = frozenset({
    "state_hash",
    "inventory_hash",
    "last_checkpoint_seq",
    "schema_version",
    "root_project",
    "root_rag",
})


def validate_hot(hot: Any) -> ValidationResult:
    """Validate a HOT (RAG_MASTER.json) structure.

    Required:
    - Top-level must be a dict
    - Must have 'meta' key (dict)
    - meta must have 'session_id' (str)
    """
    errors: list[str] = []

    if not isinstance(hot, dict):
        return _fail([f"HOT must be a dict, got {type(hot).__name__}"])

    meta = hot.get("meta")
    if meta is None:
        errors.append("Missing required field: 'meta'")
    elif not isinstance(meta, dict):
        errors.append(f"'meta' must be a dict, got {type(meta).__name__}")
    else:
        for key in REQUIRED_HOT_META_KEYS:
            if key not in meta:
                errors.append(f"meta missing required field: '{key}'")
            elif not isinstance(meta[key], str):
                errors.append(
                    f"meta.{key} must be a string, got {type(meta[key]).__name__}"
                )

    return (len(errors) == 0, errors)


# ---------------------------------------------------------------------------
# KA-INTENT-FIDELITY — next_session_directive (decision-of-record) contract
# ---------------------------------------------------------------------------
#
# Root cause this closes (E-055 / the S146-class failure): a session close
# STATED a next-session handoff/directive but never persisted it as a discrete,
# gate-checkable field — the directive lived only in the ephemeral close report
# (``--handoff`` -> report section 7) or as prose folded into an unrelated rule,
# so a later "directive banked" claim had no structured artifact backing it.
#
# The fix is a DATA-SHAPE fix, not a fuzzy-match fix (LLM proposes, System
# decides, State persists): the directive is persisted verbatim into a structured
# ``next_session_directive`` record, and the session-end gate refuses to seal
# unless the STATED handoff normalized-matches the STORED directive. Matching is
# deterministic, stdlib-only, zero-token — normalization collapses insignificant
# whitespace and case ONLY, never meaning; no threshold, no embedding, no model.
# The optional ``decision_ids`` bind the directive to tracked_items by ID, which
# the inc2 plan-vs-settled audit resolves without any prose matching.

REQUIRED_DIRECTIVE_KEYS = frozenset({
    "session",       # id of the CLOSING session that authored the directive
    "for_session",   # id of the NEXT session the directive governs
    "directive",     # the settled handoff text, stored verbatim
})


def normalize_directive_text(text: Any) -> str:
    """Deterministic normalization for directive matching.

    Collapses runs of whitespace to a single space, strips ends, and casefolds.
    This normalizes ONLY presentation-insignificant differences (spacing, case)
    — never meaning — so a match stays exact in substance while tolerating a
    reflowed or re-cased restatement. Non-str input normalizes to "" (a non-str
    directive is a validation error surfaced elsewhere, and "" never matches a
    real directive, so the gate fails loud rather than passing on junk).
    """
    if not isinstance(text, str):
        return ""
    return " ".join(text.split()).casefold()


def directive_matches(stated: Any, stored: Any) -> bool:
    """True iff two directive texts are equal after normalization.

    Empty/blank normalized text never matches — a stated or stored directive that
    reduces to "" cannot satisfy the gate (fail-loud on absence, not silent pass).
    """
    ns = normalize_directive_text(stated)
    if not ns:
        return False
    return ns == normalize_directive_text(stored)


def validate_next_session_directive(nsd: Any) -> ValidationResult:
    """Validate a ``next_session_directive`` (decision-of-record) structure.

    Required:
    - Top-level must be a dict
    - ``session``, ``for_session``, ``directive`` present and non-empty str
    Optional:
    - ``decision_ids``: list of tracked_item id strings (inc2 plan binding)
    - ``authored_utc``: ISO-8601 timestamp str
    """
    errors: list[str] = []

    if not isinstance(nsd, dict):
        return _fail(
            [f"next_session_directive must be a dict, got {type(nsd).__name__}"]
        )

    for key in REQUIRED_DIRECTIVE_KEYS:
        if key not in nsd:
            errors.append(f"next_session_directive missing required field: '{key}'")
        elif not isinstance(nsd[key], str):
            errors.append(
                f"next_session_directive.{key} must be a string, "
                f"got {type(nsd[key]).__name__}"
            )
        elif not nsd[key].strip():
            errors.append(f"next_session_directive.{key} must not be empty")

    if "decision_ids" in nsd:
        dids = nsd["decision_ids"]
        if not isinstance(dids, list) or not all(isinstance(x, str) for x in dids):
            errors.append(
                "next_session_directive.decision_ids must be a list of strings"
            )

    if "authored_utc" in nsd and not isinstance(nsd["authored_utc"], str):
        errors.append("next_session_directive.authored_utc must be a string")

    return (len(errors) == 0, errors)


def audit_plan_against_directive(
    plan: Any,
    cited_ids: Any,
    nsd: Any,
    tracked_item_ids: Any,
) -> ValidationResult:
    """KA-INTENT-FIDELITY inc2 — verify a session PLAN honors the settled directive.

    The session-START counterpart to inc1's session-END seal gate. inc1 guarantees
    the prior session's directive was PERSISTED verbatim as a structured
    ``next_session_directive`` (decision-of-record). inc2 guarantees the NEW
    session's stated plan is FAITHFUL to that record — closing the other half of
    E-055 / the S146 drift, where a session anchored on a lossy handoff line and
    recited a stale blueprint instead of the settled decision-of-record.

    Deterministic, stdlib-only, zero-token — no semantics, no embeddings, no
    threshold (determinism > flexibility for a fail-loud gate). Three checks:

    1. DIRECTIVE VALIDITY — ``nsd`` must be a well-formed directive (delegates to
       :func:`validate_next_session_directive`). A missing / degraded record cannot
       be audited against, so the gate fails loud rather than passing on absence.
    2. ID-BINDING — the plan's ``cited_ids`` bind to the directive by ID:
       (a) when the directive PINS ``decision_ids``, the cited set must equal that
       pinned set exactly — no missing (the plan skipped a settled decision), no
       extra (the plan smuggled in an unsanctioned one); (b) every cited id AND
       every pinned id must RESOLVE to a real ``tracked_items`` entry
       (``tracked_item_ids``). An id that resolves to nothing fails loud.
    3. NORMALIZED-EXACT RESTATEMENT — ``plan`` must :func:`directive_matches` the
       stored ``directive`` text (whitespace/case-insensitive only, never meaning).

    ``cited_ids`` and ``tracked_item_ids`` are iterables of id strings. Returns
    ``(ok, errors)``; errors are order-preserving and de-duplicated.
    """
    ok_d, d_errors = validate_next_session_directive(nsd)
    if not ok_d:
        # Cannot audit a plan against an unusable record — surface WHY, fail loud.
        return (
            False,
            [f"next_session_directive not auditable: {e}" for e in d_errors]
            or ["next_session_directive not auditable"],
        )

    errors: list[str] = []

    # Normalize the plan's cited ids (fail loud on a non-list rather than coerce).
    if isinstance(cited_ids, (list, tuple)):
        cited = [c for c in cited_ids if isinstance(c, str) and c.strip()]
    else:
        errors.append("cited_ids must be a list of tracked_item id strings")
        cited = []

    known = (
        set(tracked_item_ids)
        if isinstance(tracked_item_ids, (list, tuple, set, frozenset))
        else set()
    )

    pinned = nsd.get("decision_ids")
    has_pins = (
        isinstance(pinned, list)
        and bool(pinned)
        and all(isinstance(x, str) for x in pinned)
    )

    # (2a) ID-binding: cited set must equal the directive-pinned set exactly.
    if has_pins:
        pinned_set = set(pinned)
        cited_set = set(cited)
        for missing in sorted(pinned_set - cited_set):
            errors.append(
                f"plan omits directive-pinned decision id: {missing}"
            )
        for extra in sorted(cited_set - pinned_set):
            errors.append(
                f"plan cites a decision id the directive did not sanction: {extra}"
            )

    # (2b) Resolution: every cited id AND every pinned id must resolve to a
    #      tracked_item (the SOURCE decisions must actually exist).
    for cid in cited:
        if cid not in known:
            errors.append(
                f"cited decision id does not resolve to a tracked_item: {cid}"
            )
    if has_pins:
        for pid in pinned:
            if pid not in known:
                errors.append(
                    f"directive-pinned decision id does not resolve to a "
                    f"tracked_item: {pid}"
                )

    # (3) Normalized-exact restatement of the directive text.
    if not directive_matches(plan, nsd.get("directive")):
        errors.append(
            "plan restatement does not normalized-match the stored directive text"
        )

    # Order-preserving de-dup (a pinned id absent from both cited and known can
    # surface the same id twice).
    seen: set[str] = set()
    uniq: list[str] = []
    for e in errors:
        if e not in seen:
            seen.add(e)
            uniq.append(e)
    return (len(uniq) == 0, uniq)


# ---------------------------------------------------------------------------
# COLD structure validation
# ---------------------------------------------------------------------------

REQUIRED_COLD_META_KEYS = frozenset({
    "type",
})


def validate_cold(cold: Any) -> ValidationResult:
    """Validate a COLD (RAG_COLD.json) structure.

    Required:
    - Top-level must be a dict
    - Must have 'meta' key (dict)
    - meta must have 'type' == 'RAG_COLD'
    """
    errors: list[str] = []

    if not isinstance(cold, dict):
        return _fail([f"COLD must be a dict, got {type(cold).__name__}"])

    meta = cold.get("meta")
    if meta is None:
        errors.append("Missing required field: 'meta'")
    elif not isinstance(meta, dict):
        errors.append(f"'meta' must be a dict, got {type(meta).__name__}")
    else:
        if meta.get("type") != "RAG_COLD":
            errors.append(
                f"meta.type must be 'RAG_COLD', got '{meta.get('type')}'"
            )

    return (len(errors) == 0, errors)
