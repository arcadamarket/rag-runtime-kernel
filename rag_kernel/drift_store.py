"""Deterministic mutation API over the RAG for DRIFT-ELIM — increment 2.

Increment 1 (``drift_control``) supplied the *pure core*: the canonical
``ItemStatus`` enum, the ``LIFECYCLE`` state machine, the fail-loud transition
guards, and the immutable ``TrackedItem``. That core never touched a file.

This module is the *persistence + mutation* layer that sits directly on top of
it. It does three things and nothing else:

1. **Normalizes** the project's tracked state into ONE array — ``tracked_items``
   in RAG_MASTER.json — read into / written from a ``TrackedItemStore`` keyed by
   id (unique-id invariant, deterministic id-sorted serialization).

2. **Mutates** that state ONLY through guarded operations. Every status change
   routes through ``TrackedItem.with_status`` (the increment-1 lifecycle guard),
   so an illegal move (or an unknown id, or a duplicate id) fails LOUD and writes
   nothing. There is no "just set the field" path — that path is exactly how
   status drift entered the project (E-034 / E-037 / E-039 / E-040).

3. **Persists** atomically. File writes go through ``persistence.atomic_write_json``
   (tmp -> verify -> .bak -> rename), so RAG_MASTER.json is never left half-written
   and the ``.bak`` is refreshed on every commit. No hand-edited JSON: the bytes on
   disk are produced by the deterministic serializer over validated TrackedItems.

Scope boundary (mirrors GRAPH-ORCH / drift_control increment 1)
---------------------------------------------------------------
NOT yet registered in ``_KERNEL_MODULES`` / ``discover()`` / ``cmd_health`` — that
is increment 3, together with the ``rag_kernel resolve|defer`` CLI. Rendering the
legacy ``open_tasks`` / ``deferred_items`` / ERROR_LOG / status-report *from* this
canonical array is increment 4. The fail-loud session auditor is increment 5.
The orchestrator + this drift layer ship together as the single-shot v0.4.0; until
then everything here is UNRELEASED on main (a developer checkpoint, not a feature).

Design philosophy
-----------------
CS lens: a mutation is a transaction — load, apply a guarded transition over an
in-memory store with a unique-id invariant, serialize deterministically, atomic
rename. Crash at any point leaves the prior RAG_MASTER.json + its .bak intact.

ML lens: the LLM *proposes* "resolve TASK X" / "defer Y" by name; this layer
*decides* legality (lifecycle guard) and *persists* deterministically (zero model
in the write path, zero token cost). The canonical array is the one field every
later render projects from — never a competing copy.

Convergence: "LLM proposes. System decides. State persists." — applied to the
project's own bookkeeping.

@rag-kernel-manifest
{
  "module": "rag_kernel.drift_store",
  "capability": "item_store",
  "description": "Deterministic, atomic mutation API over the RAG tracked_items array (DRIFT-ELIM increment 2: store + persistence + migration, unregistered)",
  "exports": ["TrackedItemStore", "DriftStoreError", "DuplicateItemError",
              "UnknownItemError", "TRACKED_ITEMS_KEY", "DRIFT_STORE_VERSION",
              "load_hot", "mutate_hot", "transition_in_file", "set_note_in_file",
              "set_priority_in_file",
              "seed_items", "migrate_backlog", "migrate_backlog_file",
              "LEDGER_DISPOSITION_TO_STATUS", "INFERENCE_LEDGER_KEY",
              "ledger_disposition_to_status", "inference_specs_from_hot",
              "add_items", "add_items_file", "remove_item_file",
              "OPERATING_PROTOCOL_KEY", "add_operating_protocol_rule",
              "add_operating_protocol_rule_file",
              "set_operating_protocol_rule", "set_operating_protocol_rule_file",
              "SESSIONS_RECENT_KEY", "sessions_recent_duplicate_pairs",
              "dedup_sessions_recent", "dedup_sessions_recent_file",
              "CURRENT_STATUS_KEY", "CurrentStatusRefreshError",
              "compute_current_status_refresh", "refresh_current_status_file"],
  "use_when": "Reading, transitioning, or persisting the canonical status of tracked project items in RAG_MASTER.json",
  "never_bypass": true
}
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping, Optional, Sequence

from rag_kernel.drift_control import (
    ItemKind,
    ItemStatus,
    TrackedItem,
)
from rag_kernel.persistence import atomic_write_json

# Bump when the on-disk layout of tracked_items / this module's contract changes.
# 1.1.0 (FIX-5/P3): added the guarded operating_protocol rule-mutation path
# (add_operating_protocol_rule[_file]) — an additive contract extension.
# 1.2.0 (KA-2 increment B): added the governed sessions_recent dedup verb
# (dedup_sessions_recent[_file]) that REPAIRS the duplicate-bootstrap rows the
# KA-2 auditor (increment A) detects, plus the single-source detection predicate
# (sessions_recent_duplicate_pairs / _sessions_recent_key) the auditor now consumes
# instead of its own inline copy — one predicate for detect AND repair.
# 1.3.0 (KA-CUTOVER-GATE): added the governed un-add path (store.remove +
# remove_item_file) — the exact inverse of add, permitted ONLY on a pristine
# (empty-history) item, so a mis-``add`` (wrong id/kind/status) is recoverable
# without hand-editing JSON. Pairs with the record-coverage gate now counting
# only NON-retired members (drift_control.RETIRED_STATUSES).
# 1.4.0 (KA-CS-REFRESH): added the governed current_status refresh path
# (compute_current_status_refresh / refresh_current_status_file) — the atomic
# REPAIR half of the E-043 freshness guard, re-stamping the denormalized runtime
# version + git HEAD (+ optional test count) tokens a mid-session dev commit
# leaves stale. The leading-token constants/regexes it shares with
# drift_audit.check_current_status_freshness now live HERE (the lower module) as
# the single source of truth; drift_audit imports + re-exports them, so detection
# and repair can never disagree (same DRY pattern as the shared date coercers).
# 1.5.0 — KA-CS-PROSE-DRIFT: the current_status refresh now re-stamps EVERY labeled
#         "RUNTIME RELEASE vX" / "runtime-vX" token in the covered fields (shared
#         _CS_RELEASE_RE / _CS_RELEASE_FIELDS + _refresh_all_tokens), closing the
#         secondary-narrative drift the leading-token-only refresh left stale. The
#         paired guard half lives in drift_audit.check_current_status_freshness.
# 1.6.0 — REPORT-PRIORITY-GROUPS inc1: added the governed priority-assignment path
#         (store.set_priority + set_priority_in_file) — the only sanctioned way to
#         set an item's Rule 21 priority_group (P1..P5 / "" to clear), routed
#         through TrackedItem.with_priority so the bucket is never hand-edited into
#         tracked_items. Additive; existing items untouched (no group => byte-for-
#         byte serialization, per drift_control 1.1.0). Feeds the burn-down render.
DRIFT_STORE_VERSION = "1.6.0"

# The single canonical array key inside RAG_MASTER.json (HOT). Everything else
# that mentions item status is, or will become, a render of this array.
TRACKED_ITEMS_KEY = "tracked_items"

# The append-only ledger of per-session checkpoints inside RAG_MASTER.json (HOT).
SESSIONS_RECENT_KEY = "sessions_recent"

_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class DriftStoreError(Exception):
    """Base error for the drift store."""


class DuplicateItemError(DriftStoreError):
    """Raised when two tracked items share an id (the unique-id invariant)."""


class UnknownItemError(DriftStoreError):
    """Raised when an operation targets an id that is not in the store."""


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class TrackedItemStore:
    """In-memory normalized store of :class:`TrackedItem`, keyed by id.

    Invariants enforced on every path:
      * ids are unique (adding a duplicate id fails loud);
      * every element is a valid TrackedItem (constructed via the inc-1 core);
      * serialization is deterministic — items are emitted id-sorted, so a write
        produces a stable, minimal diff regardless of insertion order.

    All status changes go through :meth:`transition` (and its named shortcuts),
    which delegate to ``TrackedItem.with_status`` — the lifecycle guard. There is
    deliberately no method that sets ``status`` without a guarded transition.
    """

    def __init__(self, items: Iterable[TrackedItem] = ()) -> None:
        self._items: dict[str, TrackedItem] = {}
        for item in items:
            self.add(item)

    # -- construction -------------------------------------------------------

    @classmethod
    def from_hot(cls, hot: Mapping) -> "TrackedItemStore":
        """Build a store from a HOT dict's ``tracked_items`` array (may be absent)."""
        raw = hot.get(TRACKED_ITEMS_KEY, []) if hot else []
        if not isinstance(raw, list):
            raise DriftStoreError(
                f"{TRACKED_ITEMS_KEY!r} must be a list, got {type(raw).__name__}"
            )
        return cls(TrackedItem.from_dict(d) for d in raw)

    # -- queries ------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, item_id: object) -> bool:
        return item_id in self._items

    def __iter__(self):
        """Iterate items in deterministic id-sorted order."""
        for key in sorted(self._items):
            yield self._items[key]

    def ids(self) -> list[str]:
        return sorted(self._items)

    def get(self, item_id: str) -> TrackedItem:
        try:
            return self._items[item_id]
        except KeyError as exc:
            raise UnknownItemError(f"no tracked item with id {item_id!r}") from exc

    def by_status(self, status: ItemStatus | str) -> list[TrackedItem]:
        want = ItemStatus(status) if not isinstance(status, ItemStatus) else status
        return [it for it in self if it.status == want]

    def by_kind(self, kind: ItemKind | str) -> list[TrackedItem]:
        want = ItemKind(kind) if not isinstance(kind, ItemKind) else kind
        return [it for it in self if it.kind == want]

    # -- mutations (all guarded / fail-loud) --------------------------------

    def add(self, item: TrackedItem) -> TrackedItem:
        """Insert a new item. Fail loud on a duplicate id."""
        if not isinstance(item, TrackedItem):
            raise DriftStoreError(f"expected TrackedItem, got {type(item).__name__}")
        if item.id in self._items:
            raise DuplicateItemError(f"duplicate tracked item id: {item.id!r}")
        self._items[item.id] = item
        return item

    def transition(
        self,
        item_id: str,
        new_status: ItemStatus | str,
        *,
        session: str,
        reason: str = "",
        superseded_by: Optional[str] = None,
    ) -> TrackedItem:
        """Transition one item to ``new_status`` through the lifecycle guard.

        Unknown id -> UnknownItemError; illegal move -> ItemStateError (from the
        inc-1 core). On success the store holds the new immutable item (with an
        appended history event) and the old one is discarded.
        """
        current = self.get(item_id)
        updated = current.with_status(
            new_status, session=session, reason=reason, superseded_by=superseded_by
        )
        self._items[item_id] = updated
        return updated

    def start(self, item_id: str, *, session: str, reason: str = "") -> TrackedItem:
        return self.transition(item_id, ItemStatus.IN_PROGRESS, session=session, reason=reason)

    def resolve(self, item_id: str, *, session: str, reason: str = "") -> TrackedItem:
        return self.transition(item_id, ItemStatus.RESOLVED, session=session, reason=reason)

    def defer(self, item_id: str, *, session: str, reason: str = "") -> TrackedItem:
        return self.transition(item_id, ItemStatus.DEFERRED, session=session, reason=reason)

    def reopen(self, item_id: str, *, session: str, reason: str = "") -> TrackedItem:
        """Re-enter a DEFERRED item: DEFERRED -> OPEN (the one resume path)."""
        return self.transition(item_id, ItemStatus.OPEN, session=session, reason=reason)

    def discard(self, item_id: str, *, session: str, reason: str = "") -> TrackedItem:
        return self.transition(item_id, ItemStatus.DISCARDED, session=session, reason=reason)

    def supersede(
        self, item_id: str, *, by: str, session: str, reason: str = ""
    ) -> TrackedItem:
        return self.transition(
            item_id, ItemStatus.SUPERSEDED, session=session, reason=reason, superseded_by=by
        )

    def set_note(self, item_id: str, note: str, *, session: str) -> TrackedItem:
        """Refresh an item's one-line ``note`` through the guarded core (INS-038).

        Routes through :meth:`TrackedItem.with_note` — the only sanctioned note
        path — so a note is never refreshed by hand-editing ``tracked_items``
        (that hand-edit IS the drift). Unknown id -> UnknownItemError. The status
        is untouched (a note is metadata, not the canonical authority), so this
        adds no StatusEvent. Returns the new immutable item.
        """
        current = self.get(item_id)
        updated = current.with_note(note, session=session)
        self._items[item_id] = updated
        return updated

    def set_priority(
        self, item_id: str, priority_group: str, *, session: str
    ) -> TrackedItem:
        """Set an item's Rule 21 ``priority_group`` through the guarded core.

        Routes through :meth:`TrackedItem.with_priority` — the only sanctioned
        path — so the bucket is never assigned by hand-editing ``tracked_items``
        (that hand-edit IS the drift DRIFT-ELIM removes). The value is validated
        fail-loud against ``ALLOWED_PRIORITY_GROUPS`` (P1..P5, or "" to clear).
        Status is untouched (priority is metadata, not the canonical authority),
        so this adds no StatusEvent. Unknown id -> UnknownItemError. Returns the
        new immutable item.
        """
        current = self.get(item_id)
        updated = current.with_priority(priority_group, session=session)
        self._items[item_id] = updated
        return updated

    def remove(self, item_id: str) -> TrackedItem:
        """Un-add a PRISTINE item — the exact inverse of :meth:`add`.

        Removal deletes the record from the canonical array entirely; it is NOT a
        lifecycle transition. To keep un-add from erasing real project history it
        is permitted ONLY while the item is still in its as-added state: an EMPTY
        status ``history`` (never routed through a guarded transition). A mis-
        ``add`` (wrong id / kind / status) is caught in exactly this state, so
        un-add cleanly retracts it and lets the record-coverage cutover gate fall
        back to its correct pre-migration state. Any item that has since moved
        through the lifecycle has a non-empty history and is protected — use
        ``discard`` / ``supersede`` for those.

        Unknown id -> :class:`UnknownItemError`; a transitioned (non-pristine)
        item -> :class:`DriftStoreError`. Nothing is mutated on either failure.
        Returns the removed item.
        """
        item = self.get(item_id)  # UnknownItemError if absent — no mutation
        if item.history:
            raise DriftStoreError(
                f"cannot un-add {item_id!r}: it carries {len(item.history)} "
                f"lifecycle event(s) and is a real tracked item — un-add is only "
                f"for a pristine mis-add (empty history). Use discard/supersede."
            )
        del self._items[item_id]
        return item

    # -- serialization ------------------------------------------------------

    def to_list(self) -> list[dict]:
        """Serialize to a deterministic, id-sorted list of dicts."""
        return [self._items[key].to_dict() for key in sorted(self._items)]

    def write_into(self, hot: dict) -> dict:
        """Write the canonical array into ``hot[tracked_items]`` (in place)."""
        hot[TRACKED_ITEMS_KEY] = self.to_list()
        return hot


