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
              "render_records_by_kind", "render_status_report",
              "render_all", "apply_renders",
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
DRIFT_RENDER_VERSION = "1.1.0"

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


# ---------------------------------------------------------------------------
# Canonical status report render (Rule 12 report_before_transfer — 7 sections)
# ---------------------------------------------------------------------------
#
# REPORT-VERB (S136). Rule 12 mandates that the closing/transfer status report be
# a DETERMINISTIC RENDER of the RAG canonical fields, never hand-authored — "the
# report equals the RAG by construction". This function is that render.
#
# Sourcing discipline (operator decision S136, "structured + live-computed +
# explicit args"): every fact is either (a) read from a STRUCTURED canonical
# source (``meta`` scalars, the ``tracked_items`` store, ``inference_ledger``),
# (b) COMPUTED LIVE by the caller and passed in (health, drift-gate sha, git
# HEAD, released?), or (c) a genuinely EXTERNAL scalar the runtime cannot know
# (the LLM's ``context_pct``; the ``tests`` result, which needs a suite run) and
# is therefore an EXPLICIT argument. The renderer NEVER scrapes ``current_status``
# prose and NEVER invents a value: an unknown fact renders as ``n/a`` and can only
# pull the overall verdict toward AMBER, never toward a false GREEN
# (increment-status-honesty, Rule 14).

_RAG_GLYPH = {"GREEN": "🟢 GREEN", "AMBER": "🟡 AMBER", "RED": "🔴 RED"}


def _overall_rag(
    *,
    tests_ok: Optional[bool],
    health_ok: Optional[bool],
    drift_ok: Optional[bool],
    claims_ok: Optional[bool],
    released: Optional[bool],
) -> str:
    """Objective R/A/G per the Rule 12 thresholds (never subjective).

    RED  = any hard gate failing (tests / health / drift / a published repo-claim
           contradicting reality).
    AMBER= feature-complete but UNRELEASED/not-deployable, OR any gate UNKNOWN
           (we refuse to assert GREEN without evidence).
    GREEN= released AND tests+health+drift all green AND repo-claims reconciled.
    """
    if False in (tests_ok, health_ok, drift_ok, claims_ok):
        return "RED"
    if released is False:
        return "AMBER"
    if None in (tests_ok, health_ok, drift_ok, released):
        return "AMBER"
    return "GREEN"


def _cell(value: Optional[str]) -> str:
    """A table cell: the value, or ``n/a`` when the fact is unknown (never blank)."""
    v = (value or "").strip()
    return v if v else "n/a"


def _focus_build(build_rows: list, session: str):
    """The CURRENT build item section 2 / the milestone cell describe.

    Order (honest, never fabricated): the newest non-terminal MILESTONE (a build
    in progress), else the milestone/release that reached terminal THIS session
    (the just-shipped build), else the newest RELEASE row, else None. Shared by
    the at-a-glance milestone cell and section 2 so they never disagree.
    """
    active = [it for it in build_rows
              if it.kind == ItemKind.MILESTONE and not it.is_terminal]
    if active:
        return active[-1]
    session_terminal = [
        it for it in build_rows
        if it.is_terminal and (
            it.session == session
            or any(getattr(ev, "session", None) == session for ev in it.history)
        )
    ]
    if session_terminal:
        return session_terminal[-1]
    releases = [it for it in build_rows if it.kind == ItemKind.RELEASE]
    if releases:
        return releases[-1]
    return None


