"""Tests for the RAG Runtime Kernel validation schemas.

Coverage targets:
- Proposal: valid actions, invalid actions, custom actions, missing fields,
  wrong types, risk levels, reasoning
- Event: valid events, invalid types, missing fields, seq bounds
- HOT: valid structure, missing meta, missing session_id
- COLD: valid structure, wrong type, missing meta
"""

import pytest

from rag_kernel.schemas import (
    VALID_ACTIONS,
    VALID_EVENT_TYPES,
    VALID_RISK_LEVELS,
    validate_cold,
    validate_event,
    validate_hot,
    validate_proposal,
)


# ===== Proposal validation =====

class TestValidateProposal:
    def test_valid_minimal(self):
        valid, errors = validate_proposal({
            "action": "update_status",
            "payload": {"phase": "working"},
        })
        assert valid
        assert errors == []

    def test_valid_with_all_fields(self):
        valid, errors = validate_proposal({
            "action": "update_meta",
            "payload": {"key": "value"},
            "risk": "low",
            "reasoning": "Testing",
            "state_before": "READY",
            "state_after": "WORKING",
        })
        assert valid

    def test_valid_custom_action(self):
        valid, errors = validate_proposal({
            "action": "custom:my_action",
            "payload": {},
        })
        assert valid

    def test_all_standard_actions_valid(self):
        for action in VALID_ACTIONS:
            valid, errors = validate_proposal({
                "action": action,
                "payload": {},
            })
            assert valid, f"Action '{action}' should be valid"

    def test_missing_action(self):
        valid, errors = validate_proposal({"payload": {}})
        assert not valid
        assert any("action" in e for e in errors)

    def test_missing_payload(self):
        valid, errors = validate_proposal({"action": "update_status"})
        assert not valid
        assert any("payload" in e for e in errors)

    def test_missing_both(self):
        valid, errors = validate_proposal({})
        assert not valid
        assert len(errors) == 2

    def test_unknown_action(self):
        valid, errors = validate_proposal({
            "action": "nonexistent",
            "payload": {},
        })
        assert not valid
        assert any("Unknown action" in e for e in errors)

    def test_action_wrong_type(self):
        valid, errors = validate_proposal({
            "action": 123,
            "payload": {},
        })
        assert not valid

    def test_payload_wrong_type(self):
        valid, errors = validate_proposal({
            "action": "update_status",
            "payload": "not a dict",
        })
        assert not valid

    def test_not_a_dict(self):
        valid, errors = validate_proposal("string")
        assert not valid
        assert any("dict" in e for e in errors)

    def test_not_a_dict_list(self):
        valid, errors = validate_proposal([1, 2, 3])
        assert not valid

    def test_valid_risk_levels(self):
        for risk in VALID_RISK_LEVELS:
            valid, errors = validate_proposal({
                "action": "update_status",
                "payload": {},
                "risk": risk,
            })
            assert valid, f"Risk '{risk}' should be valid"

    def test_invalid_risk(self):
        valid, errors = validate_proposal({
            "action": "update_status",
            "payload": {},
            "risk": "extreme",
        })
        assert not valid
        assert any("risk" in e for e in errors)

    def test_risk_wrong_type(self):
        valid, errors = validate_proposal({
            "action": "update_status",
            "payload": {},
            "risk": 5,
        })
        assert not valid

    def test_reasoning_wrong_type(self):
        valid, errors = validate_proposal({
            "action": "update_status",
            "payload": {},
            "reasoning": 123,
        })
        assert not valid


# ===== Event validation =====

