"""POST-SEAL INBOX — a letter slot in a door that only opens one way.

THE DEFECT THIS EXISTS FOR (POST-SEAL-INBOX-MISSING-S208, measured three times in
this lineage). A session seals and then keeps living for hours, and it keeps
learning: S206-review learned four separate things AFTER its seal — two findings
it tried to bank and was correctly refused by ``CLOSE-DOUBLE-SEAL``, a gate
narrowness it discovered by being caught in it, and the answer to a question its
successor had asked. Every one of them reached the next session only because the
operator copied text from one window into another.

That is not inheritance. It is a courier, and a courier is exactly the "workflow
that depends on someone's attention" Rule 45 forbids. The seal is right to refuse
canonical writes — a post-seal write would make a sealed report describe a state
that never existed — so the answer is not to weaken the seal. It is to give the
sealed session somewhere legal to put a letter.

WHY *HERE*, chosen by measurement rather than taste. After the S206 seal, ``add``,
``priority`` and ``refresh-current-status`` were REFUSED while ``register-asset``
PASSED — because the latter writes ``RAG_CONTEXT.json``, not the sealed store. So
that surface is *already* proven legal for post-seal writes, and depositing into
it cannot invalidate a seal. That property is the whole design. ``.boot/`` was the
alternative and is wrong: it is scratch, the GC may remove it, no remote carries
it, and it is precisely where the four lost findings were sitting.

THE ENFORCER, without which this is one more good intention: ``session-end``
REFUSES to seal while the inbox holds undrained notes. Draining is therefore a
PRECONDITION of closing, not a courtesy — the successor must bank each note as a
governed item or explicitly discard it with a reason. Nothing here depends on
anyone remembering.

DECIDABLE PREDICATE: every record carries ``drained: false`` until a governed
transition cites its id. Undrained count is an integer; the gate compares it to
zero. There is no judgement call anywhere in this file.

WHAT A NOTE IS NOT: it is not a tracked item and it creates none. It is an
envelope. The successor reads it and decides — ``add`` it, or discard it with a
stated reason. Deposit is cheap precisely so that a sealed session never has to
weigh "is this worth the ceremony"; the weighing belongs to whoever drains it.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PARTITION_NAME = "inbox"

#: Stored beside the records, so the protocol travels with the data it governs
#: (same discipline as baked_assets) and a reader never needs this module to
#: understand what it is looking at.
DEFAULT_PROTOCOL = (
    "POST-SEAL INBOX. Append-only notes deposited by a session that has ALREADY "
    "SEALED and therefore cannot make a canonical write. A note is an envelope, "
    "not a tracked item. The next session drains each one by banking it as a "
    "governed item or discarding it with a reason; session-end REFUSES to seal "
    "while any note has drained=false. Written to RAG_CONTEXT.json because that "
    "surface is measured to accept writes after a seal, so depositing cannot "
    "invalidate the seal."
)

_ID_RE = re.compile(r"^INBOX-([A-Za-z0-9]+)-(\d{3})$")


class InboxError(RuntimeError):
    """Fail-loud base for inbox misuse."""


@dataclass
class InboxNote:
    """One deposited envelope."""

    id: str
    from_session: str
    title: str
    note: str
    posted_at: str
    drained: bool = False
    drained_by: Optional[str] = None
    drained_action: Optional[str] = None
    drained_ref: Optional[str] = None
    history: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "from_session": self.from_session,
            "title": self.title,
            "note": self.note,
            "posted_at": self.posted_at,
            "drained": self.drained,
            "drained_by": self.drained_by,
            "drained_action": self.drained_action,
            "drained_ref": self.drained_ref,
            "history": self.history,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "InboxNote":
        return cls(
            id=str(d.get("id", "")),
            from_session=str(d.get("from_session", "")),
            title=str(d.get("title", "")),
            note=str(d.get("note", "")),
            posted_at=str(d.get("posted_at", "")),
            drained=bool(d.get("drained", False)),
            drained_by=d.get("drained_by"),
            drained_action=d.get("drained_action"),
            drained_ref=d.get("drained_ref"),
            history=list(d.get("history") or []),
        )


def _manager(rag_dir: Path | str):
    from .asset_registry import _manager as asset_manager  # noqa: PLC0415

    return asset_manager(rag_dir)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_inbox(rag_dir: Path | str) -> dict:
    """The ``inbox`` partition as ``{"_protocol": str, "notes": [...]}``.

    A store or partition that does not exist yet returns the fresh default shape,
    so a deployment that has never posted costs zero boot tokens and still reads.
    """
    mgr = _manager(rag_dir)
    if not mgr.has_partition(PARTITION_NAME):
        return {"_protocol": DEFAULT_PROTOCOL, "notes": []}
    data = mgr.get(PARTITION_NAME)
    if not isinstance(data, dict):
        raise InboxError(f"{PARTITION_NAME} partition is not an object — inbox corrupt")
    if not isinstance(data.get("notes"), list):
        data = {**data, "notes": []}
    data.setdefault("_protocol", DEFAULT_PROTOCOL)
    return data


def list_notes(rag_dir: Path | str, *, undrained_only: bool = False) -> list[InboxNote]:
    """All notes in deposit order; ``undrained_only`` filters to what is owed."""
    notes = [
        InboxNote.from_dict(n)
        for n in load_inbox(rag_dir).get("notes", [])
        if isinstance(n, dict)
    ]
    return [n for n in notes if not n.drained] if undrained_only else notes


def undrained_count(rag_dir: Path | str) -> int:
    """The integer the seal gate compares against zero."""
    return len(list_notes(rag_dir, undrained_only=True))


def _next_id(existing: list[dict], from_session: str) -> str:
    used = 0
    for rec in existing:
        m = _ID_RE.match(str(rec.get("id", "")))
        if m and m.group(1) == from_session:
            used = max(used, int(m.group(2)))
    return f"INBOX-{from_session}-{used + 1:03d}"


def post_note(
    rag_dir: Path | str,
    *,
    from_session: str,
    title: str,
    note: str,
    now: Optional[str] = None,
    dry_run: bool = False,
) -> InboxNote:
    """Deposit one envelope. Append-only; nothing existing is ever rewritten.

    Deliberately permissive about WHEN it may be called: this verb exists for the
    sealed case, so refusing a sealed session here would defeat the entire point.
    It writes no canonical state and creates no tracked item, so an unnecessary
    note costs one line and a drain decision — far cheaper than a lost finding.
    """
    title = (title or "").strip()
    note = (note or "").strip()
    from_session = (from_session or "").strip()
    if not from_session:
        raise InboxError("post: --from <session id> is required")
    if not title:
        raise InboxError("post: --title is required; a note nobody can triage is noise")
    if not note:
        raise InboxError(
            "post: --note is required. RETRO-CLARITY: the successor reads this with "
            "no transcript, so state what was measured and what it implies."
        )

    data = load_inbox(rag_dir)
    records = list(data.get("notes", []))
    rec = InboxNote(
        id=_next_id(records, from_session),
        from_session=from_session,
        title=title,
        note=note,
        posted_at=now or _now(),
    )
    if dry_run:
        return rec
    records.append(rec.to_dict())
    _manager(rag_dir).update_partition(
        PARTITION_NAME, {"_protocol": data.get("_protocol", DEFAULT_PROTOCOL), "notes": records}
    )
    return rec


def drain_note(
    rag_dir: Path | str,
    note_id: str,
    *,
    by_session: str,
    action: str,
    ref: Optional[str] = None,
    now: Optional[str] = None,
    dry_run: bool = False,
) -> InboxNote:
    """Mark one note handled. ``action`` is ``banked`` or ``discarded``.

    ``banked`` REQUIRES ``ref`` — the tracked-item id the note became. Draining
    without saying what happened to it would reproduce, one level down, the exact
    defect the resolve-evidence gate exists to stop: a status with nothing behind it.
    """
    action = (action or "").strip().lower()
    if action not in ("banked", "discarded"):
        raise InboxError("drain: --action must be 'banked' or 'discarded'")
    if action == "banked" and not (ref or "").strip():
        raise InboxError(
            "drain --action banked requires --ref <tracked item id>: a drain with no "
            "destination is a status claim with nothing behind it."
        )
    if action == "discarded" and not (ref or "").strip():
        raise InboxError(
            "drain --action discarded requires --ref <reason>: discarding a note is a "
            "decision, and a decision with no stated reason cannot be reviewed."
        )

    data = load_inbox(rag_dir)
    records = list(data.get("notes", []))
    for i, raw in enumerate(records):
        if not isinstance(raw, dict) or str(raw.get("id")) != note_id:
            continue
        rec = InboxNote.from_dict(raw)
        if rec.drained:
            raise InboxError(
                f"{note_id} is already drained by {rec.drained_by} "
                f"({rec.drained_action}: {rec.drained_ref}) — draining twice would "
                f"overwrite the record of what was decided."
            )
        rec.drained = True
        rec.drained_by = by_session
        rec.drained_action = action
        rec.drained_ref = ref
        rec.history = [*rec.history, {"at": now or _now(), "by": by_session,
                                      "action": action, "ref": ref}]
        if dry_run:
            return rec
        records[i] = rec.to_dict()
        _manager(rag_dir).update_partition(
            PARTITION_NAME,
            {"_protocol": data.get("_protocol", DEFAULT_PROTOCOL), "notes": records},
        )
        return rec
    raise InboxError(f"no inbox note with id {note_id!r} — run `rag_kernel inbox` to list")


def render_boot_block(rag_dir: Path | str, *, width: int = 96) -> str:
    """The block ``session-start`` prints. Empty string when nothing is owed.

    Notes render IN FULL, never truncated to a count. A summary line would make
    the successor go looking, and a handoff that requires going looking is the
    thing this whole mechanism replaces.
    """
    owed = list_notes(rag_dir, undrained_only=True)
    if not owed:
        return ""
    lines = [f"[INBOX] {len(owed)} undelivered note(s) — you cannot seal until each is drained:"]
    for n in owed:
        lines.append(f"  {n.id}  (from {n.from_session}, {n.posted_at})")
        lines.append(f"    {n.title}")
        for para in n.note.splitlines() or [""]:
            para = para.rstrip()
            while len(para) > width:
                cut = para.rfind(" ", 0, width)
                cut = cut if cut > 0 else width
                lines.append(f"      {para[:cut]}")
                para = para[cut:].lstrip()
            lines.append(f"      {para}")
        lines.append(
            f"    DRAIN IT: rag_kernel drain {n.id} --session <yours> "
            f"--action banked --ref <item-id>   (or --action discarded --ref \"<reason>\")"
        )
    return "\n".join(lines)


def seal_blocker(rag_dir: Path | str) -> Optional[str]:
    """The refusal text for ``session-end``, or None when the inbox is drained."""
    owed = list_notes(rag_dir, undrained_only=True)
    if not owed:
        return None
    ids = ", ".join(n.id for n in owed)
    return (
        f"INBOX-DRAIN gate — {len(owed)} inbox note(s) from a prior session are "
        f"undrained: {ids}. A note deposited after a seal is knowledge that has no "
        f"other way to reach anyone; sealing over it loses it silently, which is the "
        f"POST-SEAL-INBOX-MISSING-S208 defect reappearing one level up. Bank each as "
        f"a tracked item (`rag_kernel drain <id> --session <yours> --action banked "
        f"--ref <item-id>`) or discard it with a stated reason (`--action discarded "
        f"--ref \"<why>\"`), then close again."
    )


__all__ = [
    "PARTITION_NAME",
    "DEFAULT_PROTOCOL",
    "InboxError",
    "InboxNote",
    "load_inbox",
    "list_notes",
    "undrained_count",
    "post_note",
    "drain_note",
    "render_boot_block",
    "seal_blocker",
]
