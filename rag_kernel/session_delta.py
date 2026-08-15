"""Session delta — the debit/credit ledger a session owes its successor.

WHY THIS EXISTS (SESSION-DELTA-RITUAL, S199)
    Every session so far has ended with a hand-written delta: what opened, what
    closed, which counters moved, what was left lingering. Hand-written means
    unmeasured, and this project has already banked what that costs — S198 said
    "107 session logs" where the count was 108, and S197's chat summary and its
    stored directive disagreed about the P1 ordering with only the stored one
    loaded at boot. A number stated from memory is prose wearing a number's
    clothes.

    So the delta is computed, not composed. Every line below is derived from the
    canonical store: item transitions come from each item's own ``history``
    entries, which already carry the session id that made them; counters come
    from the rendered partitions, the asset registry, the formal directory and
    git. Nothing here is passed in by the agent writing the handoff, with the
    single exception of the audit tallies, which are expensive to recompute and
    are therefore rendered as "not measured this run" when absent rather than
    guessed.

THE BASELINE PROBLEM, AND WHY IT SELF-SEEDS
    A delta needs a "before". Boot deliberately writes nothing (AUTO-SID-DERIVE),
    so there is no start-of-session snapshot to diff against and adding one would
    mean writing during boot — the exact thing the boot contract forbids.
    Instead, each run PERSISTS its own counters into the non-loaded
    ``RAG_CONTEXT.json`` partition, and the next run diffs against that. The
    first ever run has no baseline and says so, in the report, rather than
    printing zeros that look like "nothing changed".

DESIGN POSTURE (dual-POV)
    CS lens — a pure function over persisted state: ``compute`` takes HOT plus a
    baseline dict and returns a frozen dataclass; ``render`` is a total function
    over that dataclass. The only I/O is in the collectors, which self-skip to
    None on any failure so a missing git binary or an unregistered asset store
    degrades one line instead of the report.
    ML lens — this is the artifact the next agent's context is seeded from. It is
    written to be READ under a token budget: the movements first, the counters as
    before -> after pairs, and the lingering set last, because that is the part a
    successor is most likely to rediscover as a surprise.

Spec reference: session close ritual, alongside the canonical report (S139).
Pairs with: __main__._drive_close (emission), asset_registry (asset count).

@rag-kernel-manifest
{
  "module": "rag_kernel.session_delta",
  "capability": "session_delta_report",
  "description": "Deterministic end-of-session debit/credit report: item movements derived from tracked_items history, counters diffed against a self-seeding baseline persisted in the non-loaded context store, and the untouched-live-item set a successor inherits",
  "exports": ["ItemMove", "SessionDelta", "collect_counters", "compute", "render", "load_baseline", "save_baseline", "PARTITION_NAME"],
  "use_when": "Session close (session-end), checkpoint reporting, or any time a session must state what it changed in measured terms",
  "never_bypass": false
}
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

PARTITION_NAME = "session_counters"

#: Statuses that mean an item is still live work.
LIVE_STATUSES = ("OPEN", "IN_PROGRESS")
#: Statuses that mean an item left the board under its own power.
CLOSED_STATUSES = ("RESOLVED", "DISCARDED", "SUPERSEDED")


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ItemMove:
    """One status transition attributed to one session, as the item recorded it."""

    item_id: str
    kind: str
    from_status: Optional[str]
    to_status: str
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "kind": self.kind,
            "from_status": self.from_status,
            "to_status": self.to_status,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class SessionDelta:
    """Everything one session did to the board, derived rather than declared."""

    session: str
    opened: tuple[str, ...] = ()
    closed: tuple[ItemMove, ...] = ()
    reopened: tuple[ItemMove, ...] = ()
    other_moves: tuple[ItemMove, ...] = ()
    touched_notes: tuple[str, ...] = ()
    counters_before: dict = field(default_factory=dict)
    counters_after: dict = field(default_factory=dict)
    lingering_p1: tuple[str, ...] = ()
    untouched_live: tuple[str, ...] = ()
    baseline_session: Optional[str] = None
    #: Items this session last touched that carry NO history at all. Without a
    #: baseline these cannot be separated from ones it created — see compute().
    origin_undetermined: tuple[str, ...] = ()

    @property
    def opened_and_closed(self) -> tuple[str, ...]:
        """Items this session both created and closed — worth naming separately."""
        closed_ids = {m.item_id for m in self.closed}
        return tuple(i for i in self.opened if i in closed_ids)

    def to_dict(self) -> dict:
        return {
            "session": self.session,
            "opened": list(self.opened),
            "closed": [m.to_dict() for m in self.closed],
            "reopened": [m.to_dict() for m in self.reopened],
            "other_moves": [m.to_dict() for m in self.other_moves],
            "touched_notes": list(self.touched_notes),
            "counters_before": self.counters_before,
            "counters_after": self.counters_after,
            "lingering_p1": list(self.lingering_p1),
            "untouched_live": list(self.untouched_live),
            "baseline_session": self.baseline_session,
            "origin_undetermined": list(self.origin_undetermined),
        }


# --------------------------------------------------------------------------- #
# Baseline persistence (non-loaded store, same mechanism as baked_assets)
# --------------------------------------------------------------------------- #
def load_baseline(rag_dir: Optional[Path | str]) -> Optional[dict]:
    """Previous run's counters, or None when none has ever been written."""
    if rag_dir is None:
        return None
    try:
        from rag_kernel.cold_manager import ProjectContextManager
        mgr = ProjectContextManager.default(Path(rag_dir))
        if not mgr.has_partition(PARTITION_NAME):
            return None
        part = mgr.get(PARTITION_NAME)
    except Exception:  # noqa: BLE001 — absent store is a legitimate first run
        return None
    if not isinstance(part, dict):
        return None
    counters = part.get("counters")
    return part if isinstance(counters, dict) else None


