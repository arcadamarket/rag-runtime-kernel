"""Item-lifecycle core for DRIFT-ELIM — canonical project-state status control.

Pure, deterministic, stdlib-only, zero-LLM. This is increment 1 of the
DRIFT-ELIM milestone: the *pure core* only. It defines the canonical status
vocabulary and the lifecycle state machine that every tracked project item
(tasks, inference-ledger entries, errors, milestones) must obey. It does NOT
touch RAG_MASTER.json, the filesystem, or any kernel handle — persistence and
the CLI/auditor land in later increments.

What this exists to kill (the drift class)
-------------------------------------------
Project state — *what status an item is in* — has historically been recorded as
prose in several uncoordinated places: ``open_tasks`` free-text strings,
``deferred_items`` objects, ``inference_ledger`` dispositions, ERROR_LOG.md
prose, and the published repo docs. The same fact lived in many copies and the
copies drifted (E-034, E-037, E-039, E-040). ``guardgen`` already closed this
class for *state-machine transitions* by making the runtime transition table a
*derived artifact* of one formally-verified source. DRIFT-ELIM generalises that
same move to the operating protocol's own state:

    one canonical ``status`` field per item  ·  everything else renders from it
    every mutation goes through a deterministic, guarded, atomic API
    no hand-edited JSON, no prose authority

This module supplies the first two primitives of that programme: the constrained
status enum and the lifecycle transition guard — both expressed as *data plus a
fail-loud check*, exactly the guardgen philosophy ("rules are data, not
inference; illegal moves fail loud, never silently").

Design philosophy
-----------------
CS lens: the lifecycle is a small deterministic state machine. Legal moves are a
frozen transition table; an illegal move raises ItemStateError (fail-closed),
never a silent no-op. TrackedItem is immutable (frozen dataclass); a status
change returns a *new* item with an append-only history event, so the audit
trail is intrinsic and tampering is structurally visible.

ML lens: the LLM *proposes* a status change by name (resolve / defer / ...); this
core *decides* whether it is legal and *records* it deterministically. Token cost
is zero (no model in the path) and every TrackedItem round-trips through plain
JSON, so a render of canonical state into a status report (Rule 12) or ERROR_LOG
is a pure projection, never a re-authoring.

Convergence: "LLM proposes. System decides. State persists." — the same
invariant the whole kernel is built on, now applied to project bookkeeping.

@rag-kernel-manifest
{
  "module": "rag_kernel.drift_control",
  "capability": "item_lifecycle",
  "description": "Canonical project-state status enum + lifecycle state machine (DRIFT-ELIM increment 1: pure core, unregistered)",
  "exports": ["ItemStatus", "ItemKind", "TrackedItem", "StatusEvent",
              "LIFECYCLE", "TERMINAL_STATUSES", "legal_status_transition",
              "assert_status_transition", "ItemStateError", "ItemValidationError"],
  "use_when": "Recording or transitioning the canonical status of a tracked project item",
  "never_bypass": true
}
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum

# Bump when the lifecycle table or TrackedItem serialization format changes in a
# way that affects stored data (forces a migration / regression-test refresh).
LIFECYCLE_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class ItemStateError(Exception):
    """Raised on an illegal status transition.

    Fail-loud by design: a silent no-op (or a permissive 'just set the field')
    is exactly how status drift entered the project. An unknown or disallowed
    move must stop the caller, not be quietly absorbed.
    """


class ItemValidationError(Exception):
    """Raised when a TrackedItem's fields violate a structural invariant."""


# ---------------------------------------------------------------------------
# Canonical status vocabulary (the ONE constrained enum)
# ---------------------------------------------------------------------------

class ItemStatus(str, Enum):
    """The closed set of statuses a tracked item may hold.

    str-valued so members serialise to plain JSON strings and can be used as
    dict keys without conversion.
    """

    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    DEFERRED = "DEFERRED"
    SUPERSEDED = "SUPERSEDED"
    DISCARDED = "DISCARDED"


class ItemKind(str, Enum):
    """Origin/category of a tracked item.

    Kind is descriptive metadata; it does NOT affect the lifecycle (every kind
    obeys the same status machine). The set may be extended in increment 2 when
    the existing RAG stores are migrated into normalized tracked_items.
    """

    TASK = "TASK"            # open_tasks
    INFERENCE = "INFERENCE"  # inference_ledger
    ERROR = "ERROR"          # ERROR_LOG entries
    MILESTONE = "MILESTONE"  # build milestones / increments
    RELEASE = "RELEASE"      # release gates


# ---------------------------------------------------------------------------
# Lifecycle state machine (data + fail-loud guard)
# ---------------------------------------------------------------------------

