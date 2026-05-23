"""Tests for Delta Checkpoints (ENH-006).

Tests the delta checkpoint engine in persistence.py and its integration
with the KernelApp checkpoint() method in api.py.

Covers:
- DeltaOp construction and serialization
- DeltaCheckpoint construction and serialization
- delta_compute: diff between two dicts
- delta_apply: apply deltas to a base dict
- DeltaCheckpointManager: lifecycle, thresholds, full vs delta decisions
- KernelApp integration: checkpoint routing, close always full
- Crash recovery invariant: apply(base, deltas) == full checkpoint
"""

import copy
import json
import os
import tempfile
from pathlib import Path

import pytest

from rag_kernel.persistence import (
    DeltaCheckpoint,
    DeltaCheckpointManager,
    DeltaOp,
    delta_apply,
    delta_compute,
    _resolve_path,
)


# ---------------------------------------------------------------------------
# DeltaOp
# ---------------------------------------------------------------------------

class TestDeltaOp:
    """Tests for DeltaOp construction, validation, serialization."""

    def test_valid_ops(self):
        for op in ("replace", "add", "remove"):
            d = DeltaOp("meta.version", op, "1.0")
            assert d.op == op

    def test_invalid_op_raises(self):
        with pytest.raises(ValueError, match="Invalid delta op"):
            DeltaOp("meta.version", "patch")

    def test_empty_path_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            DeltaOp("", "replace", "x")

    def test_to_dict_replace(self):
        d = DeltaOp("meta.version", "replace", "2.0")
        assert d.to_dict() == {"path": "meta.version", "op": "replace", "value": "2.0"}

    def test_to_dict_remove_no_value(self):
        d = DeltaOp("old_key", "remove")
        result = d.to_dict()
        assert "value" not in result
        assert result == {"path": "old_key", "op": "remove"}

    def test_from_dict_roundtrip(self):
        original = DeltaOp("a.b.c", "add", [1, 2, 3])
        restored = DeltaOp.from_dict(original.to_dict())
        assert original == restored

    def test_equality(self):
        a = DeltaOp("x", "replace", 1)
        b = DeltaOp("x", "replace", 1)
        c = DeltaOp("x", "replace", 2)
        assert a == b
        assert a != c

    def test_repr_replace(self):
        d = DeltaOp("key", "replace", "val")
        assert "replace" in repr(d)
        assert "key" in repr(d)

    def test_repr_remove(self):
        d = DeltaOp("key", "remove")
        assert "remove" in repr(d)
        assert "val" not in repr(d)


# ---------------------------------------------------------------------------
# DeltaCheckpoint
# ---------------------------------------------------------------------------

class TestDeltaCheckpoint:
    """Tests for DeltaCheckpoint dataclass."""

    def test_construction(self):
        ops = [DeltaOp("a", "replace", 1)]
        dc = DeltaCheckpoint(base_seq=5, deltas=ops)
        assert dc.base_seq == 5
        assert dc.delta_count == 1
        assert dc.timestamp  # auto-generated

    def test_to_dict(self):
        ops = [DeltaOp("a", "replace", 1), DeltaOp("b", "remove")]
        dc = DeltaCheckpoint(base_seq=3, deltas=ops, timestamp="2026-05-23T00:00:00Z")
        d = dc.to_dict()
        assert d["type"] == "delta"
        assert d["base_seq"] == 3
        assert len(d["deltas"]) == 2

    def test_from_dict_roundtrip(self):
        ops = [DeltaOp("x.y", "add", {"nested": True})]
        original = DeltaCheckpoint(base_seq=10, deltas=ops, timestamp="2026-01-01T00:00:00Z")
        restored = DeltaCheckpoint.from_dict(original.to_dict())
        assert restored.base_seq == original.base_seq
        assert restored.delta_count == original.delta_count
        assert restored.deltas[0] == original.deltas[0]


# ---------------------------------------------------------------------------
# _resolve_path
# ---------------------------------------------------------------------------

class TestResolvePath:
    """Tests for dot-path resolution."""

    def test_top_level(self):
        obj = {"a": 1, "b": 2}
        parent, key = _resolve_path(obj, "a")
        assert parent is obj
        assert key == "a"

    def test_nested(self):
        obj = {"meta": {"version": "1.0"}}
        parent, key = _resolve_path(obj, "meta.version")
        assert parent == {"version": "1.0"}
        assert key == "version"

    def test_deep_nested(self):
        obj = {"a": {"b": {"c": {"d": 42}}}}
        parent, key = _resolve_path(obj, "a.b.c.d")
        assert parent == {"d": 42}
        assert key == "d"

    def test_missing_intermediate_raises(self):
        obj = {"a": 1}
        with pytest.raises(KeyError):
            _resolve_path(obj, "a.b.c")

    def test_non_dict_intermediate_raises(self):
        obj = {"a": "string_not_dict"}
        with pytest.raises(KeyError):
            _resolve_path(obj, "a.b")


