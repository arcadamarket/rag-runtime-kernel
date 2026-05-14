"""Tests for the RAG Runtime Kernel concurrency guards.

Coverage targets:
- ProjectLock: acquire, release, re-entrant, stale detection, force release
- LockInfo: serialization, process-alive check
- Context manager: success and conflict paths
- Split-brain detection: no conflict, foreign session, same session, edge cases
- Thread safety: concurrent acquire attempts
- Error handling: corrupt lock file, missing directory
"""

import json
import os
import threading

import pytest

from rag_kernel.concurrency import (
    LOCK_FILENAME,
    ConcurrencyError,
    LockConflictError,
    LockInfo,
    ProjectLock,
    SplitBrainError,
    detect_split_brain,
)


# ===== Fixtures =====

@pytest.fixture
def project_dir(tmp_path):
    """A temporary project directory."""
    return tmp_path / "RAG"


@pytest.fixture
def lock(project_dir):
    """ProjectLock instance. Creates dir if needed."""
    project_dir.mkdir(parents=True, exist_ok=True)
    return ProjectLock(project_dir)


# ===== LockInfo =====

class TestLockInfo:
    def test_to_dict(self):
        info = LockInfo(session_id="S9", pid=12345, acquired_at="2026-05-14T10:00:00Z")
        d = info.to_dict()
        assert d == {
            "session_id": "S9",
            "pid": 12345,
            "acquired_at": "2026-05-14T10:00:00Z",
        }

    def test_from_dict(self):
        d = {"session_id": "S9", "pid": 12345, "acquired_at": "2026-05-14T10:00:00Z"}
        info = LockInfo.from_dict(d)
        assert info.session_id == "S9"
        assert info.pid == 12345

    def test_roundtrip(self):
        original = LockInfo(session_id="S9", pid=99, acquired_at="2026-05-14T10:00:00Z")
        restored = LockInfo.from_dict(original.to_dict())
        assert restored == original

    def test_is_process_alive_self(self):
        """Current process should be alive."""
        info = LockInfo(session_id="test", pid=os.getpid(), acquired_at="now")
        assert info.is_process_alive() is True

    def test_is_process_alive_dead(self):
        """A PID that (almost certainly) doesn't exist."""
        info = LockInfo(session_id="test", pid=2_000_000_000, acquired_at="now")
        assert info.is_process_alive() is False

    def test_frozen(self):
        info = LockInfo(session_id="S9", pid=1, acquired_at="now")
        with pytest.raises(AttributeError):
            info.session_id = "S10"


# ===== ProjectLock: acquire / release =====

class TestProjectLockBasic:
    def test_acquire_no_existing_lock(self, lock):
        assert lock.acquire("S9")
        assert lock.is_locked

    def test_lock_file_created(self, lock):
        lock.acquire("S9")
        assert lock.lock_path.exists()

    def test_lock_file_contents(self, lock):
        lock.acquire("S9")
        data = json.loads(lock.lock_path.read_text())
        assert data["session_id"] == "S9"
        assert data["pid"] == os.getpid()
        assert "acquired_at" in data

    def test_release_removes_file(self, lock):
        lock.acquire("S9")
        lock.release()
        assert not lock.is_locked

    def test_release_without_acquire(self, lock):
        """Safe to call release even if never acquired."""
        lock.release()  # should not raise

    def test_double_release(self, lock):
        lock.acquire("S9")
        lock.release()
        lock.release()  # safe


# ===== Re-entrant acquire =====

class TestReentrantAcquire:
    def test_same_session_re_acquire(self, lock):
        assert lock.acquire("S9")
        assert lock.acquire("S9")  # same session — OK
        assert lock.is_locked

    def test_different_session_blocked(self, lock):
        assert lock.acquire("S9")
        assert not lock.acquire("S10", steal_stale=False)

    def test_different_session_after_release(self, lock):
        lock.acquire("S9")
        lock.release()
        assert lock.acquire("S10")


# ===== Stale lock detection =====

