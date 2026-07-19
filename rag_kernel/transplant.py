"""Governed scaffold transplant (TRANSPLANT-CLASSIFY-AUTHORITY).

WHY THIS MODULE EXISTS
----------------------
The kernel is a UNIVERSAL runtime deployed ONTO other projects. Over time a
deployment's ``operating_protocol`` diverges from this kernel's in two *different*
ways that must never be conflated:

- **Universal scaffold drift** — the deploy is missing governance rules/guards this
  kernel has since authored (it cannot benefit from fixes it never received).
- **Legitimate project divergence** — the deploy authored its own rules for its own
  domain (the eBay clone's reprice/Temu operational rules). These are NOT drift; they
  are the deployment's own value and must survive untouched.

A naive "sync the rules" would destroy the second class. ``migrate`` deliberately
refuses to go near ``operating_protocol`` content (PRESERVE-IN-PLACE, operator ruling
D2, S158). This verb is the governed way to move the FIRST class only.

CLASSIFICATION AUTHORITY — Authority A (spec-derived), operator-ratified S160
----------------------------------------------------------------------------
A rule is universal **iff its key appears in the INIT spec**
(``INIT_UNIVERSAL_RUNTIME_KERNEL_v<spec>.md``) that both sides can name. The spec IS
the definition of "universal" in this project — that is what the word means here.
Deterministic, zero tagging debt, and it ties transplant to spec adoption (closing
``SPEC-PROMOTION-DRIFT`` from the same mechanism). The universal key-set is obtained
by parsing the spec with :class:`rag_kernel.spec_parser.SpecParser` and reading the
keys of the ``operating_protocol`` it produces — the exact same parse ``init`` uses,
so classification and construction agree by DRY.

DESIGN CONTRACT (design/DESIGN_SCAFFOLD_TRANSPLANT.md §3)
--------------------------------------------------------
1. **Additive only.** May ADD a missing universal rule. Never deletes, reorders, or
   rewrites an existing rule in the target — including a universal rule the target has
   locally amended.
2. **Collision is fail-loud, never overwrite.** A universal rule whose key exists in
   the target with DIFFERENT content HALTS the run and reports the pair. Nothing is
   written. Resolution is an operator ruling, not an agent default. (``--dry-run``
   still renders the collision so the operator sees it before deciding.)
3. **Project-specific rules are invisible.** Any target key NOT in the spec's universal
   set is never read, moved, or reported as drift.
4. **Dry-run first, always.** ``--dry-run`` renders every planned addition and every
   collision line by line (STRICT-OBEY rendering discipline — never a bare count).
5. **Atomic + audited.** Reuses the FIX-4 ``tmp -> verify -> .bak parity -> rename``
   path via :func:`rag_kernel.persistence.atomic_write_json` with ``mirror_bak=True``
   and ``guard_side_stores=True``, and appends a ``meta.transplants`` entry: source
   kernel version, spec version, session, rule ids added.
6. **Idempotent.** A second run over the same pair is a no-op with no write.
7. **Direction is never assumed.** Read the TARGET's own meta; a target AHEAD of the
   spec being transplanted from is REFUSED, not downgraded.

Why not ``migrate``: ``migrate`` moves version fields and structural keys — the SHAPE
of the RAG. ``transplant`` moves governance CONTENT — the rules inside it. They stay
separate verbs with separate guards.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from rag_kernel.persistence import atomic_write_json
from rag_kernel.schema_migrate import compare_versions  # dotted-version algebra, fail-loud

_TS_FORMAT = "%Y-%m-%dT%H:%M:%S+00:00"


# --------------------------------------------------------------------------- #
# Errors — every one is a fail-loud condition; nothing is written when raised.
# --------------------------------------------------------------------------- #
class TransplantError(Exception):
    """Base: any fail-loud condition in the transplant path. Nothing is written."""


class SpecUnavailableError(TransplantError):
    """The named INIT spec is missing/unparseable — the classification authority is
    unusable, so no rule can be classified. Refuse rather than guess."""


class TargetAheadError(TransplantError):
    """Target's policy/spec version is AHEAD of the spec being transplanted from —
    refuse rather than move older governance onto a newer deployment."""


class SourceIncompleteError(TransplantError):
    """A spec-universal rule key is absent from the SOURCE — the source does not
    implement its own spec, so it cannot be the authority for that rule's content."""


