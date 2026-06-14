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
              "seed_items", "migrate_backlog", "migrate_backlog_file",
              "LEDGER_DISPOSITION_TO_STATUS", "INFERENCE_LEDGER_KEY",
              "ledger_disposition_to_status", "inference_specs_from_hot",
              "add_items", "add_items_file",
              "OPERATING_PROTOCOL_KEY", "add_operating_protocol_rule",
              "add_operating_protocol_rule_file"],
  "use_when": "Reading, transitioning, or persisting the canonical status of tracked project items in RAG_MASTER.json",
  "never_bypass": true
}
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
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
DRIFT_STORE_VERSION = "1.1.0"

# The single canonical array key inside RAG_MASTER.json (HOT). Everything else
# that mentions item status is, or will become, a render of this array.
TRACKED_ITEMS_KEY = "tracked_items"

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
    atomic_write_json(p, hot, mirror_bak=True)  # FIX-4 (K6): parity-mirror .bak
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
    atomic_write_json(p, hot, mirror_bak=True)  # FIX-4 (K6): parity-mirror .bak
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
    atomic_write_json(p, hot, mirror_bak=True)  # FIX-4 (K6): parity-mirror .bak
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
    atomic_write_json(p, hot, mirror_bak=True)  # FIX-4 (K6): parity-mirror .bak
    return hot
