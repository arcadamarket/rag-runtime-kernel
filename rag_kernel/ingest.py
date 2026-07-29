"""Document ingestion as a verb with a decidable exit predicate (S181, B4).

WHY THIS EXISTS (item BLUEPRINT-INGEST-PROTOCOL)
------------------------------------------------
RUNBOOK section 5A described how a source document should be absorbed into a
deployment: route its content into HOT/COLD, into the non-loaded context store,
into the tracked backlog, and into the root inventory. It was prose. A blueprint
declared "the clone boots holding this document" while the runbook contained
zero steps that landed it -- a precondition asserted by one document and produced
by no step of the other (E-091 defect 1).

The failure mode is not that ingestion is hard. It is that ingestion had no EXIT
CONDITION, so "we ingested it" was an opinion. This module makes it decidable:

    A document is INGESTED when every CLAIM it makes has a landing record in the
    RAG that resolves -- i.e. the deployment answers what the document answers,
    WITHOUT the document.

Anything that cannot be routed is reported as UNROUTED and blocks completion.
Silence is never taken as success.

ROUTING (RUNBOOK 5A, mechanised)
--------------------------------
    RULE        -> operating_protocol (HOT)      governance the deployment obeys
    REFERENCE   -> COLD                          bulk material, loaded on trigger
    ASSET       -> RAG_CONTEXT[baked_assets]     reusable artifacts (Rule 25)
    TASK        -> tracked_items                 work the document implies
    DELIVERABLE -> meta.root_deliverables        things the project ships

Claims are declared explicitly with an ``INGEST:`` marker, or inferred from
headings. Explicit always wins -- a document that says what it claims is
ingestible without heuristics, which is the shape every clone blueprint should
take from now on.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

#: Explicit claim marker: ``INGEST: <KIND> <id> — <text>``
CLAIM_RE = re.compile(
    # ids may be paths (ASSET/DELIVERABLE claims are routinely files), so '/'
    # and '\' are part of the id character class, not separators.
    r"^\s*(?:<!--\s*)?INGEST:\s*(?P<kind>[A-Z]+)\s+(?P<id>[A-Za-z0-9._/\\-]+)\s*"
    r"(?:[-—:]{1,2}\s*(?P<text>.*?))?\s*(?:-->)?\s*$"
)
HEADING_RE = re.compile(r"^(?P<hashes>#{1,4})\s+(?P<text>.+?)\s*#*\s*$")

KINDS = ("RULE", "REFERENCE", "ASSET", "TASK", "DELIVERABLE")

DESTINATION = {
    "RULE": "operating_protocol",
    "REFERENCE": "cold",
    "ASSET": "context:baked_assets",
    "TASK": "tracked_items",
    "DELIVERABLE": "meta.root_deliverables",
}

#: Heading keywords -> kind. Deliberately small and readable: a heuristic that
#: cannot be audited by eye is worse than no heuristic.
_HINTS: tuple[tuple[str, str], ...] = (
    ("rule", "RULE"), ("protocol", "RULE"), ("policy", "RULE"),
    ("mandate", "RULE"), ("governance", "RULE"), ("invariant", "RULE"),
    ("script", "ASSET"), ("runbook", "ASSET"), ("template", "ASSET"),
    ("checklist", "ASSET"),
    ("todo", "TASK"), ("backlog", "TASK"), ("action", "TASK"),
    ("next step", "TASK"), ("task", "TASK"),
    ("deliverable", "DELIVERABLE"), ("ship", "DELIVERABLE"),
    ("artifact", "DELIVERABLE"),
)


class IngestError(Exception):
    """Fail-loud condition raised by the ingest verb."""


@dataclass
class Claim:
    id: str
    kind: str
    text: str
    line: int
    explicit: bool

    @property
    def destination(self) -> str:
        return DESTINATION.get(self.kind, "")


@dataclass
class Route:
    claim: Claim
    destination: str
    landing_id: str
    action: str  # "present" | "create"


@dataclass
class IngestPlan:
    document: str
    claims: list[Claim] = field(default_factory=list)
    routes: list[Route] = field(default_factory=list)
    unrouted: list[Claim] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        out = {k: 0 for k in KINDS}
        for c in self.claims:
            if c.kind in out:
                out[c.kind] += 1
        return out

    @property
    def creates(self) -> list[Route]:
        return [r for r in self.routes if r.action == "create"]


def _slug(text: str) -> str:
    out = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").upper()
    return out[:48] or "CLAIM"


def classify_heading(text: str) -> Optional[str]:
    """Infer a claim kind from heading text, or None if nothing matches."""
    low = text.lower()
    for needle, kind in _HINTS:
        if needle in low:
            return kind
    return None


def extract_claims(text: str) -> list[Claim]:
    """Pull every claim a document makes.

    Explicit ``INGEST:`` markers are authoritative and are never overridden by
    heading inference. Headings contribute a claim only when they match a hint
    AND no explicit claim already carries that id.
    """
    claims: list[Claim] = []
    seen: set[str] = set()

    for lineno, raw in enumerate(text.splitlines(), start=1):
        m = CLAIM_RE.match(raw)
        if not m:
            continue
        kind = m.group("kind").upper()
        if kind not in KINDS:
            raise IngestError(
                f"line {lineno}: unknown INGEST kind {kind!r} — expected one of "
                + ", ".join(KINDS)
            )
        cid = m.group("id")
        if cid in seen:
            raise IngestError(f"line {lineno}: duplicate claim id {cid!r}")
        seen.add(cid)
        claims.append(
            Claim(id=cid, kind=kind, text=(m.group("text") or "").strip(),
                  line=lineno, explicit=True)
        )

    for lineno, raw in enumerate(text.splitlines(), start=1):
        m = HEADING_RE.match(raw)
        if not m:
            continue
        heading = m.group("text").strip()
        kind = classify_heading(heading)
        if not kind:
            continue
        cid = _slug(heading)
        if cid in seen:
            continue
        seen.add(cid)
        claims.append(
            Claim(id=cid, kind=kind, text=heading, line=lineno, explicit=False)
        )

    claims.sort(key=lambda c: c.line)
    return claims


# --------------------------------------------------------------------------- #
# Landing-record resolution — "does the RAG already answer this?"
# --------------------------------------------------------------------------- #
def _tracked_ids(rag: dict) -> set[str]:
    items = rag.get("tracked_items")
    if not isinstance(items, list):
        return set()
    return {i.get("id") for i in items if isinstance(i, dict) and i.get("id")}


def _asset_ids(context: Optional[dict]) -> set[str]:
    if not isinstance(context, dict):
        return set()
    assets = context.get("baked_assets")
    if not isinstance(assets, list):
        return set()
    out: set[str] = set()
    for a in assets:
        if isinstance(a, dict):
            for field_name in ("id", "path"):
                if a.get(field_name):
                    out.add(str(a[field_name]))
    return out


def resolve_landing(claim: Claim, rag: dict, context: Optional[dict] = None) -> tuple[str, str]:
    """Return ``(landing_id, action)`` for a claim against current state."""
    if claim.kind == "RULE":
        op = rag.get("operating_protocol") or {}
        key = claim.id if claim.id in op else claim.id.lower().replace("-", "_")
        return (key, "present" if key in op else "create")
    if claim.kind == "TASK":
        return (claim.id, "present" if claim.id in _tracked_ids(rag) else "create")
    if claim.kind == "ASSET":
        ids = _asset_ids(context)
        return (claim.id, "present" if claim.id in ids else "create")
    if claim.kind == "DELIVERABLE":
        meta = rag.get("meta") or {}
        listed = meta.get("root_deliverables")
        listed = listed if isinstance(listed, list) else []
        blob = " ".join(str(x) for x in listed)
        return (claim.id, "present" if claim.id in blob else "create")
    if claim.kind == "REFERENCE":
        return (claim.id, "create")
    raise IngestError(f"claim {claim.id!r}: unroutable kind {claim.kind!r}")


def plan_ingest(
    document: Path | str,
    rag: dict,
    *,
    context: Optional[dict] = None,
    text: Optional[str] = None,
) -> IngestPlan:
    """Compute what ingesting ``document`` would land, and where. Never writes."""
    p = Path(document)
    if text is None:
        if not p.exists():
            raise IngestError(f"document not found: {p}")
        text = p.read_text(encoding="utf-8", errors="replace")

    plan = IngestPlan(document=p.name)
    plan.claims = extract_claims(text)
    if not plan.claims:
        raise IngestError(
            f"{p.name}: no claims found. A document with nothing to land cannot "
            f"be ingested — declare what it answers with `INGEST: <KIND> <id> — "
            f"<text>` lines, or give it headings that name rules, assets, tasks "
            f"or deliverables."
        )
    for claim in plan.claims:
        try:
            landing_id, action = resolve_landing(claim, rag, context)
        except IngestError:
            plan.unrouted.append(claim)
            continue
        plan.routes.append(
            Route(claim=claim, destination=claim.destination,
                  landing_id=landing_id, action=action)
        )
    return plan


# --------------------------------------------------------------------------- #
# Exit predicate — the whole point of the verb
# --------------------------------------------------------------------------- #
def ingest_complete(
    plan: IngestPlan, rag: dict, *, context: Optional[dict] = None
) -> tuple[bool, str]:
    """Decidable: does the deployment answer what the document answers, WITHOUT it?

    True iff every claim routed AND every landing record now resolves in state.
    Re-resolves against live state rather than trusting the plan, so this is a
    real post-condition and not a restatement of intent.
    """
    if plan.unrouted:
        return False, (
            f"INCOMPLETE: {len(plan.unrouted)} claim(s) could not be routed: "
            + ", ".join(c.id for c in plan.unrouted[:8])
        )
    missing: list[str] = []
    for route in plan.routes:
        _lid, action = resolve_landing(route.claim, rag, context)
        if action != "present":
            missing.append(route.claim.id)
    if missing:
        return False, (
            f"INCOMPLETE: {len(missing)} claim(s) have no landing record yet: "
            + ", ".join(missing[:8])
            + (" …" if len(missing) > 8 else "")
        )
    return True, (
        f"COMPLETE: all {len(plan.routes)} claim(s) resolve to governed records — "
        f"the deployment answers this document without it"
    )


def render_plan(plan: IngestPlan, *, limit: int = 0) -> str:
    """Bounded, deterministic render (Rule 17)."""
    lines = [f"ingest plan — {plan.document}"]
    counts = plan.counts
    for kind in KINDS:
        lines.append(f"  {kind:<12} {counts[kind]:>3}   -> {DESTINATION[kind]}")
    creates = plan.creates
    lines.append("")
    lines.append(f"  already present: {len(plan.routes) - len(creates)}   "
                 f"to create: {len(creates)}   unrouted: {len(plan.unrouted)}")
    shown = creates if limit <= 0 else creates[:limit]
    if shown:
        lines.append("")
        for route in shown:
            mark = "!" if not route.claim.explicit else "+"
            lines.append(
                f"  {mark} [{route.claim.kind}] {route.claim.id} -> "
                f"{route.destination}"
            )
            if route.claim.text:
                lines.append(f"      {route.claim.text[:110]}")
        if len(creates) > len(shown):
            lines.append(f"  … {len(creates) - len(shown)} more (raise --limit)")
    if plan.unrouted:
        lines.append("")
        lines.append(f"  UNROUTED ({len(plan.unrouted)}) — these BLOCK completion:")
        for claim in plan.unrouted:
            lines.append(f"    ? {claim.id} (line {claim.line})")
    lines.append("")
    lines.append("  ('!' = inferred from a heading, not declared — prefer explicit "
                 "INGEST: markers)")
    return "\n".join(lines)


def unlanded_claims(plan: IngestPlan) -> list[Claim]:
    """Claims whose landing record does not exist yet — the work ingestion implies."""
    return [r.claim for r in plan.creates]


def claims_by_kind(plan: IngestPlan, kind: str) -> list[Claim]:
    return [c for c in plan.claims if c.kind == kind]


def assert_ingestable(plan: IngestPlan) -> None:
    """Fail loud before any write if the document cannot be fully landed."""
    if plan.unrouted:
        raise IngestError(
            f"{plan.document}: {len(plan.unrouted)} claim(s) cannot be routed — "
            f"ingestion would be partial, which is the failure this verb exists "
            f"to prevent: "
            + ", ".join(c.id for c in plan.unrouted[:8])
        )