# ---------------------------------------------------------------------------
# File-level atomic persistence
# ---------------------------------------------------------------------------

def load_hot(path: Path | str) -> dict:
    """Load a HOT (RAG_MASTER.json) dict from ``path``. Fail loud on bad JSON."""
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise DriftStoreError(f"HOT root must be a JSON object, got {type(data).__name__}")
    return data


def _touch_meta(hot: dict, now: Optional[str]) -> None:
    """Stamp ``meta.last_updated_utc`` so a render knows when the array last moved."""
    meta = hot.get("meta")
    if isinstance(meta, dict):
        meta["last_updated_utc"] = now or datetime.now(timezone.utc).strftime(_TS_FORMAT)


def mutate_hot(
    path: Path | str,
    mutator: Callable[[TrackedItemStore], None],
    *,
    now: Optional[str] = None,
    touch_meta: bool = True,
) -> dict:
    """Load -> guarded mutate -> atomic write, as one transaction.

    ``mutator`` receives the live :class:`TrackedItemStore`; any guard it trips
    (illegal transition, unknown/duplicate id) propagates and NOTHING is written.
    On success the updated ``tracked_items`` array is written back atomically via
    ``atomic_write_json`` (which refreshes the ``.bak``). Returns the new HOT dict.
    """
    p = Path(path)
    hot = load_hot(p)
    store = TrackedItemStore.from_hot(hot)
    mutator(store)                 # may raise -> no write, original file intact
    store.write_into(hot)
    if touch_meta:
        _touch_meta(hot, now)
    atomic_write_json(  # FIX-4 (K6): parity-mirror .bak; FIX-7 (T1): live side-store guard
        p, hot, mirror_bak=True, guard_side_stores=True
    )
    return hot


