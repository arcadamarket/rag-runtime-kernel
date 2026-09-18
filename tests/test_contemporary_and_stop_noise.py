"""Two gates S209 built from measured S207/S208 conduct.

**TWO-LIVE-SESSIONS-ONE-RAG-S207, the BOOT half.** S207 measured two sessions
running against one canonical store and one worktree for a whole day, neither
able to see the other, and nothing refused. The WRITE half closed in S209
(SEALED-SESSION-CAN-STILL-WRITE-S208): a sealed session can no longer write.
This is the other end — a boot that lands beside a session still emitting
governed calls is refused, and the refusal NAMES the holding id, because an
agent that cannot name the blocker cannot act on it (Rule 43).

The discriminator is RECENCY, not sealing, which is what separates this from
CLOSE-SEAL-ENFORCE: a crashed predecessor goes quiet, a live contemporary does
not.

**STOP-GATE-NOISE-AND-UNSENTINELABLE-S208.** Two independent defects in the Stop
gate, both measured at the S208 close: it flagged a file the KERNEL writes, which
no agent can append a sentinel to, and it re-fired nine consecutive times on an
unchanged set after the agent had already given the full status block.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from rag_kernel import hook_guard
from rag_kernel.__main__ import _CONTEMPORARY_WINDOW_S, _contemporary_live_session


def _log(rag_dir: Path, sid: str, *, ended: bool = False, age_s: float = 0.0):
    p = rag_dir / f"session_log_{sid}.jsonl"
    rows = [{"event": "session_start", "session": sid}]
    if ended:
        rows.append({"event": "session_end", "session": sid})
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    if age_s:
        old = time.time() - age_s
        import os
        os.utime(p, (old, old))
    return p


class TestTheContemporarySessionGate:
    def test_a_live_other_session_is_detected_and_named(self, tmp_path):
        _log(tmp_path, "S207")
        found = _contemporary_live_session(tmp_path, "S208")
        assert found is not None
        assert found[0] == "S207", "the refusal must be able to name the holder"

    def test_our_own_log_is_not_a_contemporary(self, tmp_path):
        """Phase 2 of the boot re-runs session-start with the SAME id."""
        _log(tmp_path, "S209")
        assert _contemporary_live_session(tmp_path, "S209") is None

    def test_a_sealed_session_is_not_a_contemporary(self, tmp_path):
        _log(tmp_path, "S207", ended=True)
        assert _contemporary_live_session(tmp_path, "S208") is None

    def test_a_quiet_log_is_a_predecessor_not_a_contemporary(self, tmp_path):
        """The other gate's business. A crashed session goes quiet; this one
        must not seize CLOSE-SEAL-ENFORCE's job and refuse for the wrong reason."""
        _log(tmp_path, "S207", age_s=_CONTEMPORARY_WINDOW_S + 600)
        assert _contemporary_live_session(tmp_path, "S208") is None

    def test_an_empty_directory_refuses_nothing(self, tmp_path):
        assert _contemporary_live_session(tmp_path, "S208") is None

    def test_an_unsealed_predecessor_belongs_to_the_other_gate(self, tmp_path):
        """PRECEDENCE. Right after a crash both predicates hold: the predecessor
        is unsealed AND its log is recent. A ``session_close`` marker naming the
        id is positive evidence that the session STOPPED and recorded stopping,
        which is CLOSE-SEAL-ENFORCE's case and has its own repair. Two refusals
        racing to speak about one situation is how an agent learns to ignore both.
        """
        _log(tmp_path, "S207")
        assert _contemporary_live_session(tmp_path, "S208") is not None
        assert _contemporary_live_session(
            tmp_path, "S208", marker_session="S207") is None

    def test_the_nearest_contemporary_is_the_one_named(self, tmp_path):
        _log(tmp_path, "S205", age_s=1200)
        _log(tmp_path, "S207", age_s=10)
        found = _contemporary_live_session(tmp_path, "S208")
        assert found is not None and found[0] == "S207"


