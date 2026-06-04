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
  "exports": ["validate_proposal", "validate_event", "validate_hot", "validate_cold"],
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