def transition_in_file(
    path: Path | str,
    item_id: str,
    new_status: ItemStatus | str,
    *,
    session: str,
    reason: str = "",
    superseded_by: Optional[str] = None,
    now: Optional[str] = None,
) -> dict:
    """Atomically apply a single guarded transition to one item in a RAG file."""
    return mutate_hot(
        path,
        lambda store: store.transition(
            item_id, new_status, session=session, reason=reason, superseded_by=superseded_by
        ),
        now=now,
    )


def set_note_in_file(
    path: Path | str,
    item_id: str,
    note: str,
    *,
    session: str,
    now: Optional[str] = None,
) -> dict:
    """Atomically refresh one item's ``note`` in a RAG file (INS-038).

    The guarded note-update counterpart to :func:`transition_in_file`: load ->
    ``store.set_note`` -> atomic write (tmp -> verify -> .bak -> rename). The
    canonical ``status`` is never touched. Fails loud (and writes nothing) on an
    unknown id or a non-string note.
    """
    return mutate_hot(
        path,
        lambda store: store.set_note(item_id, note, session=session),
        now=now,
    )


def set_priority_in_file(
    path: Path | str,
    item_id: str,
    priority_group: str,
    *,
    session: str,
    now: Optional[str] = None,
) -> dict:
    """Atomically set one item's Rule 21 ``priority_group`` in a RAG file.

    The guarded priority-assignment counterpart to :func:`set_note_in_file`: load
    -> ``store.set_priority`` -> atomic write (tmp -> verify -> .bak -> rename).
    The canonical ``status`` is never touched. Fails loud (and writes nothing) on
    an unknown id or a bucket outside ``ALLOWED_PRIORITY_GROUPS``.
    """
    return mutate_hot(
        path,
        lambda store: store.set_priority(item_id, priority_group, session=session),
        now=now,
    )


def remove_item_file(
    path: Path | str,
    item_id: str,
    *,
    now: Optional[str] = None,
) -> dict:
    """Atomically un-add a pristine item from a RAG file's tracked_items array.

    The guarded, atomic counterpart to :func:`add_items_file` (its inverse): load
    -> ``store.remove`` (pristine-only guard) -> ``atomic_write_json`` (tmp ->
    verify -> .bak -> rename). If the guard trips (unknown id, or a transitioned
    item with lifecycle history) nothing is written and the prior file + ``.bak``
    stay intact. KA-CUTOVER-GATE: makes a mis-``add`` recoverable without a
    hand-edit — the exact manual-JSON drift the project forbids (E-037/E-040).
    """
    return mutate_hot(
        path,
        lambda store: store.remove(item_id),
        now=now,
    )


# ---------------------------------------------------------------------------
# Backlog migration (legacy prose stores -> normalized tracked_items)
# ---------------------------------------------------------------------------

def seed_items(specs: Sequence[Mapping]) -> list[TrackedItem]:
    """Build validated TrackedItems from explicit seed specs (fail-loud on dups).

    A *seed* is the canonical initial status of a migrated item — constructed
    directly at its target status (no transition, empty history). The status in
    each spec is an explicit, human-authored proposal of the item's true state;
    determinism here is in the construction + unique-id validation + serialization,
    not in any text parsing of the legacy prose (which is exactly the unreliable
    authority DRIFT-ELIM removes).

    Each spec: ``{id, title, status, kind?, session?, note?, superseded_by?}``.
    """
    store = TrackedItemStore()  # reuse the unique-id invariant
    for spec in specs:
        store.add(
            TrackedItem(
                id=spec["id"],
                title=spec["title"],
                status=ItemStatus(spec["status"]) if not isinstance(spec["status"], ItemStatus) else spec["status"],
                kind=spec.get("kind", ItemKind.TASK),
                session=spec.get("session", ""),
                note=spec.get("note", ""),
                superseded_by=spec.get("superseded_by"),
            )
        )
    return list(store)