def render_status_report(
    source: TrackedItemStore | dict | Iterable[TrackedItem],
    *,
    session: str,
    meta: Optional[dict] = None,
    ledger: Optional[Iterable[dict]] = None,
    version: Optional[str] = None,
    milestone: Optional[str] = None,
    tests: Optional[str] = None,
    tests_ok: Optional[bool] = None,
    health: Optional[str] = None,
    health_ok: Optional[bool] = None,
    drift_sha: Optional[str] = None,
    drift_ok: Optional[bool] = None,
    released: Optional[bool] = None,
    release_ref: Optional[str] = None,
    claims_ok: Optional[bool] = None,
    context_pct: Optional[str] = None,
    git_head: Optional[str] = None,
    rag_bytes: Optional[int] = None,
    bak_parity: Optional[bool] = None,
    handoff: Optional[str] = None,
    gated: Optional[Callable[[TrackedItem], bool]] = None,
) -> str:
    """Render the full 7-section canonical status report as a markdown string.

    Pure: no I/O, no clock, no model — a total function of the arguments, so a
    regression test can pin it byte-for-byte. The caller (``cmd_report``) gathers
    the live-computed / external facts and passes them in; here we only project.
    """
    store = _as_store(source)
    meta = meta or {}
    ledger = list(ledger or [])

    seq = meta.get("last_checkpoint_seq")
    written_by = meta.get("written_by_session")
    rag_version = meta.get("rag_version")

    # ---- derive counts + buckets from the canonical array (id-sorted) ----
    backlog = render_backlog_section(store, gated=gated)
    n_open = len(backlog["open"]) + len(backlog["blocked_or_user_gated"])
    n_def = len(backlog["deferred"])
    n_resolved = sum(
        1
        for it in store
        if it.kind in BACKLOG_KINDS and it.status == ItemStatus.RESOLVED
    )
    build_rows = [
        it for it in store
        if it.kind in (ItemKind.MILESTONE, ItemKind.RELEASE)
    ]
    touched = [
        it for it in store
        if it.session == session
        or any(getattr(ev, "session", None) == session for ev in it.history)
    ]

    # ---- overall verdict ----
    overall = _overall_rag(
        tests_ok=tests_ok,
        health_ok=health_ok,
        drift_ok=drift_ok,
        claims_ok=claims_ok,
        released=released,
    )
    focus = _focus_build(build_rows, session)
    if milestone is None:
        # honest default via the shared focus-build resolver: name the in-progress
        # milestone, else the milestone/release shipped THIS session (so a completed
        # build still names itself instead of the bare "(no active milestone)"),
        # else the newest release — never a fabricated claim.
        if focus is None:
            milestone = "(no active milestone)"
        elif focus.kind == ItemKind.MILESTONE and not focus.is_terminal:
            milestone = f"{focus.id} — {focus.title}"
        elif focus.is_terminal and (
            focus.session == session
            or any(getattr(ev, "session", None) == session
                   for ev in focus.history)
        ):
            milestone = f"{focus.id} — {focus.title} (shipped {session})"
        else:
            milestone = f"{focus.id} — {focus.title}"

    release_cell = (
        "n/a" if released is None
        else (f"released {release_ref}" if released and release_ref else
              "released" if released else "UNRELEASED / not deployable")
    )
    tests_health = f"{_cell(tests)} · health {_cell(health)}"

    verdict = _verdict_line(
        overall,
        released=released,
        tests_ok=tests_ok,
        health_ok=health_ok,
        drift_ok=drift_ok,
        claims_ok=claims_ok,
    )

    L: list[str] = []
    ap = L.append
    ap(f"## RAG Runtime Kernel — Status Report ({session} close)")
    ap("")

    # (1) AT-A-GLANCE ------------------------------------------------------
    ap("### 1 · At a glance")
    ap("")
    ap("| Overall | Milestone | Release-ready | Tests / Health | Drift gate | Context % | RAG seq |")
    ap("| --- | --- | --- | --- | --- | --- | --- |")
    ap(
        f"| {_RAG_GLYPH.get(overall, overall)} | {milestone} | {release_cell} "
        f"| {tests_health} | {_cell(drift_sha)} | {_cell(context_pct)} "
        f"| {_cell(str(seq) if seq is not None else None)} · {_cell(written_by)} |"
    )
    ap("")
    ap(f"**Verdict:** {verdict}")
    ap("")

    # (2) BUILD (planned vs actual, scoped to the CURRENT build's increments) --
    ap("### 2 · Build (planned vs actual)")
    ap("")
    if focus is not None and focus.increments:
        ap(f"**{focus.id} — {focus.title}** "
           f"({focus.kind.value}, {focus.status.value})")
        ap("")
        ap("| # | Increment | Plan | Status | RAG | Commit-S |")
        ap("| --- | --- | --- | --- | --- | --- |")
        for i, inc in enumerate(focus.increments, 1):
            ap(
                f"| {i} | {_cell(inc.n)} | {_cell(inc.plan)} | {_cell(inc.status)} "
                f"| {_cell(inc.rag)} | {_cell(inc.commit)} |"
            )
    elif focus is not None:
        # A current build exists but records no increments yet — name it plainly
        # rather than dumping the full historical milestone list (the S136 defect a).
        ap(f"- Current build: {focus.id} — {focus.title} "
           f"({focus.kind.value}, {focus.status.value}) — no increments recorded.")
    else:
        ap("_(no active build — resolved milestones/releases are listed via `items`)_")
    ap("")

    # (3) THIS SESSION -----------------------------------------------------
    ap(f"### 3 · This session ({session})")
    ap("")
    if touched:
        for it in touched[:5]:
            ap(f"- {it.id} [{it.status.value}] — {it.title}")
        if len(touched) > 5:
            ap(f"- …and {len(touched) - 5} more (see `items`)")
    else:
        ap("- (no canonical `tracked_items` changed this session)")
    ap("")

    # (4) BACKLOG (rendered from RAG — full enumeration, never bare counts) -
    ap("### 4 · Backlog (rendered from `tracked_items`)")
    ap("")
    ap(f"**Open ({len(backlog['open'])}):**")
    for ln in backlog["open"] or ["(none)"]:
        ap(f"- {ln}")
    ap("")
    ap(f"**Blocked / user-gated ({len(backlog['blocked_or_user_gated'])}):**")
    for ln in backlog["blocked_or_user_gated"] or ["(none)"]:
        ap(f"- {ln}")
    ap("")
    ap(f"**Deferred ({n_def}):**")
    for ln in backlog["deferred"] or ["(none)"]:
        ap(f"- {ln}")
    ap("")

    # (5) RISKS & DEVIATIONS (only amber/red) ------------------------------
    ap("### 5 · Risks & deviations (Road-to-Green)")
    ap("")
    risks = _risk_lines(
        released=released, tests_ok=tests_ok, health_ok=health_ok,
        drift_ok=drift_ok, claims_ok=claims_ok,
    )
    if risks:
        for r in risks:
            ap(f"- {r}")
    else:
        ap("- (none — all gates green)")
    ap("")

    # (6) LEDGER & ERRORS --------------------------------------------------
    ledger_open = [e for e in ledger
                   if str(e.get("disposition", "")).upper() == "OPEN"]
    error_items = [
        it for it in store
        if it.kind == ItemKind.ERROR and it.status in ACTIVE_STATUSES
    ]
    ap("### 6 · Ledger & errors")
    ap("")
    ap(
        f"- Inference ledger: {len(ledger)} entries · {len(ledger_open)} OPEN"
    )
    if ledger_open:
        for e in ledger_open:
            ap(f"  - {e.get('id', '?')}: {e.get('summary', '')}")
    ap(f"- Open error items: {len(error_items)}")
    for it in error_items:
        ap(f"  - {it.id}: {it.title}")
    ap("")

    # (7) VERIFICATION & HANDOFF ------------------------------------------
    ap("### 7 · Verification & handoff")
    ap("")
    ap(f"- Runtime version: {_cell(version)}")
    ap(f"- Git HEAD: {_cell(git_head)}")
    ap(f"- Tests: {_cell(tests)} · Health: {_cell(health)}")
    ap(f"- Drift gate (source sha256): {_cell(drift_sha)}")
    parity = (
        "n/a" if bak_parity is None
        else ("HOT == .bak (identical)" if bak_parity else "HOT != .bak (DIVERGED)")
    )
    ap(
        f"- RAG: seq {_cell(str(seq) if seq is not None else None)} · "
        f"written_by {_cell(written_by)} · rag_version {_cell(rag_version)} · "
        f"{_cell(str(rag_bytes) + ' bytes' if rag_bytes is not None else None)} · {parity}"
    )
    ap(f"- Backlog: {n_open} open · {n_def} deferred · {n_resolved} resolved")
    ap(f"- Handoff: {_cell(handoff)}")

    return "\n".join(L)