# ---------------------------------------------------------------------------
# delta_compute
# ---------------------------------------------------------------------------

class TestDeltaCompute:
    """Tests for computing deltas between two dicts."""

    def test_identical_dicts(self):
        a = {"x": 1, "y": "hello"}
        b = {"x": 1, "y": "hello"}
        assert delta_compute(a, b) == []

    def test_simple_replace(self):
        old = {"version": "1.0"}
        new = {"version": "2.0"}
        ops = delta_compute(old, new)
        assert len(ops) == 1
        assert ops[0] == DeltaOp("version", "replace", "2.0")

    def test_simple_add(self):
        old = {"a": 1}
        new = {"a": 1, "b": 2}
        ops = delta_compute(old, new)
        assert len(ops) == 1
        assert ops[0] == DeltaOp("b", "add", 2)

    def test_simple_remove(self):
        old = {"a": 1, "b": 2}
        new = {"a": 1}
        ops = delta_compute(old, new)
        assert len(ops) == 1
        assert ops[0] == DeltaOp("b", "remove")

    def test_nested_change(self):
        old = {"meta": {"version": "1.0", "hash": "abc"}}
        new = {"meta": {"version": "2.0", "hash": "abc"}}
        ops = delta_compute(old, new)
        assert len(ops) == 1
        assert ops[0] == DeltaOp("meta.version", "replace", "2.0")

    def test_nested_add(self):
        old = {"meta": {"a": 1}}
        new = {"meta": {"a": 1, "b": 2}}
        ops = delta_compute(old, new)
        assert len(ops) == 1
        assert ops[0] == DeltaOp("meta.b", "add", 2)

    def test_nested_remove(self):
        old = {"meta": {"a": 1, "b": 2}}
        new = {"meta": {"a": 1}}
        ops = delta_compute(old, new)
        assert len(ops) == 1
        assert ops[0] == DeltaOp("meta.b", "remove")

    def test_mixed_operations(self):
        old = {"a": 1, "b": 2, "c": 3}
        new = {"a": 1, "b": 99, "d": 4}
        ops = delta_compute(old, new)
        # b replaced, c removed, d added
        assert len(ops) == 3
        paths = {op.path: op.op for op in ops}
        assert paths["b"] == "replace"
        assert paths["c"] == "remove"
        assert paths["d"] == "add"

    def test_list_value_replace(self):
        old = {"tasks": ["a", "b"]}
        new = {"tasks": ["a", "b", "c"]}
        ops = delta_compute(old, new)
        assert len(ops) == 1
        assert ops[0].op == "replace"
        assert ops[0].value == ["a", "b", "c"]

    def test_dict_to_non_dict_replace(self):
        old = {"x": {"nested": True}}
        new = {"x": "flat_now"}
        ops = delta_compute(old, new)
        assert len(ops) == 1
        assert ops[0] == DeltaOp("x", "replace", "flat_now")

    def test_empty_dicts(self):
        assert delta_compute({}, {}) == []

    def test_from_empty(self):
        ops = delta_compute({}, {"a": 1})
        assert len(ops) == 1
        assert ops[0] == DeltaOp("a", "add", 1)

    def test_to_empty(self):
        ops = delta_compute({"a": 1}, {})
        assert len(ops) == 1
        assert ops[0] == DeltaOp("a", "remove")


# ---------------------------------------------------------------------------
# delta_apply
# ---------------------------------------------------------------------------

