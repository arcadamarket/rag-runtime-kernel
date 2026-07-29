"""WAIT-PRIMITIVE (S176 origin, built S180) -- the sanctioned blocking wait.

Why this module exists
----------------------
Every long job in this project is launched DETACHED to a file (E-081 discipline:
never poll a running command).  That discipline left an obvious hole: once the
job is running, *how does the agent wait for it*?  Until now there were only two
answers and both were wrong --

  1. poll the pane repeatedly   -> E-081 / E-085, three logged recurrences;
  2. sleep in the Cowork sandbox -> E-082b / E-086 / E-089 / E-090, five
     consecutive sessions of banned-transport use.

A discipline with no mechanism is advice.  This module is the mechanism: a
blocking, server-side wait that runs inside the sanctioned transport, returns
the moment a sentinel condition is satisfied, and fails loud on timeout.

Design contract (deliberately narrow)
-------------------------------------
* PURE + STATELESS.  Never reads or writes the RAG, never touches project state,
  has no kernel imports.  It therefore works at session zero, on a freshly
  inited clone, before any RAG exists -- which is exactly when a birth runbook
  needs to wait on a long init.
* DETERMINISTIC STATE MACHINE.  WAITING -> FOUND | TIMEOUT, no third outcome.
  Monotonic clock only (``time.monotonic``); a wall-clock step or a DST shift
  cannot extend or truncate the wait.
* THE MACHINE POLLS, NOT THE AGENT.  The internal poll loop is an implementation
  detail costing zero tool round-trips.  E-081 bans the *agent* burning
  round-trips on a running job; it does not ban a process from watching a file.
* BOUNDED EMISSION.  ``emit_lines`` returns a capped tail of the sentinel file
  on success, so one round-trip yields both "it finished" and "here is what it
  said" -- the round-trip economy Rule 17 asks for.
* FAIL-LOUD.  Timeout is a non-zero exit, never a silent success.

The ``contains`` mode matters more than it looks.  ``> out.txt`` creates the
file at redirect time, so waiting on mere existence races the writer and reports
completion against an empty file.  Waiting for a completion TOKEN the job writes
last (``echo DONE >> log``) is the race-free form, and is what callers should
prefer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

__all__ = [
    "WaitError",
    "WaitOutcome",
    "WaitResult",
    "wait_for",
    "EXIT_FOUND",
    "EXIT_TIMEOUT",
    "EXIT_USAGE",
]

# -- exit codes (stable public contract; scripts branch on these) -------------
EXIT_FOUND = 0
EXIT_TIMEOUT = 1
EXIT_USAGE = 2

# Poll floor: below this the loop burns CPU for no latency win on a /mnt/c
# (9p/DrvFs) mount, where a stat is already ~1ms.
_MIN_POLL_MS = 10
_DEFAULT_POLL_MS = 250

# Hard ceiling on a single wait.  A wait longer than this is a hung job, not a
# slow one, and should surface to the operator rather than pin a pane for hours.
_MAX_TIMEOUT_S = 86_400


class WaitError(ValueError):
    """Invalid wait request -- a usage error, surfaced as EXIT_USAGE.

    Distinct from a timeout: a timeout is a legitimate outcome of a
    well-formed wait, this is a malformed request.
    """


class WaitOutcome:
    """The only two terminal states of the wait state machine."""

    FOUND = "FOUND"
    TIMEOUT = "TIMEOUT"


@dataclass(frozen=True)
class WaitResult:
    """Immutable outcome record.

    ``waited_s`` is measured on the monotonic clock and is the real observed
    latency of the job -- worth logging, since it is the only cheap source of
    truth about how long kernel invocations actually take on this mount.
    """

    outcome: str
    path: Path
    waited_s: float
    polls: int
    timeout_s: float
    contains: Optional[str] = None
    emitted: Optional[List[str]] = None

    @property
    def ok(self) -> bool:
        return self.outcome == WaitOutcome.FOUND

    @property
    def exit_code(self) -> int:
        return EXIT_FOUND if self.ok else EXIT_TIMEOUT

    def to_dict(self) -> dict:
        return {
            "outcome": self.outcome,
            "ok": self.ok,
            "path": str(self.path),
            "waited_s": round(self.waited_s, 3),
            "polls": self.polls,
            "timeout_s": self.timeout_s,
            "contains": self.contains,
            "emitted": self.emitted,
        }

    def render(self) -> str:
        """One-line human summary -- the bounded emission Rule 17 wants."""
        head = (
            f"wait-for: {self.outcome} after {self.waited_s:.1f}s "
            f"({self.polls} polls) -- {self.path}"
        )
        if self.contains is not None:
            head += f" [token: {self.contains!r}]"
        if not self.ok:
            head += (
                f"\n  TIMEOUT at {self.timeout_s:.0f}s. The job is still running or "
                f"died without writing its sentinel. Do NOT re-launch it blindly: "
                f"inspect the pane, then re-wait with a longer --timeout."
            )
        if self.emitted:
            head += "\n  --- tail ---\n" + "\n".join(
                f"  {ln}" for ln in self.emitted
            )
        return head


def _read_text(path: Path) -> Optional[str]:
    """Best-effort read that treats a mid-write file as 'not ready yet'.

    A partially flushed file, a file being replaced, or a permission blip must
    never crash the wait -- they simply mean the condition is not met on this
    poll.  Returning None keeps the state machine in WAITING.
    """
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None


def _condition_met(path: Path, contains: Optional[str]) -> Optional[str]:
    """Evaluate the sentinel condition. Returns the file text on success."""
    if not path.exists():
        return None
    if contains is None:
        # Existence mode. Still read, so --emit has content to return.
        return _read_text(path) or ""
    text = _read_text(path)
    if text is None or contains not in text:
        return None
    return text


def _tail(text: str, n: int) -> List[str]:
    if n <= 0:
        return []
    lines = text.splitlines()
    return lines[-n:]


def wait_for(
    path,
    timeout_s: float,
    *,
    poll_ms: int = _DEFAULT_POLL_MS,
    contains: Optional[str] = None,
    emit_lines: int = 0,
    _sleep=time.sleep,
    _clock=time.monotonic,
) -> WaitResult:
    """Block until ``path`` satisfies the sentinel condition, or time out.

    Args:
        path: sentinel file to watch.  Its parent need not exist yet.
        timeout_s: hard upper bound, measured monotonically.
        poll_ms: internal stat interval.  Costs no tool round-trips.
        contains: if given, the file must also contain this token.  Prefer this
            over bare existence -- shell redirection creates the file before the
            job writes anything, so existence alone races the writer.
        emit_lines: on success, return the last N lines of the file.
        _sleep / _clock: injected for deterministic tests; never pass in prod.

    Returns:
        WaitResult -- FOUND or TIMEOUT.  Never raises on timeout.

    Raises:
        WaitError: malformed request (EXIT_USAGE), never a wait outcome.
    """
    if path is None or str(path) == "":
        raise WaitError("a sentinel path is required")
    path = Path(path)

    try:
        timeout_s = float(timeout_s)
    except (TypeError, ValueError):
        raise WaitError(f"--timeout must be a number, got {timeout_s!r}") from None
    if timeout_s <= 0:
        raise WaitError(f"--timeout must be > 0, got {timeout_s}")
    if timeout_s > _MAX_TIMEOUT_S:
        raise WaitError(
            f"--timeout {timeout_s:.0f}s exceeds the {_MAX_TIMEOUT_S}s ceiling; "
            "a wait that long is a hung job, not a slow one -- surface it instead"
        )

    try:
        poll_ms = int(poll_ms)
    except (TypeError, ValueError):
        raise WaitError(f"--poll-ms must be an integer, got {poll_ms!r}") from None
    if poll_ms < _MIN_POLL_MS:
        raise WaitError(f"--poll-ms must be >= {_MIN_POLL_MS}, got {poll_ms}")

    try:
        emit_lines = int(emit_lines)
    except (TypeError, ValueError):
        raise WaitError(f"--emit must be an integer, got {emit_lines!r}") from None
    if emit_lines < 0:
        raise WaitError(f"--emit must be >= 0, got {emit_lines}")

    if contains is not None and contains == "":
        raise WaitError("--contains must be a non-empty token when given")

    poll_s = poll_ms / 1000.0
    started = _clock()
    polls = 0

    # WAITING loop.  Checked BEFORE the first sleep so an already-satisfied
    # sentinel returns immediately at zero latency -- the common case when the
    # job finished while the agent was composing the wait call.
    while True:
        polls += 1
        text = _condition_met(path, contains)
        if text is not None:
            return WaitResult(
                outcome=WaitOutcome.FOUND,
                path=path,
                waited_s=_clock() - started,
                polls=polls,
                timeout_s=timeout_s,
                contains=contains,
                emitted=_tail(text, emit_lines),
            )

        elapsed = _clock() - started
        if elapsed >= timeout_s:
            return WaitResult(
                outcome=WaitOutcome.TIMEOUT,
                path=path,
                waited_s=elapsed,
                polls=polls,
                timeout_s=timeout_s,
                contains=contains,
                emitted=None,
            )

        # Never overshoot the deadline: the final sleep is clipped so the
        # reported wait cannot exceed the contracted timeout.
        _sleep(min(poll_s, timeout_s - elapsed))
