"""Birth/adopt: carry hardened universal rule VALUES between kernel deployments.

WHY THIS EXISTS (S181, item BIRTH-ADOPT-VERB)
---------------------------------------------
``transplant`` is additive-only: it adds universal rules a target LACKS and
halts fail-loud on any key present with differing content. That is correct for
a scaffold, and structurally unable to do the one thing a fleet needs -- move an
IMPROVED value of an EXISTING rule onto a deployment that already has an older
one. The eBay clone sits frozen at S168 on 23 of 29 spec-universal rules for
exactly this reason: every one of them is a collision, and a collision is a halt.

``migrate`` does not touch ``operating_protocol`` at all. So between the two
verbs there is no path from "this kernel hardened a rule" to "the fleet has it".

THE DIRECTION PROBLEM, AND WHY THE SPEC SOLVES MOST OF IT
---------------------------------------------------------
Moving a value requires knowing WHICH SIDE IS AHEAD. Per-rule provenance did not
exist anywhere in the RAG before this module: ``update-rule`` stamped a single
global ``meta.last_updated_utc``, so two kernels could differ on 23 keys with no
record of who changed what, when. This module introduces
``meta.rule_provenance`` and stamps it on every governed rule write.

Provenance alone would leave every pre-S181 rule undecidable. The escape is that
the INIT SPEC IS A THIRD REFERENCE POINT. A deployment whose value for a key is
byte-identical to the spec's declared value has never hardened that key -- it is
carrying boilerplate. So:

    target == spec  and  source != spec   ->  SOURCE_TO_TARGET   (decidable now)
    source == spec  and  target != spec   ->  TARGET_TO_SOURCE   (decidable now)
    both differ from spec, provenance known -> newer session wins
    both differ from spec, provenance absent -> DIVERGED, operator decides

This makes the eBay 23 adoptable TODAY, without waiting for provenance to
accumulate, and it degrades honestly: what cannot be decided is reported as
undecidable rather than guessed.

CONTRACT
--------
* ``diff`` never writes. It is the mandatory first mode: nothing is written
  before both directions have been rendered with their reason.
* ``adopt`` is the BIRTH path -- it targets a newborn deployment and applies
  ADD_TO_TARGET + SOURCE_TO_TARGET in one governed pass, replacing the hand-run
  ``update-rule`` loop of RUNBOOK section 4.2.
* ``update`` is the RUNNING-deployment path -- it propagates an improved value of
  an EXISTING rule. It is optimistic-concurrency guarded: the target's current
  value must still hash to what its provenance recorded, or the update refuses
  rather than clobbering a value the target hardened since we last looked.
* Every mode refuses on DIVERGED unless the caller passes an explicit, per-key
  operator decision. Silence is never taken as consent.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Iterable, Optional

from rag_kernel.transplant import (
    SpecUnavailableError,
    TransplantError,
    universal_keys_from_spec,
)

#: ``meta`` sub-key holding per-rule provenance records.
PROVENANCE_KEY = "rule_provenance"
#: ``meta`` sub-key holding the pointer to the INIT spec a RAG was parsed from
#: (SPEC-DISCOVERABILITY-AT-INIT: a RAG that cannot name its spec makes the
#: completeness invariants self-skip on exactly the newborn deployments that
#: most need them).
SPEC_POINTER_KEY = "init_spec"


class AdoptError(TransplantError):
    """Any refusal raised by this module. Subclasses TransplantError so callers
    that already handle the transplant family keep working."""


class UndecidableDirectionError(AdoptError):
    """One or more keys diverge with no evidence for which side is ahead."""

    def __init__(self, keys: list[str]):
        self.keys = list(keys)
        super().__init__(
            f"{len(self.keys)} rule(s) diverge with no decidable direction "
            f"(neither side matches the spec, and provenance is absent or tied): "
            + ", ".join(sorted(self.keys)[:12])
            + (" ..." if len(self.keys) > 12 else "")
            + " -- render `diff` and pass an explicit per-key decision."
        )


class StaleProvenanceError(AdoptError):
    """The target's live value no longer matches its recorded provenance hash."""


class Direction(str, Enum):
    """What, if anything, should move -- and in which direction."""

    IDENTICAL = "IDENTICAL"
    ADD_TO_TARGET = "ADD_TO_TARGET"
    ADD_TO_SOURCE = "ADD_TO_SOURCE"
    SOURCE_TO_TARGET = "SOURCE_TO_TARGET"
    TARGET_TO_SOURCE = "TARGET_TO_SOURCE"
    DIVERGED = "DIVERGED"


