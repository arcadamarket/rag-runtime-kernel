"""Governed, deployment-facing schema/version migration (KA-SCHEMA-MIGRATE).

WHY THIS MODULE EXISTS
----------------------
The kernel is a UNIVERSAL runtime deployed ONTO other projects. A deployment that
was initialized against an older INIT spec carries an older ``meta.schema_version``
and an older ``meta.policy_version``; redeploying the pinned package does NOT move
those fields, so the deploy silently runs new code against an old-shaped RAG. Before
this verb the only "fix" was a hand-edit of another project's canonical state — the
exact forbidden move the kernel exists to prevent.

DESIGN CONTRACT (operator-banked, S158/S159)
--------------------------------------------
1. **Version-range-general.** No caller-visible hardcoding of a specific pair. The
   ladder :data:`SCHEMA_MIGRATIONS` declares each known step; the terminal node's
   ``to_version`` IS the schema the kernel currently speaks. Adding a future step is
   a data change, not a logic change.
2. **Read the target's meta — never assume direction.** A deploy can legitimately be
   AHEAD of this kernel on an independently-versioned field (at S159 the eBay clone
   ran ``policy_version`` 3.2.7 against this kernel's then-current 3.2.6 spec, a gap
   the S160 self-adoption of v3.2.7 has since closed). Every field is
   compared independently and a NEWER target is REFUSED, never silently downgraded.
3. **Fail loud on unknown.** A ``schema_version`` that is neither the current
   terminal nor a node of the ladder raises — nothing is written.
4. **No-op when already current.** Idempotent: no write, ``.bak`` untouched, exit 0.
5. **Preserve-in-place.** Steps are ADDITIVE and idempotent — they may only ensure a
   structural key exists. They never rewrite, reorder, or prune a deployment's
   ``tracked_items``, ``operating_protocol``, or narrative content.
6. **Project-owned fields are untouchable.** ``meta.rag_version`` is the deployment's
   OWN state-version counter, not a kernel-owned token; migration never moves it.

The write path reuses the established transaction contract — load -> plan -> atomic
write (tmp -> fsync -> ``.bak`` parity mirror -> rename) via
:func:`rag_kernel.persistence.atomic_write_json` with ``mirror_bak=True`` and
``guard_side_stores=True``. A plan that raises writes nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from rag_kernel.persistence import atomic_write_json

_TS_FORMAT = "%Y-%m-%dT%H:%M:%S+00:00"


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class SchemaMigrateError(Exception):
    """Base: any fail-loud condition in the migration path. Nothing is written."""


class UnknownSchemaVersionError(SchemaMigrateError):
    """Target declares a ``schema_version`` this kernel has no ladder path for."""


class SchemaAheadError(SchemaMigrateError):
    """Target is NEWER than this kernel — refuse rather than downgrade."""


# --------------------------------------------------------------------------- #
# Version algebra (dotted numeric, fail-loud)
# --------------------------------------------------------------------------- #
def parse_version(value) -> tuple[int, ...]:
    """Parse a dotted numeric version into a comparable tuple.

    Fail loud on anything non-numeric: a version we cannot order is a version we
    must not migrate across (guessing direction is the failure mode this whole
    module exists to prevent).
    """
    if not isinstance(value, str) or not value.strip():
        raise SchemaMigrateError(f"version must be a non-empty string, got {value!r}")
    parts = value.strip().split(".")
    try:
        return tuple(int(p) for p in parts)
    except ValueError as ex:
        raise SchemaMigrateError(f"unparseable version {value!r}: {ex}") from ex


def compare_versions(a: str, b: str) -> int:
    """Return -1 if ``a`` < ``b``, 0 if equal, 1 if ``a`` > ``b`` (zero-padded)."""
    ta, tb = parse_version(a), parse_version(b)
    width = max(len(ta), len(tb))
    ta = ta + (0,) * (width - len(ta))
    tb = tb + (0,) * (width - len(tb))
    return (ta > tb) - (ta < tb)


# --------------------------------------------------------------------------- #
# The ladder
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SchemaMigration:
    """One declared step of the schema ladder.

    ``apply`` receives the live HOT dict and returns a list of human-readable
    change notes. It MUST be additive and idempotent (contract rule 5) — re-running
    a step over already-migrated state produces no further change and no notes.
    """

    from_version: str
    to_version: str
    description: str
    apply: Callable[[dict], list[str]]


def _ensure_5_4(hot: dict) -> list[str]:
    """5.3 -> 5.4: the DRIFT-ELIM shape — canonical array + handoff slot.

    Additive only. Ensures the two structural keys 5.4 renders read from exist so a
    5.3-shaped deploy can be driven by 5.4 code; existing content is never touched.
    """
    notes: list[str] = []
    if not isinstance(hot.get("tracked_items"), list):
        if "tracked_items" in hot:
            raise SchemaMigrateError(
                "tracked_items exists but is not a list — refusing to coerce a "
                "deployment's canonical status array"
            )
        hot["tracked_items"] = []
        notes.append("added canonical tracked_items array (empty)")
    if "next_session_directive" not in hot:
        hot["next_session_directive"] = None
        notes.append("added next_session_directive handoff slot (null)")
    return notes


SCHEMA_MIGRATIONS: tuple[SchemaMigration, ...] = (
    SchemaMigration(
        from_version="5.3",
        to_version="5.4",
        description=(
            "DRIFT-ELIM shape: canonical tracked_items array + next_session_directive "
            "handoff slot (additive; existing content preserved in place)"
        ),
        apply=_ensure_5_4,
    ),
)

#: The schema this kernel currently speaks — derived from the ladder, not hardcoded
#: at any call site. Extending the ladder moves this automatically.
CURRENT_SCHEMA_VERSION: str = SCHEMA_MIGRATIONS[-1].to_version


def current_spec_version() -> Optional[str]:
    """The kernel's declared INIT-spec version (``rag_kernel.__spec_version__``)."""
    try:
        import rag_kernel

        return getattr(rag_kernel, "__spec_version__", None)
    except Exception:  # pragma: no cover - import of own package cannot realistically fail
        return None


