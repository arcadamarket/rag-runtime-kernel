"""Two S190 write gates: DONE must cite a file, and a corpse takes no plans.

**RESOLVE-REQUIRES-EVIDENCE (P1-C).** The S189 grand audit walked all 175 RESOLVED
items: 131 cite no artifact at all, and 11 more cite only paths that no longer
exist. 81% of the project's completion record cannot be checked by anyone,
including its author. ``resolve`` now REFUSES without ``--artifact <path>``, the
path must EXIST at the moment of the claim, and it is written into the item's
history so the evidence travels with the ledger rather than with the chat window.

**SEMANTIC-PRECONDITION-GATE (P1-D).** The lifecycle guard refuses illegal STATUS
moves, so a terminal item cannot be re-resolved — and it says nothing about the
writes that leave status alone. S189 set a priority_group on MARKETING-LANDING, an
item already terminal, and every gate in the kernel agreed. ``priority`` /
``resolve`` / ``defer`` / ``reopen`` against a terminal item now refuse unless
``--cite <live-item-id>`` names the successor the write is really about.
"""

from __future__ import annotations

import argparse
import json

import pytest

from rag_kernel.__main__ import (
    _PRECONDITION_VERBS,
    _refuse_terminal_write,
    _resolve_artifact,
    build_parser,
    cmd_item_transition,
    cmd_priority,
)
from rag_kernel.drift_control import ItemStatus, TrackedItem
from rag_kernel.drift_store import TrackedItemStore, load_hot


def _item(item_id: str, status: str, **kw) -> dict:
    return TrackedItem(
        id=item_id, title=f"item {item_id}", status=ItemStatus(status),
        session="S189", **kw,
    ).to_dict()


def _rag(tmp_path, items) -> "object":
    p = tmp_path / "RAG_MASTER.json"
    p.write_text(json.dumps({
        "meta": {"written_by_session": "S190"},
        "tracked_items": items,
    }), encoding="utf-8")
    (tmp_path / "RAG_MASTER.json.bak").write_text(p.read_text(encoding="utf-8"),
                                                  encoding="utf-8")
    return p


def _store(rag):
    return TrackedItemStore.from_hot(load_hot(rag))


def _ns(rag, command, item_id, **kw):
    base = dict(rag=rag, command=command, item_id=item_id, session="S190",
                reason="", dry_run=False, artifact=None, cite=None)
    base.update(kw)
    return argparse.Namespace(**base)


class TestResolveRequiresEvidence:
    def test_resolve_without_an_artifact_is_refused(self, tmp_path, capsys):
        rag = _rag(tmp_path, [_item("T1", "IN_PROGRESS")])
        assert cmd_item_transition(_ns(rag, "resolve", "T1")) == 1
        err = capsys.readouterr().err
        assert "RESOLVE-REQUIRES-EVIDENCE" in err
        assert "--artifact" in err, "a refusal must name its repair"
        assert json.loads(rag.read_text(encoding="utf-8"))[
            "tracked_items"][0]["status"] == "IN_PROGRESS", "nothing may be written"

    def test_an_artifact_that_does_not_exist_is_not_evidence(self, tmp_path, capsys):
        rag = _rag(tmp_path, [_item("T1", "IN_PROGRESS")])
        ns = _ns(rag, "resolve", "T1", artifact=["nope/missing.md"])
        assert cmd_item_transition(ns) == 1
        assert "do not exist" in capsys.readouterr().err

    def test_an_existing_artifact_resolves_and_is_recorded_in_history(self, tmp_path):
        rag = _rag(tmp_path, [_item("T1", "IN_PROGRESS")])
        proof = tmp_path / "PROOF.md"
        proof.write_text("evidence", encoding="utf-8")
        assert cmd_item_transition(
            _ns(rag, "resolve", "T1", artifact=[str(proof)])) == 0
        item = json.loads(rag.read_text(encoding="utf-8"))["tracked_items"][0]
        assert item["status"] == "RESOLVED"
        ev = item["history"][-1]
        assert ev["artifacts"] == [str(proof)], "evidence is a field, not prose"
        assert ev["reason"] == "", "the author's sentence is never overwritten"

    def test_artifacts_may_be_given_relative_to_the_rag_dir(self, tmp_path):
        rag = _rag(tmp_path, [_item("T1", "IN_PROGRESS")])
        (tmp_path / "PROOF.md").write_text("x", encoding="utf-8")
        assert _resolve_artifact("PROOF.md", rag) is not None
        assert _resolve_artifact("PROOF.md", rag).name == "PROOF.md"

    def test_a_missing_artifact_resolves_to_none(self, tmp_path):
        rag = _rag(tmp_path, [_item("T1", "OPEN")])
        assert _resolve_artifact("definitely/not/here.md", rag) is None

    def test_other_verbs_do_not_require_evidence(self, tmp_path):
        """The gate is aimed at DONE claims, not at parking or starting work."""
        rag = _rag(tmp_path, [_item("T1", "OPEN")])
        assert cmd_item_transition(_ns(rag, "start", "T1")) == 0

    def test_a_dry_run_refuses_too(self, tmp_path):
        """A dry run that says 'fine' about a write that would be refused lies."""
        rag = _rag(tmp_path, [_item("T1", "IN_PROGRESS")])
        assert cmd_item_transition(_ns(rag, "resolve", "T1", dry_run=True)) == 1