class TransplantCollisionError(TransplantError):
    """One or more universal rules exist in the target with DIFFERENT content.
    Fail-loud: the run halts and nothing is written. ``.collisions`` carries the
    ``(key, target_value, source_value)`` triples for the operator."""

    def __init__(self, collisions: list[tuple[str, object, object]]):
        self.collisions = collisions
        keys = ", ".join(k for k, _, _ in collisions)
        super().__init__(
            f"universal rule collision on {len(collisions)} key(s): {keys} — target "
            f"has locally-amended content; overwrite is forbidden. Resolve by operator "
            f"ruling."
        )


# --------------------------------------------------------------------------- #
# Classification authority (Authority A — spec-derived)
# --------------------------------------------------------------------------- #
def universal_keys_from_spec(spec_path: Path | str) -> tuple[set[str], str]:
    """Return ``(universal_rule_keys, spec_version)`` for the named INIT spec.

    Authority A: a rule is universal iff its key appears in the ``operating_protocol``
    that :class:`SpecParser` produces from the spec — the same parse ``init`` uses, so
    build and classification cannot diverge. ``_``-prefixed template keys are already
    stripped by the parser (mirrors ``drift_audit.check_template_keys``).
    """
    p = Path(spec_path)
    if not p.exists():
        raise SpecUnavailableError(f"INIT spec not found: {p}")
    try:
        from rag_kernel.spec_parser import SpecParser

        result = SpecParser().parse_file(p)
    except SpecUnavailableError:
        raise
    except Exception as ex:  # spec that will not parse cannot be a classifier
        raise SpecUnavailableError(f"could not parse INIT spec {p}: {ex}") from ex

    op = (result.merged or {}).get("operating_protocol")
    if not isinstance(op, dict) or not op:
        raise SpecUnavailableError(
            f"spec {p} produced no operating_protocol rules — cannot classify"
        )
    keys = {k for k in op if isinstance(k, str) and not k.startswith("_")}
    spec_version = result.spec_version or ""
    return keys, spec_version


# --------------------------------------------------------------------------- #
# Plan — deterministic, renderable, never writes
# --------------------------------------------------------------------------- #
@dataclass
class TransplantPlan:
    """What a transplant would do, computed against loaded target + source dicts."""

    spec_version: str
    source_version: Optional[str] = None
    target_version: Optional[str] = None
    #: (key, value_to_add) — universal rules absent from the target.
    additions: list[tuple[str, object]] = field(default_factory=list)
    #: (key, target_value, source_value) — universal rules present but DIFFERING.
    collisions: list[tuple[str, object, object]] = field(default_factory=list)
    #: universal rules already present AND byte-identical — the idempotent skip set.
    present_identical: list[str] = field(default_factory=list)

    @property
    def is_noop(self) -> bool:
        # A no-op is "nothing to add AND nothing colliding": the target already carries
        # every universal rule identically. Collisions are NOT a no-op — they are a
        # fail-loud halt the caller must surface.
        return not self.additions and not self.collisions


def plan_transplant(
    target: dict,
    source: dict,
    *,
    universal_keys: set[str],
    spec_version: str,
    source_version: Optional[str] = None,
) -> TransplantPlan:
    """Compute a transplant plan. Never writes. Never raises on a collision (they are
    collected so ``--dry-run`` can render them); DOES raise the hard fail-loud
    conditions (target ahead, source incomplete) that make any plan unsafe to render.
    """
    t_meta = target.get("meta")
    s_meta = source.get("meta")
    if not isinstance(t_meta, dict):
        raise TransplantError("target has no meta object — not a kernel RAG")
    if not isinstance(s_meta, dict):
        raise TransplantError("source has no meta object — not a kernel RAG")

    target_version = t_meta.get("policy_version")
    if source_version is None:
        source_version = s_meta.get("policy_version")

    # Direction guard (contract §7): a target ahead of the spec is refused, never
    # downgraded. Compared independently of any schema ladder.
    if target_version and spec_version:
        if compare_versions(target_version, spec_version) > 0:
            raise TargetAheadError(
                f"target policy_version {target_version} is AHEAD of the spec "
                f"v{spec_version} being transplanted from — refusing to move older "
                f"governance onto a newer deployment. Upgrade the source first."
            )

    t_op = target.get("operating_protocol")
    s_op = source.get("operating_protocol")
    if not isinstance(t_op, dict):
        raise TransplantError("target has no operating_protocol object")
    if not isinstance(s_op, dict):
        raise TransplantError("source has no operating_protocol object")

    plan = TransplantPlan(
        spec_version=spec_version,
        source_version=source_version,
        target_version=target_version,
    )

    for key in sorted(universal_keys):
        if key not in s_op:
            # The source is the authority for a universal rule's CONTENT; if it lacks
            # a rule its own spec declares, it cannot transplant it. Fail loud rather
            # than fabricate content or silently skip a real universal rule.
            raise SourceIncompleteError(
                f"spec-universal rule {key!r} is absent from the source kernel — the "
                f"source does not implement its own spec; cannot classify its content"
            )
        if key not in t_op:
            plan.additions.append((key, s_op[key]))
        elif t_op[key] == s_op[key]:
            plan.present_identical.append(key)
        else:
            plan.collisions.append((key, t_op[key], s_op[key]))

    plan.additions.sort(key=lambda kv: kv[0])
    plan.collisions.sort(key=lambda c: c[0])
    plan.present_identical.sort()
    return plan


