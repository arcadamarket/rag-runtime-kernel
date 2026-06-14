"""Deterministic renderers over the canonical tracked_items array — DRIFT-ELIM increment 4.

Increment 1 (``drift_control``) gave the pure item-lifecycle core; increment 2
(``drift_store``) gave the deterministic, atomic mutation API and migrated the
project backlog into ONE canonical array — ``tracked_items`` in RAG_MASTER.json;
increment 3 added the ``resolve|defer|...`` CLI and registered both modules.

This module closes the loop: it makes ``tracked_items`` the **sole authority** by
turning every other place that records item status into a *render* — a pure,
deterministic projection of the canonical array, never a hand-authored copy.

What it renders
---------------
* ``render_open_tasks``      -> the legacy ``open_tasks`` array (list of strings),
                                now holding ONLY the non-terminal (OPEN /
                                IN_PROGRESS) items, one deterministic line each.
* ``render_deferred_items``  -> the legacy ``deferred_items`` array (list of
                                objects), now holding ONLY DEFERRED items.
* ``render_backlog_section`` -> the Rule 12 status-report backlog block
                                (Open / Blocked-or-user-gated / Deferred).
* ``render_error_log_backlog`` -> the ERROR_LOG backlog-status summary (markdown).
* ``apply_renders`` / ``apply_renders_file`` -> regenerate the legacy arrays in a
                                HOT dict / RAG file *from* the canonical array,
                                atomically. After this, the legacy arrays are
                                projections; editing them by hand is the drift the
                                inc-5 auditor will catch.

Why this is the fix, not cosmetics
----------------------------------
The same status fact used to live as prose in ``open_tasks`` strings, in
``deferred_items`` objects, in ``inference_ledger`` dispositions, in ERROR_LOG
prose and in the published docs — and the copies drifted (E-034 / E-037 / E-039 /
E-040). ``guardgen`` already killed this class for the *state-machine transition
table* by deriving it from one formally-verified source. DRIFT-ELIM generalises
that move to project state: one canonical ``status`` field per item, and every
mention of status is a *derived render* of it. A render is reproducible byte-for-
byte from the canonical array, so a divergence between a render and the canonical
field is a detectable defect (the inc-5 session auditor turns that into a
fail-loud regression assertion).

Scope boundary
--------------
ERROR_LOG *forensic* entries (the E-### root-cause/fix records) are NOT migrated
into ``tracked_items`` yet — only their *backlog/status* view is rendered here.
Likewise the ``inference_ledger`` keeps its own dispositions for now. Migrating
those record kinds into ``tracked_items`` (kind=ERROR / kind=INFERENCE) and the
fail-loud session auditor are the remaining DRIFT-ELIM work; the orchestrator +
this drift layer ship together as the single-shot v0.4.0. Until then everything
here is UNRELEASED on main (a developer checkpoint, not a usable feature).

Design philosophy
-----------------
CS lens: a render is a pure total function of the canonical array — deterministic
(id-sorted), idempotent (rendering a rendered array changes nothing), and
side-effect-free except for the explicit atomic ``apply_*`` writers, which reuse
``persistence.atomic_write_json`` (tmp -> verify -> .bak -> rename).

ML lens: the LLM never hand-writes a status line again; it calls a render. Token
cost is zero (no model in the path) and the terse projection is far cheaper to
re-ingest each session than the prior multi-paragraph ``open_tasks`` prose, which
protects the no-compaction context budget.

Convergence: "LLM proposes. System decides. State persists." — and now *renders*
state, never re-authors it.

@rag-kernel-manifest
{
  "module": "rag_kernel.drift_render",
  "capability": "state_render",
  "description": "Deterministic, idempotent renderers projecting the canonical tracked_items array into the legacy open_tasks / deferred_items arrays, the Rule 12 status-report backlog, and the ERROR_LOG backlog summary (DRIFT-ELIM increment 4: renders make tracked_items the sole authority)",
  "exports": ["ACTIVE_STATUSES", "BACKLOG_KINDS", "render_open_tasks",
              "render_deferred_items", "render_backlog_section",
              "render_backlog_markdown", "render_error_log_backlog",
              "render_records_by_kind", "render_all", "apply_renders",
              "apply_renders_file", "default_gated", "DRIFT_RENDER_VERSION"],
  "use_when": "Projecting canonical tracked_items status into any legacy array, status report, or doc surface (never hand-author these)",
  "never_bypass": true
}
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, Optional

from rag_kernel.drift_control import ItemKind, ItemStatus, TERMINAL_STATUSES, TrackedItem
from rag_kernel.drift_store import (
    TRACKED_ITEMS_KEY,
    TrackedItemStore,
    load_hot,
)
from rag_kernel.persistence import atomic_write_json

# Bump when a rendered layout changes in a way a consumer / regression test pins.
DRIFT_RENDER_VERSION = "1.0.0"

# The non-terminal, actionable statuses — the "open backlog".
ACTIVE_STATUSES: frozenset[ItemStatus] = frozenset(
    {ItemStatus.OPEN, ItemStatus.IN_PROGRESS}
)

# The kinds that constitute the *task backlog* — what open_tasks / deferred_items
# / the Rule 12 backlog have always meant. Increment 6 folds the inference_ledger
# (kind=INFERENCE) and ERROR_LOG (kind=ERROR) records into the SAME canonical
# array; those are forensic records, not task backlog, so the backlog renders
# scope to these kinds and the record kinds get their own projection
# (``render_records_by_kind``). Scoping is what keeps the legacy task arrays
# byte-identical after the migration (the auditor's E-040 parity check still
# holds) instead of suddenly absorbing ~80 error/inference rows.
BACKLOG_KINDS: frozenset[ItemKind] = frozenset(
    {ItemKind.TASK, ItemKind.MILESTONE, ItemKind.RELEASE}
)

# Substrings that mark an OPEN/IN_PROGRESS item as blocked or awaiting the user.
# A deterministic, schema-free convention over the existing ``note`` field: an
# item whose note contains one of these (case-insensitive) renders under the
# Rule 12 "Blocked / user-gated" bucket instead of plain "Open". This needs no
# migration; today no canonical item carries such a note, so the bucket renders
# empty — which is the honest state (the PAT-rotation / social-posting gates are
# ERROR_LOG records, migrated into tracked_items in a later increment).
_GATE_MARKERS: tuple[str, ...] = (
    "user-gated",
    "user gated",
    "blocked",
    "needs user",
    "user action",
    "awaiting user",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _as_store(source: TrackedItemStore | dict | Iterable[TrackedItem]) -> TrackedItemStore:
    """Coerce a store / HOT dict / iterable of items into a TrackedItemStore.

    A store already iterates in deterministic id-sorted order; building a fresh
    store from a dict or iterable reuses the same unique-id invariant + ordering.
    """
    if isinstance(source, TrackedItemStore):
        return source
    if isinstance(source, dict):
        return TrackedItemStore.from_hot(source)
    return TrackedItemStore(source)


def default_gated(item: TrackedItem) -> bool:
    """True iff an active item's note marks it blocked / awaiting the user."""
    note = (item.note or "").lower()
    return any(marker in note for marker in _GATE_MARKERS)


