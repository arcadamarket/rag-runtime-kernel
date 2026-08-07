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

#: INGEST-KIND-UNVALIDATED (S187). ``CLAIM_RE`` constrains the kind token to
#: ``[A-Z]+``, so it only ever REFUSED a kind that was validly SHAPED but unknown
#: ("DECISION"). A kind carrying a hyphen, digit or lowercase — "Decision",
#: "DECISION-LOG", "task" — failed the pattern outright, and a line that does not
#: match is silently skipped. Silence is the defect: the sender declares a kind the
#: receiver never heard of, nothing is extracted, ``unrouted`` is empty and the exit
#: predicate reports COMPLETE over claims that were never read. That is how a parent
#: handoff declaring four invented kinds landed as "COMPLETE 8 of 8" over records the
#: receiver had re-authored — the enforceable half of HANDOFF-PRESCRIPTION-BAN.
#:
#: This deliberately-permissive detector recognises the INGEST marker by its LABEL
#: alone and captures whatever occupies the kind slot. Every line it matches must
#: then produce a valid claim or FAIL LOUD, so an undeclared kind can no longer be
#: answered with silence. It is intentionally looser than ``CLAIM_RE``: its job is
#: to catch what ``CLAIM_RE`` cannot parse, not to parse it.
MARKER_RE = re.compile(r"^\s*(?:<!--\s*)?INGEST:\s*(?P<kind>\S+)(?:\s+(?P<rest>.*))?$")

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
    #: WHY the claim resolved: "id" | "title" | "none" (S187). Defaulted so every
    #: pre-existing construction site keeps working unchanged.
    basis: str = "id"


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
            # INGEST-KIND-UNVALIDATED (S187): the line is not a parseable claim.
            # If it nevertheless CARRIES the INGEST marker, the sender meant to
            # declare something and the receiver could not read it. Refusing is
            # the only honest answer — silently skipping is what let four invented
            # kinds pass as a satisfied exit predicate.
            marker = MARKER_RE.match(raw)
            if marker:
                raise IngestError(
                    f"line {lineno}: undeclared INGEST kind "
                    f"{marker.group('kind')!r} — this deployment declares only "
                    + ", ".join(KINDS)
                    + ". Run `rag_kernel list-kinds` for the authoritative set; a "
                    "sender cannot introduce a kind the receiver does not define."
                )
            continue
        kind = m.group("kind").upper()
        if kind not in KINDS:
            raise IngestError(
                f"line {lineno}: undeclared INGEST kind {kind!r} — this deployment "
                "declares only " + ", ".join(KINDS)
                + ". Run `rag_kernel list-kinds` for the authoritative set; a "
                "sender cannot introduce a kind the receiver does not define."
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
#: INGEST-PREDICATE-SLUG-COUPLING (S187). The exit predicate compared a claim id to
#: a landing id by RAW STRING EQUALITY, and a heading-inferred claim's id is a SLUG
#: of the heading ("priority-actions-render"). A record landed under a semantic id
#: ("PRIORITY-ACTIONS-STALE-SNAPSHOT") therefore read as unlanded forever, and the
#: only per-kind mitigation was RULE's ad-hoc ``.lower().replace("-", "_")`` — one
#: kind's private guess at equivalence, invisible to the other four.
#:
#: Equivalence is now decided ONCE, here, for every kind: casefold and collapse every
#: run of non-alphanumerics to a single hyphen, so ``FOO_BAR``, ``foo-bar`` and
#: ``Foo Bar`` are the same landing. Purely structural — no stemming, no fuzzy
#: distance, nothing an operator cannot verify by eye.
_NORM_RE = re.compile(r"[^a-z0-9]+")


def normalize_id(value: str) -> str:
    """Canonical comparison form for a claim id / landing id / title."""
    return _NORM_RE.sub("-", str(value).strip().casefold()).strip("-")


def _tracked_ids(rag: dict) -> set[str]:
    items = rag.get("tracked_items")
    if not isinstance(items, list):
        return set()
    return {i.get("id") for i in items if isinstance(i, dict) and i.get("id")}


def _tracked_index(rag: dict) -> "tuple[dict[str, str], dict[str, str]]":
    """Return ``(by_normalized_id, by_normalized_title)`` -> real tracked_item id.

    The title index is the operator-ratified second half of the fix (S187): a
    heading is an echo of what a record is CALLED, not of what it is KEYED as, so a
    semantically-named record is reachable from the heading that describes it. Only
    INFERRED claims consult it — an explicit ``INGEST:`` marker states an id and is
    held to it. First writer wins on a collision, so resolution stays deterministic.
    """
    by_id: dict[str, str] = {}
    by_title: dict[str, str] = {}
    items = rag.get("tracked_items")
    if not isinstance(items, list):
        return by_id, by_title
    for it in items:
        if not isinstance(it, dict):
            continue
        rid = it.get("id")
        if rid:
            by_id.setdefault(normalize_id(rid), str(rid))
        title = it.get("title")
        if title and rid:
            by_title.setdefault(normalize_id(title), str(rid))
    return by_id, by_title


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


def resolve_landing_ex(
    claim: Claim, rag: dict, context: Optional[dict] = None
) -> tuple[str, str, str]:
    """Return ``(landing_id, action, basis)`` for a claim against current state.

    ``basis`` names WHY the claim resolved — ``"id"`` (normalized id equality),
    ``"title"`` (an inferred claim matched a tracked_item's title) or ``"none"``
    (nothing landed it). It is rendered, never inferred silently: a title match is a
    weaker equivalence than an id match and the reader is entitled to see which one
    the predicate used (INGEST-PREDICATE-SLUG-COUPLING, S187).
    """
    n = normalize_id(claim.id)

    if claim.kind == "RULE":
        op = rag.get("operating_protocol") or {}
        if isinstance(op, dict):
            for key in op:
                if normalize_id(key) == n:
                    return (key, "present", "id")
        return (claim.id, "create", "none")

    if claim.kind == "TASK":
        by_id, by_title = _tracked_index(rag)
        if n in by_id:
            return (by_id[n], "present", "id")
        # Operator ruling S187: an INFERRED claim may land by title. An EXPLICIT
        # marker declared an id and is held to it — otherwise a sender could land a
        # record it never named, which is the failure mode HANDOFF-PRESCRIPTION-BAN
        # exists to stop.
        if not claim.explicit and n in by_title:
            return (by_title[n], "present", "title")
        return (claim.id, "create", "none")

    if claim.kind == "ASSET":
        for real in _asset_ids(context):
            if normalize_id(real) == n:
                return (real, "present", "id")
        return (claim.id, "create", "none")

    if claim.kind == "DELIVERABLE":
        meta = rag.get("meta") or {}
        listed = meta.get("root_deliverables")
        listed = listed if isinstance(listed, list) else []
        blob = normalize_id(" ".join(str(x) for x in listed))
        return (claim.id, "present" if n and n in blob else "create",
                "id" if n and n in blob else "none")

    if claim.kind == "REFERENCE":
        return (claim.id, "create", "none")

    raise IngestError(f"claim {claim.id!r}: unroutable kind {claim.kind!r}")


def resolve_landing(claim: Claim, rag: dict, context: Optional[dict] = None) -> tuple[str, str]:
    """Return ``(landing_id, action)`` for a claim against current state.

    Thin projection of :func:`resolve_landing_ex`, kept at its original arity so
    existing callers and pins are unaffected by the S187 basis addition.
    """
    landing_id, action, _basis = resolve_landing_ex(claim, rag, context)
    return (landing_id, action)


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
            landing_id, action, basis = resolve_landing_ex(claim, rag, context)
        except IngestError:
            plan.unrouted.append(claim)
            continue
        plan.routes.append(
            Route(claim=claim, destination=claim.destination,
                  landing_id=landing_id, action=action, basis=basis)
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
    by_title = 0
    for route in plan.routes:
        _lid, action, basis = resolve_landing_ex(route.claim, rag, context)
        if action != "present":
            missing.append(route.claim.id)
        elif basis == "title":
            by_title += 1
    if missing:
        return False, (
            f"INCOMPLETE: {len(missing)} claim(s) have no landing record yet: "
            + ", ".join(missing[:8])
            + (" …" if len(missing) > 8 else "")
        )
    # S187: a title match is a weaker equivalence than an id match. COMPLETE is still
    # COMPLETE, but the verdict SAYS how many claims got there by title — a reader
    # must never have to guess which basis the predicate used.
    caveat = (
        f" ({by_title} by heading->title match, not id)" if by_title else ""
    )
    return True, (
        f"COMPLETE: all {len(plan.routes)} claim(s) resolve to governed records"
        f"{caveat} — the deployment answers this document without it"
    )


def render_plan(plan: IngestPlan, *, limit: int = 0) -> str:
    """Bounded, deterministic render (Rule 17)."""
    lines = [f"ingest plan — {plan.document}"]
    counts = plan.counts
    for kind in KINDS:
        lines.append(f"  {kind:<12} {counts[kind]:>3}   -> {DESTINATION[kind]}")
    creates = plan.creates
    titled = [r for r in plan.routes if r.basis == "title"]
    lines.append("")
    lines.append(f"  already present: {len(plan.routes) - len(creates)}   "
                 f"to create: {len(creates)}   unrouted: {len(plan.unrouted)}")
    if titled:
        # S187: never let a weaker equivalence pass as an id match without saying so.
        lines.append("")
        lines.append(f"  MATCHED BY TITLE ({len(titled)}) — heading text resolved to a "
                     "record with a different id:")
        for route in titled:
            lines.append(f"    ~ {route.claim.id} -> {route.landing_id}")
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
    lines.append("  ('~' = landed by title, not id — a weaker equivalence than an "
                 "id match)")
    return "\n".join(lines)


def unlanded_claims(plan: IngestPlan) -> list[Claim]:
    """Claims whose landing record does not exist yet — the work ingestion implies."""
    return [r.claim for r in plan.creates]


def declared_kinds() -> list[dict]:
    """The authoritative kind set a sender may declare, with its destination.

    INGEST-KIND-UNVALIDATED (S187). Refusing an undeclared kind is only half the
    contract: a sender who cannot ENUMERATE the receiver's kinds has no way to
    comply, and guessing is what HANDOFF-PRESCRIPTION-BAN exists to stop. Rendered
    from ``KINDS`` + ``DESTINATION``, so the published surface and the enforced
    predicate are the same data — they cannot drift apart.
    """
    return [{"kind": k, "destination": DESTINATION[k]} for k in KINDS]


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