def save_baseline(rag_dir: Path | str, session: str, counters: dict,
                  item_ids: Optional[list[str]] = None) -> None:
    """Persist this run's counters AND its item-id set as the next session's before.

    The id set is what makes "opened" exact instead of inferred. ``add`` writes
    an item with an EMPTY history, and so does a note rewrite on an item that
    never transitioned — from the item alone the two are indistinguishable, and
    S199's first cut duly reported RESIDENT-SUPERVISOR and ACTIVATION-GAP-S197 as
    "opened by S199" when it had only re-noted them. Set difference has no such
    ambiguity.
    """
    from rag_kernel.cold_manager import ProjectContextManager
    mgr = ProjectContextManager.default(Path(rag_dir))
    mgr.update_partition(PARTITION_NAME, {
        "_protocol": "SESSION-DELTA-RITUAL (S199): counters + tracked-item id set; "
                     "the next session diffs against this instead of remembering. "
                     "The id set exists because an added item and a re-noted item "
                     "are indistinguishable from the item alone.",
        "session": session,
        "counters": counters,
        "item_ids": sorted(item_ids or []),
    })


# --------------------------------------------------------------------------- #
# Collectors — each self-skips to None rather than failing the report
# --------------------------------------------------------------------------- #
def _git(repo: Path, *args: str) -> Optional[str]:
    try:
        out = subprocess.run(["git", "-C", str(repo), *args],
                             capture_output=True, text=True, timeout=30)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _count_live(items: list[dict]) -> int:
    return sum(1 for it in items if str(it.get("status", "")).upper() in LIVE_STATUSES)