def migrate_backlog(
    hot: dict,
    specs: Sequence[Mapping],
    *,
    allow_overwrite: bool = False,
) -> dict:
    """Populate ``hot[tracked_items]`` from seed specs (pure on the dict).

    Refuses to clobber a non-empty existing array unless ``allow_overwrite`` —
    migration is a one-time seeding, not a routine mutation path (use the store /
    ``transition_in_file`` for ongoing changes). Returns ``hot`` (mutated in place).
    """
    existing = hot.get(TRACKED_ITEMS_KEY)
    if existing and not allow_overwrite:
        raise DriftStoreError(
            f"{TRACKED_ITEMS_KEY!r} already has {len(existing)} items; "
            "pass allow_overwrite=True to re-seed"
        )
    store = TrackedItemStore(seed_items(specs))
    return store.write_into(hot)


def migrate_backlog_file(
    path: Path | str,
    specs: Sequence[Mapping],
    *,
    allow_overwrite: bool = False,
    now: Optional[str] = None,
    touch_meta: bool = True,
) -> dict:
    """Seed ``tracked_items`` in a RAG file atomically (refreshes ``.bak``)."""
    p = Path(path)
    hot = load_hot(p)
    migrate_backlog(hot, specs, allow_overwrite=allow_overwrite)
    if touch_meta:
        _touch_meta(hot, now)
    atomic_write_json(  # FIX-4 (K6): parity-mirror .bak; FIX-7 (T1): live side-store guard
        p, hot, mirror_bak=True, guard_side_stores=True
    )
    return hot


# ---------------------------------------------------------------------------
# Record migration (inference_ledger / ERROR_LOG -> canonical tracked_items)
# ---------------------------------------------------------------------------
# DRIFT-ELIM increment 6 (INS-039). Increments 1-5 made tracked_items the sole
# authority for the TASK/MILESTONE backlog (open_tasks / deferred_items became
# renders). The two remaining legacy state stores — the ``inference_ledger``
# dispositions and the ERROR_LOG ``E-###`` records — are folded into the SAME
# canonical array here (kind=INFERENCE / kind=ERROR) so the session auditor
# governs their status too. The forensic prose stays in inference_ledger /
# ERROR_LOG.md; only the *status* becomes canonical in tracked_items.

# The single canonical array also absorbs the ledger; this key names the legacy
# ledger array the INFERENCE records are projected from.
INFERENCE_LEDGER_KEY = "inference_ledger"

# Map an inference_ledger ``disposition`` onto the canonical ItemStatus. The
# ledger's disposition vocabulary predates the lifecycle enum; this is the one
# explicit, fail-loud bridge between them (an unknown disposition RAISES — it is
# never silently rounded, which is exactly the lossy drift E-040 documented).
#   OPEN       -> OPEN        (still an open intake item)
#   SCHEDULED  -> RESOLVED    (converted into a concrete tracked task / shipped)
#   DONE       -> RESOLVED    (legacy synonym for a completed intake)
#   DEFERRED   -> DEFERRED    (parked, not lost)
#   SUPERSEDED -> SUPERSEDED  (replaced; carries a superseded_by ref)
#   DISCARDED  -> DISCARDED   (dropped with a reason)
LEDGER_DISPOSITION_TO_STATUS: dict[str, ItemStatus] = {
    "OPEN": ItemStatus.OPEN,
    "SCHEDULED": ItemStatus.RESOLVED,
    "DONE": ItemStatus.RESOLVED,
    "DEFERRED": ItemStatus.DEFERRED,
    "SUPERSEDED": ItemStatus.SUPERSEDED,
    "DISCARDED": ItemStatus.DISCARDED,
}


def ledger_disposition_to_status(disposition: str) -> ItemStatus:
    """Map an inference_ledger disposition to the canonical ItemStatus (fail-loud).

    Unknown dispositions raise :class:`DriftStoreError` rather than defaulting —
    a silent default is precisely the lossy-rounding that let two stores disagree.
    """
    try:
        return LEDGER_DISPOSITION_TO_STATUS[str(disposition).strip().upper()]
    except KeyError as exc:
        raise DriftStoreError(
            f"unknown inference_ledger disposition: {disposition!r}; "
            f"known: {sorted(LEDGER_DISPOSITION_TO_STATUS)}"
        ) from exc


def _condense(text: str, *, limit: int) -> str:
    """Collapse whitespace and clip ``text`` to ``limit`` chars (ellipsis if cut)."""
    s = " ".join(str(text or "").split())
    if len(s) <= limit:
        return s
    return s[: limit - 1].rstrip() + "…"


def inference_specs_from_hot(hot: Mapping) -> list[dict]:
    """Derive canonical INFERENCE item specs from the RAG's own inference_ledger.

    A deterministic, pure projection (no I/O): id verbatim, title from the entry
    summary, status from :func:`ledger_disposition_to_status`, kind=INFERENCE,
    note carrying the scheduled_as pointer so the canonical record stays
    self-describing. The caller persists the specs via :func:`add_items_file`
    (the guarded, atomic path) — this function never writes.
    """
    led = hot.get(INFERENCE_LEDGER_KEY, []) if hot else []
    if not isinstance(led, list):
        raise DriftStoreError(
            f"{INFERENCE_LEDGER_KEY!r} must be a list, got {type(led).__name__}"
        )
    specs: list[dict] = []
    for e in led:
        status = ledger_disposition_to_status(e.get("disposition"))
        note = e.get("scheduled_as") or ""
        spec: dict = {
            "id": e["id"],
            "title": _condense(e.get("summary", e["id"]), limit=100) or e["id"],
            "status": status,
            "kind": ItemKind.INFERENCE,
            "session": e.get("session", ""),
            "note": _condense(note, limit=120),
        }
        if status == ItemStatus.SUPERSEDED:
            spec["superseded_by"] = e.get("superseded_by")
        specs.append(spec)
    return specs


def add_items(
    hot: dict,
    specs: Sequence[Mapping],
    *,
    allow_existing: bool = False,
) -> dict:
    """Additively merge new TrackedItems into an EXISTING tracked_items array.

    Where :func:`migrate_backlog` is a one-time full seed that refuses a non-empty
    array, this ADDS records alongside whatever the array already holds — the
    increment-6 path for folding the inference_ledger / ERROR_LOG records in next
    to the already-migrated task backlog. Every add goes through the store's
    unique-id invariant: a duplicate id fails loud (:class:`DuplicateItemError`)
    unless ``allow_existing`` skips ids already present (so a re-run is idempotent).
    Pure on the dict (mutates and returns ``hot``).
    """
    store = TrackedItemStore.from_hot(hot)
    for item in seed_items(specs):  # validates + dedups within the new specs
        if allow_existing and item.id in store:
            continue
        store.add(item)             # fail-loud on collision with an existing id
    return store.write_into(hot)


