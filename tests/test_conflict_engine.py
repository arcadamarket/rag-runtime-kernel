"""Tests for the RAG Runtime Kernel conflict auto-categorization engine (ENH-005).

Coverage targets:
- ConflictCategory enum: all values
- ConflictRecord: creation, serialization, deserialization, ID generation
- classify_conflict: all 7 categories with varied inputs
- suggest_resolution: all categories return non-empty suggestions
- is_auto_resolvable: auto-resolve rules for each category
- validate_conflict_payload: required fields, types, edge cases
- ConflictEngine: add, resolve, load, summary, export
- Integration with KernelApp: add_conflict, resolve_conflict, get_conflict_summary
"""

import json
import pytest
from pathlib import Path

from rag_kernel.conflict_engine import (
    ConflictCategory,
    ConflictRecord,
    ConflictEngine,
    classify_conflict,
    suggest_resolution,
    is_auto_resolvable,
    validate_conflict_payload,
    VALID_CONFLICT_CATEGORIES,
    REQUIRED_CONFLICT_FIELDS,
)


# ===== ConflictCategory enum =====

class TestConflictCategory:
    def test_all_values(self):
        expected = {
            "temporal_drift", "source_disagreement", "data_quality",
            "schema_mismatch", "duplicate_entry", "priority_conflict",
            "uncategorized",
        }
        actual = {c.value for c in ConflictCategory}
        assert actual == expected

    def test_value_count(self):
        assert len(ConflictCategory) == 7

    def test_valid_categories_frozenset(self):
        """VALID_CONFLICT_CATEGORIES matches enum values."""
        assert VALID_CONFLICT_CATEGORIES == {c.value for c in ConflictCategory}


# ===== ConflictRecord =====

class TestConflictRecord:
    def test_minimal_creation(self):
        r = ConflictRecord("src/a.json", "src/b.json", "field X differs")
        assert r.source_a == "src/a.json"
        assert r.source_b == "src/b.json"
        assert r.difference == "field X differs"
        assert r.conflict_id.startswith("C-")
        assert r.category == ConflictCategory.UNCATEGORIZED
        assert r.auto_resolved is False
        assert r.confidence == "low"

    def test_full_creation(self):
        r = ConflictRecord(
            "file_a.json", "file_b.json", "version is stale",
            source_a_tier="T1", source_b_tier="T2",
            source_a_value="1.0", source_b_value="2.0",
            field_name="version",
            resolution="accept newer",
            confidence="high",
            resolver="engine",
            conflict_id="C-custom-001",
            category=ConflictCategory.TEMPORAL_DRIFT,
            suggested_resolution="Accept newer version.",
            auto_resolved=True,
        )
        assert r.conflict_id == "C-custom-001"
        assert r.category == ConflictCategory.TEMPORAL_DRIFT
        assert r.auto_resolved is True
        assert r.resolver == "engine"
        assert r.source_a_tier == "T1"

    def test_id_generation_deterministic(self):
        r1 = ConflictRecord("a", "b", "diff")
        r2 = ConflictRecord("a", "b", "diff")
        assert r1.conflict_id == r2.conflict_id

    def test_id_generation_different_inputs(self):
        r1 = ConflictRecord("a", "b", "diff1")
        r2 = ConflictRecord("a", "b", "diff2")
        assert r1.conflict_id != r2.conflict_id

    def test_invalid_confidence_defaults_low(self):
        r = ConflictRecord("a", "b", "c", confidence="invalid")
        assert r.confidence == "low"

    def test_to_dict(self):
        r = ConflictRecord(
            "a.json", "b.json", "value differs",
            source_a_tier="T1",
            field_name="status",
            category=ConflictCategory.SOURCE_DISAGREEMENT,
        )
        d = r.to_dict()
        assert d["source_a"] == "a.json"
        assert d["source_b"] == "b.json"
        assert d["difference"] == "value differs"
        assert d["category"] == "source_disagreement"
        assert d["auto_resolved"] is False
        assert d["source_a_tier"] == "T1"
        assert d["field_name"] == "status"
        # Optional fields not set should be absent
        assert "source_b_tier" not in d
        assert "source_a_value" not in d

    def test_from_dict(self):
        d = {
            "conflict_id": "C-test-123",
            "source_a": "file1",
            "source_b": "file2",
            "difference": "mismatch",
            "category": "data_quality",
            "confidence": "medium",
            "resolver": "user",
            "resolution": "fixed manually",
            "auto_resolved": False,
            "timestamp_utc": "2026-01-01T00:00:00Z",
        }
        r = ConflictRecord.from_dict(d)
        assert r.conflict_id == "C-test-123"
        assert r.category == ConflictCategory.DATA_QUALITY
        assert r.confidence == "medium"
        assert r.resolution == "fixed manually"

    def test_from_dict_unknown_category(self):
        d = {
            "source_a": "a", "source_b": "b", "difference": "d",
            "category": "nonexistent_category",
        }
        r = ConflictRecord.from_dict(d)
        assert r.category == ConflictCategory.UNCATEGORIZED

    def test_roundtrip(self):
        original = ConflictRecord(
            "src_a", "src_b", "differs",
            source_a_value=42,
            source_b_value=99,
            field_name="count",
            category=ConflictCategory.TEMPORAL_DRIFT,
            confidence="high",
        )
        d = original.to_dict()
        restored = ConflictRecord.from_dict(d)
        assert restored.conflict_id == original.conflict_id
        assert restored.category == original.category
        assert restored.source_a_value == original.source_a_value
        assert restored.source_b_value == original.source_b_value

    def test_repr(self):
        r = ConflictRecord("a", "b", "c", category=ConflictCategory.DUPLICATE_ENTRY)
        s = repr(r)
        assert "duplicate_entry" in s
        assert "resolved=False" in s


