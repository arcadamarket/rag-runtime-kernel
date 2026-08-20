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

**SEALED-SESSION-CAN-STILL-WRITE-S208 (generalised S209).** The double-seal guard
refused one verb out of four when it was measured against real traffic: a sealed
S206-review session wrote all through S207 using ``register-asset``, ``render
--apply``, ``bootmap --refresh`` and ``update-rule``. Two holes, both closed here
and both pinned below:

* a write that named **no** session returned ``None`` — omitting ``--session`` was
  a way through, and the measured offender omitted it;
* ``render`` and ``bootmap`` are dual-mode, so a flat verb set could not guard the
  writing half without also banning the read.

``register-asset`` moved the other way, deliberately: it is now permitted after a
seal because the post-seal inbox is built on that permission.
"""

from __future__ import annotations

import argparse
import json

import pytest

from rag_kernel.__main__ import (
    _SEAL_GUARDED_VERBS,
    _SEAL_GUARDED_WHEN_FLAG,
    _refuse_mutation_after_seal,
)


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

    def test_a_write_naming_no_session_is_refused(self, tmp_path, capsys):
        """SEALED-SESSION-CAN-STILL-WRITE-S208 — the hole the S207 traffic used.

        This assertion is the INVERSE of the one it replaces. Until S209 an
        unnamed write returned ``None``, which made omitting ``--session`` a way
        through the guard rather than a reason to stop: an anonymous write cannot
        be distinguished from a write by the sealed session, and the measured
        offender was anonymous. The refusal must name the escape, since every
        guarded verb now accepts ``--session``.
        """
        rag = _rag(tmp_path, sealed_session="S187")
        assert _refuse_mutation_after_seal("add", argparse.Namespace(rag=rag)) == 1
        err = capsys.readouterr().err
        assert "--session" in err, "the refusal must name the legal escape"
        assert "S187" in err, "the refusal must name the session holding the seal"

    def test_an_empty_session_string_is_treated_as_unnamed(self, tmp_path):
        rag = _rag(tmp_path, sealed_session="S187")
        assert _refuse_mutation_after_seal("add", _ns(rag, "")) == 1
        assert _refuse_mutation_after_seal("add", _ns(rag, "   ")) == 1

    def test_an_unnamed_write_is_free_while_no_seal_stands(self, tmp_path):
        """The refusal is bound to the seal, not to the missing flag."""
        rag = _rag(tmp_path, sealed_session="S187", transfer_ready=False)
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


class TestTheS207PostSealTraffic:
    """The four verbs a sealed session actually ran, measured, not imagined.

    S207 logged ``register-asset``, ``render --apply``, ``bootmap --refresh`` and
    ``update-rule`` from a session that was already sealed. One was refused. This
    class pins the verdict this guard now returns for each of the four.
    """

    def _ns(self, rag, session=None, **flags):
        return argparse.Namespace(rag=rag, session=session, **flags)

    def test_render_apply_is_refused(self, tmp_path):
        rag = _rag(tmp_path, sealed_session="S187")
        assert _refuse_mutation_after_seal(
            "render", self._ns(rag, "S187", apply=True)
        ) == 1

    def test_a_read_only_render_stays_free_after_the_seal(self, tmp_path):
        """Guarding the verb would have banned inspecting a sealed session."""
        rag = _rag(tmp_path, sealed_session="S187")
        assert _refuse_mutation_after_seal(
            "render", self._ns(rag, "S187", apply=False)
        ) is None

    def test_bootmap_refresh_is_refused(self, tmp_path):
        rag = _rag(tmp_path, sealed_session="S187")
        assert _refuse_mutation_after_seal(
            "bootmap", self._ns(rag, "S187", refresh=True)
        ) == 1

    def test_a_read_only_bootmap_stays_free_after_the_seal(self, tmp_path):
        rag = _rag(tmp_path, sealed_session="S187")
        assert _refuse_mutation_after_seal(
            "bootmap", self._ns(rag, "S187", refresh=False)
        ) is None

    def test_a_dual_mode_write_naming_no_session_is_refused(self, tmp_path):
        rag = _rag(tmp_path, sealed_session="S187")
        assert _refuse_mutation_after_seal(
            "render", self._ns(rag, None, apply=True)
        ) == 1

    def test_the_refusal_names_the_writing_flag(self, tmp_path, capsys):
        rag = _rag(tmp_path, sealed_session="S187")
        _refuse_mutation_after_seal("bootmap", self._ns(rag, "S187", refresh=True))
        assert "bootmap --refresh" in capsys.readouterr().err

    def test_update_rule_is_still_refused(self, tmp_path):
        """The one of the four that was already caught must stay caught."""
        rag = _rag(tmp_path, sealed_session="S187")
        assert _refuse_mutation_after_seal("update-rule", _ns(rag, "S187")) == 1

    def test_register_asset_is_permitted_after_the_seal(self, tmp_path):
        """DELIBERATE EXCEPTION, measured not assumed.

        ``register-asset`` writes RAG_CONTEXT.json, not the canonical RAG, and the
        post-seal inbox depends on that permission: a sealed session must still be
        able to leave its successor a note. Guarding it would close the one channel
        that carries state across the boundary this guard defends.
        """
        rag = _rag(tmp_path, sealed_session="S187")
        assert "register-asset" not in _SEAL_GUARDED_VERBS
        assert _refuse_mutation_after_seal("register-asset", _ns(rag, "S187")) is None

    def test_post_is_permitted_after_the_seal(self, tmp_path):
        """The other half of the inbox channel."""
        rag = _rag(tmp_path, sealed_session="S187")
        assert _refuse_mutation_after_seal("post", _ns(rag, "S187")) is None


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

    def test_every_guarded_verb_can_name_its_session(self):
        """The refusal must always have a legal escape — this is what makes it a
        gate rather than an outage.

        An unnamed write is now refused, so a guarded verb whose parser has no
        ``--session`` would be permanently unusable for the whole of a successor
        session. That is not a judgement call anyone should have to remember at
        review time: adding a verb to the guarded set without the flag fails here.
        ``render`` and ``ingest`` gained ``--session`` in S209 because this
        assertion caught them.
        """
        from rag_kernel.__main__ import build_parser

        actions = build_parser()._subparsers._group_actions[0].choices
        for verb in sorted(_SEAL_GUARDED_VERBS | set(_SEAL_GUARDED_WHEN_FLAG)):
            sub = actions.get(verb)
            assert sub is not None, f"{verb} is guarded but is not a real verb"
            flags = {opt for act in sub._actions for opt in act.option_strings}
            assert "--session" in flags, (
                f"`{verb}` is refused when it names no session, so its parser "
                f"must accept --session or the verb is unusable after a seal"
            )

    def test_dual_mode_verbs_declare_a_real_writing_flag(self):
        """The flag that turns a read into a write must exist on the parser."""
        from rag_kernel.__main__ import build_parser

        actions = build_parser()._subparsers._group_actions[0].choices
        for verb, flag in _SEAL_GUARDED_WHEN_FLAG.items():
            sub = actions.get(verb)
            assert sub is not None, verb
            dests = {act.dest for act in sub._actions}
            assert flag in dests, f"`{verb}` has no --{flag} to guard"

    def test_dual_mode_verbs_are_not_also_in_the_flat_set(self):
        """Listing both would ban the read half by the back door."""
        assert not (_SEAL_GUARDED_VERBS & set(_SEAL_GUARDED_WHEN_FLAG))


class TestTheCloseOrderRunsOnAResume:
    """GRAND-AUDIT-SKIPPED-ON-RESUMED-CLOSE-S207.

    Step 0 of the close — render, commit, measure, grand audit — sat behind
    ``if not steps.get("checkpoint")``, so a RESUMED close skipped all four. S206
    put the grand audit into the close to end six sessions of sealing without it,
    then sealed itself on a resumed close where Step 0 never ran. A resume is the
    case that needs the guards MOST: it happens after something already went
    wrong, so the tree is more likely to have moved, not less.

    This is a structural assertion on purpose. The behavioural path runs a test
    suite and a full audit in subprocesses, so a test that drove it would measure
    the fixture rather than the ritual; what actually failed was a CONDITION in
    the source, and that is what is pinned. If Step 0 ever becomes conditional
    again, this is red before the next resume seals blind.
    """

    def _step0_block(self) -> str:
        """The CODE between the Step 0 marker and the call — comments stripped.

        Stripping them is not cosmetic. The first version of this test read the
        raw source and went red on the comment that EXPLAINS the removed
        condition: a predicate that fires on prose describing the defect is
        GATE-FALSE-POSITIVE-ON-PROSE-S201 in miniature, and the repair is the
        same one that item asks for — look at operands, not at text.
        """
        import inspect

        from rag_kernel import __main__ as m

        src = inspect.getsource(m._drive_close)
        assert "Step 0/4" in src, "the close order step has been renamed"
        after = src.split("Step 0/4", 1)[1]
        block = after.split("_close_order_prepare(", 1)[0]
        return "\n".join(line.split("#", 1)[0] for line in block.splitlines())

    def test_step_0_is_not_conditional_on_the_checkpoint_step(self):
        block = self._step0_block()
        assert "steps.get(\"checkpoint\")" not in block, (
            "Step 0 is guarded by the checkpoint step again — a resumed close "
            "would skip render, commit, measure and the grand audit"
        )
        assert "steps[\"checkpoint\"]" not in block

    def test_the_close_order_still_declares_its_own_skip(self):
        """The only legal skip stays the declared one, and it is per-close."""
        import inspect

        from rag_kernel import __main__ as m

        src = inspect.getsource(m._close_order_prepare)
        assert "no_auto_close_order" in src

    def test_the_grand_audit_is_inside_the_close_order(self):
        import inspect

        from rag_kernel import __main__ as m

        src = inspect.getsource(m._close_order_prepare)
        assert "grand_audit.py" in src, (
            "the grand audit must live inside the step every close runs, not "
            "beside it — GRAND-AUDIT-NOT-IN-THE-CLOSE-S206"
        )


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
