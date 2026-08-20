"""POLL-GATE-BLIND-TO-WAIT-FANOUT-S208 — the second blindness of the wait guard.

The S198 fix keyed the wait half of the poll gate on the TARGET FILE, and that
choice is why the gate never fired once. Measured on the S207 final turn: 66 of
113 tool calls were blocking waits, 45 percent of them returned in under a
second, and the guard added in bf5b6d5 fired ZERO times — every wait named a
DIFFERENT file, so nothing ever repeated.

The invariant that survives both shapes is DURATION, not repetition. A wait that
returns in half a millisecond did not wait: the sentinel was already in the file
before the call was made. These tests pin the two halves of that refusal:

* ``wait-duration`` (PostToolUse) reads the elapsed time out of the wait's own
  output and records the instant returns — the fact PreToolUse cannot observe;
* ``poll`` (PreToolUse) refuses the next wait once enough have piled up,
  **regardless of which files they named**, which is the whole fix.

Two properties are asserted as hard as the refusal itself, because a gate that
lacks either gets switched off by the first person it inconveniences: an
UNMEASURED wait is never charged, and the refusal CLEARS the window so a
genuinely long wait issued right after it is not collateral damage.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rag_kernel import hook_guard
from rag_kernel.hook_guard import (
    WAIT_FANOUT_LIMIT,
    WAIT_FANOUT_WINDOW_SECONDS,
    WAIT_INSTANT_SECONDS,
    decide,
)

NOW = 1_000_000.0


def _wait_event(path: str, response: str | None = None) -> dict:
    event: dict = {"tool_name": "mcp__rag-kernel__rag_wait",
                   "tool_input": {"path": path}}
    if response is not None:
        event["tool_response"] = response
    return event


def _instant(path: str) -> dict:
    return _wait_event(path, "wait-for: FOUND after 0.0s (1 polls)")


def _record(sd: Path, n: int, *, now: float = NOW, prefix: str = "f") -> None:
    """Record ``n`` instant returns, each on a DIFFERENT file."""
    for i in range(n):
        decide("wait-duration", _instant(f"/x/{prefix}{i}.txt"),
               state_dir=sd, now=now)


class TestTheFanOutRefusal:
    def test_the_next_wait_is_refused_after_the_limit(self, tmp_path):
        _record(tmp_path, WAIT_FANOUT_LIMIT)
        got = decide("poll", _wait_event("/x/never-seen-before.txt"),
                     state_dir=tmp_path, now=NOW + 1)
        assert got.allow is False

    def test_the_refusal_survives_every_wait_naming_a_different_file(self, tmp_path):
        """The exact S207 traffic: no file repeats, so the old guard saw nothing."""
        _record(tmp_path, WAIT_FANOUT_LIMIT, prefix="unique-")
        got = decide("poll", _wait_event("/x/unique-and-another.txt"),
                     state_dir=tmp_path, now=NOW + 1)
        assert got.allow is False
        assert "FAN-OUT" in (got.reason or "")

    def test_below_the_limit_is_allowed(self, tmp_path):
        _record(tmp_path, WAIT_FANOUT_LIMIT - 1)
        got = decide("poll", _wait_event("/x/next.txt"),
                     state_dir=tmp_path, now=NOW + 1)
        assert got.allow is True

    def test_the_refusal_names_a_repair_that_cannot_fan_out(self, tmp_path):
        _record(tmp_path, WAIT_FANOUT_LIMIT)
        got = decide("poll", _wait_event("/x/next.txt"),
                     state_dir=tmp_path, now=NOW + 1)
        reason = got.reason or ""
        assert "rag_kernel run" in reason, "a refusal must name its repair"
        assert "POLL-GATE-BLIND-TO-WAIT-FANOUT-S208" in reason

    def test_one_refusal_per_burst(self, tmp_path):
        """The window is cleared on refusal, so the NEXT wait proceeds.

        Without this a legitimately long wait issued straight after the refusal
        would be refused too, on the strength of instant returns that are already
        in the past — the gate would brick the session it protects.
        """
        _record(tmp_path, WAIT_FANOUT_LIMIT)
        assert decide("poll", _wait_event("/x/a.txt"),
                      state_dir=tmp_path, now=NOW + 1).allow is False
        assert decide("poll", _wait_event("/x/b.txt"),
                      state_dir=tmp_path, now=NOW + 2).allow is True

    def test_instants_outside_the_window_do_not_count(self, tmp_path):
        _record(tmp_path, WAIT_FANOUT_LIMIT, now=NOW)
        later = NOW + WAIT_FANOUT_WINDOW_SECONDS + 60
        got = decide("poll", _wait_event("/x/next.txt"),
                     state_dir=tmp_path, now=later)
        assert got.allow is True


class TestWhatIsNeverCharged:
    def test_a_wait_that_actually_blocked_records_nothing(self, tmp_path):
        for i in range(WAIT_FANOUT_LIMIT * 2):
            decide("wait-duration",
                   _wait_event(f"/x/slow{i}.txt",
                               "wait-for: FOUND after 41.7s (167 polls)"),
                   state_dir=tmp_path, now=NOW)
        assert decide("poll", _wait_event("/x/next.txt"),
                      state_dir=tmp_path, now=NOW + 1).allow is True

    def test_an_unmeasured_wait_is_not_treated_as_instant(self, tmp_path):
        """The gate may miss a violation. It may not invent one."""
        for i in range(WAIT_FANOUT_LIMIT * 2):
            decide("wait-duration", _wait_event(f"/x/q{i}.txt", "no timing here"),
                   state_dir=tmp_path, now=NOW)
        assert decide("poll", _wait_event("/x/next.txt"),
                      state_dir=tmp_path, now=NOW + 1).allow is True

    def test_a_missing_response_is_not_treated_as_instant(self, tmp_path):
        for i in range(WAIT_FANOUT_LIMIT * 2):
            decide("wait-duration", _wait_event(f"/x/r{i}.txt"),
                   state_dir=tmp_path, now=NOW)
        assert decide("poll", _wait_event("/x/next.txt"),
                      state_dir=tmp_path, now=NOW + 1).allow is True

    def test_a_non_wait_tool_is_untouched_by_this_gate(self, tmp_path):
        got = decide("wait-duration",
                     {"tool_name": "mcp__tmux-mcp__execute-command",
                      "tool_response": "FOUND after 0.0s"},
                     state_dir=tmp_path, now=NOW)
        assert got.allow is True
        assert not got.context


class TestTheRecordingHalf:
    def test_the_post_gate_never_refuses(self, tmp_path):
        """By PostToolUse the round-trip is already spent; a deny is theatre."""
        for i in range(WAIT_FANOUT_LIMIT * 3):
            got = decide("wait-duration", _instant(f"/x/n{i}.txt"),
                         state_dir=tmp_path, now=NOW)
            assert got.allow is True

    def test_it_warns_before_the_limit_so_the_refusal_is_not_a_surprise(self, tmp_path):
        contexts = [decide("wait-duration", _instant(f"/x/w{i}.txt"),
                           state_dir=tmp_path, now=NOW).context
                    for i in range(WAIT_FANOUT_LIMIT)]
        assert any(c and "WAIT-FAN-OUT" in c for c in contexts), \
            "the count must be visible before it becomes a refusal"

    @pytest.mark.parametrize("response,elapsed_is_instant", [
        ("wait-for: FOUND after 0.0s (1 polls)", True),
        ("wait-for: FOUND after 0.5s (3 polls)", True),
        ("wait-for: FOUND after 1.0s (4 polls)", False),
        ("wait-for: TIMEOUT after 180.0s (720 polls)", False),
    ])
    def test_the_elapsed_time_is_read_out_of_the_waits_own_output(
        self, tmp_path, response, elapsed_is_instant
    ):
        for i in range(WAIT_FANOUT_LIMIT):
            decide("wait-duration", _wait_event(f"/x/p{i}.txt", response),
                   state_dir=tmp_path, now=NOW)
        refused = decide("poll", _wait_event("/x/next.txt"),
                         state_dir=tmp_path, now=NOW + 1).allow is False
        assert refused is elapsed_is_instant

    def test_a_dict_shaped_response_is_read_too(self, tmp_path):
        for i in range(WAIT_FANOUT_LIMIT):
            decide("wait-duration",
                   {"tool_name": "mcp__rag-kernel__rag_wait",
                    "tool_input": {"path": f"/x/d{i}.txt"},
                    "tool_response": {"output": "FOUND after 0.0s (1 polls)"}},
                   state_dir=tmp_path, now=NOW)
        assert decide("poll", _wait_event("/x/next.txt"),
                      state_dir=tmp_path, now=NOW + 1).allow is False


class TestTheGateIsDeclared:
    def test_it_is_in_the_gate_list(self):
        assert "wait-duration" in hook_guard.GATES

    def test_it_is_a_post_tool_use_gate(self):
        assert hook_guard._EVENT_FOR_GATE["wait-duration"] == "PostToolUse"

    def test_the_selftest_covers_it(self):
        failures, lines = hook_guard.selftest()
        assert failures == 0, "\n".join(lines)
        assert any("wait-duration" in line for line in lines)
        assert any("poll: mcp__rag-kernel__rag_wait -> DENY" in line
                   for line in lines), \
            "the fan-out refusal itself must be probed, not just the recorder"

    def test_the_thresholds_are_overridable_without_editing_code(self, monkeypatch):
        """An operator must be able to loosen a gate without a code change."""
        assert WAIT_INSTANT_SECONDS > 0
        assert WAIT_FANOUT_LIMIT >= 2
        assert WAIT_FANOUT_WINDOW_SECONDS >= WAIT_INSTANT_SECONDS