def resolve_path(from_version: str) -> list[SchemaMigration]:
    """Ordered ladder steps carrying ``from_version`` up to the current schema.

    Empty list == already current (a no-op, not an error). Raises
    :class:`SchemaAheadError` if the target is newer than this kernel and
    :class:`UnknownSchemaVersionError` if no contiguous path exists.
    """
    cmp = compare_versions(from_version, CURRENT_SCHEMA_VERSION)
    if cmp == 0:
        return []
    if cmp > 0:
        raise SchemaAheadError(
            f"target schema_version {from_version} is AHEAD of this kernel's "
            f"{CURRENT_SCHEMA_VERSION} — refusing to downgrade. Upgrade the kernel "
            f"deployment first, then re-run."
        )

    steps: list[SchemaMigration] = []
    cursor = from_version
    by_from = {m.from_version: m for m in SCHEMA_MIGRATIONS}
    while compare_versions(cursor, CURRENT_SCHEMA_VERSION) < 0:
        step = by_from.get(cursor)
        if step is None:
            raise UnknownSchemaVersionError(
                f"no migration declared from schema_version {cursor!r}; known "
                f"origins: {sorted(by_from)} (target must be one of these or "
                f"{CURRENT_SCHEMA_VERSION})"
            )
        steps.append(step)
        cursor = step.to_version
    return steps


# --------------------------------------------------------------------------- #
# Plan
# --------------------------------------------------------------------------- #
@dataclass
class MigrationPlan:
    """Deterministic, renderable description of what a migration would do."""

    schema_from: str
    schema_to: str
    steps: list[SchemaMigration] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    policy_from: Optional[str] = None
    policy_to: Optional[str] = None
    policy_action: str = "unchanged"  # advanced | ahead-preserved | unchanged | absent

    @property
    def is_noop(self) -> bool:
        return not self.steps and self.policy_action != "advanced"