#: Directions ``adopt`` is allowed to apply without an explicit operator decision.
ADOPTABLE = frozenset({Direction.ADD_TO_TARGET, Direction.SOURCE_TO_TARGET})


def value_sha(value: object) -> str:
    """Stable content hash of a rule value (str or JSON-able dict/list)."""
    if isinstance(value, str):
        blob = value.encode("utf-8")
    else:
        blob = json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Provenance — the state this verb needed and did not have
# --------------------------------------------------------------------------- #
def read_provenance(rag: dict) -> dict:
    meta = rag.get("meta")
    if not isinstance(meta, dict):
        raise AdoptError("RAG has no meta object — not a kernel RAG")
    prov = meta.get(PROVENANCE_KEY)
    return prov if isinstance(prov, dict) else {}


def stamp_provenance(
    rag: dict,
    key: str,
    *,
    session: str,
    origin: str,
    value: object,
) -> dict:
    """Record who last set ``key``, to what content, and why. Mutates ``rag``.

    ``origin`` is one of: ``spec`` (verbatim INIT-spec text -- i.e. UNHARDENED),
    ``hardened`` (authored in this deployment), ``adopted`` (carried in from a
    source kernel), ``unknown`` (backfilled without evidence).
    """
    meta = rag.setdefault("meta", {})
    prov = meta.setdefault(PROVENANCE_KEY, {})
    record = {
        "session": session,
        "utc": _now(),
        "sha256": value_sha(value),
        "origin": origin,
    }
    prov[key] = record
    return record


def backfill_provenance(
    rag: dict,
    spec_op: dict,
    *,
    session: str,
) -> tuple[int, int]:
    """Give every un-stamped operating_protocol key a provenance record.

    A key whose value is byte-identical to the spec's is stamped ``spec`` --
    that is a POSITIVE finding, not a gap: it means the key was never hardened.
    Anything else is stamped ``unknown``, honestly, because no record of who
    changed it exists to recover.

    Returns ``(stamped_spec, stamped_unknown)``.
    """
    op = rag.get("operating_protocol")
    if not isinstance(op, dict):
        raise AdoptError("RAG has no operating_protocol object")
    prov = rag.setdefault("meta", {}).setdefault(PROVENANCE_KEY, {})
    as_spec = unknown = 0
    for key, value in op.items():
        if key in prov:
            continue
        if key in spec_op and spec_op[key] == value:
            stamp_provenance(rag, key, session=session, origin="spec", value=value)
            as_spec += 1
        else:
            stamp_provenance(rag, key, session=session, origin="unknown", value=value)
            unknown += 1
    return as_spec, unknown


def record_spec_pointer(
    rag: dict,
    spec_path: Path | str,
    spec_version: str,
    *,
    session: str,
) -> dict:
    """Record WHICH INIT spec this RAG was parsed from (SPEC-DISCOVERABILITY).

    Stores the basename (portable across deployments — the spec travels beside
    the RAG), the version, and the file's sha256 so a later check can tell
    "the spec moved" apart from "the spec is missing".
    """
    p = Path(spec_path)
    if not p.exists():
        raise SpecUnavailableError(f"INIT spec not found: {p}")
    pointer = {
        "file": p.name,
        "version": spec_version,
        "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        "recorded_by": session,
        "recorded_utc": _now(),
    }
    rag.setdefault("meta", {})[SPEC_POINTER_KEY] = pointer
    return pointer


# --------------------------------------------------------------------------- #
# Diff — per-key, both directions, with provenance. Never writes.
# --------------------------------------------------------------------------- #
@dataclass
class KeyDiff:
    key: str
    direction: Direction
    reason: str
    source_value: object = None
    target_value: object = None
    source_prov: Optional[dict] = None
    target_prov: Optional[dict] = None

    @property
    def is_move(self) -> bool:
        return self.direction not in (Direction.IDENTICAL,)


@dataclass
class AdoptDiff:
    spec_version: str
    entries: list[KeyDiff] = field(default_factory=list)

    def by(self, *directions: Direction) -> list[KeyDiff]:
        wanted = set(directions)
        return [e for e in self.entries if e.direction in wanted]

    @property
    def counts(self) -> dict[str, int]:
        out = {d.value: 0 for d in Direction}
        for e in self.entries:
            out[e.direction.value] += 1
        return out

    @property
    def undecidable(self) -> list[str]:
        return [e.key for e in self.by(Direction.DIVERGED)]

    @property
    def is_noop(self) -> bool:
        return not [e for e in self.entries if e.is_move]