# --------------------------------------------------------------------------- #
# Apply — mutate in memory + append the audit trail. Only called with no collisions.
# --------------------------------------------------------------------------- #
def apply_transplant(
    target: dict,
    plan: TransplantPlan,
    *,
    session: str,
    spec_file: Optional[str] = None,
    now: Optional[str] = None,
) -> dict:
    """Apply ``plan``'s additions to ``target`` in memory and stamp ``meta.transplants``.

    Additive only: each addition inserts a NEW key into ``operating_protocol``; an
    existing key is never touched (collisions are refused upstream, so none reach here).
    """
    if plan.collisions:  # defensive — the file path refuses before ever calling apply
        raise TransplantCollisionError(plan.collisions)

    stamp = now or datetime.now(timezone.utc).strftime(_TS_FORMAT)
    op = target["operating_protocol"]
    added_keys: list[str] = []
    for key, value in plan.additions:
        if key in op:  # pragma: no cover - plan guarantees absence; belt-and-braces
            continue
        op[key] = value
        added_keys.append(key)

    if not added_keys:
        return target  # no-op: nothing added, no audit entry, no timestamp churn

    meta = target["meta"]
    meta["last_updated_utc"] = stamp
    try:
        import rag_kernel

        runtime = getattr(rag_kernel, "__version__", None)
    except Exception:  # pragma: no cover
        runtime = None

    history = meta.setdefault("transplants", [])
    if not isinstance(history, list):
        raise TransplantError("meta.transplants exists but is not a list")
    history.append(
        {
            "utc": stamp,
            "session": session,
            "runtime": runtime,
            "source_kernel_version": plan.source_version,
            "spec_version": plan.spec_version,
            "spec_file": spec_file,
            "rules_added": sorted(added_keys),
            # Collisions halt before any write, so a recorded transplant always has an
            # empty collision set; the field is kept for shape stability / forward use.
            "collisions_skipped": [],
        }
    )
    return target


# --------------------------------------------------------------------------- #
# File-level orchestration: load -> plan -> (optionally) atomic write
# --------------------------------------------------------------------------- #
def transplant_file(
    target_path: Path | str,
    source_path: Path | str,
    spec_path: Path | str,
    *,
    session: str,
    dry_run: bool = False,
    now: Optional[str] = None,
) -> tuple[TransplantPlan, bool]:
    """Load target + source + spec, plan, and (unless dry-run/no-op/collision) apply.

    Returns ``(plan, wrote)``. ``wrote`` is False for a dry run, a no-op, and — because
    a real run RAISES on collision — is never True with collisions present. The target
    RAG is written atomically with the FIX-4 ``.bak`` parity mirror; the source and spec
    are read-only throughout.
    """
    tp = Path(target_path)
    sp = Path(source_path)
    if not tp.exists():
        raise TransplantError(f"target RAG not found: {tp}")
    if not sp.exists():
        raise TransplantError(f"source RAG not found: {sp}")

    target = json.loads(tp.read_text(encoding="utf-8"))
    source = json.loads(sp.read_text(encoding="utf-8"))
    if not isinstance(target, dict):
        raise TransplantError("target RAG root must be a JSON object")
    if not isinstance(source, dict):
        raise TransplantError("source RAG root must be a JSON object")

    universal_keys, spec_version = universal_keys_from_spec(spec_path)

    plan = plan_transplant(
        target, source, universal_keys=universal_keys, spec_version=spec_version
    )

    # Dry-run returns the full plan (additions AND collisions) for line-by-line render
    # before any decision — contract §4. Nothing is written.
    if dry_run:
        return plan, False

    # Real run: a collision is a fail-loud halt (contract §2) — nothing written.
    if plan.collisions:
        raise TransplantCollisionError(plan.collisions)

    if plan.is_noop:  # idempotent: target already carries every universal rule
        return plan, False

    apply_transplant(
        target, plan, session=session, spec_file=Path(spec_path).name, now=now
    )
    atomic_write_json(tp, target, mirror_bak=True, guard_side_stores=True)
    return plan, True
