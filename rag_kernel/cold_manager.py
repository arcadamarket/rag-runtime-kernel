"""COLD partition manager for the RAG Runtime Kernel.

Provides lazy-loading access to the COLD archive (RAG_COLD.json).
COLD data is archival — loaded on-demand, never at boot — keeping
HOT boot fast (~4000 tokens) while maintaining full project history.

Key behaviors:
- Lazy loading: partitions are deserialized only on first access.
- Token budget: callers can query estimated token consumption.
- Eviction: partitions can be evicted to free context budget.
- Thread-safe: all reads/writes protected by a lock.
- Crash-safe: writes delegate to persistence.atomic_write_json.

Design doc reference: v3.2_ARCHITECTURE_DESIGN.md §7.4
Spec reference: architecture.md — Memory Architecture (HOT/COLD)

@rag-kernel-manifest
{
  "module": "rag_kernel.cold_manager",
  "capability": "cold_storage",
  "description": "Lazy-loading COLD archive with token budgeting and partition eviction",
  "exports": ["ColdManager", "ColdPartition"],
  "use_when": "Loading archival data (past sessions, historical conflicts, old deliverables)",
  "never_bypass": false
}
Satisfies: M-015 (COLD partition lazy-loading), M-033 (token budget)
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Optional

from rag_kernel.persistence import atomic_write_json


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ColdError(Exception):
    """Base exception for COLD operations."""


class PartitionNotFoundError(ColdError):
    """Raised when a requested partition does not exist."""

    def __init__(self, partition: str, available: list[str]) -> None:
        self.partition = partition
        self.available = available
        super().__init__(
            f"Partition '{partition}' not found. "
            f"Available: {available}"
        )


class ColdFileError(ColdError):
    """Raised when the COLD file cannot be read or parsed."""


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

# Rough heuristic: 1 token ≈ 4 characters of JSON text.
# This matches OpenAI/Anthropic tokenizer averages for structured data.
CHARS_PER_TOKEN = 4


def estimate_tokens(data: Any) -> int:
    """Estimate token count for a JSON-serializable value.

    Uses compact JSON serialization (no extra whitespace) for a
    realistic estimate of what the LLM would actually consume.
    """
    text = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    return len(text) // CHARS_PER_TOKEN


# ---------------------------------------------------------------------------
# ColdManager (M-015, M-033)
# ---------------------------------------------------------------------------

class ColdManager:
    """Lazy-loading manager for the COLD archive.

    The COLD file is a single JSON object where each top-level key is
    a "partition" (e.g., "documents_inventory", "conflict_ledger",
    "session_history"). Partitions are loaded individually on first
    access, keeping memory and token budget low.

    Usage:
        cold = ColdManager(Path("RAG/RAG_COLD.json"))
        inventory = cold.get("documents_inventory")
        print(cold.token_estimate())
        cold.evict("documents_inventory")  # free context budget
    """

    def __init__(self, cold_path: Path) -> None:
        self._path = cold_path
        self._lock = threading.Lock()

        # Index: maps partition name -> True (exists in file).
        # Built from file keys without loading values.
        self._index: dict[str, bool] = {}

        # Cache: partition name -> deserialized value.
        # Only populated on get(). This IS the lazy-load mechanism.
        self._cache: dict[str, Any] = {}

        # Build index on construction (reads keys only, not values).
        self._build_index()

    # -- Public interface ---------------------------------------------------

    @property
    def path(self) -> Path:
        """Path to the COLD file."""
        return self._path

    @property
    def partitions(self) -> list[str]:
        """List of all partition names (loaded or not). Thread-safe."""
        with self._lock:
            return list(self._index.keys())

    @property
    def loaded_partitions(self) -> list[str]:
        """List of currently loaded (cached) partition names. Thread-safe."""
        with self._lock:
            return list(self._cache.keys())

    def get(self, partition: str) -> Any:
        """Get a partition's data, loading it lazily if needed.

        Args:
            partition: Top-level key in the COLD JSON file.

        Returns:
            The deserialized value for that partition.

        Raises:
            PartitionNotFoundError: If the partition doesn't exist.
            ColdFileError: If the file can't be read or parsed.
        """
        with self._lock:
            # Cache hit — fast path
            if partition in self._cache:
                return self._cache[partition]

            # Verify partition exists in index
            if partition not in self._index:
                raise PartitionNotFoundError(
                    partition, list(self._index.keys())
                )

        # Cache miss — must read from file (outside lock for I/O)
        value = self._load_partition(partition)

        with self._lock:
            # Double-check: another thread may have loaded it
            if partition not in self._cache:
                self._cache[partition] = value
            return self._cache[partition]

    def get_all(self) -> dict[str, Any]:
        """Load and return all partitions. Thread-safe.

        Useful for full COLD export via the /cold endpoint.
        """
        for name in self.partitions:
            self.get(name)
        with self._lock:
            return dict(self._cache)

    def is_loaded(self, partition: str) -> bool:
        """Check if a partition is currently in the cache."""
        with self._lock:
            return partition in self._cache

    def has_partition(self, partition: str) -> bool:
        """Check if a partition exists (loaded or not)."""
        with self._lock:
            return partition in self._index

    def evict(self, partition: str) -> bool:
        """Remove a partition from the cache to free token budget.

        The partition remains in the index and can be re-loaded via get().

        Returns:
            True if the partition was evicted, False if it wasn't loaded.
        """
        with self._lock:
            if partition in self._cache:
                del self._cache[partition]
                return True
            return False

    def evict_all(self) -> int:
        """Evict all loaded partitions. Returns count of evicted."""
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            return count

    def token_estimate(self, partition: Optional[str] = None) -> int:
        """Estimate token consumption of loaded partitions.

        Args:
            partition: If given, estimate for just that partition.
                       If None, estimate for all loaded partitions.

        Returns:
            Estimated token count.

        Raises:
            PartitionNotFoundError: If partition is specified but doesn't exist.
        """
        with self._lock:
            if partition is not None:
                if partition not in self._index:
                    raise PartitionNotFoundError(
                        partition, list(self._index.keys())
                    )
                if partition not in self._cache:
                    return 0  # not loaded = 0 tokens consumed
                return estimate_tokens(self._cache[partition])

            return sum(
                estimate_tokens(v) for v in self._cache.values()
            )

    def refresh(self) -> None:
        """Re-read the COLD file and rebuild the index.

        Evicts all cached partitions, forcing re-load on next access.
        Call this after the COLD file has been modified externally.
        """
        with self._lock:
            self._cache.clear()
        self._build_index()

    def update_partition(self, partition: str, data: Any) -> None:
        """Update a partition in the cache and persist to disk.

        If the partition doesn't exist yet, it is created.
        Uses atomic_write_json for crash safety.

        Args:
            partition: Top-level key to update.
            data: New value for the partition.
        """
        # Read the full COLD file
        full = self._read_full()
        full[partition] = data

        # Atomic write back
        atomic_write_json(self._path, full)

        # Update in-memory state
        with self._lock:
            self._index[partition] = True
            self._cache[partition] = data

    def summary(self) -> dict[str, Any]:
        """Return a lightweight summary of COLD state.

        Useful for status endpoints and diagnostics.
        """
        with self._lock:
            total = len(self._index)
            loaded = len(self._cache)
            tokens = sum(
                estimate_tokens(v) for v in self._cache.values()
            )
            return {
                "path": str(self._path),
                "total_partitions": total,
                "loaded_partitions": loaded,
                "partition_names": list(self._index.keys()),
                "loaded_names": list(self._cache.keys()),
                "estimated_tokens": tokens,
            }

    # -- Private helpers ----------------------------------------------------

    def _build_index(self) -> None:
        """Scan the COLD file for top-level keys without loading values.

        If the file doesn't exist, the index is empty (valid for
        first-run scenarios where COLD hasn't been created yet).
        """
        if not self._path.exists():
            with self._lock:
                self._index = {}
            return

        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ColdFileError(
                    f"COLD file must be a JSON object, got {type(data).__name__}"
                )
            with self._lock:
                self._index = {k: True for k in data.keys()}
        except json.JSONDecodeError as e:
            raise ColdFileError(f"COLD file is not valid JSON: {e}") from e
        except OSError as e:
            raise ColdFileError(f"Cannot read COLD file: {e}") from e

    def _load_partition(self, partition: str) -> Any:
        """Load a single partition from the COLD file.

        This reads the entire file (unavoidable with stdlib JSON) but
        only extracts the requested partition. Future optimization:
        could use ijson or split into per-partition files.
        """
        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ColdFileError(f"COLD file is not valid JSON: {e}") from e
        except OSError as e:
            raise ColdFileError(f"Cannot read COLD file: {e}") from e

        if partition not in data:
            # File may have changed since index was built
            raise PartitionNotFoundError(partition, list(data.keys()))

        return data[partition]

    def _read_full(self) -> dict:
        """Read the entire COLD file as a dict."""
        if not self._path.exists():
            return {}
        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ColdFileError(
                    f"COLD file must be a JSON object, got {type(data).__name__}"
                )
            return data
        except json.JSONDecodeError as e:
            raise ColdFileError(f"COLD file is not valid JSON: {e}") from e
        except OSError as e:
            raise ColdFileError(f"Cannot read COLD file: {e}") from e

    # -- Introspection ------------------------------------------------------

    def __repr__(self) -> str:
        with self._lock:
            return (
                f"ColdManager(path={self._path}, "
                f"partitions={len(self._index)}, "
                f"loaded={len(self._cache)})"
            )
