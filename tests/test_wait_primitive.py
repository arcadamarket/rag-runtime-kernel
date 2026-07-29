"""WAIT-PRIMITIVE — the sanctioned blocking wait (S176 origin, built S180).

Covers the design contract in rag_kernel.wait_primitive:
  * outcomes: the state machine has exactly two terminal states, FOUND and
    TIMEOUT, and never a third
  * zero-latency hit: an already-satisfied sentinel returns on the first poll
    without sleeping (the common case — the job finished while the agent was
    composing the wait)
  * contains-mode: a file that exists but lacks the completion token is NOT
    done; this is the redirection race (`> out.txt` creates the file before the
    job writes a byte) that makes existence-mode unsafe for detached jobs
  * timeout: fail-loud, non-zero, never a silent success; the reported wait
    never overshoots the contracted timeout
  * monotonic clocking: driven by an injected clock, so the test is
    deterministic and takes no wall-clock time
  * usage errors are distinct from timeouts (EXIT_USAGE vs EXIT_TIMEOUT) — a
    malformed request must never be mistakable for a job that ran and failed
  * robustness: an unreadable/mid-write file keeps the machine WAITING rather
    than crashing
  * bounded emission: --emit returns a capped tail, so one round-trip yields
    completion AND result (Rule 17)
  * CLI: wait-for registration, exit codes, JSON output, no state written
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rag_kernel.__main__ import main
from rag_kernel.wait_primitive import (
    EXIT_FOUND,
    EXIT_TIMEOUT,
    EXIT_USAGE,
    WaitError,
    WaitOutcome,
    wait_for,
)


# --------------------------------------------------------------------------- #
# A deterministic fake clock. Time advances ONLY when the code under test
# sleeps, so a "60 second" timeout costs the suite nothing and the assertions
# are exact rather than tolerance-based.
# --------------------------------------------------------------------------- #
class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def _wait(path, timeout, clock, **kw):
    return wait_for(path, timeout, _sleep=clock.sleep, _clock=clock, **kw)


# --------------------------------------------------------------------------- #
# FOUND
# --------------------------------------------------------------------------- #
def test_existing_sentinel_returns_immediately_without_sleeping(tmp_path, clock):
    sentinel = tmp_path / "done.txt"
    sentinel.write_text("finished\n", encoding="utf-8")

    result = _wait(sentinel, 60, clock)

    assert result.outcome == WaitOutcome.FOUND
    assert result.ok is True
    assert result.exit_code == EXIT_FOUND
    assert result.polls == 1
    assert result.waited_s == 0.0
    # The condition is checked BEFORE the first sleep: an already-finished job
    # must cost zero latency.
    assert clock.sleeps == []


def test_sentinel_appearing_midway_is_detected(tmp_path, clock):
    sentinel = tmp_path / "late.txt"
    appear_after = 3

    calls = {"n": 0}
    real_sleep = clock.sleep

    def sleep_then_maybe_create(seconds: float) -> None:
        real_sleep(seconds)
        calls["n"] += 1
        if calls["n"] == appear_after:
            sentinel.write_text("RC=0\n", encoding="utf-8")

    result = wait_for(sentinel, 60, poll_ms=250, _sleep=sleep_then_maybe_create,
                      _clock=clock)

    assert result.outcome == WaitOutcome.FOUND
    assert result.polls == appear_after + 1
    assert result.waited_s == pytest.approx(appear_after * 0.25)


def test_contains_mode_ignores_an_empty_file(tmp_path, clock):
    """The redirection race, which is the whole reason --contains exists.

    `cmd > out.txt` creates out.txt at redirect time. Existence-mode would
    call the job done instantly and read an empty file; contains-mode waits
    for the token the job writes LAST.
    """
    sentinel = tmp_path / "job.log"
    sentinel.write_text("", encoding="utf-8")  # created by the redirect

    result = _wait(sentinel, 1.0, clock, contains="CHAIN_DONE", poll_ms=250)
    assert result.outcome == WaitOutcome.TIMEOUT

    # Existence-mode would have (wrongly) succeeded on the same file.
    clock2 = FakeClock()
    assert _wait(sentinel, 1.0, clock2).outcome == WaitOutcome.FOUND


def test_contains_mode_matches_token_written_last(tmp_path, clock):
    sentinel = tmp_path / "job.log"
    sentinel.write_text("step 1\nstep 2\nCHAIN_DONE\n", encoding="utf-8")

    result = _wait(sentinel, 60, clock, contains="CHAIN_DONE")

    assert result.outcome == WaitOutcome.FOUND
    assert result.contains == "CHAIN_DONE"


# --------------------------------------------------------------------------- #
# TIMEOUT — fail-loud, and never overshooting the contract
# --------------------------------------------------------------------------- #
def test_missing_sentinel_times_out_loudly(tmp_path, clock):
    result = _wait(tmp_path / "never.txt", 2.0, clock, poll_ms=500)

    assert result.outcome == WaitOutcome.TIMEOUT
    assert result.ok is False
    assert result.exit_code == EXIT_TIMEOUT
    assert result.exit_code != EXIT_FOUND, "a timeout must never read as success"
    assert result.emitted is None


def test_wait_never_overshoots_the_contracted_timeout(tmp_path, clock):
    """A 700ms poll against a 1.0s timeout must stop at 1.0s, not 1.4s.

    The final sleep is clipped to the remaining budget. Without the clip, a
    caller's deadline is silently extended by up to one poll interval — which
    is exactly the kind of quiet contract violation the kernel is built to
    refuse.
    """
    result = _wait(tmp_path / "never.txt", 1.0, clock, poll_ms=700)

    assert result.outcome == WaitOutcome.TIMEOUT
    assert result.waited_s == pytest.approx(1.0)
    assert result.waited_s <= 1.0
    assert sum(clock.sleeps) == pytest.approx(1.0)


def test_a_parent_directory_that_does_not_exist_is_just_not_ready_yet(tmp_path, clock):
    """Not a crash: the job may create the directory as its first act."""
    result = _wait(tmp_path / "nodir" / "out.txt", 0.5, clock, poll_ms=100)
    assert result.outcome == WaitOutcome.TIMEOUT


def test_unreadable_file_keeps_waiting_rather_than_crashing(tmp_path, clock, monkeypatch):
    sentinel = tmp_path / "locked.txt"
    sentinel.write_text("data", encoding="utf-8")

    def boom(*_a, **_kw):
        raise OSError("mid-write / locked by the writer")

    monkeypatch.setattr(Path, "read_text", boom)

    result = _wait(sentinel, 0.5, clock, contains="DONE", poll_ms=100)
    assert result.outcome == WaitOutcome.TIMEOUT  # waited, did not raise


# --------------------------------------------------------------------------- #
# Usage errors — a malformed request is NOT a wait outcome
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "kwargs, timeout",
    [
        ({}, 0),
        ({}, -5),
        ({}, "abc"),
        ({"poll_ms": 0}, 10),
        ({"poll_ms": 5}, 10),
        ({"poll_ms": "x"}, 10),
        ({"emit_lines": -1}, 10),
        ({"contains": ""}, 10),
    ],
)
def test_malformed_requests_raise_waiterror(tmp_path, clock, kwargs, timeout):
    with pytest.raises(WaitError):
        _wait(tmp_path / "x.txt", timeout, clock, **kwargs)


def test_timeout_ceiling_is_refused(tmp_path, clock):
    """A multi-day wait is a hung job. Surface it, do not pin a pane on it."""
    with pytest.raises(WaitError):
        _wait(tmp_path / "x.txt", 86_401, clock)


def test_empty_path_is_refused(clock):
    with pytest.raises(WaitError):
        _wait("", 10, clock)


# --------------------------------------------------------------------------- #
# Bounded emission (Rule 17)
# --------------------------------------------------------------------------- #
def test_emit_returns_a_capped_tail(tmp_path, clock):
    sentinel = tmp_path / "out.txt"
    sentinel.write_text("\n".join(f"line{i}" for i in range(100)), encoding="utf-8")

    result = _wait(sentinel, 60, clock, emit_lines=3)

    assert result.emitted == ["line97", "line98", "line99"]


def test_emit_zero_returns_no_body(tmp_path, clock):
    sentinel = tmp_path / "out.txt"
    sentinel.write_text("noisy\n" * 10_000, encoding="utf-8")

    assert _wait(sentinel, 60, clock, emit_lines=0).emitted == []


def test_render_is_one_line_when_nothing_is_emitted(tmp_path, clock):
    sentinel = tmp_path / "out.txt"
    sentinel.write_text("x", encoding="utf-8")

    assert len(_wait(sentinel, 60, clock).render().splitlines()) == 1


def test_timeout_render_tells_the_operator_what_to_do(tmp_path, clock):
    text = _wait(tmp_path / "never.txt", 1.0, clock).render()

    assert "TIMEOUT" in text
    # It must NOT suggest re-launching a job that may still be running.
    assert "Do NOT re-launch" in text


# --------------------------------------------------------------------------- #
# CLI wiring
# --------------------------------------------------------------------------- #
def test_cli_exit_zero_on_hit(tmp_path, capsys):
    sentinel = tmp_path / "done.txt"
    sentinel.write_text("CHAIN_DONE\n", encoding="utf-8")

    rc = main(["wait-for", str(sentinel), "--timeout", "5", "--contains", "CHAIN_DONE"])

    assert rc == EXIT_FOUND
    assert "FOUND" in capsys.readouterr().out


def test_cli_exit_one_on_timeout(tmp_path, capsys):
    rc = main(["wait-for", str(tmp_path / "never.txt"),
               "--timeout", "0.3", "--poll-ms", "100"])

    assert rc == EXIT_TIMEOUT
    assert "TIMEOUT" in capsys.readouterr().err


def test_cli_exit_two_on_usage_error(tmp_path, capsys):
    rc = main(["wait-for", str(tmp_path / "x.txt"), "--timeout", "0"])

    assert rc == EXIT_USAGE
    assert rc != EXIT_TIMEOUT, "a usage error must not read as a timed-out job"
    assert "usage error" in capsys.readouterr().err


def test_cli_json_output_is_parseable(tmp_path, capsys):
    sentinel = tmp_path / "done.txt"
    sentinel.write_text("a\nb\n", encoding="utf-8")

    rc = main(["wait-for", str(sentinel), "--timeout", "5", "--emit", "1", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert rc == EXIT_FOUND
    assert payload["ok"] is True
    assert payload["outcome"] == "FOUND"
    assert payload["emitted"] == ["b"]


def test_wait_writes_nothing(tmp_path):
    """Stateless by contract: usable at session zero, before any RAG exists."""
    sentinel = tmp_path / "done.txt"
    sentinel.write_text("ok", encoding="utf-8")
    before = sorted(p.name for p in tmp_path.iterdir())

    main(["wait-for", str(sentinel), "--timeout", "5"])

    assert sorted(p.name for p in tmp_path.iterdir()) == before