def collect_counters(
    hot: dict,
    rag_dir: Optional[Path | str] = None,
    project_root: Optional[Path | str] = None,
    repo_root: Optional[Path | str] = None,
    audit_errors: Optional[int] = None,
    audit_warnings: Optional[int] = None,
) -> dict:
    """Every number the delta reports, measured at this instant.

    A key is present with value ``None`` when it could not be measured — that is
    deliberate. An absent measurement and a measured zero are different claims,
    and collapsing them is how "0 errors" gets printed by a run that never
    audited anything.
    """
    items = hot.get("tracked_items") or []
    counters: dict[str, Any] = {
        "tracked_items": len(items),
        "live_items": _count_live(items),
        "open_tasks": len(hot.get("open_tasks") or []),
        "deferred_items": len(hot.get("deferred_items") or []),
        "priority_actions": len(hot.get("priority_actions") or []),
        "audit_errors": audit_errors,
        "audit_warnings": audit_warnings,
    }

    ledger = hot.get("inference_ledger") or []
    if isinstance(ledger, list):
        counters["ledger_total"] = len(ledger)
        counters["ledger_open"] = sum(
            1 for e in ledger
            if isinstance(e, dict) and str(e.get("status", "")).upper() == "OPEN")
    else:
        counters["ledger_total"] = None
        counters["ledger_open"] = None

    gate = ((hot.get("meta") or {}).get("test_gate") or {})
    counters["tests"] = gate.get("count") if isinstance(gate, dict) else None
    counters["tests_commit"] = gate.get("git_head") if isinstance(gate, dict) else None

    try:
        from rag_kernel import __version__ as _v
        counters["runtime"] = _v
    except Exception:  # pragma: no cover - version always importable
        counters["runtime"] = None

    counters["baked_assets"] = None
    if rag_dir is not None:
        try:
            from rag_kernel.asset_registry import list_assets
            counters["baked_assets"] = len(list_assets(Path(rag_dir)))
        except Exception:  # noqa: BLE001 — no registry is a legitimate state
            counters["baked_assets"] = None

    counters["tla_specs"] = None
    counters["tlc_configs"] = None
    for base in [p for p in (repo_root, project_root) if p is not None]:
        formal = Path(base) / "formal"
        if formal.is_dir():
            counters["tla_specs"] = len(list(formal.glob("*.tla")))
            counters["tlc_configs"] = len(list(formal.glob("*.cfg")))
            break

    counters["git_head"] = None
    counters["git_dirty"] = None
    if repo_root is not None and Path(repo_root).exists():
        counters["git_head"] = _git(Path(repo_root), "rev-parse", "--short", "HEAD")
        porcelain = _git(Path(repo_root), "status", "--porcelain")
        if porcelain is not None:
            counters["git_dirty"] = len([l for l in porcelain.splitlines() if l.strip()])

    return counters


# --------------------------------------------------------------------------- #
# Compute
# --------------------------------------------------------------------------- #
def _p1_id(entry: Any) -> str:
    """Item id out of a rendered priority_actions entry.

    The partition holds RENDERED strings — ``ID [P1 · OPEN · S199]: title…`` —
    not objects. Reading them as objects yields an empty id set, which makes
    "no untouched P1 items" print on a board with eleven of them. Measured on
    this deployment, S199.
    """
    if isinstance(entry, dict):
        return str(entry.get("id") or entry.get("item_id") or "")
    text = str(entry or "").strip()
    for sep in (" [", ":", " "):
        if sep in text:
            return text.split(sep, 1)[0].strip()
    return text


def _moves_for(item: dict, session: str) -> list[ItemMove]:
    out: list[ItemMove] = []
    for h in item.get("history") or []:
        if not isinstance(h, dict) or h.get("session") != session:
            continue
        out.append(ItemMove(
            item_id=str(item.get("id", "")),
            kind=str(item.get("kind", "")),
            from_status=h.get("from_status"),
            to_status=str(h.get("to_status", "")),
            reason=str(h.get("reason") or ""),
        ))
    return out


