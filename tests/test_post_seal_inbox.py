"""POST-SEAL INBOX — the letter slot, and the refusal that makes it load-bearing.

POST-SEAL-INBOX-MISSING-S208: a sealed session keeps learning and, until this
module, had nowhere to put what it learned — `add` and `priority` are correctly
refused after a seal, so four S206-review findings reached their successor only
because the operator copied text between two windows.

These tests pin the two halves that make the fix a mechanism rather than a
feature: the deposit must survive, and the SEAL MUST REFUSE while anything is
undrained. The second half is the one worth guarding — a deposit box nobody is
forced to empty is a slower way to lose the same notes.
"""
from __future__ import annotations

import pytest

from rag_kernel import inbox


def test_post_then_owed(tmp_path) -> None:
    rec = inbox.post_note(tmp_path, from_session="S207",
                          title="poll gate is blind to fan-out",
                          note="66 of 113 calls, 45% under a second, gate never fired.")
    assert rec.id == "INBOX-S207-001"
    assert rec.drained is False
    assert inbox.undrained_count(tmp_path) == 1


def test_ids_are_sequential_per_session(tmp_path) -> None:
    inbox.post_note(tmp_path, from_session="S207", title="a", note="x")
    inbox.post_note(tmp_path, from_session="S207", title="b", note="y")
    inbox.post_note(tmp_path, from_session="S206", title="c", note="z")
    ids = [n.id for n in inbox.list_notes(tmp_path)]
    assert ids == ["INBOX-S207-001", "INBOX-S207-002", "INBOX-S206-001"]


def test_seal_is_blocked_while_undrained(tmp_path) -> None:
    """The load-bearing half: an undrained note must refuse the seal."""
    assert inbox.seal_blocker(tmp_path) is None
    inbox.post_note(tmp_path, from_session="S207", title="t", note="n")
    blocker = inbox.seal_blocker(tmp_path)
    assert blocker is not None
    assert "INBOX-S207-001" in blocker, "the refusal must NAME what is owed"
    assert "drain" in blocker, "the refusal must name the remedy, not just the problem"


def test_drain_clears_the_blocker(tmp_path) -> None:
    inbox.post_note(tmp_path, from_session="S207", title="t", note="n")
    inbox.drain_note(tmp_path, "INBOX-S207-001", by_session="S208",
                     action="banked", ref="SOME-ITEM-S208")
    assert inbox.undrained_count(tmp_path) == 0
    assert inbox.seal_blocker(tmp_path) is None


def test_banked_drain_requires_a_destination(tmp_path) -> None:
    """A drain with no ref is a status claim with nothing behind it."""
    inbox.post_note(tmp_path, from_session="S207", title="t", note="n")
    with pytest.raises(inbox.InboxError, match="requires --ref"):
        inbox.drain_note(tmp_path, "INBOX-S207-001", by_session="S208",
                         action="banked", ref="")


def test_discard_requires_a_stated_reason(tmp_path) -> None:
    inbox.post_note(tmp_path, from_session="S207", title="t", note="n")
    with pytest.raises(inbox.InboxError, match="requires --ref"):
        inbox.drain_note(tmp_path, "INBOX-S207-001", by_session="S208",
                         action="discarded", ref=None)


def test_draining_twice_is_fail_loud(tmp_path) -> None:
    """Re-draining would overwrite the record of what was decided."""
    inbox.post_note(tmp_path, from_session="S207", title="t", note="n")
    inbox.drain_note(tmp_path, "INBOX-S207-001", by_session="S208",
                     action="banked", ref="X-1")
    with pytest.raises(inbox.InboxError, match="already drained"):
        inbox.drain_note(tmp_path, "INBOX-S207-001", by_session="S208",
                         action="banked", ref="X-2")


def test_unknown_id_is_fail_loud(tmp_path) -> None:
    with pytest.raises(inbox.InboxError, match="no inbox note"):
        inbox.drain_note(tmp_path, "INBOX-S999-001", by_session="S208",
                         action="banked", ref="X-1")


def test_post_refuses_an_untriageable_note(tmp_path) -> None:
    with pytest.raises(inbox.InboxError):
        inbox.post_note(tmp_path, from_session="S207", title="", note="body")
    with pytest.raises(inbox.InboxError):
        inbox.post_note(tmp_path, from_session="S207", title="t", note="   ")
    with pytest.raises(inbox.InboxError):
        inbox.post_note(tmp_path, from_session="", title="t", note="n")


def test_boot_block_renders_the_note_in_full(tmp_path) -> None:
    """A count would send the successor looking; the body must be in the briefing."""
    body = "HEAD moved under an in-flight close twice; the stamp and status staled each other."
    assert inbox.render_boot_block(tmp_path) == ""
    inbox.post_note(tmp_path, from_session="S207", title="two live writers", note=body)
    block = inbox.render_boot_block(tmp_path)
    assert "two live writers" in block
    for word in body.split():
        assert word in block, "the note body must render in full, never summarised"
    assert "rag_kernel drain INBOX-S207-001" in block, "name the exact command"


def test_drained_notes_leave_the_boot_block(tmp_path) -> None:
    inbox.post_note(tmp_path, from_session="S207", title="t", note="n")
    inbox.drain_note(tmp_path, "INBOX-S207-001", by_session="S208",
                     action="discarded", ref="already tracked as X-1")
    assert inbox.render_boot_block(tmp_path) == ""
    kept = inbox.list_notes(tmp_path)[0]
    assert kept.drained_action == "discarded"
    assert kept.drained_ref == "already tracked as X-1"
    assert kept.history and kept.history[-1]["by"] == "S208"


def test_dry_run_writes_nothing(tmp_path) -> None:
    inbox.post_note(tmp_path, from_session="S207", title="t", note="n", dry_run=True)
    assert inbox.undrained_count(tmp_path) == 0
