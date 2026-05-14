"""Tests for the RAG Runtime Kernel persistence engine.

Coverage targets:
- Atomic writes: success, verification failure, backup rotation
- WAL: open/close, append+fsync, replay, since filter, truncate,
  crash recovery (resume seq), corrupt entry handling, context manager
- Hash computation: deterministic, exclude_keys, verify_hashes
- Hash verification: match, mismatch, empty/PENDING sentinels
"""

import hashlib
import json
import os
import tempfile
from pathlib import Path

import pytest

from rag_kernel.persistence import (
    HashMismatchError,
    PersistenceError,
    WAL,
    WALEntry,
    WALError,
    WriteVerificationError,
    atomic_write,
    atomic_write_json,
    compute_hash,
    rotate_backup,
    verify_hashes,
)


# ===== Fixtures =====

@pytest.fixture
def tmp_dir():
    """Temporary directory for test files."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def sample_hot():
    """Minimal HOT dict for hash testing."""
    return {
        "meta": {
            "schema_version": "5.3",
            "state_hash": "",
            "inventory_hash": "",
        },
        "current_status": {"phase": "test"},
    }


# ===== Atomic writes =====

class TestAtomicWrite:
    def test_basic_write(self, tmp_dir):
        path = tmp_dir / "test.json"
        data = b'{"hello": "world"}'
        atomic_write(path, data)
        assert path.read_bytes() == data

    def test_creates_parent_dirs_not_required(self, tmp_dir):
        """atomic_write assumes parent dir exists (caller responsibility)."""
        path = tmp_dir / "test.json"
        atomic_write(path, b"test")
        assert path.exists()

    def test_backup_created_on_overwrite(self, tmp_dir):
        path = tmp_dir / "test.json"
        bak_path = tmp_dir / "test.json.bak"

        # First write — no backup
        atomic_write(path, b"version1")
        assert not bak_path.exists()

        # Second write — backup of version1
        atomic_write(path, b"version2")
        assert bak_path.exists()
        assert bak_path.read_bytes() == b"version1"
        assert path.read_bytes() == b"version2"

    def test_tmp_cleaned_on_success(self, tmp_dir):
        path = tmp_dir / "test.json"
        atomic_write(path, b"data")
        tmp_path = tmp_dir / "test.json.tmp"
        assert not tmp_path.exists()

    def test_preserves_unicode(self, tmp_dir):
        path = tmp_dir / "test.json"
        data = '{"emoji": "✔", "dash": "—"}'.encode("utf-8")
        atomic_write(path, data)
        assert path.read_bytes() == data

    def test_empty_file(self, tmp_dir):
        path = tmp_dir / "test.json"
        atomic_write(path, b"")
        assert path.read_bytes() == b""

    def test_large_file(self, tmp_dir):
        path = tmp_dir / "large.json"
        data = b"x" * (1024 * 1024)  # 1MB
        atomic_write(path, data)
        assert path.read_bytes() == data


class TestAtomicWriteJson:
    def test_json_roundtrip(self, tmp_dir):
        path = tmp_dir / "test.json"
        obj = {"key": "value", "nested": {"a": 1}}
        atomic_write_json(path, obj)
        result = json.loads(path.read_bytes())
        assert result == obj

    def test_unicode_preserved(self, tmp_dir):
        path = tmp_dir / "test.json"
        obj = {"symbol": "—", "text": "§ section"}
        atomic_write_json(path, obj)
        content = path.read_text("utf-8")
        assert "—" in content  # em dash preserved, not escaped
        assert "§" in content


# ===== Backup rotation =====

class TestRotateBackup:
    def test_creates_bak(self, tmp_dir):
        path = tmp_dir / "file.json"
        path.write_bytes(b"original")
        bak = rotate_backup(path)
        assert bak is not None
        assert bak.read_bytes() == b"original"

    def test_no_file_returns_none(self, tmp_dir):
        path = tmp_dir / "missing.json"
        assert rotate_backup(path) is None

    def test_overwrites_existing_bak(self, tmp_dir):
        path = tmp_dir / "file.json"
        bak_path = tmp_dir / "file.json.bak"
        path.write_bytes(b"v2")
        bak_path.write_bytes(b"v1_bak")
        rotate_backup(path)
        assert bak_path.read_bytes() == b"v2"


# ===== Hash computation =====

class TestComputeHash:
    def test_deterministic(self):
        data = {"b": 2, "a": 1}
        h1 = compute_hash(data)
        h2 = compute_hash(data)
        assert h1 == h2

    def test_key_order_independent(self):
        d1 = {"a": 1, "b": 2}
        d2 = {"b": 2, "a": 1}
        assert compute_hash(d1) == compute_hash(d2)

    def test_excludes_hash_keys_by_default(self):
        d1 = {"data": "test", "state_hash": "abc", "inventory_hash": "def"}
        d2 = {"data": "test", "state_hash": "xyz", "inventory_hash": "000"}
        assert compute_hash(d1) == compute_hash(d2)

    def test_custom_exclude_keys(self):
        d1 = {"a": 1, "b": 2, "c": 3}
        d2 = {"a": 1, "b": 999, "c": 3}
        assert compute_hash(d1, exclude_keys={"b"}) == compute_hash(d2, exclude_keys={"b"})

    def test_different_data_different_hash(self):
        d1 = {"a": 1}
        d2 = {"a": 2}
        assert compute_hash(d1) != compute_hash(d2)

    def test_hash_is_sha256_hex(self):
        h = compute_hash({"test": True})
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


# ===== Hash verification =====

class TestVerifyHashes:
    def test_empty_hashes_pass(self, sample_hot):
        """Empty string = not yet computed -> skip, no error."""
        errors = verify_hashes(sample_hot)
        assert errors == []

    def test_pending_sentinel_passes(self, sample_hot):
        sample_hot["meta"]["state_hash"] = "PENDING"
        errors = verify_hashes(sample_hot)
        assert errors == []

    def test_correct_hash_passes(self, sample_hot):
        h = compute_hash(sample_hot, exclude_keys={"state_hash"})
        sample_hot["meta"]["state_hash"] = h
        errors = verify_hashes(sample_hot)
        # state_hash should pass; inventory_hash is empty -> skipped
        assert not any("state_hash" in e for e in errors)

    def test_wrong_hash_fails(self, sample_hot):
        sample_hot["meta"]["state_hash"] = "deadbeef" * 8
        errors = verify_hashes(sample_hot)
        assert len(errors) >= 1
        assert "state_hash" in errors[0]

    def test_flat_structure_also_works(self):
        """Hashes can be at top level instead of nested in meta."""
        hot = {"data": "test", "state_hash": "", "inventory_hash": ""}
        errors = verify_hashes(hot)
        assert errors == []


# ===== WAL =====

class TestWAL:
    def test_open_close(self, tmp_dir):
        wal = WAL(tmp_dir / "WAL.jsonl")
        wal.open()
        assert wal.path.exists()
        wal.close()

    def test_context_manager(self, tmp_dir):
        path = tmp_dir / "WAL.jsonl"
        with WAL(path) as wal:
            wal.append("TEST")
        # File should be closed now — verify by reading
        assert path.exists()

    def test_append_creates_entry(self, tmp_dir):
        with WAL(tmp_dir / "WAL.jsonl") as wal:
            entry = wal.append("TRANSITION", from_state="BOOTING", to_state="READY")
            assert entry.seq == 1
            assert entry.event == "TRANSITION"
            assert entry.data["from_state"] == "BOOTING"

    def test_append_increments_seq(self, tmp_dir):
        with WAL(tmp_dir / "WAL.jsonl") as wal:
            e1 = wal.append("A")
            e2 = wal.append("B")
            e3 = wal.append("C")
            assert e1.seq == 1
            assert e2.seq == 2
            assert e3.seq == 3

    def test_append_without_open_raises(self, tmp_dir):
        wal = WAL(tmp_dir / "WAL.jsonl")
        with pytest.raises(WALError, match="not open"):
            wal.append("TEST")

    def test_replay_all(self, tmp_dir):
        path = tmp_dir / "WAL.jsonl"
        with WAL(path) as wal:
            wal.append("A", x=1)
            wal.append("B", x=2)
            wal.append("C", x=3)

        wal2 = WAL(path)
        entries = wal2.replay()
        assert len(entries) == 3
        assert entries[0].event == "A"
        assert entries[2].event == "C"

    def test_replay_since(self, tmp_dir):
        path = tmp_dir / "WAL.jsonl"
        with WAL(path) as wal:
            wal.append("A")
            wal.append("B")
            wal.append("C")

        entries = WAL(path).replay(since=2)
        assert len(entries) == 1
        assert entries[0].event == "C"
        assert entries[0].seq == 3

    def test_replay_empty_file(self, tmp_dir):
        path = tmp_dir / "WAL.jsonl"
        path.write_text("")
        entries = WAL(path).replay()
        assert entries == []

    def test_replay_nonexistent_file(self, tmp_dir):
        entries = WAL(tmp_dir / "nope.jsonl").replay()
        assert entries == []

    def test_replay_skips_corrupt_lines(self, tmp_dir):
        path = tmp_dir / "WAL.jsonl"
        path.write_text(
            '{"seq":1,"ts":"2026-01-01T00:00:00Z","event":"A"}\n'
            'NOT JSON\n'
            '{"seq":3,"ts":"2026-01-01T00:00:01Z","event":"C"}\n'
        )
        entries = WAL(path).replay()
        assert len(entries) == 2
        assert entries[0].seq == 1
        assert entries[1].seq == 3

    def test_resume_seq_after_reopen(self, tmp_dir):
        path = tmp_dir / "WAL.jsonl"
        with WAL(path) as wal:
            wal.append("A")
            wal.append("B")

        with WAL(path) as wal2:
            entry = wal2.append("C")
            assert entry.seq == 3  # continues from 2

    def test_truncate(self, tmp_dir):
        path = tmp_dir / "WAL.jsonl"
        with WAL(path) as wal:
            wal.append("A")
            wal.append("B")
            wal.truncate()
            entry = wal.append("C")
            # Seq preserved across truncate
            assert entry.seq == 3

        # Only C should be on disk after truncate
        entries = WAL(path).replay()
        assert len(entries) == 1
        assert entries[0].event == "C"

    def test_fsync_called(self, tmp_dir, monkeypatch):
        """Verify fsync is actually called on each append."""
        fsync_calls = []
        original_fsync = os.fsync

        def tracking_fsync(fd):
            fsync_calls.append(fd)
            original_fsync(fd)

        monkeypatch.setattr(os, "fsync", tracking_fsync)

        with WAL(tmp_dir / "WAL.jsonl") as wal:
            wal.append("A")
            wal.append("B")

        # 2 appends + 1 close = 3 fsync calls
        assert len(fsync_calls) == 3

    def test_entry_to_dict(self):
        entry = WALEntry(seq=1, timestamp="2026-01-01T00:00:00Z",
                         event="TEST", data={"key": "val"})
        d = entry.to_dict()
        assert d["seq"] == 1
        assert d["event"] == "TEST"
        assert d["key"] == "val"

    def test_entry_roundtrip(self):
        entry = WALEntry(seq=5, timestamp="2026-01-01T00:00:00Z",
                         event="COMMIT", data={"file": "test.json"})
        line = entry.to_json_line()
        d = json.loads(line)
        restored = WALEntry.from_dict(d)
        assert restored.seq == 5
        assert restored.event == "COMMIT"
        assert restored.data == {"file": "test.json"}

    def test_repr(self, tmp_dir):
        wal = WAL(tmp_dir / "WAL.jsonl")
        assert "closed" in repr(wal)
        wal.open()
        assert "open" in repr(wal)
        wal.close()

    def test_double_open_is_safe(self, tmp_dir):
        wal = WAL(tmp_dir / "WAL.jsonl")
        wal.open()
        wal.open()  # should not error
        wal.close()

    def test_double_close_is_safe(self, tmp_dir):
        wal = WAL(tmp_dir / "WAL.jsonl")
        wal.open()
        wal.close()
        wal.close()  # should not error