def compute(hot: dict, session: str, baseline: Optional[dict] = None,
            counters_after: Optional[dict] = None) -> SessionDelta:
    """Derive the delta for ``session`` from the canonical store.

    OPENED is a set difference against the baseline's id set, not an inference.
    ``add`` writes an item with an empty history and stamps ``session``; a note
    rewrite on an untransitioned item leaves exactly the same fingerprint. The
    first cut of this module inferred from the item alone and reported two items
    S199 had merely re-noted as items S199 had opened. With no baseline the two
    populations are reported together, named, and labelled undetermined — an
    honest gap beats a confident wrong number.
    """
    items = [i for i in (hot.get("tracked_items") or []) if isinstance(i, dict)]
    known = set((baseline or {}).get("item_ids") or [])
    have_baseline_ids = bool(known)

    opened: list[str] = []
    closed: list[ItemMove] = []
    reopened: list[ItemMove] = []
    other: list[ItemMove] = []
    touched: list[str] = []
    undetermined: list[str] = []

    for it in items:
        iid = str(it.get("id", ""))
        hist = [h for h in (it.get("history") or []) if isinstance(h, dict)]
        moves = _moves_for(it, session)

        if have_baseline_ids:
            if iid not in known:
                opened.append(iid)
        elif not hist and it.get("session") == session:
            undetermined.append(iid)

        for m in moves:
            to_s = (m.to_status or "").upper()
            from_s = (m.from_status or "").upper()
            if to_s in CLOSED_STATUSES:
                closed.append(m)
            elif from_s in CLOSED_STATUSES and to_s in LIVE_STATUSES:
                reopened.append(m)
            elif m.from_status:
                other.append(m)

        if (not moves and iid not in opened and iid not in undetermined
                and it.get("session") == session):
            # Touched without a transition: a note rewrite or a priority change.
            touched.append(iid)

    live = [i for i in items if str(i.get("status", "")).upper() in LIVE_STATUSES]
    moved_ids = ({m.item_id for m in closed + reopened + other}
                 | set(opened) | set(touched) | set(undetermined))
    untouched_live = tuple(sorted(
        str(i.get("id", "")) for i in live if str(i.get("id", "")) not in moved_ids))

    p1_ids = {_p1_id(x) for x in (hot.get("priority_actions") or [])}
    lingering_p1 = tuple(sorted(i for i in p1_ids if i and i in set(untouched_live)))

    base_counters = (baseline or {}).get("counters") if baseline else None
    return SessionDelta(
        session=session,
        opened=tuple(sorted(opened)),
        closed=tuple(closed),
        reopened=tuple(reopened),
        other_moves=tuple(other),
        touched_notes=tuple(sorted(touched)),
        counters_before=base_counters or {},
        counters_after=counters_after or {},
        lingering_p1=lingering_p1,
        untouched_live=untouched_live,
        baseline_session=(baseline or {}).get("session") if baseline else None,
        origin_undetermined=tuple(sorted(undetermined)),
    )


# --------------------------------------------------------------------------- #
# Render
# --------------------------------------------------------------------------- #
import re

#: Claim patterns -> the counter each one asserts (E-132, S199).
#: Each regex captures ONE number group. They are deliberately narrow: a gate
#: that guesses at prose will fire on a sentence that happens to contain a
#: digit, get overridden once, and then get deleted.
_CLAIM_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bgate\s+([\d,]+)\s+green\b", "tests"),
    (r"\b([\d,]+)\s+green\b", "tests"),
    (r"\b([\d,]+)\s+open\s+tasks?\b", "open_tasks"),
    (r"\bopen\s+tasks?\s+([\d,]+)\b", "open_tasks"),
    (r"\baudit\s+([\d,]+)\s+errors?\b", "audit_errors"),
    (r"\b([\d,]+)\s+errors?\s*/\s*[\d,]+\s+warnings?\b", "audit_errors"),
    (r"\b[\d,]+\s+errors?\s*/\s*([\d,]+)\s+warnings?\b", "audit_warnings"),
    (r"\b([\d,]+)\s+baked\s+assets?\b", "baked_assets"),
    (r"\b([\d,]+)\s+deferred\b", "deferred_items"),
    (r"\bdirty\s+([\d,]+)\b", "git_dirty"),
    (r"\bTLC\s+([\d,]+)\s*/\s*[\d,]+\b", "tlc_configs"),
    (r"\b([\d,]+)\s+TLC\s+configs?\b", "tlc_configs"),
    (r"\b([\d,]+)\s+specs?\b", "tla_specs"),
)

