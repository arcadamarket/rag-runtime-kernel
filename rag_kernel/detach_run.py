"""RUN-DETACH-AWAIT (S185) — launch and wait as ONE indivisible operation.

Origin: the clone's own S5 diagnosis, which was correct and is worth preserving
verbatim because it names the defect better than a rule ever did.

  CS view — ``get-command-result`` is callable in any state and returns the same
  "could not be captured properly" for THREE different states: job running,
  capture race, pane blocked. An operation whose return value does not
  discriminate its own states is not a decidable predicate. So the agent
  substitutes judgment, and judgment under latency degrades into acting.

  Pipeline view — launch and wait are two separate agent actions, and between
  them a POLLABLE HANDLE exists. That window is the defect: the same shape as
  the fan-out lesson, where the fix was persisting a marker so there is never an
  ambiguous middle.

Four banked ERROR items (re-run it, kill it, ask about it, poll it) share one
generator: the pollable middle. Writing a fifth rule about discipline would not
have helped — the clone logged "I will stop polling" and then polled ~14 more
times waiting on its own seal, which is the strongest possible evidence that the
affordance, not the intent, is the problem.

So this verb REMOVES THE AFFORDANCE. One call owns launch AND wait. There is no
intermediate handle to poll, because the caller never gets control back until the
job has reached a terminal state. The return value discriminates every state it
can be in — a decidable predicate, not a judgment call:

    DONE      sentinel observed              exit 0
    FAILED    sentinel observed, rc != 0     exit 1
    TIMEOUT   deadline hit, process ALIVE    exit 2   (unobserved, NOT failed)
    DIED      process gone, no sentinel      exit 3   (crashed before writing)

TIMEOUT and DIED are deliberately distinct. "The job is still running" and "the
job vanished" demand opposite responses, and collapsing them is precisely what
made the old handle undecidable.
"""
from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

#: Written by the wrapper AFTER the payload exits, carrying its real exit code.
SENTINEL = "__RAG_RUN_DONE__"


@dataclass(frozen=True)
class RunResult:
    """Terminal state of a detached run. Every field is observed, never inferred."""

    state: str                 # DONE | FAILED | TIMEOUT | DIED
    returncode: "int | None"   # payload rc; None unless the sentinel was seen
    pid: int
    log: Path
    waited_s: float
    tail: str

    @property
    def exit_code(self) -> int:
        return {"DONE": 0, "FAILED": 1, "TIMEOUT": 2, "DIED": 3}[self.state]

    def render(self, *, emit: int = 20) -> str:
        lines = [
            f"[run] state={self.state} rc={self.returncode} pid={self.pid} "
            f"waited={self.waited_s:.1f}s",
            f"[run] log: {self.log}",
        ]
        if self.state == "TIMEOUT":
            lines.append(
                "[run] TIMEOUT means UNOBSERVED, not failed — the process is still "
                "alive. Re-await with a longer --timeout. Do NOT relaunch."
            )
        elif self.state == "DIED":
            lines.append(
                "[run] DIED — the process is gone and never wrote its sentinel. It "
                "crashed or was killed; the tail below is all there is."
            )
        body = self.tail.splitlines()[-emit:] if emit > 0 else self.tail.splitlines()
        if body:
            lines.append("--- tail ---")
            lines.extend("  " + b for b in body)
        return "\n".join(lines)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True


def _read_tail(log: Path, limit: int = 8000) -> str:
    try:
        data = log.read_bytes()
    except OSError:
        return ""
    return data[-limit:].decode("utf-8", errors="replace")


def _parse_sentinel(text: str) -> "int | None":
    """Return the payload's exit code if the sentinel line is present."""
    for line in reversed(text.splitlines()):
        if line.startswith(SENTINEL):
            _, _, rc = line.partition("=")
            try:
                return int(rc.strip())
            except ValueError:
                return 0
    return None


def run_detached_await(
    command: str,
    log: "str | Path",
    *,
    cwd: "str | Path | None" = None,
    timeout: float = 900.0,
    poll_ms: int = 1000,
    shell: str = "/bin/bash",
) -> RunResult:
    """Launch ``command`` detached, then block until it reaches a terminal state.

    The caller gets control back ONCE, with an answer. There is deliberately no
    API to ask "is it done yet?" — that question is what this verb exists to
    delete.
    """
    log_path = Path(log).resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists():
        log_path.unlink()

    # The wrapper writes the sentinel LAST, carrying the payload's real rc. A
    # sentinel that could be written before the payload finishes would recreate
    # the ambiguous middle in a new place.
    # The payload runs in a SUBSHELL. Without it, a payload calling `exit 7`
    # terminates the wrapper before the sentinel line is reached, and a job that
    # ran and failed becomes indistinguishable from a job that vanished — the
    # exact state collapse this verb exists to prevent. Caught by
    # test_nonzero_exit_is_FAILED_not_DONE.
    wrapped = (
        f"( {command}\n )\n"
        f"__rc=$?\n"
        f"echo '{SENTINEL}='\"$__rc\" >> {shlex.quote(str(log_path))}\n"
    )
    with open(log_path, "w", encoding="utf-8") as fh:
        proc = subprocess.Popen(
            [shell, "-c", wrapped],
            cwd=str(cwd) if cwd else None,
            stdout=fh, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,        # detached: survives our own exit
        )

    started = time.time()
    interval = max(poll_ms, 50) / 1000.0
    while True:
        text = _read_tail(log_path)
        rc = _parse_sentinel(text)
        if rc is not None:
            waited = time.time() - started
            return RunResult("DONE" if rc == 0 else "FAILED", rc, proc.pid,
                             log_path, waited, text)
        # proc.poll() REAPS the child. os.kill(pid, 0) alone is not enough: a
        # killed child sits as a zombie and answers "alive" forever, so a DIED job
        # aged into a TIMEOUT — again collapsing two states that demand opposite
        # responses. Caught by test_a_process_that_dies_without_a_sentinel.
        alive = proc.poll() is None and _pid_alive(proc.pid)
        waited = time.time() - started
        if not alive:
            # One last read: the sentinel may have landed between the two checks.
            text = _read_tail(log_path)
            rc = _parse_sentinel(text)
            if rc is not None:
                return RunResult("DONE" if rc == 0 else "FAILED", rc, proc.pid,
                                 log_path, waited, text)
            return RunResult("DIED", None, proc.pid, log_path, waited, text)
        if waited >= timeout:
            return RunResult("TIMEOUT", None, proc.pid, log_path, waited, text)
        time.sleep(interval)


def cmd_run(args) -> int:
    """CLI entry: ``rag_kernel run --detach --await -- <command>``."""
    command = getattr(args, "cmd_argv", None)
    if isinstance(command, list):
        command = " ".join(command)
    command = command or ""
    if not command.strip():
        print("ERROR: nothing to run.", file=sys.stderr)
        return 2

    log = args.log or (Path(args.cwd or ".") / ".boot" / "run.log")
    result = run_detached_await(
        command, log, cwd=args.cwd, timeout=args.timeout,
        poll_ms=args.poll_ms,
    )
    print(result.render(emit=args.emit))
    if args.kill_on_timeout and result.state == "TIMEOUT":
        try:
            os.killpg(os.getpgid(result.pid), signal.SIGTERM)
            print(f"[run] --kill-on-timeout: SIGTERM sent to process group {result.pid}.")
        except OSError as exc:
            print(f"[run] --kill-on-timeout: could not signal ({exc}).", file=sys.stderr)
    return result.exit_code
