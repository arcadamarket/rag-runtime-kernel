"""SEAL-GUARD-COVERS-ONLY-STATE-MACHINE-VERBS-S209 — the denylist inversion.

WHAT WAS WRONG, measured not supposed. ``_refuse_mutation_after_seal`` decided
whether a verb writes governed state by looking it up in ``_SEAL_GUARDED_VERBS``,
a hand-maintained set of verb names. A set of names is a denylist, and a denylist
is complete only while someone remembers to extend it. Three verbs were measured
writing governed state after a seal with nothing refusing, because each was added
to the dispatcher after the set was written:

* ``gc``                 — deletes governed files from the project root unless ``--dry-run``
* ``tests --run``        — stamps ``meta.test_gate`` and refreshes ``.bak`` to byte-parity
* ``deployment --field`` — writes ``meta.deployments`` through the atomic store

WHAT REPLACES IT. ``_VERB_CANON_WRITE`` declares the WRITES_CANONICAL property
per verb, and this module is the mechanism that keeps the declaration honest —
the refusal is the deliverable, not the fix (Rule 45). Three properties are
asserted, and each one is a refusal a careless successor cannot talk their way
past:

1. COVERAGE — every verb ``_dispatch_table`` dispatches carries a declaration.
   Add a verb, forget the declaration, the suite goes red.
2. NO ORPHANS — every declaration names a verb that really exists. Rename a verb
   and the stale declaration is named here rather than silently guarding nothing.
3. FAIL-CLOSED — an undeclared verb reaching the live guard is treated as a
   WRITER and refused. So even while the suite is red, the running kernel does
   not wave the new verb through; the two halves fail in the same direction.

Note the asymmetry this pins, because it is the design and not an accident:
whether a SEAL STANDS is read from disk, can legitimately be unknowable, and
fails OPEN — a guard that cannot read state must not become an outage. Whether a
VERB WRITES is a declaration this repository owns, cannot legitimately be
unknown, and fails CLOSED.
"""

from __future__ import annotations

import argparse
import json

from rag_kernel.__main__ import (
    _VERB_CANON_WRITE,
    _dispatch_table,
    _refuse_mutation_after_seal,
    build_parser,
)

SEALED = "S187"


def _rag(tmp_path, *, sealed_session=SEALED, transfer_ready=True):
    """A RAG whose last close is sealed COMPLETE — the state the guard reacts to."""
    p = tmp_path / "RAG_MASTER.json"
    hot = {"tracked_items": [], "meta": {"written_by_session": "S188"}}
    if sealed_session:
        hot["session_close"] = {
            "session": sealed_session,
            "phase": "COMPLETE" if transfer_ready else "CLOSED",
            "transfer_ready": transfer_ready,
            "completed_utc": "2026-08-07T15:31:02Z",
            "steps": {},
        }
    p.write_text(json.dumps(hot), encoding="utf-8")
    return p


def _ns(rag, session=SEALED, **flags):
    return argparse.Namespace(rag=rag, session=session, **flags)


def _subparser_choices():
    return build_parser()._subparsers._group_actions[0].choices


class TestTheTableCoversTheDispatcherExactly:
    """The anti-staleness gate. Without these two, the fix decays into the defect."""

    def test_every_dispatched_verb_declares_whether_it_writes(self):
        undeclared = sorted(set(_dispatch_table()) - set(_VERB_CANON_WRITE))
        assert not undeclared, (
            "these verbs are dispatched but declare nothing in _VERB_CANON_WRITE, "
            "so the post-seal guard is guessing about them: "
            f"{undeclared}. Add a declaration in rag_kernel/__main__.py — "
            "_never() / _always() / _when(<dest>) / _unless(<dest>) / "
            "_custom(<fn>) / _exempt('<measured reason>')."
        )

    def test_no_declaration_names_a_verb_that_does_not_exist(self):
        orphans = sorted(set(_VERB_CANON_WRITE) - set(_dispatch_table()))
        assert not orphans, (
            "these declarations guard nothing — the verb was renamed or removed "
            f"and the entry outlived it: {orphans}"
        )

    def test_every_parseable_verb_is_dispatchable(self):
        """A subparser with no handler is a KeyError waiting for a user to find."""
        undispatched = sorted(set(_subparser_choices()) - set(_dispatch_table()))
        assert not undispatched, undispatched