#: A claim written as a progression — "2,758 -> 2,764 -> 2,788" — asserts the
#: LAST value. Collapsing the arrow first is what stops the gate reading a
#: session's own starting point as its closing claim.
_ARROW = re.compile(r"([\d,]+)\s*(?:->|→|to)\s*(?=[\d,]+)")


def _num(text: str) -> Optional[int]:
    try:
        return int(text.replace(",", ""))
    except ValueError:
        return None


def check_handoff_claims(handoff: str, counters: dict) -> list[str]:
    """Contradictions between a handoff's stated numbers and the measured ones.

    E-132: ``session-end --handoff`` asserts backlog facts that nothing checks
    against tracked_items, and the project has two recorded instances of that
    going wrong — S197 stating a remedy measurement had already ruled out, and
    S197's chat summary and stored directive carrying different P1 orderings
    with only the stored one loaded at boot.

    Returns a list of human-readable contradictions (empty = clean). A counter
    that was NOT measured cannot contradict anything, so an unmeasured audit
    silences the audit clauses instead of failing them — the alternative is a
    gate that refuses every close run without a fresh audit.
    """
    if not handoff:
        return []
    text = _ARROW.sub("", " ".join(handoff.split()))
    problems: list[str] = []
    seen: set[tuple[str, int]] = set()
    for pattern, key in _CLAIM_PATTERNS:
        measured = counters.get(key)
        if measured is None or not isinstance(measured, int):
            continue
        for m in re.finditer(pattern, text, flags=re.IGNORECASE):
            claimed = _num(m.group(1))
            if claimed is None or (key, claimed) in seen:
                continue
            seen.add((key, claimed))
            if claimed != measured:
                problems.append(
                    f"handoff claims {key}={claimed:,} but the measured value is "
                    f"{measured:,} — {m.group(0).strip()!r}")
    return problems


_COUNTER_LABELS = (
    ("priority_actions", "P1"),
    ("open_tasks", "Open tasks"),
    ("deferred_items", "Deferred"),
    ("live_items", "Live items (OPEN+IN_PROGRESS)"),
    ("tracked_items", "Tracked items"),
    ("ledger_open", "Inference ledger OPEN"),
    ("ledger_total", "Inference ledger total"),
    ("tests", "Tests"),
    ("runtime", "Runtime"),
    ("baked_assets", "Baked assets"),
    ("tla_specs", "TLA+ specs"),
    ("tlc_configs", "TLC configs"),
    ("audit_errors", "Audit errors"),
    ("audit_warnings", "Audit warnings"),
)


def _fmt(v: Any) -> str:
    if v is None:
        return "not measured"
    if isinstance(v, int) and not isinstance(v, bool):
        return f"{v:,}"
    return str(v)