# Legal status transitions. Derived directly from the DRIFT-ELIM directive:
#   OPEN -> IN_PROGRESS -> {RESOLVED | DEFERRED | SUPERSEDED | DISCARDED}
#   DEFERRED <-> OPEN
# plus the minimal sound closure: an OPEN item that was never started may still
# be parked (DEFERRED), replaced (SUPERSEDED) or dropped (DISCARDED) directly.
#
# RESOLVED / SUPERSEDED / DISCARDED are TERMINAL — no outgoing edges. This
# encodes the resolved-item discipline (E-030): a closed item never resurfaces;
# "reopening" is creating a new item, not mutating a terminal one. To resume a
# DEFERRED item there is exactly one re-entry path: DEFERRED -> OPEN -> IN_PROGRESS,
# which keeps every active item funnelled through OPEN.
LIFECYCLE: dict[ItemStatus, frozenset[ItemStatus]] = {
    ItemStatus.OPEN: frozenset({
        ItemStatus.IN_PROGRESS,
        ItemStatus.DEFERRED,
        ItemStatus.SUPERSEDED,
        ItemStatus.DISCARDED,
    }),
    ItemStatus.IN_PROGRESS: frozenset({
        ItemStatus.RESOLVED,
        ItemStatus.DEFERRED,
        ItemStatus.SUPERSEDED,
        ItemStatus.DISCARDED,
    }),
    ItemStatus.DEFERRED: frozenset({
        ItemStatus.OPEN,
    }),
    ItemStatus.RESOLVED: frozenset(),
    ItemStatus.SUPERSEDED: frozenset(),
    ItemStatus.DISCARDED: frozenset(),
}

# Statuses with no outgoing transitions.
TERMINAL_STATUSES: frozenset[ItemStatus] = frozenset(
    s for s, targets in LIFECYCLE.items() if not targets
)

# A transition into one of these statuses requires a superseding/justifying ref.
_REQUIRES_SUPERSEDED_BY: frozenset[ItemStatus] = frozenset({ItemStatus.SUPERSEDED})

# Structural self-check: every status appears as a LIFECYCLE key, and every
# target is itself a known status. Runs at import so a malformed table fails
# loud immediately rather than at the first transition.
_all = set(ItemStatus)
assert set(LIFECYCLE) == _all, "LIFECYCLE must define every ItemStatus as a source"
for _src, _targets in LIFECYCLE.items():
    _bad = set(_targets) - _all
    assert not _bad, f"LIFECYCLE[{_src}] has unknown targets: {_bad}"
del _all, _src, _targets, _bad


def _coerce_status(value: ItemStatus | str) -> ItemStatus:
    """Coerce a str or ItemStatus into an ItemStatus, fail-loud on unknown."""
    if isinstance(value, ItemStatus):
        return value
    try:
        return ItemStatus(value)
    except ValueError as exc:
        raise ItemStateError(f"unknown status: {value!r}") from exc


def legal_status_transition(
    from_status: ItemStatus | str, to_status: ItemStatus | str
) -> bool:
    """True iff ``from_status -> to_status`` is a legal lifecycle move.

    Pure predicate (no exceptions for a merely-illegal move — that's a False).
    Unknown status names DO raise ItemStateError, because an unrecognised status
    is a programming error, not a lifecycle decision.
    """
    src = _coerce_status(from_status)
    dst = _coerce_status(to_status)
    return dst in LIFECYCLE.get(src, frozenset())


def assert_status_transition(
    from_status: ItemStatus | str, to_status: ItemStatus | str
) -> None:
    """Raise ItemStateError unless ``from_status -> to_status`` is legal."""
    src = _coerce_status(from_status)
    dst = _coerce_status(to_status)
    if dst not in LIFECYCLE.get(src, frozenset()):
        allowed = sorted(s.value for s in LIFECYCLE.get(src, frozenset()))
        raise ItemStateError(
            f"illegal status transition {src.value} -> {dst.value}; "
            f"allowed from {src.value}: {allowed or '(terminal — none)'}"
        )


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StatusEvent:
    """One append-only record of a status transition."""

    from_status: ItemStatus
    to_status: ItemStatus
    session: str
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "from_status": self.from_status.value,
            "to_status": self.to_status.value,
            "session": self.session,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, d: dict) -> StatusEvent:
        return cls(
            from_status=_coerce_status(d["from_status"]),
            to_status=_coerce_status(d["to_status"]),
            session=d.get("session", ""),
            reason=d.get("reason", ""),
        )