def _session_ordinal(prov: Optional[dict]) -> Optional[int]:
    """Numeric part of a session id (``S181`` -> 181) for newer-wins ordering."""
    if not isinstance(prov, dict):
        return None
    sid = prov.get("session")
    if not isinstance(sid, str):
        return None
    digits = "".join(ch for ch in sid if ch.isdigit())
    return int(digits) if digits else None


def _decide(
    key: str,
    s_val: object,
    t_val: object,
    s_prov: Optional[dict],
    t_prov: Optional[dict],
    spec_val: object,
    have_spec: bool,
) -> tuple[Direction, str]:
    if have_spec:
        t_is_spec = t_val == spec_val
        s_is_spec = s_val == spec_val
        if t_is_spec and not s_is_spec:
            return (
                Direction.SOURCE_TO_TARGET,
                "target holds verbatim spec text (never hardened); source has diverged",
            )
        if s_is_spec and not t_is_spec:
            return (
                Direction.TARGET_TO_SOURCE,
                "source holds verbatim spec text (never hardened); target has diverged",
            )
        if s_is_spec and t_is_spec:
            # Cannot happen (values differ), guarded for total-function safety.
            return Direction.IDENTICAL, "both match the spec"

    s_ord, t_ord = _session_ordinal(s_prov), _session_ordinal(t_prov)
    if s_ord is not None and t_ord is not None and s_ord != t_ord:
        if s_ord > t_ord:
            return (
                Direction.SOURCE_TO_TARGET,
                f"provenance: source last set at S{s_ord}, target at S{t_ord}",
            )
        return (
            Direction.TARGET_TO_SOURCE,
            f"provenance: target last set at S{t_ord}, source at S{s_ord}",
        )
    if s_ord is not None and t_ord is None:
        return (
            Direction.SOURCE_TO_TARGET,
            f"provenance: source last set at S{s_ord}; target has none recorded",
        )
    if t_ord is not None and s_ord is None:
        return (
            Direction.TARGET_TO_SOURCE,
            f"provenance: target last set at S{t_ord}; source has none recorded",
        )
    return (
        Direction.DIVERGED,
        "values differ; neither matches the spec and provenance is absent or tied",
    )


def diff_rules(
    source: dict,
    target: dict,
    *,
    universal_keys: Iterable[str],
    spec_version: str = "",
    spec_op: Optional[dict] = None,
) -> AdoptDiff:
    """Classify every universal key in BOTH directions. Never writes, never raises
    on divergence -- undecidables are reported so ``diff`` can render them."""
    s_op = source.get("operating_protocol")
    t_op = target.get("operating_protocol")
    if not isinstance(s_op, dict):
        raise AdoptError("source has no operating_protocol object")
    if not isinstance(t_op, dict):
        raise AdoptError("target has no operating_protocol object")

    s_prov_all = read_provenance(source)
    t_prov_all = read_provenance(target)
    spec_op = spec_op or {}
    out = AdoptDiff(spec_version=spec_version)

    for key in sorted(set(universal_keys)):
        s_has, t_has = key in s_op, key in t_op
        s_val = s_op.get(key)
        t_val = t_op.get(key)
        s_prov = s_prov_all.get(key)
        t_prov = t_prov_all.get(key)

        if s_has and not t_has:
            direction, reason = Direction.ADD_TO_TARGET, "absent from target"
        elif t_has and not s_has:
            direction, reason = (
                Direction.ADD_TO_SOURCE,
                "absent from source — the target hardened a universal rule the "
                "source does not implement (back-flow candidate)",
            )
        elif not s_has and not t_has:
            continue
        elif s_val == t_val:
            direction, reason = Direction.IDENTICAL, "byte-identical"
            if spec_op and key in spec_op and s_val == spec_op[key]:
                reason = (
                    "byte-identical — but BOTH hold verbatim spec text, so this "
                    "is agreement by absence, not by hardening"
                )
        else:
            direction, reason = _decide(
                key, s_val, t_val, s_prov, t_prov,
                spec_op.get(key), key in spec_op,
            )

        out.entries.append(
            KeyDiff(
                key=key,
                direction=direction,
                reason=reason,
                source_value=s_val,
                target_value=t_val,
                source_prov=s_prov,
                target_prov=t_prov,
            )
        )
    return out


