"""Persistence engine for the RAG Runtime Kernel.

Provides crash-safe filesystem operations:
- Atomic writes (tmp -> verify -> rename)
- Write-Ahead Log (append-only JSONL with fsync)
- SHA-256 hash verification
- Backup rotation (.bak on every commit)

All operations use Python stdlib only. Zero external dependencies.

Design doc reference: v3.2_ARCHITECTURE_DESIGN.md section 7
Spec reference: design_principles.md -- Persistence Stack
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class PersistenceError(Exception):
    """Base exception for persistence operations."""


class WriteVerificationError(PersistenceError):
    """Raised when a written file fails post-write hash verification."""

    def __init__(self, path: Path, expected: str, actual: str) -> None:
        self.path = path
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Write verification failed for {path}: "
            f"expected {expected[:16]}..., got {actual[:16]}..."
        )


class HashMismatchError(PersistenceError):
    """Raised when a stored hash does not match the computed hash."""

    def __init__(self, key: str, stored: str, computed: str) -> None:
        self.key = key
        self.stored = stored
        self.computed = computed
        super().__init__(
            f"Hash mismatch for {key}: "
            f"stored {stored[:16]}..., computed {computed[:16]}..."
        )


class WALError(PersistenceError):
    """Raised on WAL I/O failures."""


# ---------------------------------------------------------------------------
# Atomic writes (M-020)
# ---------------------------------------------------------------------------

def atomic_write(path: Path, data: bytes) -> None:
    """Write data to path atomically with post-write verification.

    Sequence: write to .tmp -> verify hash -> backup existing to .bak -> rename.
    The rename is the commit point. Crash at any earlier stage leaves the
    original file intact.
    """
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    bak_path = path.with_suffix(path.suffix + ".bak")

    expected_hash = hashlib.sha256(data).hexdigest()

    # 1. Write to temp file
    tmp_path.write_bytes(data)

    # 2. Verify what was written
    actual_hash = hashlib.sha256(tmp_path.read_bytes()).hexdigest()
    if actual_hash != expected_hash:
        tmp_path.unlink(missing_ok=True)
        raise WriteVerificationError(path, expected_hash, actual_hash)

    # 3. Backup existing file (if any)
    if path.exists():
        shutil.copy2(path, bak_path)

    # 4. Atomic rename (commit point)
    tmp_path.replace(path)


def atomic_write_json(path: Path, obj: Any, indent: int = 2) -> None:
    """Convenience wrapper: serialize obj to JSON, then atomic_write."""
    data = json.dumps(obj, indent=indent, ensure_ascii=False).encode("utf-8")
    atomic_write(path, data)


# ---------------------------------------------------------------------------
# Backup rotation
# ---------------------------------------------------------------------------

def rotate_backup(path: Path) -> Optional[Path]:
    """Copy current file to .bak if it exists. Returns .bak path or None."""
    if not path.exists():
        return None
    bak_path = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, bak_path)
    return bak_path


# ---------------------------------------------------------------------------
# SHA-256 hash computation and verification (M-030)
# ---------------------------------------------------------------------------

def compute_hash(
    data: dict,
    exclude_keys: Optional[set[str]] = None,
) -> str:
    """Compute SHA-256 hash of a dict, excluding specified keys.

    Args:
        data: The dictionary to hash.
        exclude_keys: Keys to exclude from the hash computation.
            Defaults to {"state_hash", "inventory_hash"}.

    Returns:
        Hex-encoded SHA-256 hash string.
    """
    if exclude_keys is None:
        exclude_keys = {"state_hash", "inventory_hash"}

    # Build a filtered copy for hashing
    filtered = {k: v for k, v in data.items() if k not in exclude_keys}

    # Deterministic serialization: sorted keys, compact separators
    canonical = json.dumps(
        filtered, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")

    return hashlib.sha256(canonical).hexdigest()


def verify_hashes(hot: dict) -> list[str]:
    """Verify stored hashes in a HOT dict against computed values.

    Follows the GPT-W003 sentinel rule: empty strings and "PENDING"
    are treated as "not yet computed" and skipped (no error).

    The hash is computed over the entire HOT dict with the hash field
    itself temporarily blanked, regardless of whether it is at the top
    level or nested inside "meta".
    """
    errors: list[str] = []
    skip_sentinels = {"", "PENDING"}

    for key in ("state_hash", "inventory_hash"):
        # Find the stored value (could be top-level or nested in meta)
        stored = _get_nested(hot, ["meta", key]) or hot.get(key, "")

        if stored in skip_sentinels:
            continue

        # Temporarily blank the hash field for computation, then restore.
        old_top = hot.get(key)
        old_meta = None
        if "meta" in hot and isinstance(hot["meta"], dict):
            old_meta = hot["meta"].get(key)
            if key in hot["meta"]:
                hot["meta"][key] = ""
        if key in hot:
            hot[key] = ""

        computed = compute_hash(hot, exclude_keys={key})

        # Restore
        if old_top is not None:
            hot[key] = old_top
        elif key in hot:
            del hot[key]
        if "meta" in hot and isinstance(hot["meta"], dict):
            if old_meta is not None:
                hot["meta"][key] = old_meta

        if stored != computed:
            errors.append(
                f"{key}: stored={stored[:16]}... computed={computed[:16]}..."
            )

    return errors


def _get_nested(d: dict, keys: list[str]) -> Optional[str]:
    """Safely retrieve a nested dict value by key path."""
    current = d
    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return None
    return current if isinstance(current, str) else None


# ---------------------------------------------------------------------------
# Write-Ahead Log (M-022)
# ---------------------------------------------------------------------------

@dataclass
class WALEntry:
    """A single WAL entry. Immutable after creation."""

    seq: int
    timestamp: str
    event: str
    data: dict

    def to_dict(self) -> dict:
        return {
            "seq": self.seq,
            "ts": self.timestamp,
            "event": self.event,
            **self.data,
        }

    def to_json_line(self) -> str:
        """Serialize to a single JSON line (no trailing newline)."""
        return json.dumps(self.to_dict(), separators=(",", ":"), ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: dict) -> "WALEntry":
        """Deserialize from a WAL dict."""
        seq = d.pop("seq")
        ts = d.pop("ts")
        event = d.pop("event")
        return cls(seq=seq, timestamp=ts, event=event, data=d)


class WAL:
    """Write-Ahead Log -- append-only JSONL file with fsync guarantees.

    Usage:
        wal = WAL(Path("WAL.jsonl"))
        wal.open()
        wal.append("TRANSITION", from_state="BOOTING", to_state="READY")
        entries = wal.replay(since=0)
        wal.close()
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: Optional[int] = None
        self._file = None
        self._seq: int = 0

    @property
    def seq(self) -> int:
        """Current sequence number."""
        return self._seq

    def open(self) -> None:
        """Open or create the WAL file for appending.

        If the file already exists, scans it to determine the next
        sequence number (crash recovery: resume from where we left off).
        """
        if self._file is not None:
            return  # Already open

        # Determine starting seq from existing entries
        if self.path.exists():
            self._seq = self._scan_max_seq()

        # Open for appending with line buffering
        self._file = open(self.path, "a", encoding="utf-8")
        self._fd = self._file.fileno()

    def close(self) -> None:
        """Flush, fsync, and close the WAL file."""
        if self._file is not None:
            self._file.flush()
            os.fsync(self._fd)
            self._file.close()
            self._file = None
            self._fd = None

    def append(self, event: str, **kwargs: Any) -> WALEntry:
        """Append an event to the WAL. Flushed+fsynced before return."""
        if self._file is None:
            raise WALError("WAL is not open. Call open() first.")

        self._seq += 1
        entry = WALEntry(
            seq=self._seq,
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            event=event,
            data=kwargs,
        )

        line = entry.to_json_line() + "\n"
        self._file.write(line)
        self._file.flush()
        os.fsync(self._fd)

        return entry

    def replay(self, since: int = 0) -> list[WALEntry]:
        """Read WAL entries with seq > since."""
        entries: list[WALEntry] = []

        if not self.path.exists():
            return entries

        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    entry = WALEntry.from_dict(d)
                    if entry.seq > since:
                        entries.append(entry)
                except (json.JSONDecodeError, KeyError):
                    continue

        return entries

    def truncate(self) -> None:
        """Truncate the WAL file (after successful checkpoint).

        Resets the file to empty but preserves the current sequence number.
        """
        saved_seq = self._seq
        was_open = self._file is not None
        if was_open:
            self.close()

        # Truncate
        self.path.write_text("", encoding="utf-8")

        if was_open:
            self.open()

        # Restore seq -- open() would have scanned empty file and got 0
        self._seq = saved_seq

    def _scan_max_seq(self) -> int:
        """Scan existing WAL to find the highest sequence number."""
        max_seq = 0
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        seq = d.get("seq", 0)
                        if seq > max_seq:
                            max_seq = seq
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass
        return max_seq

    def __enter__(self) -> "WAL":
        self.open()
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        state = "open" if self._file is not None else "closed"
        return f"WAL(path={self.path}, seq={self._seq}, {state})"