def add_items_file(
    path: Path | str,
    specs: Sequence[Mapping],
    *,
    allow_existing: bool = False,
    now: Optional[str] = None,
    touch_meta: bool = True,
) -> dict:
    """Atomically add records to a RAG file's tracked_items array (refreshes .bak).

    The guarded, atomic counterpart to :func:`add_items`: load -> add (unique-id
    invariant) -> ``atomic_write_json`` (tmp -> verify -> .bak -> rename). On any
    duplicate-id failure nothing is written and the prior file + .bak are intact.
    """
    p = Path(path)
    hot = load_hot(p)
    add_items(hot, specs, allow_existing=allow_existing)
    if touch_meta:
        _touch_meta(hot, now)
    atomic_write_json(  # FIX-4 (K6): parity-mirror .bak; FIX-7 (T1): live side-store guard
        p, hot, mirror_bak=True, guard_side_stores=True
    )
    return hot


# ---------------------------------------------------------------------------
# Operating-protocol rule mutation (FIX-5 / P3) — governed, atomic
# ---------------------------------------------------------------------------
# ``operating_protocol`` is the project's rule vault. New rules (e.g. the
# STRICT-OBEY operator directive) were previously introduced by hand-editing
# RAG_MASTER.json — the exact manual-JSON drift the project forbids (E-037 /
# E-039): an authority changed outside the guarded, atomic, .bak-mirroring write
# path. This is the guarded counterpart, mirroring the tracked_items add path:
# validate -> fail-loud on an already-present key (no silent overwrite) -> atomic
# write (tmp -> verify -> .bak parity -> rename). LLM proposes the rule text; this
# layer decides legality (key collision, shape) and persists deterministically.

# The rule vault key inside RAG_MASTER.json (HOT).
OPERATING_PROTOCOL_KEY = "operating_protocol"


def add_operating_protocol_rule(
    hot: dict,
    key: str,
    value: str,
    *,
    allow_overwrite: bool = False,
) -> dict:
    """Append a NEW string-valued rule into ``hot[operating_protocol]`` (pure on dict).

    Fail-loud guards (nothing is mutated unless all pass):
      * ``operating_protocol`` must exist and be a JSON object;
      * ``key`` must be a non-empty string and (unless ``allow_overwrite``) must
        NOT already be present — a collision raises :class:`DuplicateItemError`,
        so an existing rule is never silently clobbered;
      * ``value`` must be a non-empty string (rules are string-valued; nested
        structured config is out of scope for this guarded verb).

    Mutates and returns ``hot``.
    """
    op = hot.get(OPERATING_PROTOCOL_KEY)
    if op is None:
        raise DriftStoreError(
            f"{OPERATING_PROTOCOL_KEY!r} is absent; cannot add a rule to a RAG without one"
        )
    if not isinstance(op, dict):
        raise DriftStoreError(
            f"{OPERATING_PROTOCOL_KEY!r} must be a JSON object, got {type(op).__name__}"
        )
    if not isinstance(key, str) or not key.strip():
        raise DriftStoreError("operating_protocol rule key must be a non-empty string")
    if not isinstance(value, str) or not value.strip():
        raise DriftStoreError("operating_protocol rule value must be a non-empty string")
    if key in op and not allow_overwrite:
        raise DuplicateItemError(
            f"operating_protocol already has a rule {key!r}; "
            "pass allow_overwrite=True to replace it"
        )
    op[key] = value
    return hot


def add_operating_protocol_rule_file(
    path: Path | str,
    key: str,
    value: str,
    *,
    allow_overwrite: bool = False,
    now: Optional[str] = None,
    touch_meta: bool = True,
) -> dict:
    """Atomically append a new ``operating_protocol`` rule to a RAG file (refreshes .bak).

    The guarded, atomic counterpart to :func:`add_operating_protocol_rule`: load ->
    add (key-collision invariant) -> ``atomic_write_json`` (tmp -> verify -> .bak
    parity -> rename). On any guard failure nothing is written and the prior file +
    its ``.bak`` are intact.
    """
    p = Path(path)
    hot = load_hot(p)
    add_operating_protocol_rule(hot, key, value, allow_overwrite=allow_overwrite)
    if touch_meta:
        _touch_meta(hot, now)
    atomic_write_json(  # FIX-4 (K6): parity-mirror .bak; FIX-7 (T1): live side-store guard
        p, hot, mirror_bak=True, guard_side_stores=True
    )
    return hot


def set_operating_protocol_rule(
    hot: dict,
    key: str,
    value,
    *,
    subkey: Optional[str] = None,
    create: bool = False,
) -> dict:
    """Re-set an EXISTING ``operating_protocol`` rule (UPDATE-RULE-VERB; pure on dict).

    The UPDATE counterpart to :func:`add_operating_protocol_rule`, with two
    deliberate differences that close the dict-trim gap (add-rule is string-only and
    its default is ADD):

      * ``value`` may be a string OR a JSON object/array/scalar — so structured
        rules (e.g. ``tool_hierarchy``) can be re-set wholesale, not only strings;
      * the default contract is the inverse of add: the target MUST already exist
        (fail-loud) unless ``create=True``. This makes ``update-rule`` safe to run
        against a typo'd key (it refuses) rather than silently minting a new rule.

    With ``subkey`` the verb sets a single sub-key of a dict-valued rule (e.g.
    ``tool_hierarchy.file_read_write_list``), which is how a dict rule is trimmed
    one sub-entry at a time without re-sending the whole object.

    Fail-loud guards (nothing is mutated unless all pass):
      * ``operating_protocol`` exists and is a JSON object;
      * ``key`` is a non-empty string; ``value`` is not ``None`` and (if a string)
        non-empty;
      * without ``subkey``: ``key`` must already exist unless ``create=True``;
      * with ``subkey``: ``key`` must exist AND be a JSON object, ``subkey`` is a
        non-empty string, and ``subkey`` must already exist unless ``create=True``.

    Mutates and returns ``hot``.
    """
    op = hot.get(OPERATING_PROTOCOL_KEY)
    if op is None:
        raise DriftStoreError(
            f"{OPERATING_PROTOCOL_KEY!r} is absent; cannot update a rule in a RAG without one"
        )
    if not isinstance(op, dict):
        raise DriftStoreError(
            f"{OPERATING_PROTOCOL_KEY!r} must be a JSON object, got {type(op).__name__}"
        )
    if not isinstance(key, str) or not key.strip():
        raise DriftStoreError("operating_protocol rule key must be a non-empty string")
    if value is None:
        raise DriftStoreError("operating_protocol rule value must not be null")
    if isinstance(value, str) and not value.strip():
        raise DriftStoreError("operating_protocol rule value must be a non-empty string")

    if subkey is None:
        if key not in op and not create:
            raise DriftStoreError(
                f"operating_protocol has no rule {key!r} to update; "
                "pass create=True to add it (or use add_operating_protocol_rule)"
            )
        op[key] = value
        return hot

    # Sub-key update of a dict-valued rule (e.g. tool_hierarchy.file_read_write_list).
    if not isinstance(subkey, str) or not subkey.strip():
        raise DriftStoreError("operating_protocol rule subkey must be a non-empty string")
    if key not in op:
        raise DriftStoreError(
            f"operating_protocol has no rule {key!r}; cannot set sub-key {subkey!r}"
        )
    target = op[key]
    if not isinstance(target, dict):
        raise DriftStoreError(
            f"operating_protocol rule {key!r} is {type(target).__name__}, not a JSON object; "
            "sub-key update requires a dict-valued rule"
        )
    if subkey not in target and not create:
        raise DriftStoreError(
            f"operating_protocol rule {key!r} has no sub-key {subkey!r}; "
            "pass create=True to add it"
        )
    target[subkey] = value
    return hot


