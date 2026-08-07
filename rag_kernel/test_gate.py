"""@rag-kernel-manifest
{
  "module": "rag_kernel.test_gate",
  "capability": "measured_test_gate",
  "description": "The seal MEASURES the suite instead of repeating a typed count: run the suite, parse the summary, stamp meta.test_gate with the count AND the runtime/git HEAD it was measured against, then grade that stamp tri-state (green / red / stale-or-unmeasured) so a cached pass decays by itself (REPORT-TESTS-GATE-UNMEASURED).",
  "exports": ["TestGateError", "TEST_GATE_KEY", "parse_pytest_summary",
              "resolve_repo_root", "run_suite", "read_stamp",
              "set_test_gate_file", "verdict"]
}

Measured test gate — the repair for REPORT-TESTS-GATE-UNMEASURED (S186/S188).

The defect this closes: the ``Tests / Health`` cell of the canonical report was
populated from ``--tests``, a number the *agent* typed. S184 and S185 sealed with
``n/a``; S186 typed ``2294``; S187 typed ``2,409``. None of those numbers was sourced
from a run, and none carried provenance, so "the suite is green" was an assertion the
seal repeated rather than a fact the seal checked.

This module makes the count a **measurement with provenance**, and makes staleness
**decidable**:

* :func:`parse_pytest_summary` is a pure parser over pytest's final summary line.
* :func:`run_suite` executes the suite and returns a structured result.
* :func:`set_test_gate_file` stamps the result into ``meta.test_gate`` atomically,
  together with the runtime version and the git HEAD it was measured against.
* :func:`verdict` compares a stamp with the LIVE head/runtime and returns a tri-state
  ``ok`` plus a human cell: fresh-and-green, fresh-and-red, STALE, or UNMEASURED.

The tri-state matters. ``None`` (unmeasured) must not read as pass — an unmeasured
gate is the S184/S185 failure — and it must not read as fail either, because "we did
not look" is not "it is broken". It renders AMBER and says which it is.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from rag_kernel.drift_store import DriftStoreError, load_hot, _touch_meta
from rag_kernel.persistence import atomic_write_json

__all__ = [
    "TestGateError",
    "TEST_GATE_KEY",
    "parse_pytest_summary",
    "resolve_repo_root",
    "run_suite",
    "read_stamp",
    "set_test_gate_file",
    "verdict",
]


class TestGateError(DriftStoreError):
    """Raised when the suite cannot be located or its output cannot be parsed."""

    # The name begins with "Test", so pytest would try to COLLECT this exception as a
    # test class (and warn that it cannot, having an __init__). Opt out explicitly.
    __test__ = False


TEST_GATE_KEY = "test_gate"

# pytest's terminal summary, e.g.
#   "2409 passed in 168.98s (0:02:48)"
#   "4 failed, 2294 passed in 233.30s"
#   "1 failed, 2 passed, 3 skipped, 1 xfailed, 2 warnings in 4.20s"
_COUNT_RE = re.compile(
    r"(?<![\w.])(\d+)\s+(passed|failed|error|errors|skipped|xfailed|xpassed|deselected)\b"
)
_DURATION_RE = re.compile(r"\bin\s+([\d.]+)s\b")
_NO_TESTS_RE = re.compile(r"\bno tests ran\b", re.IGNORECASE)


def parse_pytest_summary(text: str) -> dict[str, Any]:
    """Parse pytest ``-q`` output into counts. Pure; fail loud on unparseable input.

    Reads the LAST line that carries a count, because pytest prints per-file progress
    above the summary and a short-summary block that also contains the word ``failed``.
    ``no tests ran`` is a real, distinct outcome and is reported as zero-collected
    rather than silently as a pass — that distinction is what caught the RAG/ copy
    having no ``tests/`` directory at all.
    """
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        raise TestGateError("empty pytest output — nothing to parse")

    summary_line = None
    for ln in reversed(lines):
        if _COUNT_RE.search(ln) or _NO_TESTS_RE.search(ln):
            summary_line = ln
            break
    if summary_line is None:
        raise TestGateError(
            "no pytest summary line found in output (last line: %r)" % lines[-1][:120]
        )

    counts = {
        "passed": 0, "failed": 0, "error": 0,
        "skipped": 0, "xfailed": 0, "xpassed": 0, "deselected": 0,
    }
    for n, word in _COUNT_RE.findall(summary_line):
        key = "error" if word in ("error", "errors") else word
        counts[key] = counts.get(key, 0) + int(n)

    collected = (
        counts["passed"] + counts["failed"] + counts["error"]
        + counts["skipped"] + counts["xfailed"] + counts["xpassed"]
    )
    dur = _DURATION_RE.search(summary_line)
    return {
        **counts,
        "collected": collected,
        "no_tests_ran": bool(_NO_TESTS_RE.search(summary_line)) or collected == 0,
        "duration_s": float(dur.group(1)) if dur else None,
        "summary_line": summary_line,
    }


def resolve_repo_root(rag_path: Path | str, hot: Optional[dict] = None) -> Path:
    """Locate the directory that holds the suite, from DECLARED state — never a guess.

    ``meta.reconciliation_docs_root`` already names the git worktree this deployment
    reconciles against; the suite lives there. Resolved relative to the project root
    (the RAG file's grandparent). Fails loud when the resolved path has no ``tests``
    directory, because silently measuring the wrong tree is how a green number gets
    attached to code nobody ran.
    """
    p = Path(rag_path)
    hot = hot if hot is not None else load_hot(p)
    meta = hot.get("meta", {}) if isinstance(hot, dict) else {}
    declared = (meta or {}).get("reconciliation_docs_root")

    candidates: list[Path] = []
    project_root = p.parent.parent
    if declared:
        candidates.append(project_root / str(declared).replace("\\", "/"))
    candidates.append(p.parent)          # the RAG/ deployment itself
    candidates.append(project_root)

    for cand in candidates:
        try:
            if (cand / "tests").is_dir() and (cand / "rag_kernel").is_dir():
                return cand.resolve()
        except OSError:
            continue
    raise TestGateError(
        "cannot locate a suite: none of "
        + ", ".join(str(c) for c in candidates)
        + " holds both tests/ and rag_kernel/ "
        "(meta.reconciliation_docs_root=%r)" % declared
    )


def run_suite(
    repo_root: Path | str,
    *,
    timeout: int = 1800,
    extra_args: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Run the suite under ``repo_root`` and return the parsed result + raw tail.

    Blocking by design: this is the measurement. Callers that must not block should
    launch it detached and use ``rag_kernel wait-for`` (WAIT-PRIMITIVE) — never poll.
    """
    root = Path(repo_root)
    cmd = [sys.executable, "-m", "pytest", "-q", *(extra_args or [])]
    started = time.time()
    try:
        proc = subprocess.run(
            cmd, cwd=str(root), capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired as exc:
        raise TestGateError(
            f"suite exceeded {timeout}s under {root} — measurement abandoned, "
            "nothing stamped"
        ) from exc
    wall = round(time.time() - started, 2)
    raw = (proc.stdout or "") + (proc.stderr or "")
    parsed = parse_pytest_summary(raw)
    parsed["exit_code"] = proc.returncode
    parsed["wall_s"] = wall
    parsed["command"] = " ".join(cmd)
    parsed["repo_root"] = str(root)
    parsed["raw_tail"] = "\n".join(raw.splitlines()[-12:])
    return parsed


def read_stamp(hot: dict) -> Optional[dict]:
    """Return ``meta.test_gate`` from a loaded HOT dict, or ``None``. Pure."""
    meta = hot.get("meta") if isinstance(hot, dict) else None
    if not isinstance(meta, dict):
        return None
    stamp = meta.get(TEST_GATE_KEY)
    return stamp if isinstance(stamp, dict) else None


def set_test_gate_file(
    path: Path | str,
    result: dict,
    *,
    session: str,
    runtime: Optional[str] = None,
    git_head: Optional[str] = None,
    now: Optional[str] = None,
    dry_run: bool = False,
) -> "tuple[dict, bool]":
    """Atomically stamp a measured result into ``meta.test_gate``. -> ``(stamp, wrote)``.

    The stamp records what was measured AND what it was measured against. Without the
    ``git_head`` half a green count survives every subsequent commit, which is exactly
    how a report cell stays green while the code underneath it moves.
    """
    if not session:
        raise TestGateError("--session is required: a measurement must be attributable")
    p = Path(path)
    hot = load_hot(p)
    meta = hot.get("meta")
    if not isinstance(meta, dict):
        raise TestGateError("HOT has no meta object")

    stamp = {
        "passed": int(result.get("passed", 0)),
        "failed": int(result.get("failed", 0)) + int(result.get("error", 0)),
        "skipped": int(result.get("skipped", 0)),
        "collected": int(result.get("collected", 0)),
        "exit_code": int(result.get("exit_code", 0)),
        "duration_s": result.get("duration_s") or result.get("wall_s"),
        "measured_utc": now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "session": session,
        "runtime": runtime,
        "git_head": git_head,
        "repo_root": result.get("repo_root"),
        "command": result.get("command"),
        "summary_line": result.get("summary_line"),
    }
    if dry_run:
        return stamp, False

    meta[TEST_GATE_KEY] = stamp
    _touch_meta(hot, now)
    atomic_write_json(p, hot, mirror_bak=True, guard_side_stores=True)
    return stamp, True


def _fmt(n: int) -> str:
    return f"{n:,}"


def verdict(
    stamp: Optional[dict],
    *,
    live_head: Optional[str] = None,
    live_runtime: Optional[str] = None,
) -> "tuple[Optional[bool], str, str]":
    """Grade a stamp against live facts. -> ``(ok, cell, reason)``.

    ``ok`` is tri-state on purpose:

    * ``None``  — UNMEASURED or STALE: we do not know, and the report must say so
      rather than inheriting the last green number (the S184/S185 failure).
    * ``False`` — measured and red.
    * ``True``  — measured, green, and measured against the code that is live now.
    """
    if not stamp:
        return None, "n/a — UNMEASURED", "no meta.test_gate stamp; run `rag_kernel tests --run`"

    passed = int(stamp.get("passed", 0))
    failed = int(stamp.get("failed", 0))
    collected = int(stamp.get("collected", 0))
    sess = stamp.get("session") or "?"
    head = stamp.get("git_head")
    short = (head or "")[:7]

    if collected == 0:
        return (
            None,
            f"n/a — NO TESTS COLLECTED (measured {sess})",
            "the suite collected zero tests — measured the wrong tree?",
        )

    stale_bits = []
    if live_head and head and live_head[:7] != head[:7]:
        stale_bits.append(f"measured @ {short}, live @ {live_head[:7]}")
    if live_runtime and stamp.get("runtime") and live_runtime != stamp.get("runtime"):
        stale_bits.append(f"measured on {stamp.get('runtime')}, live {live_runtime}")

    if failed:
        cell = f"{_fmt(passed)} passed / {_fmt(failed)} FAILED (measured {sess} @ {short})"
        return False, cell, "the suite is red"
    if stale_bits:
        cell = f"{_fmt(passed)} green — STALE ({'; '.join(stale_bits)})"
        return None, cell, "the code moved since the measurement"
    return (
        True,
        f"{_fmt(passed)} green (measured {sess} @ {short})",
        "measured, green, and current",
    )