class TestTheStopGateStopsRepeatingItself:
    def _rag(self, tmp_path: Path) -> Path:
        rag = tmp_path / "RAG"
        (rag / ".boot").mkdir(parents=True)
        return rag

    def _fire(self, root: Path, state_dir: Path, now: float):
        return hook_guard.decide("stop-status", {}, project_root=root,
                                 state_dir=state_dir, now=now)

    def test_an_unchanged_set_is_not_reported_twice(self, tmp_path):
        rag = self._rag(tmp_path)
        (rag / ".boot" / "job.txt").write_text("output, no sentinel\n",
                                               encoding="utf-8")
        state = tmp_path / "state"
        first = self._fire(tmp_path, state, 1000.0)
        second = self._fire(tmp_path, state, 1001.0)
        assert first.context, "the first firing must carry the checklist"
        assert not second.context, (
            "an unchanged repeat is noise; S208 got nine of them on one file"
        )

    def test_a_changed_set_reports_again_in_full(self, tmp_path):
        rag = self._rag(tmp_path)
        (rag / ".boot" / "job.txt").write_text("a\n", encoding="utf-8")
        state = tmp_path / "state"
        assert self._fire(tmp_path, state, 1000.0).context
        assert not self._fire(tmp_path, state, 1001.0).context
        (rag / ".boot" / "second.txt").write_text("b\n", encoding="utf-8")
        third = self._fire(tmp_path, state, 1002.0)
        assert third.context, "a new job file is new information"
        assert "second.txt" in third.context

    def test_suppression_never_hides_a_clean_state(self, tmp_path):
        """Nothing flagged still means nothing said — for the original reason."""
        self._rag(tmp_path)
        got = self._fire(tmp_path, tmp_path / "state", 1000.0)
        assert got.allow is True and not got.context

    def test_the_same_job_quiet_one_minute_longer_is_still_a_repeat(self, tmp_path):
        """STOP-GATE-FINGERPRINT-EMBEDS-ELAPSED-TIME-S211 — the proving test.

        The two tests above fire one SECOND apart, so the rendered "quiet Nm"
        never changes and they passed over the defect for three sessions. The key
        was built from the RENDERED lines, which carry those minutes, so it moved
        every sixty seconds and `repeated` was never true in real use: four
        consecutive firings on an identical flagged set in S210, four again in
        S211, at which point the operator asked what the blocks were for.

        Here nothing changes except how long the SAME file has been quiet. The
        causes are identical, so the second firing must say nothing.
        """
        import os

        rag = self._rag(tmp_path)
        job = rag / ".boot" / "job.txt"
        job.write_text("output, no sentinel\n", encoding="utf-8")
        state = tmp_path / "state"
        base = time.time()

        os.utime(job, (base - hook_guard._QUIET_S - 60, base - hook_guard._QUIET_S - 60))
        first = self._fire(tmp_path, state, 1000.0)
        assert first.context, "the first firing must carry the checklist"
        assert "quiet" in first.context, "fixture must exercise the quiet-time path"

        os.utime(job, (base - hook_guard._QUIET_S - 120,
                       base - hook_guard._QUIET_S - 120))
        second = self._fire(tmp_path, state, 1060.0)
        assert not second.context, (
            "only the elapsed minutes moved — the flagged SET is unchanged, and "
            "the comment above the key has always claimed it is keyed on the set"
        )

    def test_the_key_is_built_from_causes_not_from_rendered_text(self, tmp_path):
        """Guards the SHAPE, so a future edit cannot reintroduce the same bug.

        A behavioural test alone would pass again the moment someone re-renders
        the bits into the key with the minutes rounded differently. The key must
        name its causes.
        """
        import inspect

        src = inspect.getsource(hook_guard._gate_stop_status)
        assert 'fingerprint = "|".join(causes)' in src, (
            "the repeat key must be built from the causes tuple"
        )
        assert 'fingerprint = "|".join(sorted(bits))' not in src, (
            "hashing the rendered lines is the defect itself"
        )