def set_operating_protocol_rule_file(
    path: Path | str,
    key: str,
    value,
    *,
    subkey: Optional[str] = None,
    create: bool = False,
    now: Optional[str] = None,
    touch_meta: bool = True,
) -> dict:
    """Atomically update an ``operating_protocol`` rule (or one sub-key) in a RAG file.

    The guarded, atomic counterpart to :func:`set_operating_protocol_rule`: load ->
    set (existence + type invariants) -> ``atomic_write_json`` (tmp -> verify ->
    .bak parity -> rename). On any guard failure nothing is written and the prior
    file + its ``.bak`` are intact (refreshes ``.bak`` on success).
    """
    p = Path(path)
    hot = load_hot(p)
    set_operating_protocol_rule(hot, key, value, subkey=subkey, create=create)
    if touch_meta:
        _touch_meta(hot, now)
    atomic_write_json(  # FIX-4 (K6): parity-mirror .bak; FIX-7 (T1): live side-store guard
        p, hot, mirror_bak=True, guard_side_stores=True
    )
    return hot


# ---------------------------------------------------------------------------
# current_status freshness refresh (KA-CS-REFRESH) — the governed REPAIR half of
# the E-043 freshness guard.
# ---------------------------------------------------------------------------
# current_status DENORMALIZES two machine-facts whose authority lives OUTSIDE the
# RAG — the deployed ``rag_kernel.__version__`` and the published git HEAD.
# ``drift_audit.check_current_status_freshness`` FAILS LOUD when the narrative's
# stated fact drifts from the live authority (E-043). Until now there was no
# governed way to REPAIR that drift: a mid-session dev commit bumped the version /
# moved HEAD, current_status went stale, and the only fix was a hand-edit of the
# RAG (exactly the drift the project forbids) — the S116 + S127 field incidents.
#
# This is the repair half. The leading-token field-names + regexes BELOW are the
# single source of truth: ``drift_audit`` imports and re-exports them, so the guard
# that DETECTS staleness and this verb that REPAIRS it read the IDENTICAL token
# definitions and can never disagree — the same DRY discipline that keeps the
# ``open_tasks`` render tied to ``tracked_items`` and the date coercers shared. The
# refresh is deterministic and zero-LLM: it re-stamps ONLY the machine-fact token
# in place, leaving all surrounding narrative untouched (a narrative claim is the
# agent's to rewrite at checkpoint — increment_status_honesty).

CURRENT_STATUS_KEY = "current_status"
# current_status.rag_kernel_version LEADS with the live "vX.Y.Z" runtime token.
_CS_VERSION_FIELD = "rag_kernel_version"
# current_status.github_repo carries the "LATEST COMMIT <sha>" published pointer.
_CS_HEAD_FIELDS: tuple[str, ...] = ("github_repo",)
# current_status.unit_tests LEADS with the passing-test count ("1,639 tests ...").
_CS_TESTS_FIELD = "unit_tests"
# Leading semver token, tolerant of an optional 'v' prefix (v0.4.2 or 0.4.2).
_CS_VERSION_TOKEN_RE = re.compile(r"\bv?(\d+\.\d+\.\d+)\b")
# A 7–40 hex git sha introduced by COMMIT / HEAD (e.g. "LATEST COMMIT e109794").
_CS_HEAD_RE = re.compile(
    r"\b(?:latest\s+commit|head|commit)[`'\s:=]{1,6}([0-9a-f]{7,40})\b", re.IGNORECASE
)
# Leading integer count, comma-grouped ("1,639") or bare ("1639").
_CS_TESTS_COUNT_RE = re.compile(r"\b(\d{1,3}(?:,\d{3})+|\d+)\b")
# current_status CURRENT-RELEASE version claim (KA-CS-PROSE-DRIFT). A LABELED
# "RUNTIME RELEASE vX" / "runtime-vX" token denotes the CURRENTLY published release,
# which by this project's convention tracks ``rag_kernel.__version__``. It is
# label-anchored — exactly the way ``_CS_HEAD_RE`` keys off "LATEST COMMIT <sha>" —
# so the E-043 guard/refresh reach the SECONDARY release narrative that the leading
# version token cannot: the drift KA-CS-PROSE-DRIFT caught was a field whose leading
# version token was re-stamped fresh while an embedded "RUNTIME RELEASE v0.4.27" /
# "runtime-v0.4.27" stayed frozen and ``audit`` still passed clean. TWO invariants make
# this provable rather than prose-guessing: (1) historical releases are written
# UNLABELED ("Prior: v0.4.26 @ <sha>") per the existing convention, so they are never
# matched; and (2) the spec version and sub-component versions carry no release label,
# so they are untouched. ALL matches in a covered field are governed (not just the
# first) — a single stale labeled token left behind is precisely the drift this closes.
_CS_RELEASE_RE = re.compile(
    r"\b(?:runtime\s+release\s+v?|runtime-v)(\d+\.\d+\.\d+)\b", re.IGNORECASE
)
# Fields whose labeled release tokens are current-release claims to guard/refresh.
_CS_RELEASE_FIELDS: tuple[str, ...] = ("github_repo", _CS_VERSION_FIELD)


class CurrentStatusRefreshError(DriftStoreError):
    """Raised when current_status is absent, or a --strict-required token is missing."""


def _refresh_token(pattern: "re.Pattern[str]", want: str, text: str):
    """Replace ONLY group(1) of the FIRST match, preserving all surrounding text.

    Returns ``(new_text, old_token)``; ``old_token`` is ``None`` when the pattern
    does not match (nothing replaced). Mirrors the guard, which reads only the
    first token of each field.
    """
    m = pattern.search(text)
    if not m:
        return text, None
    old = m.group(1)
    s, e = m.span(1)
    return text[:s] + want + text[e:], old


def _refresh_all_tokens(pattern: "re.Pattern[str]", want: str, text: str):
    """Replace group(1) of EVERY match, preserving all surrounding text.

    Returns ``(new_text, old_tokens)`` where ``old_tokens`` lists the replaced tokens
    in order (empty when the pattern does not match). The all-matches sibling of
    :func:`_refresh_token`, used for the labeled current-release token which may
    legitimately appear more than once in one field ("RUNTIME RELEASE v0.4.27 ... tag
    runtime-v0.4.27") — leaving any single occurrence stale would re-open
    KA-CS-PROSE-DRIFT.
    """
    olds: list[str] = []

    def _sub(m: "re.Match[str]") -> str:
        olds.append(m.group(1))
        full = m.group(0)
        s = m.start(1) - m.start(0)
        e = m.end(1) - m.start(0)
        return full[:s] + want + full[e:]

    return pattern.sub(_sub, text), olds