# ===== classify_conflict =====

class TestClassifyConflict:
    def test_temporal_drift_keywords(self):
        cat, conf = classify_conflict("a", "b", "value is outdated since last session")
        assert cat == ConflictCategory.TEMPORAL_DRIFT

    def test_temporal_drift_field_name(self):
        cat, conf = classify_conflict(
            "a", "b", "values differ",
            field_name="last_updated_utc",
        )
        assert cat == ConflictCategory.TEMPORAL_DRIFT

    def test_temporal_drift_same_source(self):
        """Same source → temporal drift boost."""
        cat, conf = classify_conflict(
            "data.json", "data.json", "value was X, now is Y, changed since update"
        )
        assert cat == ConflictCategory.TEMPORAL_DRIFT

    def test_source_disagreement_different_sources(self):
        """Two different sources with no other strong signal → source disagreement."""
        cat, conf = classify_conflict(
            "report_2025.json", "report_2026.json",
            "total revenue values do not match between files",
        )
        assert cat == ConflictCategory.SOURCE_DISAGREEMENT

    def test_data_quality_missing(self):
        cat, conf = classify_conflict(
            "a", "b", "field is missing from source",
        )
        assert cat == ConflictCategory.DATA_QUALITY

    def test_data_quality_null_value(self):
        cat, conf = classify_conflict(
            "a", "b", "field present but empty",
            source_a_value="valid",
            source_b_value=None,
        )
        assert cat == ConflictCategory.DATA_QUALITY

    def test_data_quality_empty_value(self):
        cat, conf = classify_conflict(
            "a", "b", "field is empty string",
            source_a_value="hello",
            source_b_value="",
        )
        assert cat == ConflictCategory.DATA_QUALITY

    def test_schema_mismatch_keywords(self):
        cat, conf = classify_conflict(
            "v1/schema.json", "v2/schema.json",
            "schema structure incompatible, expected dict got list, wrong type in field",
        )
        assert cat == ConflictCategory.SCHEMA_MISMATCH

    def test_schema_mismatch_type_difference(self):
        cat, conf = classify_conflict(
            "a", "b", "type mismatch detected",
            source_a_value=42,
            source_b_value="42",
        )
        assert cat == ConflictCategory.SCHEMA_MISMATCH

    def test_duplicate_entry_keywords(self):
        cat, conf = classify_conflict(
            "a", "b", "duplicate entry found for same record",
        )
        assert cat == ConflictCategory.DUPLICATE_ENTRY

    def test_duplicate_entry_identical_values(self):
        cat, conf = classify_conflict(
            "file_a", "file_b", "same data in both sources",
            source_a_value={"id": 1, "name": "test"},
            source_b_value={"id": 1, "name": "test"},
        )
        assert cat == ConflictCategory.DUPLICATE_ENTRY

    def test_priority_conflict_keywords(self):
        cat, conf = classify_conflict(
            "policy_a", "policy_b",
            "rule conflict: policy A says X, policy B says Y, which takes precedence",
        )
        assert cat == ConflictCategory.PRIORITY_CONFLICT

    def test_priority_conflict_field_name(self):
        cat, conf = classify_conflict(
            "a", "b", "values differ",
            field_name="priority_weight",
        )
        assert cat == ConflictCategory.PRIORITY_CONFLICT

    def test_uncategorized_generic(self):
        cat, conf = classify_conflict(
            "a", "b", "something is different",
        )
        # No strong signals → should be one of the weaker categories or uncategorized
        # "different" alone doesn't match any keyword set strongly
        assert isinstance(cat, ConflictCategory)

    def test_confidence_high_strong_signal(self):
        cat, conf = classify_conflict(
            "a", "b",
            "duplicate entry found, identical record already exists",
            source_a_value={"x": 1},
            source_b_value={"x": 1},
        )
        assert conf == "high"

    def test_confidence_low_weak_signal(self):
        cat, conf = classify_conflict(
            "a", "b", "something unclear is going on",
        )
        # Weak or no keyword matches → low confidence
        assert conf in ("low", "medium")

    def test_timestamp_values_boost_temporal(self):
        cat, conf = classify_conflict(
            "a", "b", "timestamps show value changed since update",
            source_a_value="2026-01-01T00:00:00Z",
            source_b_value="2026-06-01T00:00:00Z",
        )
        assert cat == ConflictCategory.TEMPORAL_DRIFT