def diff_from_spec(
    source: dict,
    target: dict,
    spec_path: Path | str,
) -> AdoptDiff:
    """Convenience: derive the universal key set + spec values from an INIT spec,
    using the same parse authority ``transplant`` uses (Authority A)."""
    keys, spec_version = universal_keys_from_spec(spec_path)
    from rag_kernel.spec_parser import SpecParser

    spec_op = (SpecParser().parse_file(Path(spec_path)).merged or {}).get(
        "operating_protocol"
    ) or {}
    return diff_rules(
        source,
        target,
        universal_keys=keys,
        spec_version=spec_version,
        spec_op=spec_op,
    )


# --------------------------------------------------------------------------- #
# Apply — adopt (birth) and update (running deployment)
# --------------------------------------------------------------------------- #
@dataclass
class ApplyResult:
    applied: list[tuple[str, Direction]] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    refused: list[tuple[str, str]] = field(default_factory=list)

    @property
    def wrote_anything(self) -> bool:
        return bool(self.applied)


def apply_adopt(
    target: dict,
    diff: AdoptDiff,
    *,
    session: str,
    decisions: Optional[dict[str, str]] = None,
) -> ApplyResult:
    """BIRTH path. Apply every ADD_TO_TARGET and SOURCE_TO_TARGET in one pass.

    ``decisions`` maps a DIVERGED key to ``"source"`` or ``"target"`` — an
    explicit operator ruling. Any DIVERGED key without one is a hard refusal:
    this verb never resolves a tie by picking a side.
    """
    decisions = decisions or {}
    undecided = [k for k in diff.undecidable if k not in decisions]
    if undecided:
        raise UndecidableDirectionError(undecided)

    op = target.setdefault("operating_protocol", {})
    result = ApplyResult()

    for entry in diff.entries:
        direction = entry.direction
        if direction is Direction.DIVERGED:
            choice = decisions.get(entry.key)
            if choice == "target":
                result.skipped.append((entry.key, "operator ruled: keep target"))
                continue
            direction = Direction.SOURCE_TO_TARGET

        if direction in ADOPTABLE:
            op[entry.key] = entry.source_value
            stamp_provenance(
                target,
                entry.key,
                session=session,
                origin="adopted",
                value=entry.source_value,
            )
            result.applied.append((entry.key, direction))
        elif direction is Direction.IDENTICAL:
            result.skipped.append((entry.key, "already identical"))
        elif direction in (Direction.ADD_TO_SOURCE, Direction.TARGET_TO_SOURCE):
            result.skipped.append(
                (entry.key, "target is ahead — back-flow, not an adoption")
            )
    return result


def apply_update(
    target: dict,
    diff: AdoptDiff,
    *,
    session: str,
    keys: Optional[Iterable[str]] = None,
    force: bool = False,
) -> ApplyResult:
    """RUNNING-deployment path: propagate improved values of EXISTING rules.

    This is the hole that leaves a clone frozen: ``transplant`` halts on exactly
    these keys. Unlike ``adopt`` it is NARROW by default (name the keys), and it
    is optimistic-concurrency guarded -- if the target's live value no longer
    hashes to what its own provenance recorded, someone changed it since we last
    looked and the update REFUSES rather than clobbering their work.
    """
    op = target.setdefault("operating_protocol", {})
    prov_all = read_provenance(target)
    wanted = set(keys) if keys is not None else None
    result = ApplyResult()

    for entry in diff.by(Direction.SOURCE_TO_TARGET):
        if wanted is not None and entry.key not in wanted:
            continue
        recorded = prov_all.get(entry.key)
        if recorded and not force:
            live_sha = value_sha(op.get(entry.key))
            if recorded.get("sha256") and live_sha != recorded["sha256"]:
                raise StaleProvenanceError(
                    f"rule {entry.key!r}: the target's live value does not match "
                    f"its recorded provenance hash — it was changed outside the "
                    f"governed path, or by a session this diff has not seen. "
                    f"Re-run `diff` and confirm before updating (or pass --force "
                    f"if the overwrite is intended)."
                )
        op[entry.key] = entry.source_value
        stamp_provenance(
            target,
            entry.key,
            session=session,
            origin="adopted",
            value=entry.source_value,
        )
        result.applied.append((entry.key, Direction.SOURCE_TO_TARGET))

    if wanted is not None:
        movable = {e.key for e in diff.by(Direction.SOURCE_TO_TARGET)}
        for key in sorted(wanted - movable):
            result.refused.append(
                (key, "not a SOURCE_TO_TARGET move in this diff — nothing to propagate")
            )
    return result