class TestKernelAuthoredFilesAreNotFlagged:
    def test_the_close_commit_message_is_excluded(self, tmp_path):
        """It is a git commit MESSAGE passed to `git commit -F`.

        The tempting repair — have the kernel append its own sentinel — would put
        a QQ token into the project's permanent history. Exclusion is the right
        half of this fix, and the reason is why it is written down.
        """
        boot = tmp_path / ".boot"
        boot.mkdir()
        (boot / "close_commit_S208.txt").write_text(
            "S208: close-order commit (1 path(s))\n", encoding="utf-8")
        assert hook_guard._inflight_jobs(tmp_path) == []

    def test_an_agent_authored_file_is_still_flagged(self, tmp_path):
        boot = tmp_path / ".boot"
        boot.mkdir()
        (boot / "s209_probe.txt").write_text("no sentinel here\n", encoding="utf-8")
        assert hook_guard._inflight_jobs(tmp_path) == ["s209_probe.txt"]

    def test_a_sentinelled_file_is_never_flagged(self, tmp_path):
        boot = tmp_path / ".boot"
        boot.mkdir()
        (boot / "s209_done.txt").write_text("out\nQQ_S209_DONE_QQ rc=0\n",
                                            encoding="utf-8")
        assert hook_guard._inflight_jobs(tmp_path) == []


class TestAnInputIsNotAJob:
    """STOP-GATE-FLAGS-FILES-THAT-WERE-NEVER-JOBS-S210, closed S211.

    S208 excluded two KERNEL-authored files with an argument that was about the
    file's ROLE, not its author: a git commit message cannot carry a completion
    sentinel, because a QQ token passed to ``git commit -F`` lands in the
    project's history forever. The exclusion then named those two files and
    stopped, so the same argument went unapplied to the AGENT-authored files
    with the identical property.

    MEASURED: S211 was told five separate times, at five separate stops, that
    its commit messages and its rule-value file were "job output with no
    completion sentinel". A gate that cries wolf at every stop is one the reader
    learns to scroll past — which is indistinguishable from a gate never wired,
    and is the exact failure mode the S207 note in this module warns about.

    THE PREDICATE IS THE ROLE: a file fed TO a verb is an input, and only a file
    a verb writes INTO can be a job.
    """

    @staticmethod
    def _boot(tmp_path, name, body="content with no sentinel\n"):
        boot = tmp_path / ".boot"
        boot.mkdir(exist_ok=True)
        (boot / name).write_text(body, encoding="utf-8")
        return tmp_path

    def test_an_agent_authored_commit_message_is_excluded(self, tmp_path):
        """`git commit -F` — a QQ token here would enter history forever."""
        self._boot(tmp_path, "s211_commit_msg.txt")
        assert hook_guard._inflight_jobs(tmp_path) == []

    def test_a_numbered_commit_message_is_excluded(self, tmp_path):
        self._boot(tmp_path, "s211_commit_msg2.txt")
        self._boot(tmp_path, "s211_imm_commit_msg.txt")
        assert hook_guard._inflight_jobs(tmp_path) == []

    def test_a_rule_value_file_is_excluded(self, tmp_path):
        """`add-rule --value-file` — worse than history: it enters the RULE."""
        self._boot(tmp_path, "rule_cowork_session_lookup.txt")
        assert hook_guard._inflight_jobs(tmp_path) == []

    def test_a_declared_value_input_is_excluded(self, tmp_path):
        self._boot(tmp_path, "transport_allowlist_value.txt")
        assert hook_guard._inflight_jobs(tmp_path) == []

    def test_the_kernel_authored_exclusions_still_hold(self, tmp_path):
        self._boot(tmp_path, "close_commit_S208.txt")
        self._boot(tmp_path, "session_start_S211.txt")
        assert hook_guard._inflight_jobs(tmp_path) == []

    def test_real_job_output_is_still_flagged(self, tmp_path):
        """The narrowing must not become a licence. A false negative on a safety
        gate is worse than a false alarm — output that CAN carry a sentinel is
        still asked for one, whatever transport wrote it."""
        self._boot(tmp_path, "s211_pytest1.txt")
        self._boot(tmp_path, "s211_cowork_list.txt")
        assert sorted(hook_guard._inflight_jobs(tmp_path)) == [
            "s211_cowork_list.txt", "s211_pytest1.txt",
        ]

    def test_a_name_that_merely_mentions_a_rule_is_not_excluded(self, tmp_path):
        """GATE-FALSE-POSITIVE-ON-PROSE-S201 in the other direction: the
        exclusion keys on the input NAMING CONVENTION, not on a substring
        appearing anywhere."""
        self._boot(tmp_path, "s211_rule47.txt")
        assert hook_guard._inflight_jobs(tmp_path) == ["s211_rule47.txt"]