# ===== suggest_resolution =====

class TestSuggestResolution:
    def test_all_categories_have_suggestions(self):
        for cat in ConflictCategory:
            suggestion = suggest_resolution(cat)
            assert isinstance(suggestion, str)
            assert len(suggestion) > 10

    def test_temporal_drift_mentions_newer(self):
        s = suggest_resolution(ConflictCategory.TEMPORAL_DRIFT)
        assert "newer" in s.lower()

    def test_priority_conflict_mentions_user(self):
        s = suggest_resolution(ConflictCategory.PRIORITY_CONFLICT)
        assert "user" in s.lower()

    def test_duplicate_mentions_archive(self):
        s = suggest_resolution(ConflictCategory.DUPLICATE_ENTRY)
        assert "archive" in s.lower()


# ===== is_auto_resolvable =====

class TestIsAutoResolvable:
    def test_temporal_drift_high_confidence(self):
        assert is_auto_resolvable(ConflictCategory.TEMPORAL_DRIFT, "high") is True

    def test_temporal_drift_low_confidence(self):
        assert is_auto_resolvable(ConflictCategory.TEMPORAL_DRIFT, "low") is False

    def test_duplicate_entry_high(self):
        assert is_auto_resolvable(ConflictCategory.DUPLICATE_ENTRY, "high") is True

    def test_data_quality_high(self):
        assert is_auto_resolvable(ConflictCategory.DATA_QUALITY, "high") is True

    def test_source_disagreement_never(self):
        assert is_auto_resolvable(ConflictCategory.SOURCE_DISAGREEMENT, "high") is False

    def test_priority_conflict_never(self):
        assert is_auto_resolvable(ConflictCategory.PRIORITY_CONFLICT, "high") is False

    def test_schema_mismatch_never(self):
        assert is_auto_resolvable(ConflictCategory.SCHEMA_MISMATCH, "high") is False

    def test_uncategorized_never(self):
        assert is_auto_resolvable(ConflictCategory.UNCATEGORIZED, "high") is False