def _verdict_line(
    overall: str,
    *,
    released: Optional[bool],
    tests_ok: Optional[bool],
    health_ok: Optional[bool],
    drift_ok: Optional[bool],
    claims_ok: Optional[bool],
) -> str:
    """One honest sentence — states UNRELEASED/not-deployable when true (Rule 14)."""
    if overall == "RED":
        fails = []
        if tests_ok is False:
            fails.append("tests failing")
        if health_ok is False:
            fails.append("health below full")
        if drift_ok is False:
            fails.append("drift gate red")
        if claims_ok is False:
            fails.append("a published repo-claim contradicts reality")
        return "RED — " + (", ".join(fails) or "a hard gate is failing") + "."
    if overall == "AMBER":
        if released is False:
            return "AMBER — feature-complete but UNRELEASED / not deployable yet."
        return "AMBER — within tolerance, but one or more gates are unverified this session."
    return "GREEN — released, tests/health/drift all green, repo-claims reconciled."


def _risk_lines(
    *,
    released: Optional[bool],
    tests_ok: Optional[bool],
    health_ok: Optional[bool],
    drift_ok: Optional[bool],
    claims_ok: Optional[bool],
) -> list[str]:
    """Only amber/red items, each ``risk -> impact -> corrective action``."""
    out: list[str] = []
    if tests_ok is False:
        out.append("Tests failing -> build not shippable -> fix + rerun the full suite before release.")
    if health_ok is False:
        out.append("Health below full -> a capability module fails to import -> repair the module + re-run health.")
    if drift_ok is False:
        out.append("Drift gate red -> generated guards diverge from the .tla source -> regenerate guards + guardgen --check.")
    if claims_ok is False:
        out.append("A published repo-claim contradicts reality (Rule 11) -> public status is wrong -> reconcile the surface + re-audit.")
    if released is False:
        out.append("Unreleased -> capability not usable by deployments -> cut the release + redeploy the pinned package.")
    if tests_ok is None or health_ok is None or drift_ok is None or released is None:
        out.append("One or more gates unverified this session -> cannot assert GREEN -> run the verification suite before transfer.")
    return out