def render(delta: SessionDelta) -> str:
    """The report, as markdown. Total over any SessionDelta, including an empty one."""
    L: list[str] = []
    L.append(f"# SESSION DELTA — {delta.session}")
    L.append("")
    L.append("Computed from tracked_items history and measured counters, not composed. "
             "A line reading `not measured` means exactly that: no value was taken "
             "this run.")
    L.append("")

    L.append("## Ledger")
    both = set(delta.opened_and_closed)
    if delta.origin_undetermined and not delta.opened:
        L.append(f"- Opened: undetermined "
                 f"({len(delta.origin_undetermined)} added-or-re-noted, listed below)")
    else:
        L.append(f"- Opened: {len(delta.opened)}"
                 + (f" ({len(both)} of them also closed this session)" if both else ""))
    L.append(f"- Closed: {len(delta.closed)}")
    if delta.reopened:
        L.append(f"- Reopened: {len(delta.reopened)}")
    if delta.other_moves:
        L.append(f"- Other transitions: {len(delta.other_moves)}")
    if delta.touched_notes:
        L.append(f"- Touched without a transition (note/priority): "
                 f"{len(delta.touched_notes)}")
    L.append("")

    if delta.opened:
        L.append("### Opened")
        for i in delta.opened:
            tag = " — and closed here" if i in both else ""
            L.append(f"- `{i}`{tag}")
        L.append("")

    if delta.origin_undetermined:
        L.append("### Added or re-noted — origin undetermined")
        L.append("")
        L.append("These carry no history and name this session. With no baseline "
                 "id set there is no way to tell an item this session ADDED from "
                 "one it merely re-noted, so they are listed together rather than "
                 "guessed apart. From the next run this section disappears.")
        L.append("")
        for i in delta.origin_undetermined:
            L.append(f"- `{i}`")
        L.append("")

    if delta.closed:
        L.append("### Closed")
        L.append("")
        L.append("| Item | Kind | From | To |")
        L.append("| --- | --- | --- | --- |")
        for m in delta.closed:
            L.append(f"| `{m.item_id}` | {m.kind or '—'} | "
                     f"{m.from_status or '—'} | {m.to_status} |")
        L.append("")

    if delta.reopened:
        L.append("### Reopened")
        for m in delta.reopened:
            L.append(f"- `{m.item_id}` ({m.from_status} -> {m.to_status})")
        L.append("")

    if delta.touched_notes:
        L.append("### Notes rewritten (no status change)")
        L.append(", ".join(f"`{i}`" for i in delta.touched_notes))
        L.append("")

    L.append("## Counters")
    if not delta.counters_before:
        L.append("")
        L.append("_No baseline: this is the first run of the delta ritual in this "
                 "deployment, so there is nothing to diff against. This run's "
                 "counters are recorded and the next session will have a measured "
                 "before._")
        L.append("")
    before, after = delta.counters_before, delta.counters_after
    L.append("")
    L.append("| Counter | Before | After | Δ |")
    L.append("| --- | --- | --- | --- |")
    for key, label in _COUNTER_LABELS:
        b, a = before.get(key), after.get(key)
        if b is None and a is None:
            continue
        arrow = ""
        if isinstance(b, int) and isinstance(a, int) and not isinstance(b, bool):
            d = a - b
            arrow = "0" if d == 0 else (f"+{d}" if d > 0 else str(d))
        elif b != a and b is not None and a is not None:
            arrow = "changed"
        L.append(f"| {label} | {_fmt(b)} | {_fmt(a)} | {arrow} |")
    L.append("")

    head = after.get("git_head")
    dirty = after.get("git_dirty")
    if head or dirty is not None:
        L.append(f"HEAD `{head or 'not measured'}`, "
                 f"dirty {_fmt(dirty)}, "
                 f"gate stamped at `{after.get('tests_commit') or 'not measured'}`.")
        L.append("")

    L.append("## Left lingering")
    if delta.lingering_p1:
        L.append("")
        L.append("**P1 items this session did not touch at all:**")
        for i in delta.lingering_p1:
            L.append(f"- `{i}`")
        L.append("")
    else:
        L.append("")
        L.append("_No untouched P1 items._")
        L.append("")
    L.append(f"Live items untouched by {delta.session}: {len(delta.untouched_live)}.")
    if delta.untouched_live:
        L.append("")
        L.append("<details><summary>All untouched live items</summary>")
        L.append("")
        L.append(", ".join(f"`{i}`" for i in delta.untouched_live))
        L.append("")
        L.append("</details>")
    L.append("")
    return "\n".join(L)