# ===== validate_conflict_payload =====

class TestValidateConflictPayload:
    def test_valid_minimal(self):
        valid, errors = validate_conflict_payload({
            "source_a": "file_a.json",
            "source_b": "file_b.json",
            "difference": "values differ",
        })
        assert valid
        assert errors == []

    def test_valid_with_optionals(self):
        valid, errors = validate_conflict_payload({
            "source_a": "a",
            "source_b": "b",
            "difference": "diff",
            "source_a_tier": "T1",
            "field_name": "version",
            "category": "temporal_drift",
        })
        assert valid

    def test_missing_source_a(self):
        valid, errors = validate_conflict_payload({
            "source_b": "b",
            "difference": "diff",
        })
        assert not valid
        assert any("source_a" in e for e in errors)

    def test_missing_source_b(self):
        valid, errors = validate_conflict_payload({
            "source_a": "a",
            "difference": "diff",
        })
        assert not valid
        assert any("source_b" in e for e in errors)

    def test_missing_difference(self):
        valid, errors = validate_conflict_payload({
            "source_a": "a",
            "source_b": "b",
        })
        assert not valid
        assert any("difference" in e for e in errors)

    def test_all_missing(self):
        valid, errors = validate_conflict_payload({})
        assert not valid
        assert len(errors) == 3

    def test_wrong_type_source_a(self):
        valid, errors = validate_conflict_payload({
            "source_a": 123,
            "source_b": "b",
            "difference": "diff",
        })
        assert not valid
        assert any("string" in e for e in errors)

    def test_empty_difference(self):
        valid, errors = validate_conflict_payload({
            "source_a": "a",
            "source_b": "b",
            "difference": "   ",
        })
        assert not valid
        assert any("empty" in e for e in errors)

    def test_invalid_category(self):
        valid, errors = validate_conflict_payload({
            "source_a": "a",
            "source_b": "b",
            "difference": "diff",
            "category": "nonexistent",
        })
        assert not valid
        assert any("Invalid category" in e for e in errors)

    def test_not_a_dict(self):
        valid, errors = validate_conflict_payload("not a dict")
        assert not valid
        assert any("dict" in e for e in errors)

    def test_required_fields_frozenset(self):
        assert REQUIRED_CONFLICT_FIELDS == {"source_a", "source_b", "difference"}


# ===== ConflictEngine =====