class TestSemanticPreconditionGate:
    def test_the_s189_marketing_landing_write_is_refused(self, tmp_path, capsys):
        """Replay of the exact S189 drift: a priority write onto a terminal item."""
        rag = _rag(tmp_path, [_item("MARKETING-LANDING", "RESOLVED")])
        ns = argparse.Namespace(rag=rag, item_id="MARKETING-LANDING",
                                priority_group="P1", session="S190",
                                dry_run=False, cite=None)
        assert cmd_priority(ns) == 1
        err = capsys.readouterr().err
        assert "SEMANTIC-PRECONDITION-GATE" in err
        assert "--cite" in err
        assert json.loads(rag.read_text(encoding="utf-8"))[
            "tracked_items"][0].get("priority_group", "") == ""

    def test_a_live_citation_lets_the_write_through(self, tmp_path):
        rag = _rag(tmp_path, [_item("MARKETING-LANDING", "RESOLVED"),
                              _item("SITE-SEO", "OPEN")])
        ns = argparse.Namespace(rag=rag, item_id="MARKETING-LANDING",
                                priority_group="P1", session="S190",
                                dry_run=False, cite="SITE-SEO")
        assert cmd_priority(ns) == 0

    def test_a_citation_that_resolves_to_nothing_is_refused(self, tmp_path, capsys):
        rag = _rag(tmp_path, [_item("X", "RESOLVED")])
        item, store = _store(rag).get("X"), _store(rag)
        assert _refuse_terminal_write("priority", item, "GHOST", store) == 1
        assert "resolves to no" in capsys.readouterr().err

    def test_a_citation_of_another_terminal_item_is_refused(self, tmp_path, capsys):
        rag = _rag(tmp_path, [_item("X", "RESOLVED"), _item("Y", "DISCARDED")])
        store = _store(rag)
        assert _refuse_terminal_write("priority", store.get("X"), "Y", store) == 1
        assert "terminal" in capsys.readouterr().err

    def test_live_items_are_untouched_by_the_gate(self, tmp_path):
        rag = _rag(tmp_path, [_item("X", "OPEN")])
        store = _store(rag)
        for verb in _PRECONDITION_VERBS:
            assert _refuse_terminal_write(verb, store.get("X"), None, store) is None

    def test_unguarded_verbs_are_untouched(self, tmp_path):
        rag = _rag(tmp_path, [_item("X", "RESOLVED")])
        store = _store(rag)
        assert _refuse_terminal_write("note", store.get("X"), None, store) is None

    def test_the_four_named_verbs_are_all_guarded(self):
        assert _PRECONDITION_VERBS == {"priority", "reopen", "resolve", "defer"}


class TestFlagsExist:
    @pytest.mark.parametrize("verb", ("resolve", "defer", "reopen", "start"))
    def test_artifact_and_cite_are_flags_on_the_item_verbs(self, verb):
        ns = build_parser().parse_args(
            [verb, "T1", "--session", "S190", "--artifact", "a.md",
             "--artifact", "b.md", "--cite", "T2"])
        assert ns.artifact == ["a.md", "b.md"]
        assert ns.cite == "T2"

    def test_priority_takes_cite(self):
        ns = build_parser().parse_args(
            ["priority", "T1", "P1", "--session", "S190", "--cite", "T2"])
        assert ns.cite == "T2"