def plan_migration(hot: dict, *, spec_version: Optional[str] = None) -> MigrationPlan:
    """Compute the migration plan for an already-loaded HOT dict. Never writes.

    ``spec_version`` defaults to the kernel's live ``__spec_version__``. The policy
    field is compared INDEPENDENTLY of the schema ladder: a deployment ahead on
    policy keeps its own value (reported as ``ahead-preserved``) — the migration is
    not a one-way uplift.
    """
    meta = hot.get("meta")
    if not isinstance(meta, dict):
        raise SchemaMigrateError("HOT has no meta object — not a kernel RAG")
    schema_from = meta.get("schema_version")
    if not schema_from:
        raise SchemaMigrateError(
            "meta.schema_version is missing — refusing to guess the target's shape"
        )

    steps = resolve_path(schema_from)
    plan = MigrationPlan(
        schema_from=schema_from, schema_to=CURRENT_SCHEMA_VERSION, steps=steps
    )

    want_policy = spec_version if spec_version is not None else current_spec_version()
    have_policy = meta.get("policy_version")
    if not have_policy:
        plan.policy_action = "absent"
    elif want_policy:
        cmp = compare_versions(have_policy, want_policy)
        plan.policy_from, plan.policy_to = have_policy, want_policy
        if cmp < 0:
            plan.policy_action = "advanced"
        elif cmp > 0:
            plan.policy_action = "ahead-preserved"
        else:
            plan.policy_action = "unchanged"
    return plan


def apply_migration(
    hot: dict, plan: MigrationPlan, *, session: str, now: Optional[str] = None
) -> dict:
    """Apply ``plan`` to ``hot`` in memory and re-stamp the migration audit trail."""
    stamp = now or datetime.now(timezone.utc).strftime(_TS_FORMAT)
    for step in plan.steps:
        plan.notes.extend(step.apply(hot))

    meta = hot["meta"]
    if plan.steps:
        meta["schema_version"] = plan.schema_to
    if plan.policy_action == "advanced":
        meta["policy_version"] = plan.policy_to
    if plan.steps or plan.policy_action == "advanced":
        meta["last_updated_utc"] = stamp
        history = meta.setdefault("migrations", [])
        if not isinstance(history, list):
            raise SchemaMigrateError("meta.migrations exists but is not a list")
        try:
            import rag_kernel

            runtime = getattr(rag_kernel, "__version__", None)
        except Exception:  # pragma: no cover
            runtime = None
        history.append(
            {
                "utc": stamp,
                "session": session,
                "runtime": runtime,
                "schema_from": plan.schema_from,
                "schema_to": meta.get("schema_version"),
                "policy_from": plan.policy_from,
                "policy_to": meta.get("policy_version"),
                "policy_action": plan.policy_action,
                "steps": [f"{s.from_version}->{s.to_version}" for s in plan.steps],
            }
        )
    return hot


def migrate_file(
    path: Path | str,
    *,
    session: str,
    spec_version: Optional[str] = None,
    dry_run: bool = False,
    now: Optional[str] = None,
) -> tuple[MigrationPlan, bool]:
    """Load -> plan -> (optionally) atomically migrate a deployment's RAG file.

    Returns ``(plan, wrote)``. ``wrote`` is False for a dry run and for a no-op, so
    an already-current deployment leaves the file and its ``.bak`` byte-untouched.
    """
    p = Path(path)
    if not p.exists():
        raise SchemaMigrateError(f"RAG file not found: {p}")
    hot = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(hot, dict):
        raise SchemaMigrateError(f"HOT root must be a JSON object, got {type(hot).__name__}")

    plan = plan_migration(hot, spec_version=spec_version)
    if plan.is_noop or dry_run:
        return plan, False

    apply_migration(hot, plan, session=session, now=now)
    atomic_write_json(p, hot, mirror_bak=True, guard_side_stores=True)
    return plan, True