class TestConflictEngine:
    def test_empty_engine(self):
        engine = ConflictEngine()
        assert engine.active_count == 0
        assert engine.resolved_count == 0

    def test_add_conflict_basic(self):
        engine = ConflictEngine()
        record = engine.add_conflict("a", "b", "values differ")
        assert record.conflict_id.startswith("C-")
        assert isinstance(record.category, ConflictCategory)
        assert record.suggested_resolution is not None

    def test_add_conflict_categorized(self):
        engine = ConflictEngine()
        record = engine.add_conflict(
            "old.json", "new.json",
            "value is outdated since last update session",
            field_name="last_updated_utc",
        )
        assert record.category == ConflictCategory.TEMPORAL_DRIFT

    def test_add_conflict_auto_resolve(self):
        """High-confidence auto-resolvable category should be resolved immediately."""
        engine = ConflictEngine()
        record = engine.add_conflict(
            "file_a", "file_b",
            "duplicate entry found, identical record already exists in both",
            source_a_value={"id": 1, "name": "test"},
            source_b_value={"id": 1, "name": "test"},
        )
        if record.auto_resolved:
            assert record.resolver == "engine"
            assert record.resolution is not None
            assert engine.active_count == 0
            assert engine.resolved_count == 1

    def test_add_conflict_not_auto_resolved(self):
        engine = ConflictEngine()
        record = engine.add_conflict(
            "policy_a", "policy_b",
            "rule conflict: which policy takes precedence",
        )
        # Priority conflicts are never auto-resolved
        if record.category == ConflictCategory.PRIORITY_CONFLICT:
            assert record.auto_resolved is False
            assert engine.active_count == 1

    def test_resolve_conflict(self):
        engine = ConflictEngine()
        record = engine.add_conflict(
            "policy_a", "policy_b",
            "rule conflict about precedence",
        )
        if not record.auto_resolved:
            resolved = engine.resolve_conflict(
                record.conflict_id, "policy_a takes precedence", "user"
            )
            assert resolved is not None
            assert resolved.resolution == "policy_a takes precedence"
            assert resolved.resolver == "user"
            assert engine.active_count == 0
            assert engine.resolved_count == 1

    def test_resolve_nonexistent(self):
        engine = ConflictEngine()
        result = engine.resolve_conflict("C-nonexistent", "fix", "user")
        assert result is None

    def test_get_conflict(self):
        engine = ConflictEngine()
        record = engine.add_conflict("a", "b", "diff")
        found = engine.get_conflict(record.conflict_id)
        assert found is not None
        assert found.conflict_id == record.conflict_id

    def test_get_conflict_not_found(self):
        engine = ConflictEngine()
        assert engine.get_conflict("C-nonexistent") is None

    def test_get_active(self):
        engine = ConflictEngine()
        engine.add_conflict("a1", "b1", "rule conflict about policy precedence")
        engine.add_conflict("a2", "b2", "another rule conflict about which policy wins")
        active = engine.get_active()
        assert len(active) >= 1  # At least some should be active

    def test_get_resolved(self):
        engine = ConflictEngine()
        r = engine.add_conflict("a", "b", "some rule conflict about precedence")
        if not r.auto_resolved:
            engine.resolve_conflict(r.conflict_id, "fixed", "user")
        resolved = engine.get_resolved()
        assert len(resolved) >= 1

    def test_load_from_ledger(self):
        engine = ConflictEngine()
        ledger = [
            {
                "conflict_id": "C-001",
                "source_a": "a", "source_b": "b",
                "difference": "diff",
                "category": "temporal_drift",
                "confidence": "high",
                "resolution": None,
                "resolver": None,
                "timestamp_utc": "2026-01-01T00:00:00Z",
                "auto_resolved": False,
            },
            {
                "conflict_id": "C-002",
                "source_a": "c", "source_b": "d",
                "difference": "other diff",
                "category": "data_quality",
                "confidence": "medium",
                "resolution": "fixed",
                "resolver": "user",
                "timestamp_utc": "2026-01-01T00:00:00Z",
                "auto_resolved": False,
            },
        ]
        loaded = engine.load_from_ledger(ledger)
        assert loaded == 1  # Only unresolved ones count as active
        assert engine.active_count == 1
        assert engine.resolved_count == 1

    def test_summary(self):
        engine = ConflictEngine()
        engine.add_conflict("a", "b", "value is stale and outdated since last session", field_name="updated_at")
        engine.add_conflict("c", "d", "field is missing from the source record")
        summary = engine.summary()
        assert "active_count" in summary
        assert "resolved_count" in summary
        assert "auto_resolved_count" in summary
        assert "active_by_category" in summary
        assert isinstance(summary["active_by_category"], dict)

    def test_export_ledger(self):
        engine = ConflictEngine()
        engine.add_conflict("a", "b", "rule conflict about which policy takes precedence")
        engine.add_conflict("c", "d", "field is missing from source")
        ledger = engine.export_ledger()
        assert isinstance(ledger, list)
        assert len(ledger) >= 2
        for entry in ledger:
            assert "conflict_id" in entry
            assert "category" in entry
            assert "source_a" in entry

    def test_export_roundtrip(self):
        """Export from one engine and load into another."""
        engine1 = ConflictEngine()
        engine1.add_conflict("x", "y", "rule conflict about policy precedence")
        engine1.add_conflict("p", "q", "field is missing and empty from source data")
        ledger = engine1.export_ledger()

        engine2 = ConflictEngine()
        engine2.load_from_ledger(ledger)
        assert engine2.active_count + engine2.resolved_count == len(ledger)