# --------------------------------------------------------------------------- #
# Exit predicate — GATE-OR-HOPE: an adoption that cannot be checked is a hope
# --------------------------------------------------------------------------- #
def adoption_complete(diff: AdoptDiff) -> tuple[bool, str]:
    """Decidable exit condition for ``adopt``: re-diff after applying and this
    must hold. Zero adoptable moves left and zero undecidables."""
    left = diff.by(*ADOPTABLE)
    undecided = diff.undecidable
    if left or undecided:
        return False, (
            f"adoption INCOMPLETE: {len(left)} adoptable move(s) remain, "
            f"{len(undecided)} undecidable"
        )
    return True, "adoption COMPLETE: no adoptable move remains, none undecidable"


# --------------------------------------------------------------------------- #
# File-level orchestration: load -> backfill provenance -> diff -> apply -> write
# --------------------------------------------------------------------------- #
def adopt_file(
    target_path: Path | str,
    source_path: Path | str,
    spec_path: Path | str,
    *,
    session: str,
    mode: str = "diff",
    keys: Optional[Iterable[str]] = None,
    decisions: Optional[dict[str, str]] = None,
    dry_run: bool = False,
    force: bool = False,
) -> tuple[AdoptDiff, Optional[ApplyResult], bool]:
    """Load target + source + spec, classify, and (unless diff/dry-run) apply.

    Returns ``(diff, result, wrote)``. The SOURCE and SPEC are read-only
    throughout; only the target is ever written, atomically with .bak parity.

    Provenance is backfilled on the target before classification, so the very
    first run of this verb against any deployment leaves it able to answer
    "who last set this rule" — the state the fleet needed and did not have.
    """
    from rag_kernel.persistence import atomic_write_json

    tp, sp = Path(target_path), Path(source_path)
    if not tp.exists():
        raise AdoptError(f"target RAG not found: {tp}")
    if not sp.exists():
        raise AdoptError(f"source RAG not found: {sp}")

    target = json.loads(tp.read_text(encoding="utf-8"))
    source = json.loads(sp.read_text(encoding="utf-8"))
    if not isinstance(target, dict) or not isinstance(source, dict):
        raise AdoptError("both RAG roots must be JSON objects")

    keys_set, spec_version = universal_keys_from_spec(spec_path)
    from rag_kernel.spec_parser import SpecParser

    spec_op = (SpecParser().parse_file(Path(spec_path)).merged or {}).get(
        "operating_protocol"
    ) or {}

    # Backfill provenance + record the spec pointer on the TARGET only.
    before = dict(read_provenance(target))
    backfill_provenance(target, spec_op, session=session)
    record_spec_pointer(target, spec_path, spec_version, session=session)
    provenance_changed = read_provenance(target) != before

    diff = diff_rules(
        source, target,
        universal_keys=keys_set,
        spec_version=spec_version,
        spec_op=spec_op,
    )

    if mode == "diff":
        return diff, None, False

    if mode == "adopt":
        result = apply_adopt(target, diff, session=session, decisions=decisions)
    elif mode == "update":
        result = apply_update(
            target, diff, session=session, keys=keys, force=force
        )
    else:
        raise AdoptError(f"unknown mode {mode!r} — expected diff, adopt or update")

    if dry_run:
        return diff, result, False
    if not result.wrote_anything and not provenance_changed:
        return diff, result, False

    atomic_write_json(tp, target, mirror_bak=True, guard_side_stores=True)
    return diff, result, True


def render_diff(diff: AdoptDiff, *, limit: int = 0, show_reason: bool = True) -> str:
    """Bounded, deterministic text render (Rule 17: emissions are capped)."""
    lines: list[str] = []
    counts = diff.counts
    lines.append(f"birth-adopt diff — spec v{diff.spec_version or '?'}")
    for name in (
        Direction.IDENTICAL, Direction.ADD_TO_TARGET, Direction.SOURCE_TO_TARGET,
        Direction.TARGET_TO_SOURCE, Direction.ADD_TO_SOURCE, Direction.DIVERGED,
    ):
        lines.append(f"  {name.value:<18} {counts[name.value]}")
    moves = [e for e in diff.entries if e.is_move]
    shown = moves if limit <= 0 else moves[:limit]
    if shown:
        lines.append("")
        for e in shown:
            lines.append(f"  [{e.direction.value}] {e.key}")
            if show_reason:
                lines.append(f"      {e.reason}")
        if len(moves) > len(shown):
            lines.append(f"  … {len(moves) - len(shown)} more (raise --limit)")
    return "\n".join(lines)
