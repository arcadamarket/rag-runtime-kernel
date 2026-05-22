"""
Tests for rag_kernel.spec_parser — v3.3 deterministic spec parser.

Covers: deep_merge, SpecParser (parse_string, parse_file, _extract_version,
_merge_blocks, validate_rag, write_rag, write_cold, report), ParsedBlock,
ParseError, ParseResult, VOID_RAG, error handling (invalid JSON, non-dict,
unclosed fences).

Run: pytest tests/test_spec_parser.py -v
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from copy import deepcopy
from pathlib import Path

import pytest

# Ensure rag_kernel is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag_kernel.spec_parser import (
    VOID_RAG,
    ParsedBlock,
    ParseError,
    ParseResult,
    SpecParser,
    deep_merge,
)


# ── Fixtures ──────────────────────────────────────────────────


@pytest.fixture
def parser():
    return SpecParser()


@pytest.fixture
def minimal_spec():
    """Minimal valid spec with one rag-config block."""
    return '''\
# INIT_UNIVERSAL_RUNTIME_KERNEL v3.1.8

## §0 — Preamble

Some introductory text.

```rag-config
{
  "meta": {
    "schema_version": "5.3",
    "rag_type": "HOT",
    "root_project": "/test",
    "root_deliverables": "/test",
    "root_rag": "/test/RAG",
    "rag_files": {
      "hot": "RAG_MASTER.json",
      "cold": "RAG_COLD.json",
      "backup": "RAG_MASTER.json.bak",
      "snapshot_log": "RUNTIME_SNAPSHOT.log",
      "init_prompt": "INIT_v3.1.8.md"
    }
  },
  "execution_mode": "autonomous",
  "state_machine_status": "BOOTING",
  "policy_flags": {
    "atomic_writes_required": true,
    "hash_validation_required": true,
    "load_cold_on_demand_only": true,
    "session_close_audit_required": true,
    "proposal_validation_commit_required": true
  },
  "operating_protocol": {},
  "pov_mandate": {"count": 2, "mode": "strict"},
  "project_context": {"brief": "test", "principals": {}, "domain": "", "end_goal": ""},
  "priority_actions": [],
  "open_tasks": []
}
```

## §1 — State Machine

More text.
'''


@pytest.fixture
def multi_block_spec():
    """Spec with multiple rag-config blocks across sections."""
    return '''\
# INIT v3.1.8

## §0 — Preamble

```rag-config
{
  "meta": {"schema_version": "5.3", "rag_type": "HOT", "root_project": "", "root_deliverables": "", "root_rag": "", "rag_files": {"hot": "RAG_MASTER.json", "cold": "RAG_COLD.json", "backup": "RAG_MASTER.json.bak", "snapshot_log": "RUNTIME_SNAPSHOT.log", "init_prompt": ""}},
  "execution_mode": "autonomous",
  "state_machine_status": "BOOTING",
  "policy_flags": {"atomic_writes_required": true, "hash_validation_required": true, "load_cold_on_demand_only": true, "session_close_audit_required": true, "proposal_validation_commit_required": true},
  "operating_protocol": {},
  "pov_mandate": {"count": 0, "mode": "strict"},
  "project_context": {"brief": "", "principals": {}, "domain": "", "end_goal": ""},
  "priority_actions": [],
  "open_tasks": []
}
```

## §3 — Operating Protocol

```rag-config
{
  "operating_protocol": {
    "tool_hierarchy": "Filesystem MCP > wsl-exec",
    "circuit_breaker": "2-strike rule"
  }
}
```

## §5 — POV Mandate

```rag-config
{
  "pov_mandate": {"count": 2, "mode": "strict"},
  "pov_roles": ["AI/ML Engineer", "Systems Architect"]
}
```
'''


@pytest.fixture
def template_spec():
    """Spec with a template block."""
    return '''\
# INIT v3.1.8

## §0 — Preamble

```rag-config:template
{
  "meta": {"schema_version": "5.3", "rag_type": "HOT", "root_project": "", "root_deliverables": "", "root_rag": "", "rag_files": {}},
  "execution_mode": "autonomous",
  "state_machine_status": "BOOTING",
  "policy_flags": {},
  "operating_protocol": {},
  "custom_base_key": "from_template"
}
```

## §1 — Config Overlay

```rag-config
{
  "meta": {"project_name": "Test Project"},
  "operating_protocol": {"rule_one": "value_one"}
}
```
'''


# ── deep_merge tests ──────────────────────────────────────────


class TestDeepMerge:
    def test_flat_merge(self):
        base = {"a": 1, "b": 2}
        overlay = {"b": 3, "c": 4}
        result = deep_merge(base, overlay)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_nested_dict_merge(self):
        base = {"meta": {"version": "1.0", "name": "old"}}
        overlay = {"meta": {"name": "new", "extra": True}}
        result = deep_merge(base, overlay)
        assert result == {"meta": {"version": "1.0", "name": "new", "extra": True}}

    def test_list_replacement(self):
        """Lists are replaced, not appended."""
        base = {"items": [1, 2, 3]}
        overlay = {"items": [4, 5]}
        result = deep_merge(base, overlay)
        assert result == {"items": [4, 5]}

    def test_scalar_overwrite(self):
        base = {"count": 10}
        overlay = {"count": 20}
        result = deep_merge(base, overlay)
        assert result["count"] == 20

    def test_deep_nested_merge(self):
        base = {"a": {"b": {"c": 1, "d": 2}}}
        overlay = {"a": {"b": {"d": 3, "e": 4}}}
        result = deep_merge(base, overlay)
        assert result == {"a": {"b": {"c": 1, "d": 3, "e": 4}}}

    def test_does_not_mutate_inputs(self):
        base = {"a": {"b": 1}}
        overlay = {"a": {"c": 2}}
        base_copy = deepcopy(base)
        overlay_copy = deepcopy(overlay)
        deep_merge(base, overlay)
        assert base == base_copy
        assert overlay == overlay_copy

    def test_empty_overlay(self):
        base = {"a": 1}
        result = deep_merge(base, {})
        assert result == {"a": 1}

    def test_empty_base(self):
        overlay = {"a": 1}
        result = deep_merge({}, overlay)
        assert result == {"a": 1}

    def test_dict_over_scalar(self):
        """Overlay dict replaces base scalar (not recursive merge)."""
        base = {"a": "string"}
        overlay = {"a": {"nested": True}}
        result = deep_merge(base, overlay)
        assert result == {"a": {"nested": True}}

    def test_scalar_over_dict(self):
        """Overlay scalar replaces base dict."""
        base = {"a": {"nested": True}}
        overlay = {"a": "string"}
        result = deep_merge(base, overlay)
        assert result == {"a": "string"}


# ── VOID_RAG tests ────────────────────────────────────────────


class TestVoidRAG:
    def test_has_required_top_level_keys(self):
        required = [
            "meta", "execution_mode", "state_machine_status",
            "policy_flags", "operating_protocol", "pov_mandate",
            "project_context", "priority_actions", "open_tasks",
        ]
        for key in required:
            assert key in VOID_RAG, f"Missing key: {key}"

    def test_meta_has_required_fields(self):
        meta_required = [
            "schema_version", "rag_type", "root_project",
            "root_deliverables", "root_rag", "rag_files",
        ]
        for key in meta_required:
            assert key in VOID_RAG["meta"], f"Missing meta key: {key}"

    def test_policy_flags_complete(self):
        pf_required = [
            "atomic_writes_required", "hash_validation_required",
            "load_cold_on_demand_only", "session_close_audit_required",
            "proposal_validation_commit_required",
        ]
        for key in pf_required:
            assert key in VOID_RAG["policy_flags"], f"Missing flag: {key}"

    def test_void_rag_validates_clean(self):
        """VOID_RAG itself should pass validation."""
        errors = SpecParser.validate_rag(VOID_RAG)
        assert errors == [], f"VOID_RAG validation errors: {errors}"

    def test_void_rag_state_is_booting(self):
        assert VOID_RAG["state_machine_status"] == "BOOTING"


# ── ParsedBlock / ParseError / ParseResult tests ─────────────


class TestDataStructures:
    def test_parsed_block_repr(self):
        b = ParsedBlock("config", "3a", "Title", 10, 20, "{}", {})
        assert "§3a" in repr(b)
        assert "config" in repr(b)

    def test_parse_error_repr(self):
        e = ParseError("5", 42, "bad json")
        assert "§5" in repr(e)
        assert "42" in repr(e)
        assert "bad json" in repr(e)

    def test_parse_result_init(self):
        r = ParseResult()
        assert r.blocks == []
        assert r.errors == []
        assert r.template is None
        assert r.cold_template is None
        assert r.merged == {}
        assert r.source_file == ""
        assert r.spec_version == ""
        assert r.sections_found == []


# ── SpecParser.parse_string tests ─────────────────────────────


class TestParseString:
    def test_minimal_spec(self, parser, minimal_spec):
        result = parser.parse_string(minimal_spec)
        assert len(result.blocks) == 1
        assert result.blocks[0].block_type == "config"
        assert result.blocks[0].section_id == "0"
        assert result.errors == []
        assert result.spec_version == "3.1.8"

    def test_multi_block_spec(self, parser, multi_block_spec):
        result = parser.parse_string(multi_block_spec)
        assert len(result.blocks) == 3
        assert result.blocks[0].section_id == "0"
        assert result.blocks[1].section_id == "3"
        assert result.blocks[2].section_id == "5"

    def test_section_tracking(self, parser, multi_block_spec):
        result = parser.parse_string(multi_block_spec)
        assert "§0" in result.sections_found
        assert "§3" in result.sections_found
        assert "§5" in result.sections_found

    def test_deep_merge_ordering(self, parser, multi_block_spec):
        result = parser.parse_string(multi_block_spec)
        rag = result.merged
        # §3 config block should have been merged
        assert rag["operating_protocol"]["tool_hierarchy"] == "Filesystem MCP > wsl-exec"
        assert rag["operating_protocol"]["circuit_breaker"] == "2-strike rule"
        # §5 config block
        assert rag["pov_mandate"]["count"] == 2
        assert rag["pov_roles"] == ["AI/ML Engineer", "Systems Architect"]

    def test_template_block(self, parser, template_spec):
        result = parser.parse_string(template_spec)
        assert result.template is not None
        assert "custom_base_key" in result.template
        # Config overlay should be merged on top of template
        assert result.merged["meta"]["project_name"] == "Test Project"
        assert result.merged.get("custom_base_key") == "from_template"

    def test_cold_template_block(self, parser):
        spec = '''\
# INIT v3.1.8

```rag-config:cold-template
{
  "meta": {"rag_type": "COLD"},
  "cold_sections": {}
}
```
'''
        result = parser.parse_string(spec)
        assert result.cold_template is not None
        assert result.cold_template["meta"]["rag_type"] == "COLD"

    def test_empty_input(self, parser):
        result = parser.parse_string("")
        assert result.blocks == []
        assert result.errors == []
        # Should produce a VOID_RAG-based merged result
        assert result.merged["state_machine_status"] == "BOOTING"

    def test_no_rag_config_blocks(self, parser):
        spec = "# Just a regular markdown file\n\nNo config blocks here.\n"
        result = parser.parse_string(spec)
        assert result.blocks == []
        assert result.merged["state_machine_status"] == "BOOTING"

    def test_metadata_stamping(self, parser, minimal_spec):
        result = parser.parse_string(minimal_spec)
        assert result.merged["meta"]["created_utc"] != ""
        assert result.merged["meta"]["last_updated_utc"] != ""

    def test_version_in_merged_meta(self, parser, minimal_spec):
        result = parser.parse_string(minimal_spec)
        assert result.merged["meta"]["policy_version"] == "3.1.8"

    def test_preamble_block(self, parser):
        """Block before any section header gets section_id='preamble'."""
        spec = '''\
# Title v1.0.0

```rag-config
{"meta": {"schema_version": "5.3", "rag_type": "HOT", "root_project": "", "root_deliverables": "", "root_rag": "", "rag_files": {}}, "execution_mode": "autonomous", "state_machine_status": "BOOTING", "policy_flags": {"atomic_writes_required": true, "hash_validation_required": true, "load_cold_on_demand_only": true, "session_close_audit_required": true, "proposal_validation_commit_required": true}, "operating_protocol": {}, "pov_mandate": {"count": 0, "mode": "strict"}, "project_context": {"brief": "", "principals": {}, "domain": "", "end_goal": ""}, "priority_actions": [], "open_tasks": []}
```
'''
        result = parser.parse_string(spec)
        assert result.blocks[0].section_id == "preamble"


# ── Error handling tests ──────────────────────────────────────


class TestErrorHandling:
    def test_invalid_json(self, parser):
        spec = '''\
# INIT v1.0.0

## §1 — Bad Block

```rag-config
{this is not valid json}
```
'''
        result = parser.parse_string(spec)
        assert len(result.errors) == 1
        assert "Invalid JSON" in result.errors[0].message
        assert result.errors[0].section_id == "1"

    def test_non_dict_block(self, parser):
        spec = '''\
# INIT v1.0.0

## §2 — Array Block

```rag-config
[1, 2, 3]
```
'''
        result = parser.parse_string(spec)
        assert len(result.errors) == 1
        assert "must be a JSON object" in result.errors[0].message

    def test_unclosed_fence(self, parser):
        spec = '''\
# INIT v1.0.0

## §3 — Unclosed

```rag-config
{"key": "value"}
'''
        result = parser.parse_string(spec)
        assert len(result.errors) == 1
        assert "Unclosed" in result.errors[0].message

    def test_errors_dont_halt_parsing(self, parser):
        """Parser continues after errors — resilient design."""
        spec = '''\
# INIT v1.0.0

## §1 — Bad

```rag-config
{bad json}
```

## §2 — Good

```rag-config
{"meta": {"schema_version": "5.3", "rag_type": "HOT", "root_project": "", "root_deliverables": "", "root_rag": "", "rag_files": {}}, "execution_mode": "autonomous", "state_machine_status": "BOOTING", "policy_flags": {"atomic_writes_required": true, "hash_validation_required": true, "load_cold_on_demand_only": true, "session_close_audit_required": true, "proposal_validation_commit_required": true}, "operating_protocol": {}, "pov_mandate": {"count": 0, "mode": "strict"}, "project_context": {"brief": "", "principals": {}, "domain": "", "end_goal": ""}, "priority_actions": [], "open_tasks": []}
```
'''
        result = parser.parse_string(spec)
        assert len(result.errors) == 1
        assert len(result.blocks) == 1
        assert result.blocks[0].section_id == "2"


# ── SpecParser.parse_file tests ───────────────────────────────


class TestParseFile:
    def test_file_not_found(self, parser):
        with pytest.raises(FileNotFoundError):
            parser.parse_file("/nonexistent/path/to/file.md")

    def test_actual_file(self, parser, minimal_spec):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write(minimal_spec)
            f.flush()
            tmp_path = f.name

        try:
            result = parser.parse_file(tmp_path)
            assert len(result.blocks) == 1
            assert result.source_file == tmp_path
            assert result.spec_version == "3.1.8"
        finally:
            os.unlink(tmp_path)


# ── Version extraction tests ──────────────────────────────────


class TestVersionExtraction:
    def test_standard_version(self, parser):
        spec = "# INIT_UNIVERSAL_RUNTIME_KERNEL v3.1.8\n"
        result = parser.parse_string(spec)
        assert result.spec_version == "3.1.8"

    def test_version_in_later_line(self, parser):
        spec = "# Title\n## Subtitle v2.0.1\n"
        result = parser.parse_string(spec)
        # Only searches first 10 lines
        assert result.spec_version == "2.0.1"

    def test_no_version(self, parser):
        spec = "# No version here\n\nJust text.\n"
        result = parser.parse_string(spec)
        assert result.spec_version == ""

    def test_version_beyond_10_lines(self, parser):
        lines = ["Line\n"] * 11 + ["v9.9.9\n"]
        spec = "".join(lines)
        result = parser.parse_string(spec)
        assert result.spec_version == ""  # Not found — only searches first 10


# ── Validation tests ──────────────────────────────────────────


class TestValidation:
    def test_valid_rag(self):
        errors = SpecParser.validate_rag(deepcopy(VOID_RAG))
        assert errors == []

    def test_missing_top_level_key(self):
        rag = deepcopy(VOID_RAG)
        del rag["meta"]
        errors = SpecParser.validate_rag(rag)
        assert any("meta" in e for e in errors)

    def test_missing_meta_key(self):
        rag = deepcopy(VOID_RAG)
        del rag["meta"]["schema_version"]
        errors = SpecParser.validate_rag(rag)
        assert any("schema_version" in e for e in errors)

    def test_missing_policy_flag(self):
        rag = deepcopy(VOID_RAG)
        del rag["policy_flags"]["atomic_writes_required"]
        errors = SpecParser.validate_rag(rag)
        assert any("atomic_writes_required" in e for e in errors)

    def test_invalid_execution_mode(self):
        rag = deepcopy(VOID_RAG)
        rag["execution_mode"] = "yolo"
        errors = SpecParser.validate_rag(rag)
        assert any("execution_mode" in e for e in errors)

    def test_valid_execution_modes(self):
        for mode in ("autonomous", "enforced", ""):
            rag = deepcopy(VOID_RAG)
            rag["execution_mode"] = mode
            errors = SpecParser.validate_rag(rag)
            mode_errors = [e for e in errors if "execution_mode" in e]
            assert mode_errors == [], f"Mode {mode!r} should be valid"

    def test_invalid_state_machine_status(self):
        rag = deepcopy(VOID_RAG)
        rag["state_machine_status"] = "EXPLODING"
        errors = SpecParser.validate_rag(rag)
        assert any("state_machine_status" in e for e in errors)

    def test_valid_states(self):
        valid = ["BOOTING", "READY", "INGESTING", "WORKING",
                 "CHECKPOINTING", "CLOSING", "RECOVERY", ""]
        for state in valid:
            rag = deepcopy(VOID_RAG)
            rag["state_machine_status"] = state
            errors = SpecParser.validate_rag(rag)
            state_errors = [e for e in errors if "state_machine_status" in e]
            assert state_errors == [], f"State {state!r} should be valid"

    def test_empty_dict(self):
        errors = SpecParser.validate_rag({})
        assert len(errors) > 0  # Should flag many missing keys


# ── Write tests ───────────────────────────────────────────────


class TestWrite:
    def test_write_rag_creates_file(self, parser):
        with tempfile.TemporaryDirectory() as tmpdir:
            rag = deepcopy(VOID_RAG)
            path = os.path.join(tmpdir, "RAG", "RAG_MASTER.json")
            written = parser.write_rag(rag, path)
            assert os.path.exists(written)
            with open(written, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            assert loaded["meta"]["schema_version"] == "5.3"

    def test_write_rag_creates_parent_dirs(self, parser):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "deep", "nested", "RAG_MASTER.json")
            parser.write_rag(deepcopy(VOID_RAG), path)
            assert os.path.exists(path)

    def test_write_rag_atomic(self, parser):
        """Atomic write should not leave .tmp files behind."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "RAG_MASTER.json")
            parser.write_rag(deepcopy(VOID_RAG), path, atomic=True)
            assert os.path.exists(path)
            assert not os.path.exists(path + ".tmp")
            # Verify it's not the .tmp file renamed — content should be valid JSON
            with open(path, "r", encoding="utf-8") as f:
                json.load(f)  # Should not raise

    def test_write_rag_non_atomic(self, parser):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "RAG_MASTER.json")
            parser.write_rag(deepcopy(VOID_RAG), path, atomic=False)
            assert os.path.exists(path)

    def test_write_cold(self, parser):
        with tempfile.TemporaryDirectory() as tmpdir:
            cold = {"meta": {"rag_type": "COLD"}, "cold_sections": {}}
            path = os.path.join(tmpdir, "RAG_COLD.json")
            written = parser.write_cold(cold, path)
            assert os.path.exists(written)
            with open(written, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            assert loaded["meta"]["rag_type"] == "COLD"

    def test_write_rag_utf8(self, parser):
        """Verify UTF-8 special characters survive write."""
        with tempfile.TemporaryDirectory() as tmpdir:
            rag = deepcopy(VOID_RAG)
            rag["meta"]["project_name"] = "Tëst Prøject — with em dash"
            path = os.path.join(tmpdir, "RAG_MASTER.json")
            parser.write_rag(rag, path)
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            assert loaded["meta"]["project_name"] == "Tëst Prøject — with em dash"


# ── Report tests ──────────────────────────────────────────────


class TestReport:
    def test_report_contains_key_info(self, parser, minimal_spec):
        result = parser.parse_string(minimal_spec)
        report = parser.report(result)
        assert "Spec Parser Report" in report
        assert "3.1.8" in report
        assert "Validation: PASSED" in report

    def test_report_shows_errors(self, parser):
        spec = '''\
# INIT v1.0.0

```rag-config
{bad json}
```
'''
        result = parser.parse_string(spec)
        report = parser.report(result)
        assert "Errors:" in report
        assert "Invalid JSON" in report

    def test_report_shows_validation_issues(self, parser):
        """If merged RAG has validation issues, report should show them."""
        spec = '''\
# INIT v1.0.0

```rag-config
{"execution_mode": "yolo"}
```
'''
        result = parser.parse_string(spec)
        report = parser.report(result)
        assert "Validation:" in report


# ── Integration: merge ordering ───────────────────────────────


class TestMergeOrdering:
    def test_later_blocks_override_earlier(self, parser):
        """Document-order merge: last config block wins on conflict."""
        spec = '''\
# INIT v1.0.0

## §1 — First

```rag-config
{"meta": {"schema_version": "5.3", "rag_type": "HOT", "root_project": "", "root_deliverables": "", "root_rag": "", "rag_files": {}}, "execution_mode": "autonomous", "state_machine_status": "BOOTING", "policy_flags": {"atomic_writes_required": true, "hash_validation_required": true, "load_cold_on_demand_only": true, "session_close_audit_required": true, "proposal_validation_commit_required": true}, "operating_protocol": {}, "pov_mandate": {"count": 0, "mode": "strict"}, "project_context": {"brief": "first", "principals": {}, "domain": "", "end_goal": ""}, "priority_actions": [], "open_tasks": []}
```

## §2 — Second

```rag-config
{"project_context": {"brief": "second"}}
```
'''
        result = parser.parse_string(spec)
        assert result.merged["project_context"]["brief"] == "second"

    def test_template_used_as_base(self, parser, template_spec):
        result = parser.parse_string(template_spec)
        # Template's custom key should survive
        assert result.merged.get("custom_base_key") == "from_template"
        # Config overlay should be applied on top
        assert result.merged["meta"]["project_name"] == "Test Project"

    def test_void_rag_fills_missing_keys(self, parser):
        """Merged RAG should have all VOID_RAG keys even if spec doesn't define them."""
        spec = '''\
# INIT v1.0.0

```rag-config
{"meta": {"schema_version": "5.3", "rag_type": "HOT", "root_project": "", "root_deliverables": "", "root_rag": "", "rag_files": {}}}
```
'''
        result = parser.parse_string(spec)
        for key in VOID_RAG:
            assert key in result.merged, f"Missing VOID_RAG key: {key}"


# ── Fence detection edge cases ────────────────────────────────


class TestFenceEdgeCases:
    def test_regular_code_block_ignored(self, parser):
        """Non-rag-config fenced blocks should be ignored."""
        spec = '''\
# INIT v1.0.0

```python
print("hello")
```

```json
{"not": "a rag-config block"}
```
'''
        result = parser.parse_string(spec)
        assert result.blocks == []
        assert result.errors == []

    def test_nested_backticks_in_content(self, parser):
        """JSON content with backtick-like strings shouldn't break parsing."""
        spec = '''\
# INIT v1.0.0

```rag-config
{"meta": {"schema_version": "5.3", "rag_type": "HOT", "root_project": "", "root_deliverables": "", "root_rag": "", "rag_files": {}}, "operating_protocol": {"note": "use triple backticks"}}
```
'''
        result = parser.parse_string(spec)
        assert len(result.blocks) == 1

    def test_section_header_variants(self, parser):
        """Section headers with different dash types."""
        spec = '''\
# INIT v1.0.0

## §1 — Em Dash

## §2 – En Dash

## §3 - Hyphen
'''
        result = parser.parse_string(spec)
        assert "§1" in result.sections_found
        assert "§2" in result.sections_found
        assert "§3" in result.sections_found

    def test_alphanumeric_section_ids(self, parser):
        """Section IDs like §3a, §12b should work."""
        spec = '''\
# INIT v1.0.0

## §3a — Sub-section A

```rag-config
{"operating_protocol": {"from": "3a"}}
```
'''
        result = parser.parse_string(spec)
        assert result.blocks[0].section_id == "3a"