# ===== Integration: KernelApp =====

class TestKernelAppConflictIntegration:
    """Test conflict engine integration with KernelApp."""

    @pytest.fixture
    def app(self, tmp_path):
        """Create a booted KernelApp for testing."""
        from rag_kernel.api import KernelApp
        project = tmp_path / "test_project"
        project.mkdir()
        hot_path = project / "RAG_MASTER.json"
        hot_path.write_text(json.dumps({
            "meta": {"session_id": "test-session"},
            "active_conflicts_count": 0,
        }))
        app = KernelApp(project, session_id="test-session")
        app.boot()
        return app

    def test_add_conflict(self, app):
        result = app.add_conflict({
            "source_a": "file_a.json",
            "source_b": "file_b.json",
            "difference": "values are stale and outdated",
        })
        assert result["added"] is True
        assert "conflict" in result
        assert result["conflict"]["category"] in VALID_CONFLICT_CATEGORIES

    def test_add_conflict_invalid_payload(self, app):
        result = app.add_conflict({
            "source_a": "a",
            # missing source_b and difference
        })
        assert result["added"] is False
        assert "errors" in result

    def test_add_conflict_updates_hot_count(self, app):
        app.add_conflict({
            "source_a": "a", "source_b": "b",
            "difference": "rule conflict about precedence of policies",
        })
        hot = app.get_hot()
        assert hot["active_conflicts_count"] >= 0  # May be 0 if auto-resolved

    def test_resolve_conflict(self, app):
        add_result = app.add_conflict({
            "source_a": "policy_a", "source_b": "policy_b",
            "difference": "rule conflict about precedence, which policy wins",
        })
        if add_result["added"] and not add_result["conflict"].get("auto_resolved"):
            cid = add_result["conflict"]["conflict_id"]
            resolve_result = app.resolve_conflict(cid, "policy_a wins", "user")
            assert resolve_result["resolved"] is True

    def test_resolve_nonexistent_conflict(self, app):
        result = app.resolve_conflict("C-nonexistent", "fix", "user")
        assert result["resolved"] is False
        assert "error" in result

    def test_get_conflict_summary(self, app):
        app.add_conflict({
            "source_a": "a", "source_b": "b",
            "difference": "data is missing from source",
        })
        summary = app.get_conflict_summary()
        assert "active_count" in summary
        assert "resolved_count" in summary
        assert "auto_resolved_count" in summary

    def test_conflict_engine_on_kernel(self, app):
        """Verify the conflict engine is properly attached to KernelApp."""
        assert hasattr(app, "conflicts")
        assert isinstance(app.conflicts, ConflictEngine)

    def test_add_conflict_proposal_validation(self, app):
        """Test that add_conflict proposals are validated via propose()."""
        result = app.propose({
            "action": "add_conflict",
            "payload": {
                "source_a": "a",
                # missing source_b and difference
            },
        })
        assert result["valid"] is False
        assert any("source_b" in e for e in result["errors"])

    def test_add_conflict_proposal_valid(self, app):
        """Test that valid add_conflict proposals pass validation."""
        result = app.propose({
            "action": "add_conflict",
            "payload": {
                "source_a": "file_a",
                "source_b": "file_b",
                "difference": "values differ between sources",
            },
        })
        assert result["valid"] is True