def compute_current_status_refresh(
    hot: dict,
    *,
    version: Optional[str] = None,
    git_head: Optional[str] = None,
    tests: Optional[str] = None,
    strict: bool = False,
) -> "tuple[dict, list[dict]]":
    """Pure planner: compute the refreshed ``current_status`` + a per-token change list.

    Does NOT write. Returns ``(new_current_status, changes)`` where each change is
    ``{field, kind, old, new, action}`` and ``action`` ∈ {``updated``, ``unchanged``,
    ``skipped``}. Each fact is planned only when its live authority is supplied
    (``None`` = not reconciled → not planned), mirroring the guard's self-skip. With
    ``strict`` a supplied authority whose target field/token is missing raises
    :class:`CurrentStatusRefreshError` instead of recording a ``skipped`` change.
    """
    cs = hot.get(CURRENT_STATUS_KEY)
    if not isinstance(cs, dict):
        raise CurrentStatusRefreshError(
            f"{CURRENT_STATUS_KEY!r} is absent or not a JSON object — nothing to refresh"
        )
    new_cs = dict(cs)
    changes: "list[dict]" = []

    def _plan(field: str, kind: str, pattern: "re.Pattern[str]", want: str) -> None:
        raw = cs.get(field)
        if isinstance(raw, str):
            new_text, old = _refresh_token(pattern, want, raw)
            if old is not None:
                new_cs[field] = new_text
                changes.append({
                    "field": field, "kind": kind, "old": old, "new": want,
                    "action": "unchanged" if old == want else "updated",
                })
                return
        if strict:
            raise CurrentStatusRefreshError(
                f"current_status.{field} is missing or has no {kind} token to refresh"
            )
        changes.append({"field": field, "kind": kind, "old": None,
                        "new": want, "action": "skipped"})

    # 1. runtime version — the leading vX.Y.Z token must equal rag_kernel.__version__.
    if version:
        _plan(_CS_VERSION_FIELD, "version", _CS_VERSION_TOKEN_RE, version.lstrip("v"))

    # 2. git HEAD — the FIRST head-bearing field's "LATEST COMMIT <sha>" pointer.
    if git_head:
        want_head = git_head.lower()
        planned = False
        for fld in _CS_HEAD_FIELDS:
            raw = cs.get(fld)
            if isinstance(raw, str) and _CS_HEAD_RE.search(raw):
                new_text, old = _refresh_token(_CS_HEAD_RE, want_head, raw)
                new_cs[fld] = new_text
                # Prefix-equal (short vs long sha) counts as already-fresh, matching
                # the guard's prefix comparison — no needless rewrite.
                fresh = old.lower().startswith(want_head) or want_head.startswith(old.lower())
                changes.append({
                    "field": fld, "kind": "head", "old": old, "new": want_head,
                    "action": "unchanged" if fresh else "updated",
                })
                if fresh:
                    new_cs[fld] = raw  # leave the existing (possibly longer) sha as-is
                planned = True
                break
        if not planned:
            if strict:
                raise CurrentStatusRefreshError(
                    "current_status has no COMMIT/HEAD sha field to refresh"
                )
            changes.append({"field": _CS_HEAD_FIELDS[0], "kind": "head",
                            "old": None, "new": want_head, "action": "skipped"})

    # 3. unit_tests count — OPTIONAL; only when an explicit count is supplied (the
    #    auditor does not guard it, and a count must never be fabricated).
    if tests:
        _plan(_CS_TESTS_FIELD, "tests", _CS_TESTS_COUNT_RE, tests)

    # 4. current-RELEASE version tokens (KA-CS-PROSE-DRIFT) — every LABELED
    #    "RUNTIME RELEASE vX" / "runtime-vX" claim in a covered field must track
    #    rag_kernel.__version__. Governs ALL matches (not just the leading one), and
    #    composes on top of steps 1–2 (reads new_cs, so github_repo's already-restamped
    #    HEAD and rag_kernel_version's already-restamped leading token are preserved).
    if version:
        want_v = version.lstrip("v")
        for fld in _CS_RELEASE_FIELDS:
            raw = new_cs.get(fld)
            if not isinstance(raw, str):
                continue
            new_text, olds = _refresh_all_tokens(_CS_RELEASE_RE, want_v, raw)
            if not olds:
                continue
            new_cs[fld] = new_text
            stale = any(o != want_v for o in olds)
            changes.append({
                "field": fld, "kind": "release", "old": ",".join(olds),
                "new": want_v, "action": "updated" if stale else "unchanged",
            })

    return new_cs, changes


def refresh_current_status_file(
    path: Path | str,
    *,
    version: Optional[str] = None,
    git_head: Optional[str] = None,
    tests: Optional[str] = None,
    strict: bool = False,
    now: Optional[str] = None,
    touch_meta: bool = True,
    dry_run: bool = False,
) -> "tuple[list[dict], bool]":
    """Atomically re-stamp ``current_status`` machine-facts (KA-CS-REFRESH).

    Load -> plan the token refresh (shared guard regexes) -> on a real change:
    write ``current_status`` back, ``_touch_meta``, ``atomic_write_json`` (tmp ->
    verify -> .bak parity -> rename). Returns ``(changes, wrote)``. Nothing is
    written on ``dry_run`` or when no token actually changed (idempotent no-op), so
    HOT == ``.bak`` is preserved either way. On any guard failure nothing is written
    and the prior file + its ``.bak`` are intact.
    """
    p = Path(path)
    hot = load_hot(p)
    new_cs, changes = compute_current_status_refresh(
        hot, version=version, git_head=git_head, tests=tests, strict=strict
    )
    changed = any(c["action"] == "updated" for c in changes)
    if dry_run or not changed:
        return changes, False
    hot[CURRENT_STATUS_KEY] = new_cs
    if touch_meta:
        _touch_meta(hot, now)
    atomic_write_json(  # FIX-4 (K6): parity-mirror .bak; FIX-7 (T1): live side-store guard
        p, hot, mirror_bak=True, guard_side_stores=True
    )
    return changes, True


# ---------------------------------------------------------------------------
# sessions_recent dedup (KA-2 increment B) — governed repair of the rows the
# KA-2 auditor (increment A) detects.
# ---------------------------------------------------------------------------
# ``sessions_recent`` is the append-only ledger of per-session checkpoints. The
# eBay Session-Zero deploy carried duplicate *bootstrap* rows — two rows minted at
# the SAME checkpoint instant, one never actually run — and there was no governed
# way to repair them (a hand-edit of the array is exactly the drift the project
# forbids). Increment A added ``drift_audit.check_sessions_recent_coherence`` to
# FAIL LOUD on that signature; this is its repair half: a guarded, atomic verb that
# removes the phantom duplicates, keeping exactly one row per checkpoint timestamp.
#
# Single source of truth: the duplicate-detection predicate lives HERE
# (``_sessions_recent_key`` + ``sessions_recent_duplicate_pairs``) and is consumed
# by BOTH the auditor (to flag) and this dedup verb (to repair), so detection and
# repair can never disagree — the same DRY principle the whole DRIFT-ELIM layer is
# built on. The date coercers (``_coerce_utc_date`` / ``_coerce_utc_instant``) live
# here too (drift_store is the lower module drift_audit imports from); drift_audit
# re-exports them so its public surface is unchanged.