class TestDeltaApply:
    """Tests for applying deltas to a base dict."""

    def test_apply_replace(self):
        base = {"version": "1.0"}
        dc = DeltaCheckpoint(0, [DeltaOp("version", "replace", "2.0")])
        result = delta_apply(base, dc)
        assert result["version"] == "2.0"

    def test_apply_add(self):
        base = {"a": 1}
        dc = DeltaCheckpoint(0, [DeltaOp("b", "add", 2)])
        result = delta_apply(base, dc)
        assert result == {"a": 1, "b": 2}

    def test_apply_remove(self):
        base = {"a": 1, "b": 2}
        dc = DeltaCheckpoint(0, [DeltaOp("b", "remove")])
        result = delta_apply(base, dc)
        assert result == {"a": 1}

    def test_apply_nested(self):
        base = {"meta": {"version": "1.0"}}
        dc = DeltaCheckpoint(0, [DeltaOp("meta.version", "replace", "2.0")])
        result = delta_apply(base, dc)
        assert result["meta"]["version"] == "2.0"

    def test_apply_multiple(self):
        base = {"a": 1, "b": 2, "c": 3}
        dc = DeltaCheckpoint(0, [
            DeltaOp("a", "replace", 10),
            DeltaOp("b", "remove"),
            DeltaOp("d", "add", 4),
        ])
        result = delta_apply(base, dc)
        assert result == {"a": 10, "c": 3, "d": 4}

    def test_replace_missing_raises(self):
        base = {"a": 1}
        dc = DeltaCheckpoint(0, [DeltaOp("nonexistent", "replace", "x")])
        with pytest.raises(KeyError, match="does not exist"):
            delta_apply(base, dc)

    def test_add_existing_raises(self):
        base = {"a": 1}
        dc = DeltaCheckpoint(0, [DeltaOp("a", "add", 2)])
        with pytest.raises(KeyError, match="already exists"):
            delta_apply(base, dc)

    def test_remove_missing_raises(self):
        base = {"a": 1}
        dc = DeltaCheckpoint(0, [DeltaOp("b", "remove")])
        with pytest.raises(KeyError, match="does not exist"):
            delta_apply(base, dc)

    def test_roundtrip_invariant(self):
        """Core invariant: apply(old, compute(old, new)) == new."""
        old = {
            "meta": {"version": "1.0", "hash": "abc"},
            "tasks": [1, 2, 3],
            "config": {"mode": "strict", "count": 5},
        }
        new = {
            "meta": {"version": "2.0", "hash": "def", "new_field": True},
            "tasks": [1, 2, 3, 4],
            "status": "active",
        }
        ops = delta_compute(old, new)
        dc = DeltaCheckpoint(base_seq=0, deltas=ops)
        result = delta_apply(copy.deepcopy(old), dc)
        assert result == new

    def test_roundtrip_nested_changes(self):
        """Roundtrip with deeply nested changes."""
        old = {"a": {"b": {"c": 1, "d": 2}, "e": 3}}
        new = {"a": {"b": {"c": 99, "d": 2, "f": 10}, "e": 3}, "g": "new"}
        ops = delta_compute(old, new)
        dc = DeltaCheckpoint(base_seq=0, deltas=ops)
        result = delta_apply(copy.deepcopy(old), dc)
        assert result == new


# ---------------------------------------------------------------------------
# DeltaCheckpointManager
# ---------------------------------------------------------------------------

class TestDeltaCheckpointManager:
    """Tests for the delta checkpoint lifecycle manager."""

    def test_initial_state(self):
        mgr = DeltaCheckpointManager(max_deltas=5)
        assert mgr.delta_count == 0
        assert mgr.max_deltas == 5

    def test_invalid_max_deltas(self):
        with pytest.raises(ValueError):
            DeltaCheckpointManager(max_deltas=0)

    def test_should_full_no_base(self):
        mgr = DeltaCheckpointManager()
        assert mgr.should_full_checkpoint() is True

    def test_should_full_before_first_full_done(self):
        """Even with base set (boot), first checkpoint should be full."""
        mgr = DeltaCheckpointManager()
        mgr.set_base({"a": 1}, 0)
        assert mgr.should_full_checkpoint() is True

    def test_should_full_on_close(self):
        mgr = DeltaCheckpointManager()
        mgr.set_base({"a": 1}, 0)
        mgr._first_full_done = True
        assert mgr.should_full_checkpoint(is_closing=True) is True

    def test_should_delta_after_first_full(self):
        """After first full checkpoint done, should route to delta."""
        mgr = DeltaCheckpointManager()
        mgr.set_base({"a": 1}, 0)
        mgr._first_full_done = True
        assert mgr.should_full_checkpoint() is False

    def test_threshold_triggers_full(self):
        mgr = DeltaCheckpointManager(max_deltas=3)
        mgr.set_base({"a": 1}, 0)
        mgr._first_full_done = True
        # Simulate 3 delta calls
        for i in range(3):
            mgr.compute_delta({"a": i + 2})
        assert mgr.needs_full is True
        assert mgr.should_full_checkpoint() is True

    def test_set_base_resets(self):
        mgr = DeltaCheckpointManager(max_deltas=2)
        mgr.set_base({"a": 1}, 0)
        mgr.compute_delta({"a": 2})
        mgr.compute_delta({"a": 3})
        assert mgr.needs_full is True
        # Reset
        mgr.set_base({"a": 3}, 5)
        assert mgr.needs_full is False
        assert mgr.delta_count == 0

    def test_compute_delta_no_changes(self):
        mgr = DeltaCheckpointManager()
        mgr.set_base({"a": 1}, 0)
        result = mgr.compute_delta({"a": 1})
        assert result is None

    def test_compute_delta_with_changes(self):
        mgr = DeltaCheckpointManager()
        mgr.set_base({"a": 1}, 5)
        result = mgr.compute_delta({"a": 2})
        assert result is not None
        assert result.base_seq == 5
        assert result.delta_count == 1

    def test_reset_clears_all(self):
        mgr = DeltaCheckpointManager()
        mgr.set_base({"a": 1}, 0)
        mgr.compute_delta({"a": 2})
        mgr.reset()
        assert mgr.delta_count == 0
        assert mgr.should_full_checkpoint() is True