def _line(item: TrackedItem) -> str:
    """One deterministic backlog line: ``id — title``."""
    return f"{item.id} — {item.title}"


# ---------------------------------------------------------------------------
# Array renders (replace the legacy hand-authored arrays)
# ---------------------------------------------------------------------------

def render_open_tasks(
    source: TrackedItemStore | dict | Iterable[TrackedItem],
) -> list[str]:
    """Render the legacy ``open_tasks`` array from the canonical store.

    Holds ONLY non-terminal items (OPEN / IN_PROGRESS), id-sorted, one stable
    line each. Resolved / deferred / superseded / discarded items deliberately
    drop out — a closed or parked item is not an *open* task. The narrative that
    once bloated ``open_tasks`` (commit hashes, per-increment test counts) lives
    in the CHANGELOG and the item ``history``, not in this status projection.
    """
    store = _as_store(source)
    out: list[str] = []
    for it in store:  # id-sorted
        if it.kind not in BACKLOG_KINDS:
            continue  # INFERENCE / ERROR records are not task backlog
        if it.status not in ACTIVE_STATUSES:
            continue
        session = it.session or "—"
        line = f"{it.id} [{it.status.value} · {session}]: {it.title}"
        if it.note:
            line += f" — {it.note}"
        out.append(line)
    return out