class TestTheThreeMeasuredEscapees:
    """gc, tests --run and deployment --field — refused now, free in their read half."""

    def test_a_deleting_gc_is_refused_after_the_seal(self, tmp_path):
        rag = _rag(tmp_path)
        assert _refuse_mutation_after_seal("gc", _ns(rag, dry_run=False)) == 1

    def test_a_dry_run_gc_stays_free(self, tmp_path):
        """Inspecting a sealed session must always be free — including the sweep
        the boot ritual itself runs as step 2."""
        rag = _rag(tmp_path)
        assert _refuse_mutation_after_seal("gc", _ns(rag, dry_run=True)) is None

    def test_tests_run_is_refused_after_the_seal(self, tmp_path):
        rag = _rag(tmp_path)
        assert _refuse_mutation_after_seal("tests", _ns(rag, run=True)) == 1

    def test_reading_the_test_stamp_stays_free(self, tmp_path):
        rag = _rag(tmp_path)
        assert _refuse_mutation_after_seal(
            "tests", _ns(rag, run=False, verify=True, show=False)
        ) is None

    def test_deployment_set_is_refused_after_the_seal(self, tmp_path):
        rag = _rag(tmp_path)
        assert _refuse_mutation_after_seal(
            "deployment", _ns(rag, field="root", key="imm", value="/x", list=False)
        ) == 1

    def test_deployment_list_stays_free(self, tmp_path):
        rag = _rag(tmp_path)
        assert _refuse_mutation_after_seal(
            "deployment", _ns(rag, field=None, list=True)
        ) is None

    def test_doctor_recover_is_refused_but_plain_doctor_is_not(self, tmp_path):
        """--recover restores the RAG from .bak, which would undo the seal itself."""
        rag = _rag(tmp_path)
        assert _refuse_mutation_after_seal("doctor", _ns(rag, recover=True)) == 1
        assert _refuse_mutation_after_seal("doctor", _ns(rag, recover=False)) is None

    def test_inventory_writes_only_in_its_writing_modes(self, tmp_path):
        rag = _rag(tmp_path)
        assert _refuse_mutation_after_seal(
            "inventory", _ns(rag, mode="scan", dry_run=False)
        ) is None
        assert _refuse_mutation_after_seal(
            "inventory", _ns(rag, mode="backfill", dry_run=True)
        ) is None
        assert _refuse_mutation_after_seal(
            "inventory", _ns(rag, mode="backfill", dry_run=False)
        ) == 1

    def test_configure_writes_unless_it_is_a_dry_run(self, tmp_path):
        rag = _rag(tmp_path)
        assert _refuse_mutation_after_seal(
            "configure", _ns(rag, dry_run=True)
        ) is None
        assert _refuse_mutation_after_seal(
            "configure", _ns(rag, dry_run=False)
        ) == 1


class TestFailClosed:
    def test_an_undeclared_verb_is_refused(self, tmp_path):
        """The half that holds while the suite is red.

        A verb the table has never heard of is treated as a writer. This is the
        opposite of the old behaviour, where anything absent from the denylist was
        permitted — which is how three writers ran post-seal unchallenged.
        """
        rag = _rag(tmp_path)
        assert "verb-invented-by-a-later-session" not in _VERB_CANON_WRITE
        assert _refuse_mutation_after_seal(
            "verb-invented-by-a-later-session", _ns(rag)
        ) == 1

    def test_failing_closed_still_fails_open_on_unreadable_state(self, tmp_path):
        """The two questions fail in different directions, deliberately.

        An undeclared verb is a defect in this repository (closed). An unreadable
        marker is a fact about the disk (open).
        """
        p = tmp_path / "broken.json"
        p.write_text("{not json", encoding="utf-8")
        assert _refuse_mutation_after_seal(
            "verb-invented-by-a-later-session", _ns(p)
        ) is None

    def test_an_undeclared_verb_is_free_while_no_seal_stands(self, tmp_path):
        rag = _rag(tmp_path, transfer_ready=False)
        assert _refuse_mutation_after_seal(
            "verb-invented-by-a-later-session", _ns(rag)
        ) is None


class TestTheRefusalStaysUsable:
    def test_every_refusable_verb_can_name_its_session(self):
        """A refusal with no escape is an outage, not a gate.

        The guard refuses a write that names no session, so any verb it can refuse
        must accept ``--session`` or it becomes permanently unusable for the whole
        of the successor session. ``gc``, ``configure`` and ``doctor`` gained the
        flag in S211 because this assertion caught them, the same way ``render``
        and ``ingest`` gained it in S209.
        """
        choices = _subparser_choices()
        refusable = sorted(
            verb for verb, decl in _VERB_CANON_WRITE.items()
            if decl.kind in ("always", "when", "unless", "custom")
        )
        for verb in refusable:
            sub = choices.get(verb)
            assert sub is not None, f"{verb} is guarded but is not a real verb"
            flags = {opt for act in sub._actions for opt in act.option_strings}
            assert "--session" in flags, (
                f"`{verb}` can be refused after a seal, so its parser must accept "
                f"--session or the refusal names no legal escape"
            )

    def test_every_flag_a_declaration_names_exists_on_its_parser(self):
        """A guard keyed on a dest that no longer exists guards nothing."""
        choices = _subparser_choices()
        for verb, decl in _VERB_CANON_WRITE.items():
            if not decl.dests:
                continue
            sub = choices.get(verb)
            assert sub is not None, verb
            dests = {act.dest for act in sub._actions}
            for dest in decl.dests:
                assert dest in dests, f"`{verb}` declares --{dest}, which it has no"

    def test_every_exemption_states_its_measured_reason(self):
        """A permission granted after a seal must carry its argument with it.

        Rule 43: the reader months later has no transcript. An exemption with no
        reason is indistinguishable from an oversight, which is the entire class
        of defect this table replaced.
        """
        for verb, decl in _VERB_CANON_WRITE.items():
            if decl.kind != "exempt":
                continue
            assert len(decl.reason) > 40, (
                f"`{verb}` is permitted after a seal with no stated reason"
            )

    def test_the_derived_views_still_describe_the_same_table(self):
        """The S209 gate tests assert against _SEAL_GUARDED_VERBS. It is now DERIVED
        from the declaration table rather than hand-kept, so the two cannot drift."""
        from rag_kernel.__main__ import (
            _SEAL_GUARDED_VERBS, _SEAL_GUARDED_WHEN_FLAG,
        )

        assert _SEAL_GUARDED_VERBS == frozenset(
            v for v, d in _VERB_CANON_WRITE.items() if d.kind == "always"
        )
        assert not (_SEAL_GUARDED_VERBS & set(_SEAL_GUARDED_WHEN_FLAG))
        assert _SEAL_GUARDED_WHEN_FLAG["render"] == "apply"
        assert _SEAL_GUARDED_WHEN_FLAG["bootmap"] == "refresh"
