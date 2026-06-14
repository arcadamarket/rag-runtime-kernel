"""Persistence engine for the RAG Runtime Kernel.

Provides crash-safe filesystem operations:
- Atomic writes (tmp -> verify -> rename)
- Write-Ahead Log (append-only JSONL with fsync)
- SHA-256 hash verification
- Backup rotation (.bak on every commit)

All operations use Python stdlib only. Zero external dependencies.

Design doc reference: v3.2_ARCHITECTURE_DESIGN.md section 7
Spec reference: design_principles.md -- Persistence Stack

@rag-kernel-manifest
{
  "module": "rag_kernel.persistence",
  "capability": "persistence",
  "description": "Crash-safe filesystem: atomic writes, WAL, hash verification, backup rotation",
  "exports": ["atomic_write_json", "WALWriter", "WALReader", "compute_hash", "verify_hash", "DeltaOp", "DeltaCheckpoint", "DeltaCheckpointManager", "delta_apply", "delta_compute"],
  "use_when": "Any write to RAG_MASTER.json, COLD, or WAL — never write these files directly",
  "never_bypass": true
}
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
from typing import Any, List, Optional, Sequence, Tuple


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

def atomic_write(path: Path, data: bytes, *, mirror_bak: bool = False) -> None:
    """Write data to path atomically with post-write verification.

    Sequence: write to .tmp -> verify hash -> backup existing to .bak -> rename.
    The rename is the commit point. Crash at any earlier stage leaves the
    original file intact.

    ``mirror_bak`` (FIX-4 / K6 — parity-mirror contract): the step-3 backup above
    captures the PRIOR file, which is the crash-safety copy that protects the
    write window (default, unchanged). When ``mirror_bak`` is True the ``.bak`` is
    additionally refreshed AFTER the commit rename to a BYTE-IDENTICAL copy of the
    just-committed file, so the backup restores the exact known-good state rather
    than the previous one. This realizes the operator-settled *parity-mirror*
    contract (not rollback-prev). Canonical RAG-state writers — full checkpoint /
    session close, drift_store mutations, drift_render apply — opt in; generic
    writes (COLD, etc.) keep the prior-file crash backup.
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

    # 3. Backup existing file (if any) — prior-file crash copy for the write window
    if path.exists():
        shutil.copy2(path, bak_path)

    # 4. Atomic rename (commit point)
    tmp_path.replace(path)

    # 5. FIX-4 (K6): parity-mirror refresh — .bak := byte-identical copy of HOT.
    if mirror_bak:
        shutil.copy2(path, bak_path)


def atomic_write_json(
    path: Path, obj: Any, indent: int = 2, *, mirror_bak: bool = False
) -> None:
    """Convenience wrapper: serialize obj to JSON, then atomic_write.

    ``mirror_bak`` forwards to :func:`atomic_write` to enforce the FIX-4 / K6
    parity-mirror ``.bak`` contract for canonical RAG-state writes.
    """
    data = json.dumps(obj, indent=indent, ensure_ascii=False).encode("utf-8")
    atomic_write(path, data, mirror_bak=mirror_bak)


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

    def verify_integrity(self) -> list[str]:
        """Replay the WAL and return monotonicity anomalies (empty list == OK).

        The WAL contract is a single monotonic allocator: ``seq[n+1] == seq[n]+1``.
        A duplicate, a gap, or a decrease all break it (the eBay Session-Zero WAL
        recorded two ``seq:3`` and skipped ``seq:4``). This replay self-test is the
        fail-loud check consumed by ``health`` and the drift auditor (FIX-1 / K1);
        it reads the file directly, so it works whether or not the WAL is open and
        self-skips a non-existent WAL (``replay`` returns no entries).
        """
        anomalies: list[str] = []
        prev: Optional[int] = None
        for entry in self.replay(since=0):
            s = entry.seq
            if prev is not None and s != prev + 1:
                if s == prev:
                    anomalies.append(f"duplicate seq {s}")
                elif s < prev:
                    anomalies.append(f"decreasing seq {prev} -> {s}")
                else:
                    anomalies.append(f"gap {prev} -> {s} (skipped {prev + 1})")
            prev = s
        return anomalies

    def __enter__(self) -> "WAL":
        self.open()
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        state = "open" if self._file is not None else "closed"
        return f"WAL(path={self.path}, seq={self._seq}, {state})"