def render_deferred_items(
    source: TrackedItemStore | dict | Iterable[TrackedItem],
) -> list[dict]:
    """Render the legacy ``deferred_items`` array from the canonical store.

    Holds ONLY DEFERRED items, id-sorted, each a small object projected from the
    canonical fields (no free-text ``target`` authority — the lifecycle status is
    the authority now).
    """
    store = _as_store(source)
    return [
        {
            "id": it.id,
            "title": it.title,
            "status": it.status.value,
            "kind": it.kind.value,
            "session": it.session,
            "note": it.note,
        }
        for it in store
        if it.status == ItemStatus.DEFERRED and it.kind in BACKLOG_KINDS
    ]


# ---------------------------------------------------------------------------
# Status-report backlog render (Rule 12, section 4)
# ---------------------------------------------------------------------------

def render_backlog_section(
    source: TrackedItemStore | dict | Iterable[TrackedItem],
    *,
    gated: Optional[Callable[[TrackedItem], bool]] = None,
) -> dict[str, list[str]]:
    """Render the Rule 12 backlog block as three id-sorted buckets.

    Returns ``{"open": [...], "blocked_or_user_gated": [...], "deferred": [...]}``
    where each entry is a deterministic ``"id — title"`` line. ``open`` excludes
    items the ``gated`` predicate flags (those move to ``blocked_or_user_gated``);
    ``gated`` defaults to :func:`default_gated`. Terminal items are not backlog.
    """
    store = _as_store(source)
    is_gated = gated or default_gated
    open_lines: list[str] = []
    gated_lines: list[str] = []
    deferred_lines: list[str] = []
    for it in store:  # id-sorted
        if it.kind not in BACKLOG_KINDS:
            continue  # INFERENCE / ERROR records are not task backlog
        if it.status == ItemStatus.DEFERRED:
            deferred_lines.append(_line(it))
        elif it.status in ACTIVE_STATUSES:
            (gated_lines if is_gated(it) else open_lines).append(_line(it))
        # terminal statuses are intentionally omitted from the backlog
    return {
        "open": open_lines,
        "blocked_or_user_gated": gated_lines,
        "deferred": deferred_lines,
    }


def _bullets(lines: list[str]) -> str:
    return "\n".join(f"- {ln}" for ln in lines) if lines else "- (none)"


def render_backlog_markdown(
    source: TrackedItemStore | dict | Iterable[TrackedItem],
    *,
    gated: Optional[Callable[[TrackedItem], bool]] = None,
) -> str:
    """Render the Rule 12 backlog block as a compact markdown string."""
    section = render_backlog_section(source, gated=gated)
    return (
        "**Open:** " + ("; ".join(section["open"]) or "(none)") + "\n"
        "**Blocked / user-gated:** "
        + ("; ".join(section["blocked_or_user_gated"]) or "(none)") + "\n"
        "**Deferred:** " + ("; ".join(section["deferred"]) or "(none)")
    )


# ---------------------------------------------------------------------------
# ERROR_LOG backlog-status render
# ---------------------------------------------------------------------------

def render_error_log_backlog(
    source: TrackedItemStore | dict | Iterable[TrackedItem],
    *,
    gated: Optional[Callable[[TrackedItem], bool]] = None,
) -> str:
    """Render the ERROR_LOG *backlog-status* summary section (markdown).

    This is the status view ERROR_LOG carries, projected from ``tracked_items``;
    it is NOT the E-### forensic records (those remain their own entries until
    error items are migrated into the canonical array in a later increment).
    """
    section = render_backlog_section(source, gated=gated)
    n_open = len(section["open"]) + len(section["blocked_or_user_gated"])
    n_def = len(section["deferred"])
    return (
        "## Backlog status (rendered from RAG `tracked_items` — do NOT hand-edit)\n"
        "\n"
        f"_Deterministic render of the canonical `tracked_items` array: "
        f"{n_open} open, {n_def} deferred. Regenerate via "
        "`python -m rag_kernel render --what error_log`._\n"
        "\n"
        "### Open\n"
        f"{_bullets(section['open'])}\n"
        "\n"
        "### Blocked / user-gated\n"
        f"{_bullets(section['blocked_or_user_gated'])}\n"
        "\n"
        "### Deferred\n"
        f"{_bullets(section['deferred'])}\n"
    )


