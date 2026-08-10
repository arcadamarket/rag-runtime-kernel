"""Two close-ritual gates found by the S188 forensic audit of S178-S187.

**CLOSE-DOUBLE-SEAL-S187.** ``session_log_S187.jsonl`` holds two ``session_end``
records — 15:27:18Z and 15:31:02Z — with eight canonical mutations between them.
The first seal attested a state that then changed. ``SEAL-REPORT-STALE-SURFACE``
already caught the report drifting away from state; nothing caught state drifting
away from the seal. ``_refuse_mutation_after_seal`` does.

**CLOSE-STEP-ERRLOG-UNENFORCED.** ``steps["error_log"]`` was recorded from S139 and
checked by nothing. S184, S185, S186 and S187 each sealed with it ``False``, and
ERROR_LOG.md's last write is 2026-07-29 (E-096, S183) — four sessions of errors,
including two S187 named to the operator in prose, never landed. A step that is
recorded and never gates is a comment.

Both gates are REFUSE-BY-DEFAULT with a named repair, and both fail OPEN when the
marker cannot be read: a guard that cannot read state must not become an outage.
"""

from __future__ import annotations

import argparse
import json

import pytest

from rag_kernel.__main__ import _SEAL_GUARDED_VERBS, _refuse_mutation_after_seal


def _rag(tmp_path, *, sealed_session=None, transfer_ready=True, marker=True):
    p = tmp_path / "RAG_MASTER.json"
    hot = {"tracked_items": [], "meta": {"written_by_session": "S188"}}
    if marker and sealed_session:
        hot["session_close"] = {
            "session": sealed_session,
            "phase": "COMPLETE" if transfer_ready else "CLOSED",
            "transfer_ready": transfer_ready,
            "completed_utc": "2026-08-07T15:31:02Z",
            "steps": {},
        }
    p.write_text(json.dumps(hot), encoding="utf-8")
    return p


def _ns(rag, session):
    return argparse.Namespace(rag=rag, session=session)


class TestDoubleSealGuard:
    def test_refuses_a_mutation_naming_the_sealed_session(self, tmp_path, capsys):
        rag = _rag(tmp_path, sealed_session="S187")
        assert _refuse_mutation_after_seal("add", _ns(rag, "S187")) == 1
        err = capsys.readouterr().err
        assert "CLOSE-DOUBLE-SEAL" in err
        assert "session-resume" in err, "a refusal must name its repair"

    def test_every_guarded_verb_is_refused(self, tmp_path):
        rag = _rag(tmp_path, sealed_session="S187")
        for verb in _SEAL_GUARDED_VERBS:
            assert _refuse_mutation_after_seal(verb, _ns(rag, "S187")) == 1, verb

    def test_the_eight_s187_post_seal_mutations_would_all_be_refused(self, tmp_path):
        """The exact verb sequence logged between S187's two session_end records."""
        rag = _rag(tmp_path, sealed_session="S187")
        for verb in ("un-add", "un-add", "add", "add", "note"):
            assert _refuse_mutation_after_seal(verb, _ns(rag, "S187")) == 1, verb

    def test_read_only_verbs_are_never_refused(self, tmp_path):
        rag = _rag(tmp_path, sealed_session="S187")
        for verb in ("items", "audit", "report", "render", "measured", "health"):
            assert _refuse_mutation_after_seal(verb, _ns(rag, "S187")) is None, verb

    def test_a_different_session_may_still_write(self, tmp_path):
        """The next session must not inherit its predecessor's seal."""
        rag = _rag(tmp_path, sealed_session="S187")
        assert _refuse_mutation_after_seal("add", _ns(rag, "S188")) is None

    def test_an_incomplete_close_does_not_block(self, tmp_path):
        rag = _rag(tmp_path, sealed_session="S187", transfer_ready=False)
        assert _refuse_mutation_after_seal("add", _ns(rag, "S187")) is None

    def test_no_marker_does_not_block(self, tmp_path):
        rag = _rag(tmp_path, marker=False)
        assert _refuse_mutation_after_seal("add", _ns(rag, "S187")) is None

    def test_missing_session_arg_does_not_block(self, tmp_path):
        rag = _rag(tmp_path, sealed_session="S187")
        assert _refuse_mutation_after_seal("add", argparse.Namespace(rag=rag)) is None

    def test_unreadable_rag_fails_open(self, tmp_path):
        """A guard that cannot read state must not become an outage."""
        p = tmp_path / "broken.json"
        p.write_text("{not json", encoding="utf-8")
        assert _refuse_mutation_after_seal("add", _ns(p, "S187")) is None

    def test_absent_rag_fails_open(self, tmp_path):
        assert _refuse_mutation_after_seal(
            "add", _ns(tmp_path / "nope.json", "S187")
        ) is None


class TestGuardedVerbSet:
    def test_covers_the_state_machine_transitions(self):
        for verb in ("add", "un-add", "start", "resolve", "defer",
                     "reopen", "discard", "supersede"):
            assert verb in _SEAL_GUARDED_VERBS

    def test_covers_the_governed_setters(self):
        for verb in ("note", "priority", "add-rule", "update-rule",
                     "refresh-current-status", "prune-current-status", "meta"):
            assert verb in _SEAL_GUARDED_VERBS

    def test_excludes_read_only_verbs(self):
        for verb in ("items", "audit", "report", "render", "health",
                     "wait-for", "measured", "reuse-check", "list-kinds"):
            assert verb not in _SEAL_GUARDED_VERBS

    def test_excludes_the_recovery_paths(self):
        """session-resume must stay reachable — it is the named repair."""
        for verb in ("session-resume", "session-start", "session-end", "doctor"):
            assert verb not in _SEAL_GUARDED_VERBS


class TestCloseTestGateStaleBlocks:
    """CLOSE-TESTGATE-STALE-BLOCKS (S191, E-115).

    S190 measured the suite at e8fbb96, then committed 9d68bf0, then sealed.
    The commit in between shipped `_boot_axis1_audit` with no `import
    subprocess`, so every S191 boot died before rendering the operating frame.
    The grand audit had already flagged the stamp STALE — but only as a report,
    and the close never consulted it. A detector nobody consults is not a guard.

    These pin the tri-state contract the seal now obeys: ONLY a measured, green,
    current stamp may seal.
    """

    def _verdict(self, **kw):
        from rag_kernel import test_gate
        stamp = {"passed": 2509, "failed": 0, "collected": 2509,
                 "session": "S190", "git_head": "e8fbb96abc"}
        stamp.update(kw.pop("stamp", {}))
        return test_gate.verdict(stamp, **kw)

    def test_the_exact_s190_stamp_does_not_seal(self):
        ok, cell, _ = self._verdict(live_head="9d68bf0def")
        assert ok is not True
        assert "STALE" in cell

    def test_a_red_suite_does_not_seal(self):
        ok, _, _ = self._verdict(stamp={"failed": 1}, live_head="e8fbb96abc")
        assert ok is False

    def test_an_unmeasured_gate_does_not_seal(self):
        from rag_kernel import test_gate
        ok, _, _ = test_gate.verdict(None, live_head="e8fbb96abc")
        assert ok is None

    def test_zero_collected_does_not_seal_even_with_no_failures(self):
        # measuring the wrong tree yields 0 collected and 0 failed; that must
        # never read as green.
        ok, _, _ = self._verdict(
            stamp={"passed": 0, "collected": 0}, live_head="e8fbb96abc"
        )
        assert ok is None

    def test_only_measured_green_and_current_seals(self):
        ok, _, _ = self._verdict(live_head="e8fbb96abc")
        assert ok is True