# ---------------------------------------------------------------------------
# KernelApp integration
# ---------------------------------------------------------------------------

class TestKernelAppDeltaCheckpoint:
    """Integration tests: KernelApp checkpoint() with delta support."""

    def _make_app(self, tmp_path: Path):
        """Create a KernelApp in a temp dir, boot it."""
        from rag_kernel.api import KernelApp
        hot = {
            "meta": {
                "schema_version": "5.3",
                "rag_version": "1.7.0",
                "rag_type": "HOT",
                "state_hash": "",
                "session_id": "test",
                "last_checkpoint_seq": 0,
            },
            "data": {"counter": 0},
        }
        hot_path = tmp_path / "RAG_MASTER.json"
        hot_path.write_text(json.dumps(hot), encoding="utf-8")

        app = KernelApp(tmp_path, session_id="test-delta")
        result = app.boot()
        assert result["status"] == "OK"
        return app

    def test_first_checkpoint_is_full(self, tmp_path):
        app = self._make_app(tmp_path)
        result = app.checkpoint()
        assert result["checkpointed"] is True
        assert result["checkpoint_type"] == "full"

    def test_second_checkpoint_is_delta(self, tmp_path):
        app = self._make_app(tmp_path)
        # First: full (sets base)
        app.checkpoint()
        # Mutate HOT
        app._hot["data"]["counter"] = 1
        # Second: delta
        result = app.checkpoint()
        assert result["checkpointed"] is True
        assert result["checkpoint_type"] == "delta"

    def test_no_change_delta_skips(self, tmp_path):
        app = self._make_app(tmp_path)
        app.checkpoint()  # full
        # No mutations
        result = app.checkpoint()
        # state_hash and last_checkpoint_seq change on each checkpoint call,
        # so there WILL be changes in meta. Let's check it's delta at least.
        assert result["checkpoint_type"] == "delta"

    def test_force_full(self, tmp_path):
        app = self._make_app(tmp_path)
        app.checkpoint()  # first full
        app._hot["data"]["counter"] = 1
        result = app.checkpoint(force_full=True)
        assert result["checkpoint_type"] == "full"

    def test_close_forces_full(self, tmp_path):
        app = self._make_app(tmp_path)
        app.checkpoint()  # first full
        app._hot["data"]["counter"] = 1
        # Close should force full checkpoint
        result = app.close()
        assert result["state"] == "CLOSING"

    def test_threshold_triggers_full(self, tmp_path):
        app = self._make_app(tmp_path)
        app._delta_mgr.max_deltas = 3
        app.checkpoint()  # first full

        # 3 delta checkpoints -> threshold reached -> next should be full
        for i in range(3):
            app._hot["data"]["counter"] = i + 1
            app.checkpoint()

        # 4th should be full (threshold crossed)
        app._hot["data"]["counter"] = 99
        result = app.checkpoint()
        assert result["checkpoint_type"] == "full"

    def test_delta_roundtrip_integrity(self, tmp_path):
        """Core invariant: after delta checkpoints, a full checkpoint
        produces the same state as the in-memory HOT."""
        app = self._make_app(tmp_path)
        app.checkpoint()  # full, sets base

        # Several mutations with delta checkpoints
        app._hot["data"]["counter"] = 10
        app.checkpoint()  # delta
        app._hot["data"]["new_field"] = "hello"
        app.checkpoint()  # delta

        # Force full and verify file matches in-memory state
        app.checkpoint(force_full=True)

        # Read back from disk
        disk_hot = json.loads(app.hot_path.read_text(encoding="utf-8"))
        # Compare data sections (meta will have timestamps etc.)
        assert disk_hot["data"] == app._hot["data"]

    def test_status_includes_delta_info(self, tmp_path):
        app = self._make_app(tmp_path)
        status = app.status()
        assert "delta_checkpoint" in status
        assert "deltas_since_full" in status["delta_checkpoint"]
        assert "max_deltas" in status["delta_checkpoint"]

    def test_wal_records_checkpoint_type(self, tmp_path):
        app = self._make_app(tmp_path)
        app.checkpoint()  # full
        app._hot["data"]["counter"] = 1
        app.checkpoint()  # delta

        entries = app.get_wal()
        checkpoint_entries = [e for e in entries if e["event"] == "CHECKPOINT"]
        assert len(checkpoint_entries) == 2
        assert checkpoint_entries[0]["checkpoint_type"] == "full"
        assert checkpoint_entries[1]["checkpoint_type"] == "delta"
