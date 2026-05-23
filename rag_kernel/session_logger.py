"""Universal session logger for the RAG Runtime Kernel.

Structured JSONL observability module. Captures state transitions, I/O ops,
errors, recoveries, RAG mutations, and arbitrary events. Output is
self-contained: a log file dropped to Claude should be fully interpretable
without additional context.

NOT project-specific. NOT tied to any spec version. This is an OS-level
diagnostic tool that any rag_kernel user can enable for debug/patch/release
cycles.

Zero external dependencies. Python 3.10+ standard library only.

@rag-kernel-manifest
{
  "module": "rag_kernel.session_logger",
  "capability": "session_logging",
  "description": "Structured JSONL session logger — universal observability for debug/patch/release cycles",
  "exports": ["SessionLogger", "LogLevel", "SessionLogEntry", "load_session_log", "summarize_session_log"],
  "use_when": "Enable at session boot for full observability. Drop log file to Claude for automated diagnosis.",
  "never_bypass": false
}
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Sequence


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_LOG_DIR = "RAG"
LOG_FILE_PREFIX = "session_log_"
LOG_FILE_EXT = ".jsonl"


# ---------------------------------------------------------------------------
# Log Levels
# ---------------------------------------------------------------------------

class LogLevel(Enum):
    """Severity levels for session log entries.

    Ordered by severity: DEBUG < INFO < WARN < ERROR < FATAL.
    """
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"
    FATAL = "FATAL"

    def __ge__(self, other: "LogLevel") -> bool:
        order = list(LogLevel)
        return order.index(self) >= order.index(other)

    def __gt__(self, other: "LogLevel") -> bool:
        order = list(LogLevel)
        return order.index(self) > order.index(other)

    def __le__(self, other: "LogLevel") -> bool:
        order = list(LogLevel)
        return order.index(self) <= order.index(other)

    def __lt__(self, other: "LogLevel") -> bool:
        order = list(LogLevel)
        return order.index(self) < order.index(other)


# ---------------------------------------------------------------------------
# Event Categories
# ---------------------------------------------------------------------------

class EventCategory(Enum):
    """Broad categories for log events.

    Used for filtering and analysis. Each log entry belongs to exactly one
    category.
    """
    STATE = "state"           # State machine transitions
    IO = "io"                 # File read/write/delete operations
    RAG = "rag"               # RAG mutations (HOT/COLD writes)
    CHECKPOINT = "checkpoint" # Checkpoint operations (full/delta)
    ERROR = "error"           # Errors and exceptions
    RECOVERY = "recovery"     # Recovery operations
    LIFECYCLE = "lifecycle"   # Session boot/close/init
    TOOL = "tool"             # Tool/command invocations
    VALIDATION = "validation" # Schema/proposal validation
    CUSTOM = "custom"         # User-defined events


# ---------------------------------------------------------------------------
# Log Entry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SessionLogEntry:
    """A single session log entry. Immutable after creation.

    Every field is self-describing so a log file can be interpreted
    without external context.
    """
    seq: int                    # Monotonic sequence number within session
    timestamp: str              # ISO 8601 UTC timestamp
    session_id: str             # Session identifier
    level: str                  # LogLevel value
    category: str               # EventCategory value
    event: str                  # Short event name (e.g., "state_transition")
    message: str                # Human-readable description
    data: dict                  # Structured payload (event-specific)
    duration_ms: Optional[float] = None  # Operation duration if timed
    error: Optional[dict] = None         # Error details if applicable

    def to_dict(self) -> dict:
        """Serialize to a flat dict suitable for JSON."""
        d = {
            "seq": self.seq,
            "ts": self.timestamp,
            "sid": self.session_id,
            "level": self.level,
            "cat": self.category,
            "event": self.event,
            "msg": self.message,
            "data": self.data,
        }
        if self.duration_ms is not None:
            d["dur_ms"] = self.duration_ms
        if self.error is not None:
            d["error"] = self.error
        return d

    def to_json_line(self) -> str:
        """Serialize to a compact JSON line (no trailing newline)."""
        return json.dumps(
            self.to_dict(), separators=(",", ":"), ensure_ascii=False
        )

    @classmethod
    def from_dict(cls, d: dict) -> "SessionLogEntry":
        """Deserialize from a log dict."""
        return cls(
            seq=d["seq"],
            timestamp=d["ts"],
            session_id=d["sid"],
            level=d["level"],
            category=d["cat"],
            event=d["event"],
            message=d["msg"],
            data=d.get("data", {}),
            duration_ms=d.get("dur_ms"),
            error=d.get("error"),
        )


# ---------------------------------------------------------------------------
# Session Logger
# ---------------------------------------------------------------------------

class SessionLogger:
    """Structured JSONL session logger.

    Usage:
        logger = SessionLogger(
            session_id="S23",
            log_dir=Path("RAG"),
        )
        logger.open()

        # Log events
        logger.state_transition("BOOTING", "READY")
        logger.io_operation("read", Path("RAG_MASTER.json"), size=4096)
        logger.error("Hash mismatch", category="validation", exc=some_exception)

        # Timed operations
        with logger.timed("checkpoint", category=EventCategory.CHECKPOINT):
            do_checkpoint()

        logger.close()

    Log file: RAG/session_log_<session_id>.jsonl
    """

    def __init__(
        self,
        session_id: str,
        log_dir: Optional[Path] = None,
        min_level: LogLevel = LogLevel.DEBUG,
        log_filename: Optional[str] = None,
    ) -> None:
        self.session_id = session_id
        self.log_dir = Path(log_dir) if log_dir else Path(DEFAULT_LOG_DIR)
        self.min_level = min_level
        self._seq: int = 0
        self._fd: Optional[int] = None
        self._file = None
        self._closed = False
        self._boot_time: Optional[str] = None

        # Allow custom filename, otherwise derive from session_id
        if log_filename:
            self._log_path = self.log_dir / log_filename
        else:
            safe_id = session_id.replace("/", "_").replace("\\", "_")
            self._log_path = self.log_dir / f"{LOG_FILE_PREFIX}{safe_id}{LOG_FILE_EXT}"

    @property
    def log_path(self) -> Path:
        """Path to the current log file."""
        return self._log_path

    @property
    def seq(self) -> int:
        """Current sequence number."""
        return self._seq

    @property
    def is_open(self) -> bool:
        """Whether the logger is currently open for writing."""
        return self._file is not None and not self._closed

    def open(self) -> None:
        """Open the log file for appending.

        If the file already exists, scans it to resume the sequence counter.
        Writes a session_start header entry.
        """
        if self._file is not None:
            return  # Already open

        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Resume seq from existing entries
        if self._log_path.exists():
            self._seq = self._scan_max_seq()

        self._file = open(self._log_path, "a", encoding="utf-8")
        self._fd = self._file.fileno()
        self._closed = False

        self._boot_time = _utc_now()
        self._append_entry(
            level=LogLevel.INFO,
            category=EventCategory.LIFECYCLE,
            event="session_start",
            message=f"Session {self.session_id} started",
            data={
                "session_id": self.session_id,
                "min_level": self.min_level.value,
                "log_path": str(self._log_path),
            },
        )

    def close(self) -> None:
        """Write session_end entry, flush, fsync, and close."""
        if self._file is None or self._closed:
            return

        end_time = _utc_now()
        # total_entries = entries logged before session_end (not counting end marker itself)
        self._append_entry(
            level=LogLevel.INFO,
            category=EventCategory.LIFECYCLE,
            event="session_end",
            message=f"Session {self.session_id} ended",
            data={
                "session_id": self.session_id,
                "total_entries": self._seq,
                "boot_time": self._boot_time,
                "end_time": end_time,
            },
        )

        self._file.flush()
        os.fsync(self._fd)
        self._file.close()
        self._file = None
        self._fd = None
        self._closed = True

    # -- Convenience logging methods ----------------------------------------

    def state_transition(
        self,
        from_state: str,
        to_state: str,
        trigger: str = "",
        **extra: Any,
    ) -> SessionLogEntry:
        """Log a state machine transition."""
        return self._append_entry(
            level=LogLevel.INFO,
            category=EventCategory.STATE,
            event="state_transition",
            message=f"{from_state} -> {to_state}",
            data={"from": from_state, "to": to_state, "trigger": trigger, **extra},
        )

    def io_operation(
        self,
        op: str,
        path: Path | str,
        size: Optional[int] = None,
        duration_ms: Optional[float] = None,
        success: bool = True,
        **extra: Any,
    ) -> SessionLogEntry:
        """Log a file I/O operation (read, write, delete, copy, rename)."""
        data: dict[str, Any] = {"op": op, "path": str(path), "success": success, **extra}
        if size is not None:
            data["size_bytes"] = size
        return self._append_entry(
            level=LogLevel.INFO if success else LogLevel.ERROR,
            category=EventCategory.IO,
            event=f"io_{op}",
            message=f"{op} {path}" + (f" ({size}B)" if size else ""),
            data=data,
            duration_ms=duration_ms,
        )

    def rag_mutation(
        self,
        target: str,
        mutation_type: str,
        fields: Optional[list[str]] = None,
        **extra: Any,
    ) -> SessionLogEntry:
        """Log a RAG mutation (HOT/COLD write, field update)."""
        data: dict[str, Any] = {
            "target": target,
            "mutation_type": mutation_type,
            **extra,
        }
        if fields:
            data["fields"] = fields
        return self._append_entry(
            level=LogLevel.INFO,
            category=EventCategory.RAG,
            event="rag_mutation",
            message=f"{mutation_type} on {target}",
            data=data,
        )

    def checkpoint(
        self,
        checkpoint_type: str,
        seq: Optional[int] = None,
        duration_ms: Optional[float] = None,
        **extra: Any,
    ) -> SessionLogEntry:
        """Log a checkpoint operation (full or delta)."""
        data: dict[str, Any] = {"type": checkpoint_type, **extra}
        if seq is not None:
            data["checkpoint_seq"] = seq
        return self._append_entry(
            level=LogLevel.INFO,
            category=EventCategory.CHECKPOINT,
            event="checkpoint",
            message=f"{checkpoint_type} checkpoint" + (f" seq={seq}" if seq else ""),
            data=data,
            duration_ms=duration_ms,
        )

    def error(
        self,
        message: str,
        category: str | EventCategory = EventCategory.ERROR,
        exc: Optional[Exception] = None,
        **extra: Any,
    ) -> SessionLogEntry:
        """Log an error."""
        error_data = None
        if exc is not None:
            error_data = {
                "type": type(exc).__name__,
                "message": str(exc),
            }
        cat = category if isinstance(category, EventCategory) else EventCategory(category)
        return self._append_entry(
            level=LogLevel.ERROR,
            category=cat,
            event="error",
            message=message,
            data=extra,
            error=error_data,
        )

    def warning(self, message: str, **extra: Any) -> SessionLogEntry:
        """Log a warning."""
        return self._append_entry(
            level=LogLevel.WARN,
            category=EventCategory.CUSTOM,
            event="warning",
            message=message,
            data=extra,
        )

    def info(self, message: str, category: EventCategory = EventCategory.CUSTOM, **extra: Any) -> SessionLogEntry:
        """Log an informational event."""
        return self._append_entry(
            level=LogLevel.INFO,
            category=category,
            event="info",
            message=message,
            data=extra,
        )

    def debug(self, message: str, **extra: Any) -> SessionLogEntry:
        """Log a debug event."""
        return self._append_entry(
            level=LogLevel.DEBUG,
            category=EventCategory.CUSTOM,
            event="debug",
            message=message,
            data=extra,
        )

    def tool_invocation(
        self,
        tool: str,
        command: str = "",
        args: Optional[dict] = None,
        result: Optional[str] = None,
        success: bool = True,
        duration_ms: Optional[float] = None,
        **extra: Any,
    ) -> SessionLogEntry:
        """Log a tool or CLI command invocation."""
        data: dict[str, Any] = {"tool": tool, "success": success, **extra}
        if command:
            data["command"] = command
        if args:
            data["args"] = args
        if result:
            data["result"] = result
        return self._append_entry(
            level=LogLevel.INFO if success else LogLevel.ERROR,
            category=EventCategory.TOOL,
            event="tool_invocation",
            message=f"{tool}" + (f" {command}" if command else ""),
            data=data,
            duration_ms=duration_ms,
        )

    def validation(
        self,
        target: str,
        passed: bool,
        errors: Optional[list[str]] = None,
        **extra: Any,
    ) -> SessionLogEntry:
        """Log a validation result (schema, proposal, hash)."""
        data: dict[str, Any] = {"target": target, "passed": passed, **extra}
        if errors:
            data["errors"] = errors
        return self._append_entry(
            level=LogLevel.INFO if passed else LogLevel.WARN,
            category=EventCategory.VALIDATION,
            event="validation",
            message=f"Validation {'PASSED' if passed else 'FAILED'}: {target}",
            data=data,
        )

    def recovery(
        self,
        strategy: str,
        success: bool,
        **extra: Any,
    ) -> SessionLogEntry:
        """Log a recovery operation."""
        return self._append_entry(
            level=LogLevel.WARN if success else LogLevel.ERROR,
            category=EventCategory.RECOVERY,
            event="recovery",
            message=f"Recovery ({strategy}): {'success' if success else 'failed'}",
            data={"strategy": strategy, "success": success, **extra},
        )

    # -- Timed context manager ----------------------------------------------

    class _TimedContext:
        """Context manager for timed operations."""

        def __init__(
            self,
            logger: "SessionLogger",
            event: str,
            category: EventCategory,
            level: LogLevel,
            data: dict,
        ) -> None:
            self._logger = logger
            self._event = event
            self._category = category
            self._level = level
            self._data = data
            self._start: float = 0.0
            self.entry: Optional[SessionLogEntry] = None

        def __enter__(self) -> "SessionLogger._TimedContext":
            self._start = time.monotonic()
            return self

        def __exit__(self, exc_type, exc_val, exc_tb) -> None:
            duration_ms = (time.monotonic() - self._start) * 1000
            error_data = None
            if exc_val is not None:
                error_data = {
                    "type": type(exc_val).__name__,
                    "message": str(exc_val),
                }
            self.entry = self._logger._append_entry(
                level=LogLevel.ERROR if exc_val else self._level,
                category=self._category,
                event=self._event,
                message=f"{self._event} completed in {duration_ms:.1f}ms"
                if not exc_val
                else f"{self._event} failed after {duration_ms:.1f}ms",
                data=self._data,
                duration_ms=duration_ms,
                error=error_data,
            )
            # Don't suppress exceptions
            return False

    def timed(
        self,
        event: str,
        category: EventCategory = EventCategory.CUSTOM,
        level: LogLevel = LogLevel.INFO,
        **data: Any,
    ) -> _TimedContext:
        """Context manager that logs an event with its duration.

        Usage:
            with logger.timed("checkpoint", category=EventCategory.CHECKPOINT):
                do_checkpoint()
        """
        return self._TimedContext(self, event, category, level, data)

    # -- Internal -----------------------------------------------------------

    def _append_entry(
        self,
        level: LogLevel,
        category: EventCategory,
        event: str,
        message: str,
        data: dict,
        duration_ms: Optional[float] = None,
        error: Optional[dict] = None,
    ) -> SessionLogEntry:
        """Create and append a log entry. Flushed+fsynced before return."""
        if level < self.min_level:
            # Below threshold — create entry but don't write
            self._seq += 1
            return SessionLogEntry(
                seq=self._seq,
                timestamp=_utc_now(),
                session_id=self.session_id,
                level=level.value,
                category=category.value,
                event=event,
                message=message,
                data=data,
                duration_ms=duration_ms,
                error=error,
            )

        self._seq += 1
        entry = SessionLogEntry(
            seq=self._seq,
            timestamp=_utc_now(),
            session_id=self.session_id,
            level=level.value,
            category=category.value,
            event=event,
            message=message,
            data=data,
            duration_ms=duration_ms,
            error=error,
        )

        if self._file is not None:
            self._file.write(entry.to_json_line() + "\n")
            self._file.flush()
            os.fsync(self._fd)

        return entry

    def _scan_max_seq(self) -> int:
        """Scan existing log file to find the highest sequence number."""
        max_seq = 0
        try:
            with open(self._log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        if d.get("seq", 0) > max_seq:
                            max_seq = d["seq"]
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass
        return max_seq


# ---------------------------------------------------------------------------
# Log Analysis Utilities
# ---------------------------------------------------------------------------

def load_session_log(path: Path | str) -> list[SessionLogEntry]:
    """Load all entries from a session log file.

    Args:
        path: Path to a .jsonl session log file.

    Returns:
        List of SessionLogEntry objects, ordered by sequence number.

    Raises:
        FileNotFoundError: If the log file doesn't exist.
    """
    path = Path(path)
    entries: list[SessionLogEntry] = []

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                entries.append(SessionLogEntry.from_dict(d))
            except (json.JSONDecodeError, KeyError):
                continue  # Skip malformed lines

    entries.sort(key=lambda e: e.seq)
    return entries


def summarize_session_log(entries: Sequence[SessionLogEntry]) -> dict[str, Any]:
    """Produce a summary dict from a list of session log entries.

    The summary is designed for LLM consumption — structured enough to
    be machine-parsed, readable enough for a human to scan.

    Returns a dict with:
    - session_id: str
    - entry_count: int
    - time_range: {start, end}
    - level_counts: {DEBUG: n, INFO: n, ...}
    - category_counts: {state: n, io: n, ...}
    - errors: list of error entries (full)
    - warnings: list of warning entries (full)
    - state_transitions: list of {from, to, ts}
    - io_summary: {reads: n, writes: n, deletes: n, ...}
    - checkpoints: list of {type, seq, ts}
    - duration_ms: total session duration if start/end present
    """
    if not entries:
        return {"session_id": "", "entry_count": 0}

    session_id = entries[0].session_id
    level_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    errors: list[dict] = []
    warnings: list[dict] = []
    state_transitions: list[dict] = []
    io_ops: dict[str, int] = {}
    checkpoints: list[dict] = []
    timestamps: list[str] = []

    for entry in entries:
        # Level counts
        level_counts[entry.level] = level_counts.get(entry.level, 0) + 1

        # Category counts
        category_counts[entry.category] = category_counts.get(entry.category, 0) + 1

        # Timestamps
        timestamps.append(entry.timestamp)

        # Errors
        if entry.level == LogLevel.ERROR.value:
            errors.append(entry.to_dict())

        # Warnings
        if entry.level == LogLevel.WARN.value:
            warnings.append(entry.to_dict())

        # State transitions
        if entry.event == "state_transition":
            state_transitions.append({
                "from": entry.data.get("from", ""),
                "to": entry.data.get("to", ""),
                "ts": entry.timestamp,
            })

        # I/O ops
        if entry.category == EventCategory.IO.value:
            op = entry.data.get("op", "unknown")
            io_ops[op] = io_ops.get(op, 0) + 1

        # Checkpoints
        if entry.event == "checkpoint":
            checkpoints.append({
                "type": entry.data.get("type", "unknown"),
                "seq": entry.data.get("checkpoint_seq"),
                "ts": entry.timestamp,
            })

    time_range = {}
    if timestamps:
        time_range = {"start": timestamps[0], "end": timestamps[-1]}

    return {
        "session_id": session_id,
        "entry_count": len(entries),
        "time_range": time_range,
        "level_counts": level_counts,
        "category_counts": category_counts,
        "errors": errors,
        "warnings": warnings,
        "state_transitions": state_transitions,
        "io_summary": io_ops,
        "checkpoints": checkpoints,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc_now() -> str:
    """ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