# ---------------------------------------------------------------------------
# Record renders (INFERENCE / ERROR kinds — increment 6)
# ---------------------------------------------------------------------------

def render_records_by_kind(
    source: TrackedItemStore | dict | Iterable[TrackedItem],
    kind: ItemKind | str,
) -> list[dict]:
    """Render the canonical records of one ``kind`` (e.g. INFERENCE / ERROR).

    A deterministic, id-sorted status projection of the migrated forensic records
    (increment 6). Each row is the canonical status view of one ``E-###`` /
    ``INS-###`` record; the full root-cause/fix prose stays in ERROR_LOG.md /
    inference_ledger (this is the *status* render, not a re-authoring of the
    forensics). Used by the status report and as the surface the auditor's
    record-coverage / ledger-consistency checks reconcile against.
    """
    want = ItemKind(kind) if not isinstance(kind, ItemKind) else kind
    store = _as_store(source)
    return [
        {
            "id": it.id,
            "title": it.title,
            "status": it.status.value,
            "kind": it.kind.value,
            "session": it.session,
            "note": it.note,
            "superseded_by": it.superseded_by,
        }
        for it in store
        if it.kind == want
    ]


# ---------------------------------------------------------------------------
# Aggregate + apply
# ---------------------------------------------------------------------------

def render_all(
    source: TrackedItemStore | dict | Iterable[TrackedItem],
    *,
    gated: Optional[Callable[[TrackedItem], bool]] = None,
) -> dict:
    """Return every render as one dict (pure; no I/O)."""
    store = _as_store(source)
    return {
        "open_tasks": render_open_tasks(store),
        "deferred_items": render_deferred_items(store),
        "backlog": render_backlog_section(store, gated=gated),
        "inference_records": render_records_by_kind(store, ItemKind.INFERENCE),
        "error_records": render_records_by_kind(store, ItemKind.ERROR),
    }


def apply_renders(hot: dict) -> dict:
    """Regenerate ``open_tasks`` + ``deferred_items`` in ``hot`` from tracked_items.

    Pure on the dict (mutates and returns ``hot``). The canonical ``tracked_items``
    array is never touched — only the derived arrays are overwritten — so this is
    idempotent: ``apply_renders(apply_renders(h)) == apply_renders(h)``.
    """
    if not isinstance(hot, dict):
        raise TypeError(f"hot must be a dict, got {type(hot).__name__}")
    if TRACKED_ITEMS_KEY not in hot:
        raise KeyError(
            f"{TRACKED_ITEMS_KEY!r} missing from HOT — nothing to render from"
        )
    store = TrackedItemStore.from_hot(hot)
    hot["open_tasks"] = render_open_tasks(store)
    hot["deferred_items"] = render_deferred_items(store)
    return hot


def apply_renders_file(
    path: Path | str,
    *,
    touch_meta: bool = True,
) -> dict:
    """Atomically regenerate the legacy arrays in a RAG file from tracked_items.

    Load -> render -> ``atomic_write_json`` (tmp -> verify -> .bak -> rename), so
    the prior RAG_MASTER.json is preserved in ``.bak`` and a crash never leaves a
    half-written file. Returns the new HOT dict.
    """
    from datetime import datetime, timezone

    p = Path(path)
    hot = load_hot(p)
    apply_renders(hot)
    if touch_meta:
        meta = hot.get("meta")
        if isinstance(meta, dict):
            meta["last_updated_utc"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
    atomic_write_json(  # FIX-4 (K6): parity-mirror .bak; FIX-7 (T1): live side-store guard
        p, hot, mirror_bak=True, guard_side_stores=True
    )
    return hot
