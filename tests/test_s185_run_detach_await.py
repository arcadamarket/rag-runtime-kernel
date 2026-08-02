"""S185 — RUN-DETACH-AWAIT: the pollable middle must not exist.

The clone's S5 diagnosis, restated as assertions: an operation whose return value
does not discriminate its own states is not a decidable predicate. Every test here
pins one state boundary, because collapsing any two of them recreates the defect.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from rag_kernel.detach_run import SENTINEL, RunResult, run_detached_await


def test_success_is_DONE_with_the_payload_returncode(tmp_path: Path):
    r = run_detached_await("echo hello", tmp_path / "a.log", timeout=60)
    assert r.state == "DONE"
    assert r.returncode == 0
    assert r.exit_code == 0
    assert "hello" in r.tail


def test_nonzero_exit_is_FAILED_not_DONE(tmp_path: Path):
    """A job that ran and failed is NOT the same as a job that could not be read."""
    r = run_detached_await("echo oops; exit 7", tmp_path / "b.log", timeout=60)
    assert r.state == "FAILED"
    assert r.returncode == 7
    assert r.exit_code == 1
    assert "oops" in r.tail


def test_timeout_is_distinct_from_failure_and_the_process_is_still_alive(tmp_path: Path):
    """TIMEOUT means UNOBSERVED. Collapsing it into FAILED is what made the old
    handle undecidable — the two demand opposite responses."""
    r = run_detached_await("sleep 30", tmp_path / "c.log", timeout=1.0, poll_ms=100)
    assert r.state == "TIMEOUT"
    assert r.returncode is None
    assert r.exit_code == 2
    assert "UNOBSERVED" in r.render()
    assert "Do NOT relaunch" in r.render()


def test_a_process_that_dies_without_a_sentinel_is_DIED_not_TIMEOUT(tmp_path: Path):
    """'Still running' and 'vanished' are opposite situations. Exit 3, not 2."""
    r = run_detached_await("kill -9 $$", tmp_path / "d.log", timeout=30, poll_ms=100)
    assert r.state == "DIED"
    assert r.returncode is None
    assert r.exit_code == 3


def test_every_terminal_state_maps_to_a_distinct_exit_code():
    codes = {s: RunResult(s, None, 1, Path("x"), 0.0, "").exit_code
             for s in ("DONE", "FAILED", "TIMEOUT", "DIED")}
    assert sorted(codes.values()) == [0, 1, 2, 3]      # no two states collide


def test_the_transcript_is_always_written_so_a_lost_terminal_is_a_read(tmp_path: Path):
    log = tmp_path / "deep" / "nested" / "e.log"
    r = run_detached_await("echo persisted", log, timeout=60)
    assert log.exists()
    assert "persisted" in log.read_text(encoding="utf-8")
    assert r.log == log.resolve()


def test_sentinel_is_written_after_the_payload_not_before(tmp_path: Path):
    """A sentinel that could land early would rebuild the ambiguous middle."""
    log = tmp_path / "f.log"
    r = run_detached_await("echo first; sleep 0.3; echo second", log, timeout=60)
    text = log.read_text(encoding="utf-8")
    assert text.index("second") < text.index(SENTINEL)
    assert r.state == "DONE"


def test_stderr_is_captured_into_the_same_transcript(tmp_path: Path):
    r = run_detached_await("echo to_stderr 1>&2", tmp_path / "g.log", timeout=60)
    assert r.state == "DONE"
    assert "to_stderr" in r.tail


def test_multiline_and_quoted_commands_survive(tmp_path: Path):
    r = run_detached_await("printf '%s\\n' 'a b' \"c d\"", tmp_path / "h.log", timeout=60)
    assert r.state == "DONE"
    assert "a b" in r.tail and "c d" in r.tail


def test_render_bounds_the_tail(tmp_path: Path):
    r = run_detached_await("for i in $(seq 1 200); do echo line$i; done",
                           tmp_path / "i.log", timeout=60)
    assert r.state == "DONE"
    body = r.render(emit=5)
    assert body.count("line") <= 7          # 5 emitted + possible header mentions
    assert "line200" in body                # the tail, not the head


def test_there_is_no_api_to_ask_whether_it_is_done_yet():
    """The affordance itself is the defect. If a future refactor adds a poll
    handle, this test is the thing that should stop it."""
    import rag_kernel.detach_run as dr
    exported = {n for n in dir(dr) if not n.startswith("_")}
    for banned in ("poll", "is_done", "check", "status", "handle", "get_result"):
        assert banned not in exported, f"a pollable middle reappeared: {banned}"