# ---------------------------------------------------------------------------
# Delta Checkpoints (ENH-006)
# ---------------------------------------------------------------------------

class DeltaOp:
    """A single delta operation on a JSON document.

    Operations follow RFC 6902 JSON Patch semantics, using dot-path
    addressing for nested keys (e.g., "meta.state_hash").

    Ops:
        replace — overwrite existing value at path
        add     — insert new key at path (error if exists)
        remove  — delete key at path (error if missing)
    """

    VALID_OPS = frozenset({"replace", "add", "remove"})

    __slots__ = ("path", "op", "value")

    def __init__(self, path: str, op: str, value: Any = None) -> None:
        if op not in self.VALID_OPS:
            raise ValueError(f"Invalid delta op: {op!r}. Must be one of {self.VALID_OPS}")
        if not path:
            raise ValueError("Delta path must be non-empty")
        self.path = path
        self.op = op
        self.value = value

    def to_dict(self) -> dict:
        d: dict = {"path": self.path, "op": self.op}
        if self.op != "remove":
            d["value"] = self.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "DeltaOp":
        return cls(path=d["path"], op=d["op"], value=d.get("value"))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DeltaOp):
            return NotImplemented
        return self.path == other.path and self.op == other.op and self.value == other.value

    def __repr__(self) -> str:
        if self.op == "remove":
            return f"DeltaOp({self.path!r}, {self.op!r})"
        return f"DeltaOp({self.path!r}, {self.op!r}, {self.value!r})"