# ---------------------------------------------------------------------------
# Tracked item
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TrackedItem:
    """A single normalized project-state item with ONE canonical status.

    Immutable: a status change returns a *new* TrackedItem via ``with_status``,
    appending a StatusEvent to ``history``. The status field here is the single
    source of truth for this item; any ERROR_LOG line, status-report cell, or
    doc mention is a *render* of it, never a competing copy.
    """

    id: str
    title: str
    status: ItemStatus
    kind: ItemKind = ItemKind.TASK
    session: str = ""              # session that last touched this item
    note: str = ""                # one-line context (not a prose authority)
    superseded_by: str | None = None   # set iff status == SUPERSEDED
    history: tuple[StatusEvent, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        # Coerce string inputs (e.g. from JSON) to enums without mutating a
        # frozen instance illegally — object.__setattr__ is the sanctioned path.
        object.__setattr__(self, "status", _coerce_status(self.status))
        if not isinstance(self.kind, ItemKind):
            try:
                object.__setattr__(self, "kind", ItemKind(self.kind))
            except ValueError as exc:
                raise ItemValidationError(f"unknown kind: {self.kind!r}") from exc
        if not self.id or not self.id.strip():
            raise ItemValidationError("TrackedItem.id must be non-empty")
        if not self.title or not self.title.strip():
            raise ItemValidationError("TrackedItem.title must be non-empty")
        self._validate_supersede_invariant(self.status, self.superseded_by)

    @staticmethod
    def _validate_supersede_invariant(
        status: ItemStatus, superseded_by: str | None
    ) -> None:
        if status in _REQUIRES_SUPERSEDED_BY:
            if not superseded_by or not str(superseded_by).strip():
                raise ItemValidationError(
                    f"status {status.value} requires a non-empty superseded_by"
                )
        elif superseded_by:
            raise ItemValidationError(
                f"superseded_by is only valid for {sorted(s.value for s in _REQUIRES_SUPERSEDED_BY)}, "
                f"not {status.value}"
            )

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def with_status(
        self,
        new_status: ItemStatus | str,
        *,
        session: str,
        reason: str = "",
        superseded_by: str | None = None,
    ) -> TrackedItem:
        """Return a new TrackedItem in ``new_status``, fail-loud if illegal.

        Validates the move against LIFECYCLE, enforces the superseded_by
        invariant, and appends a StatusEvent. Never mutates ``self``.
        """
        dst = _coerce_status(new_status)
        assert_status_transition(self.status, dst)
        new_ref = superseded_by if dst in _REQUIRES_SUPERSEDED_BY else None
        self._validate_supersede_invariant(dst, new_ref)
        event = StatusEvent(
            from_status=self.status,
            to_status=dst,
            session=session,
            reason=reason,
        )
        return replace(
            self,
            status=dst,
            session=session,
            superseded_by=new_ref,
            history=self.history + (event,),
        )

    def with_note(self, note: str, *, session: str) -> TrackedItem:
        """Return a new TrackedItem with an updated one-line ``note`` (metadata).

        The ``note`` is context, NOT the canonical status authority — refreshing
        it never changes ``status`` and therefore appends no StatusEvent (the
        history records lifecycle moves only). This is the *guarded* note path:
        the only sanctioned way to refresh a note is through this method (and the
        ``drift_store`` verb that wraps it), never a hand-edit of ``tracked_items``
        — that hand-edit is exactly the drift DRIFT-ELIM removes (INS-038). A
        stale note left behind by the absence of this path is what the inc-5
        auditor's note/status-contradiction check now flags.

        Fail-loud: ``note`` must be a string. ``session`` stamps who last touched
        the item, matching ``with_status``.
        """
        if not isinstance(note, str):
            raise ItemValidationError(
                f"note must be a string, got {type(note).__name__}"
            )
        return replace(self, note=note, session=session)

    def to_dict(self) -> dict:
        """Serialise to a JSON-round-trippable dict (canonical field included)."""
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status.value,
            "kind": self.kind.value,
            "session": self.session,
            "note": self.note,
            "superseded_by": self.superseded_by,
            "history": [e.to_dict() for e in self.history],
        }

    @classmethod
    def from_dict(cls, d: dict) -> TrackedItem:
        """Reconstruct a TrackedItem from its serialised form."""
        return cls(
            id=d["id"],
            title=d["title"],
            status=_coerce_status(d["status"]),
            kind=ItemKind(d["kind"]) if d.get("kind") else ItemKind.TASK,
            session=d.get("session", ""),
            note=d.get("note", ""),
            superseded_by=d.get("superseded_by"),
            history=tuple(StatusEvent.from_dict(e) for e in d.get("history", [])),
        )