class TestValidateEvent:
    def test_valid_event(self):
        valid, errors = validate_event({
            "seq": 1,
            "ts": "2026-05-14T10:00:00Z",
            "event": "TRANSITION",
        })
        assert valid
        assert errors == []

    def test_all_event_types_valid(self):
        for event_type in VALID_EVENT_TYPES:
            valid, errors = validate_event({
                "seq": 1,
                "ts": "2026-05-14T10:00:00Z",
                "event": event_type,
            })
            assert valid, f"Event type '{event_type}' should be valid"

    def test_missing_seq(self):
        valid, errors = validate_event({
            "ts": "2026-05-14T10:00:00Z",
            "event": "TRANSITION",
        })
        assert not valid

    def test_missing_ts(self):
        valid, errors = validate_event({
            "seq": 1,
            "event": "TRANSITION",
        })
        assert not valid

    def test_missing_event(self):
        valid, errors = validate_event({
            "seq": 1,
            "ts": "2026-05-14T10:00:00Z",
        })
        assert not valid

    def test_seq_zero(self):
        valid, errors = validate_event({
            "seq": 0,
            "ts": "2026-05-14T10:00:00Z",
            "event": "TRANSITION",
        })
        assert not valid
        assert any("positive" in e for e in errors)

    def test_seq_negative(self):
        valid, errors = validate_event({
            "seq": -1,
            "ts": "2026-05-14T10:00:00Z",
            "event": "TRANSITION",
        })
        assert not valid

    def test_unknown_event_type(self):
        valid, errors = validate_event({
            "seq": 1,
            "ts": "2026-05-14T10:00:00Z",
            "event": "MADE_UP",
        })
        assert not valid

    def test_not_a_dict(self):
        valid, errors = validate_event("string")
        assert not valid

    def test_seq_wrong_type(self):
        valid, errors = validate_event({
            "seq": "not_int",
            "ts": "2026-05-14T10:00:00Z",
            "event": "TRANSITION",
        })
        assert not valid

    def test_event_with_extra_fields(self):
        valid, errors = validate_event({
            "seq": 1,
            "ts": "2026-05-14T10:00:00Z",
            "event": "PROPOSAL_COMMITTED",
            "proposal_id": "S9-P1",
            "session_id": "S9",
        })
        assert valid  # extra fields are fine


# ===== HOT validation =====

class TestValidateHot:
    def test_valid_hot(self):
        valid, errors = validate_hot({
            "meta": {"session_id": "S9"},
        })
        assert valid

    def test_valid_hot_full(self):
        valid, errors = validate_hot({
            "meta": {
                "session_id": "S9",
                "state_hash": "abc123",
                "last_checkpoint_seq": 5,
            },
            "current_status": {"phase": "idle"},
        })
        assert valid

    def test_missing_meta(self):
        valid, errors = validate_hot({"current_status": {}})
        assert not valid
        assert any("meta" in e for e in errors)

    def test_meta_not_dict(self):
        valid, errors = validate_hot({"meta": "string"})
        assert not valid

    def test_missing_session_id(self):
        valid, errors = validate_hot({"meta": {}})
        assert not valid
        assert any("session_id" in e for e in errors)

    def test_session_id_wrong_type(self):
        valid, errors = validate_hot({"meta": {"session_id": 123}})
        assert not valid

    def test_not_a_dict(self):
        valid, errors = validate_hot([])
        assert not valid


# ===== COLD validation =====

class TestValidateCold:
    def test_valid_cold(self):
        valid, errors = validate_cold({
            "meta": {"type": "RAG_COLD"},
        })
        assert valid

    def test_valid_cold_with_partitions(self):
        valid, errors = validate_cold({
            "meta": {"type": "RAG_COLD", "schema_version": "5.1"},
            "documents_inventory": {"files": []},
            "session_history": [],
        })
        assert valid

    def test_missing_meta(self):
        valid, errors = validate_cold({"documents_inventory": {}})
        assert not valid

    def test_meta_not_dict(self):
        valid, errors = validate_cold({"meta": "string"})
        assert not valid

    def test_wrong_type(self):
        valid, errors = validate_cold({
            "meta": {"type": "RAG_HOT"},
        })
        assert not valid
        assert any("RAG_COLD" in e for e in errors)

    def test_missing_type(self):
        valid, errors = validate_cold({"meta": {}})
        assert not valid

    def test_not_a_dict(self):
        valid, errors = validate_cold("string")
        assert not valid