@dataclass
class DeltaCheckpoint:
    """A delta checkpoint: base sequence + list of changes since that base.

    The base_seq refers to the WAL sequence of the last full checkpoint.
    Applying all deltas to the base state produces the current state.
    """

    base_seq: int
    deltas: list  # list[DeltaOp]
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def to_dict(self) -> dict:
        return {
            "type": "delta",
            "base_seq": self.base_seq,
            "timestamp": self.timestamp,
            "deltas": [d.to_dict() for d in self.deltas],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DeltaCheckpoint":
        return cls(
            base_seq=d["base_seq"],
            deltas=[DeltaOp.from_dict(op) for op in d["deltas"]],
            timestamp=d.get("timestamp", ""),
        )

    @property
    def delta_count(self) -> int:
        return len(self.deltas)


def _resolve_path(obj: dict, path: str) -> Tuple[dict, str]:
    """Walk a dot-path to find the parent dict and final key.

    Given obj={"meta": {"version": "1.0"}} and path="meta.version",
    returns ({"version": "1.0"}, "version").

    Raises KeyError if any intermediate key is missing or not a dict.
    """
    parts = path.split(".")
    current = obj
    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            raise KeyError(f"Path segment {part!r} not found in {path!r}")
        current = current[part]
        if not isinstance(current, dict):
            raise KeyError(f"Path segment {part!r} is not a dict in {path!r}")
    return current, parts[-1]


def delta_apply(base: dict, delta: DeltaCheckpoint) -> dict:
    """Apply a DeltaCheckpoint to a base dict, returning the modified dict.

    Modifies base in-place and returns it.

    Raises KeyError on invalid paths, ValueError on invalid ops.
    """
    for op in delta.deltas:
        parent, key = _resolve_path(base, op.path)

        if op.op == "replace":
            if key not in parent:
                raise KeyError(f"Cannot replace: {op.path!r} does not exist")
            parent[key] = op.value

        elif op.op == "add":
            if key in parent:
                raise KeyError(f"Cannot add: {op.path!r} already exists")
            parent[key] = op.value

        elif op.op == "remove":
            if key not in parent:
                raise KeyError(f"Cannot remove: {op.path!r} does not exist")
            del parent[key]

    return base


def delta_compute(old: dict, new: dict, prefix: str = "") -> List[DeltaOp]:
    """Compute the minimal delta between two dicts.

    Recursively compares old and new, producing DeltaOp entries:
    - Keys in new but not old → add
    - Keys in old but not new → remove
    - Keys in both but with different values → replace (if leaf) or recurse (if both dicts)

    Returns a list of DeltaOp objects. An empty list means the dicts are identical.
    """
    ops: List[DeltaOp] = []

    all_keys = set(old.keys()) | set(new.keys())

    for key in sorted(all_keys):
        path = f"{prefix}.{key}" if prefix else key

        if key not in old:
            # Added in new
            ops.append(DeltaOp(path, "add", new[key]))
        elif key not in new:
            # Removed in new
            ops.append(DeltaOp(path, "remove"))
        elif old[key] != new[key]:
            # Changed — recurse if both are dicts, otherwise replace
            if isinstance(old[key], dict) and isinstance(new[key], dict):
                ops.extend(delta_compute(old[key], new[key], prefix=path))
            else:
                ops.append(DeltaOp(path, "replace", new[key]))

    return ops


class DeltaCheckpointManager:
    """Manages the delta checkpoint lifecycle.

    Tracks how many deltas have been accumulated since the last full
    checkpoint and triggers a full checkpoint when the threshold is reached,
    on session close, or on structural changes.

    Config:
        max_deltas: Maximum deltas before forcing a full checkpoint (default: 10).
    """

    def __init__(self, max_deltas: int = 10) -> None:
        if max_deltas < 1:
            raise ValueError(f"max_deltas must be >= 1, got {max_deltas}")
        self.max_deltas = max_deltas
        self._base_snapshot: Optional[dict] = None
        self._base_seq: int = 0
        self._accumulated: List[DeltaOp] = []
        self._first_full_done: bool = False

    @property
    def delta_count(self) -> int:
        """Number of delta ops accumulated since last full checkpoint."""
        return len(self._accumulated)

    @property
    def needs_full(self) -> bool:
        """True if accumulated deltas have reached the threshold."""
        return self._delta_count_logical >= self.max_deltas

    @property
    def _delta_count_logical(self) -> int:
        """Logical delta count: number of delta_checkpoint() calls, not ops."""
        # We track calls via a separate counter
        return getattr(self, "_call_count", 0)

    def set_base(self, snapshot: dict, seq: int) -> None:
        """Set the base snapshot (deep copy) after a full checkpoint.

        Must be called after every full checkpoint to establish the
        comparison baseline.
        """
        import copy
        self._base_snapshot = copy.deepcopy(snapshot)
        self._base_seq = seq
        self._accumulated = []
        self._call_count = 0

    def compute_delta(self, current: dict) -> Optional[DeltaCheckpoint]:
        """Compute delta between base snapshot and current state.

        Returns None if no changes detected.
        Returns a DeltaCheckpoint with all accumulated changes.
        """
        if self._base_snapshot is None:
            return None

        ops = delta_compute(self._base_snapshot, current)
        if not ops:
            return None

        self._accumulated = ops
        self._call_count = getattr(self, "_call_count", 0) + 1

        return DeltaCheckpoint(
            base_seq=self._base_seq,
            deltas=ops,
        )

    def should_full_checkpoint(self, is_closing: bool = False) -> bool:
        """Determine if a full checkpoint is required.

        Full checkpoint triggers:
        1. No base snapshot exists (first checkpoint)
        2. First checkpoint after boot (before any full has been done)
        3. Delta count >= max_deltas threshold
        4. Session is closing (always full on close)
        """
        if self._base_snapshot is None:
            return True
        if not self._first_full_done:
            return True
        if is_closing:
            return True
        return self.needs_full

    def reset(self) -> None:
        """Clear all state. Called on full checkpoint completion."""
        self._base_snapshot = None
        self._base_seq = 0
        self._accumulated = []
        self._call_count = 0
        self._first_full_done = False