class TestStaleLock:
    def test_stale_lock_reclaimed(self, lock):
        """Lock with dead PID should be reclaimable."""
        # Write a lock with a PID that doesn't exist
        lock_info = LockInfo(
            session_id="dead_session",
            pid=2_000_000_000,
            acquired_at="2026-01-01T00:00:00Z",
        )
        lock.lock_path.write_text(
            json.dumps(lock_info.to_dict()), encoding="utf-8"
        )
        assert lock.acquire("S9", steal_stale=True)
        info = lock.read_lock()
        assert info.session_id == "S9"

    def test_stale_lock_not_reclaimed_when_disabled(self, lock):
        lock_info = LockInfo(
            session_id="dead_session",
            pid=2_000_000_000,
            acquired_at="2026-01-01T00:00:00Z",
        )
        lock.lock_path.write_text(
            json.dumps(lock_info.to_dict()), encoding="utf-8"
        )
        assert not lock.acquire("S9", steal_stale=False)

    def test_active_lock_not_stolen(self, lock):
        """Lock with alive PID (this process) should NOT be stolen."""
        lock_info = LockInfo(
            session_id="other_session",
            pid=os.getpid(),  # alive
            acquired_at="2026-01-01T00:00:00Z",
        )
        lock.lock_path.write_text(
            json.dumps(lock_info.to_dict()), encoding="utf-8"
        )
        assert not lock.acquire("S9", steal_stale=True)


# ===== Corrupt lock file =====

class TestCorruptLock:
    def test_corrupt_json_reclaimed(self, lock):
        lock.lock_path.write_text("{bad json", encoding="utf-8")
        assert lock.acquire("S9")
        info = lock.read_lock()
        assert info.session_id == "S9"

    def test_incomplete_lock_reclaimed(self, lock):
        lock.lock_path.write_text('{"session_id": "old"}', encoding="utf-8")
        # Missing pid and acquired_at — from_dict raises KeyError
        assert lock.acquire("S9")


# ===== Read lock =====

class TestReadLock:
    def test_read_no_lock(self, lock):
        assert lock.read_lock() is None

    def test_read_existing_lock(self, lock):
        lock.acquire("S9")
        info = lock.read_lock()
        assert info is not None
        assert info.session_id == "S9"

    def test_read_corrupt_raises(self, lock):
        lock.lock_path.write_text("not json", encoding="utf-8")
        with pytest.raises(ConcurrencyError, match="corrupt"):
            lock.read_lock()


# ===== Force release =====

class TestForceRelease:
    def test_force_release_existing(self, lock):
        lock.acquire("S9")
        assert lock.force_release() is True
        assert not lock.is_locked

    def test_force_release_no_lock(self, lock):
        assert lock.force_release() is False


# ===== Context manager =====

class TestContextManager:
    def test_context_acquires_and_releases(self, project_dir):
        project_dir.mkdir(parents=True, exist_ok=True)
        with ProjectLock.context(project_dir, "S9") as lk:
            assert lk.is_locked
            info = lk.read_lock()
            assert info.session_id == "S9"
        # After context, lock should be released
        assert not (project_dir / LOCK_FILENAME).exists()

    def test_context_conflict_raises(self, project_dir):
        project_dir.mkdir(parents=True, exist_ok=True)
        # Pre-existing lock from alive process, different session
        lock_info = LockInfo(
            session_id="other",
            pid=os.getpid(),
            acquired_at="2026-01-01T00:00:00Z",
        )
        (project_dir / LOCK_FILENAME).write_text(
            json.dumps(lock_info.to_dict()), encoding="utf-8"
        )
        with pytest.raises(LockConflictError) as exc_info:
            with ProjectLock.context(project_dir, "S9", steal_stale=False):
                pass
        assert exc_info.value.lock_info.session_id == "other"


# ===== Repr =====

class TestRepr:
    def test_repr_no_lock(self, lock):
        r = repr(lock)
        assert "ProjectLock" in r
        assert "none" in r

    def test_repr_with_lock(self, lock):
        lock.acquire("S9")
        r = repr(lock)
        assert "S9" in r


# ===== Thread safety =====