def _coerce_utc_date(value) -> Optional[date]:
    """Parse an ISO date / datetime string to its UTC calendar day, else ``None``.

    Accepts a bare ``YYYY-MM-DD`` (``current_status.last_updated``'s usual shape)
    or a full ISO instant (``meta.last_updated_utc``, possibly ``Z``-suffixed). A
    timezone-aware instant is normalized to UTC before its day is taken; a naive
    one is read as-is. Anything unparseable yields ``None`` so the caller silently
    self-skips rather than crashing on a malformed field.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    try:                                  # bare calendar day, the common case
        return date.fromisoformat(raw)
    except ValueError:
        pass
    iso = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.date()


def _coerce_utc_instant(value) -> Optional[datetime]:
    """Parse an ISO datetime string to a UTC-aware instant, else ``None``.

    The instant-resolution sibling of :func:`_coerce_utc_date`, used by the
    sessions_recent coherence check (KA-2) where two checkpoints in the SAME
    calendar day must still be distinguished. Accepts a full ISO instant
    (optionally ``Z``-suffixed); a timezone-aware value is normalized to UTC and a
    naive one is read as UTC, so two rows are compared on the same footing. A bare
    calendar day degrades to midnight UTC. Anything unparseable yields ``None`` so
    the caller silently self-skips rather than crashing on a malformed field.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    iso = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        d = _coerce_utc_date(raw)
        if d is None:
            return None
        dt = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _sessions_recent_key(row) -> Optional[tuple]:
    """Return a hashable duplicate-detection key for a ``sessions_recent`` row,
    or ``None`` for a row that must be skipped (not a dict, or a missing/blank ``d``).

    Two rows are duplicate-bootstrap iff they yield the SAME key. Parse ``d`` to a
    UTC instant when possible (so a ``Z``-suffixed instant and its offset twin
    collide), else fall back to the trimmed literal (so two identical *unparseable*
    timestamps still collide). This is the one predicate both the KA-2 auditor and
    the dedup verb share.
    """
    if not isinstance(row, dict):
        return None
    d_raw = row.get("d")
    d = d_raw.strip() if isinstance(d_raw, str) and d_raw.strip() else None
    if d is None:
        return None
    inst = _coerce_utc_instant(d)
    if inst is not None:
        return ("inst", inst)
    return ("lit", d)


def sessions_recent_duplicate_pairs(sr) -> list[tuple[int, int, str, str]]:
    """Find duplicate-bootstrap rows in a ``sessions_recent`` list.

    Returns one ``(kept_index, dup_index, kind, literal)`` tuple per row that shares
    a checkpoint timestamp with an EARLIER row, where ``kind`` is ``"instant"`` (the
    ``d``\\ s parsed to the same UTC instant) or ``"literal"`` (identical unparseable
    ``d``), and ``literal`` is the duplicate row's trimmed ``d``. Order-agnostic and
    deterministic; rows that are not dicts, or whose ``d`` is missing/blank, are
    skipped. Returns ``[]`` when ``sr`` is not a list of at least two rows. This is
    the exact set of pairs the KA-2 auditor reports and the dedup verb repairs.
    """
    pairs: list[tuple[int, int, str, str]] = []
    if not isinstance(sr, list) or len(sr) < 2:
        return pairs
    seen: dict[tuple, int] = {}
    for i, row in enumerate(sr):
        key = _sessions_recent_key(row)
        if key is None:
            continue
        prior = seen.get(key)
        if prior is not None:
            kind = "instant" if key[0] == "inst" else "literal"
            pairs.append((prior, i, kind, row["d"].strip()))
        else:
            seen[key] = i
    return pairs


def dedup_sessions_recent(hot: dict, *, keep: str = "first") -> tuple[dict, list[dict]]:
    """Remove duplicate-bootstrap rows from ``hot[sessions_recent]`` (pure on the dict).

    Keeps exactly one row per checkpoint timestamp and drops the phantom duplicate(s)
    — the repair half of the KA-2 invariant. ``keep="first"`` (default) retains the
    earliest-indexed row of each duplicate group; ``keep="last"`` retains the latest.
    Rows with a missing/blank ``d`` and non-dict rows are NEVER removed. Group-correct
    (handles 3+ rows sharing one timestamp) and idempotent: a second run finds no
    duplicates and removes nothing. Returns ``(hot, removed_rows)`` with ``hot``
    mutated in place; ``removed_rows`` are the dropped row dicts in original order.
    """
    if keep not in ("first", "last"):
        raise DriftStoreError(f"keep must be 'first' or 'last', got {keep!r}")
    sr = hot.get(SESSIONS_RECENT_KEY)
    if not isinstance(sr, list) or len(sr) < 2:
        return hot, []
    groups: dict[tuple, list[int]] = {}
    for i, row in enumerate(sr):
        key = _sessions_recent_key(row)
        if key is None:                       # untouchable (kept verbatim)
            continue
        groups.setdefault(key, []).append(i)
    drop: set[int] = set()
    for idxs in groups.values():
        if len(idxs) < 2:
            continue
        chosen = idxs[0] if keep == "first" else idxs[-1]
        drop.update(j for j in idxs if j != chosen)
    if not drop:
        return hot, []
    removed = [sr[i] for i in sorted(drop)]
    hot[SESSIONS_RECENT_KEY] = [row for i, row in enumerate(sr) if i not in drop]
    return hot, removed


def dedup_sessions_recent_file(
    path: Path | str,
    *,
    keep: str = "first",
    now: Optional[str] = None,
    touch_meta: bool = True,
) -> tuple[dict, list[dict]]:
    """Atomically dedup ``sessions_recent`` in a RAG file (refreshes ``.bak``).

    The guarded, atomic counterpart to :func:`dedup_sessions_recent`: load -> dedup
    -> ``atomic_write_json`` (tmp -> verify -> .bak parity -> rename). When there is
    nothing to repair NOTHING is written (no spurious .bak churn / meta touch) and
    ``(hot, [])`` is returned. Returns ``(hot, removed_rows)``.
    """
    p = Path(path)
    hot = load_hot(p)
    _, removed = dedup_sessions_recent(hot, keep=keep)
    if not removed:
        return hot, []
    if touch_meta:
        _touch_meta(hot, now)
    atomic_write_json(  # FIX-4 (K6): parity-mirror .bak; FIX-7 (T1): live side-store guard
        p, hot, mirror_bak=True, guard_side_stores=True
    )
    return hot, removed
