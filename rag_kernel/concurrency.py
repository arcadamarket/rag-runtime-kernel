"""Concurrency guards for the RAG Runtime Kernel.

Provides filesystem-based mutual exclusion and split-brain detection:
- ProjectLock: file-based mutex (.rag_kernel.lock) ensuring only one
  session owns a project directory at a time.
- Split-brain detection: compares WAL state against HOT checkpoint to
  detect conflicting sessions.

All operations use Python stdlib only. Zero external dependencies.

Design doc reference: v3.2_ARCHITECTURE_DESIGN.md §8
Spec reference: architecture.md — Concurrency section
Satisfies: M-016 (concurrency guard, split-brain detection)
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ConcurrencyError(Exception):
    """Base exception for concurrency operations."""


class LockConflictError(ConcurrencyError):
    """Raised when a lock is held by another session."""

    def __init__(self, lock_info: "LockInfo") -> None:
        self.lock_info = lock_info
        super().__init__(
            f"Project locked by session '{lock_info.session_id}' "
            f"(pid={lock_info.pid}, acquired={lock_info.acquired_at})"
        )


class SplitBrainError(ConcurrencyError):
    """Raised when WAL state conflicts with HOT checkpoint.

    This indicates that another session modified the project after our
    last checkpoint. The safe response is to enter RECOVERY.
    """

    def __init__(
        self,
        hot_seq: int,
        wal_seq: int,
        hot_session: str,
        wal_session: str,
    ) -> None:
        self.hot_seq = hot_seq
        self.wal_seq = wal_seq
        self.hot_session = hot_session
        self.wal_session = wal_session
        super().__init__(
            f"Split-brain detected: HOT checkpoint seq={hot_seq} "
            f"(session={hot_session}), WAL max seq={wal_seq} "
            f"(session={wal_session}). Enter RECOVERY."
        )


# ---------------------------------------------------------------------------
# Lock info
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LockInfo:
    """Information stored in the lock file. Immutable."""

    session_id: str
    pid: int
    acquired_at: str

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "pid": self.pid,
            "acquired_at": self.acquired_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LockInfo":
        return cls(
            session_id=d["session_id"],
            pid=d["pid"],
            acquired_at=d["acquired_at"],
        )

    def is_process_alive(self) -> bool:
        """Check if the lock holder's process is still running.

        Uses os.kill(pid, 0) which checks for existence without
        sending a signal. Returns False if the process doesn't exist.
        """
        try:
            os.kill(self.pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False


# ---------------------------------------------------------------------------
# ProjectLock (M-016)
# ---------------------------------------------------------------------------

LOCK_FILENAME = ".rag_kernel.lock"


class ProjectLock:
    """File-based project mutex.

    Ensures only one session can own a project directory at a time.
    The lock file contains JSON with session_id, pid, and timestamp.

    Features:
    - Re-entrant for the same session_id (idempotent acquire)
    - Stale lock detection (dead PID = auto-release)
    - Context manager support
    - Thread-safe internal state

    Usage:
        lock = ProjectLock(Path("/path/to/project/RAG"))
        if lock.acquire("S9"):
            try:
                # ... do work ...
            finally:
                lock.release()

        # Or as context manager:
        with ProjectLock.context(Path("RAG"), "S9") as lock:
            # ... do work ...
    """

    def __init__(self, project_dir: Path) -> None:
        self._project_dir = project_dir
        self._lock_path = project_dir / LOCK_FILENAME
        self._held_session: Optional[str] = None
        self._mutex = threading.Lock()

    @property
    def lock_path(self) -> Path:
        """Path to the lock file."""
        return self._lock_path

    @property
    def is_locked(self) -> bool:
        """Check if a lock file exists on disk."""
        return self._lock_path.exists()

    def acquire(
        self,
        session_id: str,
        *,
        steal_stale: bool = True,
    ) -> bool:
        """Attempt to acquire the project lock.

        Args:
            session_id: Unique session identifier.
            steal_stale: If True (default), automatically reclaim
                locks held by dead processes.

        Returns:
            True if the lock was acquired (or re-acquired by same session).
            False if another active session holds the lock.
        """
        with self._mutex:
            if self._lock_path.exists():
                try:
                    existing = self._read_lock()
                except (json.JSONDecodeError, KeyError, OSError):
                    # Corrupt lock file — reclaim it
                    self._write_lock(session_id)
                    return True

                # Same session — re-entrant acquire
                if existing.session_id == session_id:
                    self._held_session = session_id
                    return True

                # Different session — check if stale
                if steal_stale and not existing.is_process_alive():
                    self._write_lock(session_id)
                    return True

                # Active conflict
                return False

            # No lock file — acquire
            self._write_lock(session_id)
            return True

    def release(self) -> None:
        """Release the project lock by removing the lock file.

        Safe to call even if the lock isn't held.
        """
        with self._mutex:
            self._lock_path.unlink(missing_ok=True)
            self._held_session = None

    def read_lock(self) -> Optional[LockInfo]:
        """Read the current lock info, if any.

        Returns None if no lock file exists.

        Raises:
            ConcurrencyError: If the lock file exists but is corrupt.
        """
        with self._mutex:
            if not self._lock_path.exists():
                return None
            try:
                return self._read_lock()
            except (json.JSONDecodeError, KeyError) as e:
                raise ConcurrencyError(
                    f"Lock file is corrupt: {e}"
                ) from e

    def force_release(self) -> bool:
        """Force-release the lock regardless of who holds it.

        Returns True if a lock file was removed, False if none existed.
        Use for manual recovery only.
        """
        with self._mutex:
            if self._lock_path.exists():
                self._lock_path.unlink()
                self._held_session = None
                return True
            return False

    @classmethod
    def context(
        cls,
        project_dir: Path,
        session_id: str,
        *,
        steal_stale: bool = True,
    ) -> "_ProjectLockContext":
        """Create a context manager that acquires/releases the lock.

        Raises LockConflictError if the lock can't be acquired.

        Usage:
            with ProjectLock.context(Path("RAG"), "S9") as lock:
                # lock is acquired
                pass
            # lock is released
        """
        return _ProjectLockContext(project_dir, session_id, steal_stale)

    # -- Private helpers ----------------------------------------------------

    def _write_lock(self, session_id: str) -> None:
        """Write lock file. Must be called with self._mutex held."""
        info = LockInfo(
            session_id=session_id,
            pid=os.getpid(),
            acquired_at=datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
        )
        self._lock_path.write_text(
            json.dumps(info.to_dict(), indent=2),
            encoding="utf-8",
        )
        self._held_session = session_id

    def _read_lock(self) -> LockInfo:
        """Read and parse lock file. Must be called with self._mutex held."""
        raw = self._lock_path.read_text(encoding="utf-8")
        return LockInfo.from_dict(json.loads(raw))

    def __repr__(self) -> str:
        with self._mutex:
            held = self._held_session or "none"
            return (
                f"ProjectLock(path={self._lock_path}, "
                f"held_by={held})"
            )


class _ProjectLockContext:
    """Context manager wrapper for ProjectLock."""

    def __init__(
        self, project_dir: Path, session_id: str, steal_stale: bool
    ) -> None:
        self._lock = ProjectLock(project_dir)
        self._session_id = session_id
        self._steal_stale = steal_stale

    def __enter__(self) -> ProjectLock:
        if not self._lock.acquire(
            self._session_id, steal_stale=self._steal_stale
        ):
            info = self._lock.read_lock()
            raise LockConflictError(info)
        return self._lock

    def __exit__(self, *args: Any) -> None:
        self._lock.release()


# ---------------------------------------------------------------------------
# Split-brain detection
# ---------------------------------------------------------------------------

def detect_split_brain(
    hot: dict,
    wal_entries: list[dict],
) -> Optional[SplitBrainError]:
    """Check for split-brain: WAL entries from a different session
    that advanced beyond HOT's last checkpoint.

    Args:
        hot: The HOT (RAG_MASTER) dict, expected to contain:
            - meta.last_checkpoint_seq (int): last committed WAL seq
            - meta.session_id (str): session that wrote the checkpoint
        wal_entries: List of WAL entry dicts, each with:
            - seq (int): sequence number
            - session_id (str, optional): originating session

    Returns:
        SplitBrainError if conflict detected, None otherwise.

    This function does NOT raise — the caller decides whether to
    raise or enter RECOVERY based on the return value.
    """
    # Extract HOT checkpoint state
    meta = hot.get("meta", {})
    hot_seq = meta.get("last_checkpoint_seq", 0)
    hot_session = meta.get("session_id", "")

    if not hot_session:
        # No session recorded in HOT — can't detect split-brain
        return None

    if not wal_entries:
        return None

    # Find the highest WAL seq from a DIFFERENT session
    foreign_max_seq = 0
    foreign_session = ""

    for entry in wal_entries:
        entry_session = entry.get("session_id", "")
        entry_seq = entry.get("seq", 0)

        if entry_session and entry_session != hot_session:
            if entry_seq > foreign_max_seq:
                foreign_max_seq = entry_seq
                foreign_session = entry_session

    # Split-brain: foreign session advanced WAL beyond HOT checkpoint
    if foreign_max_seq > hot_seq and foreign_session:
        return SplitBrainError(
            hot_seq=hot_seq,
            wal_seq=foreign_max_seq,
            hot_session=hot_session,
            wal_session=foreign_session,
        )

    return None