class TestThreadSafety:
    def test_concurrent_acquire(self, lock):
        """Multiple threads racing to acquire. Only one should succeed."""
        results = []
        barrier = threading.Barrier(10)

        def try_acquire(sid):
            barrier.wait()
            ok = lock.acquire(sid, steal_stale=False)
            results.append((sid, ok))

        threads = []
        for i in range(10):
            t = threading.Thread(target=try_acquire, args=(f"S{i}",))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        successes = [r for r in results if r[1]]
        # Exactly one should win (they all use current PID, so
        # stale detection won't reclaim — the winner holds it)
        assert len(successes) == 1

    def test_concurrent_read_lock(self, lock):
        """Reading lock info from multiple threads is safe."""
        lock.acquire("S9")
        results = []

        def read():
            for _ in range(50):
                results.append(lock.read_lock())

        threads = [threading.Thread(target=read) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 250
        assert all(r.session_id == "S9" for r in results)


# ===== Split-brain detection =====

class TestSplitBrainDetection:
    def test_no_conflict_same_session(self):
        hot = {"meta": {"last_checkpoint_seq": 10, "session_id": "S9"}}
        wal = [
            {"seq": 11, "session_id": "S9"},
            {"seq": 12, "session_id": "S9"},
        ]
        result = detect_split_brain(hot, wal)
        assert result is None

    def test_no_conflict_no_wal(self):
        hot = {"meta": {"last_checkpoint_seq": 10, "session_id": "S9"}}
        result = detect_split_brain(hot, [])
        assert result is None

    def test_foreign_session_higher_seq(self):
        hot = {"meta": {"last_checkpoint_seq": 10, "session_id": "S9"}}
        wal = [
            {"seq": 11, "session_id": "S9"},
            {"seq": 15, "session_id": "S_OTHER"},
        ]
        result = detect_split_brain(hot, wal)
        assert result is not None
        assert isinstance(result, SplitBrainError)
        assert result.hot_seq == 10
        assert result.wal_seq == 15
        assert result.hot_session == "S9"
        assert result.wal_session == "S_OTHER"

    def test_foreign_session_lower_seq_no_conflict(self):
        hot = {"meta": {"last_checkpoint_seq": 20, "session_id": "S9"}}
        wal = [
            {"seq": 5, "session_id": "S_OLD"},
        ]
        result = detect_split_brain(hot, wal)
        assert result is None

    def test_no_session_in_hot(self):
        hot = {"meta": {"last_checkpoint_seq": 10}}
        wal = [{"seq": 15, "session_id": "S_OTHER"}]
        result = detect_split_brain(hot, wal)
        assert result is None

    def test_no_meta_in_hot(self):
        hot = {}
        wal = [{"seq": 15, "session_id": "S_OTHER"}]
        result = detect_split_brain(hot, wal)
        assert result is None

    def test_wal_entries_without_session_id(self):
        hot = {"meta": {"last_checkpoint_seq": 10, "session_id": "S9"}}
        wal = [
            {"seq": 15},  # no session_id
        ]
        result = detect_split_brain(hot, wal)
        assert result is None

    def test_split_brain_error_message(self):
        err = SplitBrainError(
            hot_seq=10, wal_seq=20,
            hot_session="S9", wal_session="S10",
        )
        assert "Split-brain" in str(err)
        assert "RECOVERY" in str(err)

    def test_lock_conflict_error_message(self):
        info = LockInfo(session_id="S9", pid=123, acquired_at="2026-05-14T10:00:00Z")
        err = LockConflictError(info)
        assert "S9" in str(err)
        assert "123" in str(err)


# ===== Edge cases =====

class TestEdgeCases:
    def test_lock_path_property(self, lock, project_dir):
        assert lock.lock_path == project_dir / LOCK_FILENAME

    def test_is_locked_false_initially(self, lock):
        assert not lock.is_locked

    def test_acquire_creates_parent_dir_not_needed(self, tmp_path):
        """Lock assumes project_dir exists (it must for RAG)."""
        project_dir = tmp_path / "RAG"
        project_dir.mkdir()
        lock = ProjectLock(project_dir)
        assert lock.acquire("S9")

    def test_lock_file_name_constant(self):
        assert LOCK_FILENAME == ".rag_kernel.lock"
