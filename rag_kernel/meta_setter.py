"""@rag-kernel-manifest
{
  "module": "rag_kernel.meta_setter",
  "capability": "governed_meta_scalar_setter",
  "description": "REFUSE-BY-DEFAULT setter for declared meta.* scalars: an undeclared key is refused with the allowlist, a container key is refused by name with the verb that owns it, values are typed-coerced fail-loud, a no-op writes nothing, and every real write is atomic with .bak parity (META-SETTER-GAP).",
  "exports": ["MetaSetterError", "SETTABLE", "CONTAINER_KEYS", "coerce",
              "get_meta_scalar", "set_meta_scalar_file"]
}

Governed setter for ``meta.*`` scalars — the repair for META-SETTER-GAP (S186/S188).

Before this module every ``meta`` scalar was either written as a side effect of some
larger ritual (``checkpoint`` bumping ``last_checkpoint_seq``) or not writable at all
except by hand-editing the canonical file. Hand-editing is exactly what the drift
auditor exists to catch, so the only sanctioned repair for a wrong ``meta`` scalar was
a ritual that also changed six other things. That is the gap.

Design, in the house style:

* **REFUSE-BY-DEFAULT.** A key is settable iff it appears in :data:`SETTABLE`. Absence
  of a declaration is not permission. An undeclared key is refused with the list of
  what *is* declared, so the refusal teaches.
* **Containers are refused by name.** ``rag_files``, ``deployments``, ``migrations``
  and ``reconciliation_surfaces`` are dicts/lists with their own governed verbs; a
  scalar setter aimed at one of them is a category error and says so.
* **Typed coercion, fail-loud.** ``last_checkpoint_seq`` is an ``int``; handing it
  ``"two hundred"`` raises rather than silently storing prose (KA-CS-PROSE-DRIFT is
  the whole reason this project distrusts free text in machine fields).
* **No-op is not a write.** Setting a key to the value it already holds writes
  nothing, so HOT == ``.bak`` parity is preserved and the seal sees no phantom churn.
* **Atomic.** Every real write goes through ``atomic_write_json`` with ``mirror_bak``
  and ``guard_side_stores``, identically to every other governed mutation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from rag_kernel.drift_store import DriftStoreError, load_hot, _touch_meta
from rag_kernel.persistence import atomic_write_json

__all__ = [
    "MetaSetterError",
    "SETTABLE",
    "CONTAINER_KEYS",
    "coerce",
    "get_meta_scalar",
    "set_meta_scalar_file",
]


class MetaSetterError(DriftStoreError):
    """Raised on an undeclared key, a container key, or a value that will not coerce."""


#: The declared settable ``meta`` scalars, mapped to their required Python type.
#: Adding a key here is the governed way to widen this verb's authority.
SETTABLE: dict[str, type] = {
    "written_by_session": str,
    "last_checkpoint_seq": int,
    "last_ingest_seq": int,
    "rag_version": str,
    "policy_version": str,
    "project_name": str,
    "state_hash": str,
    "inventory_hash": str,
    "reconciliation_docs_root": str,
    "root_project": str,
    "root_deliverables": str,
    "root_rag": str,
}

#: Container ``meta`` keys this verb refuses on purpose, with the verb that owns each.
CONTAINER_KEYS: dict[str, str] = {
    "rag_files": "edited by init/configure, not by a scalar setter",
    "deployments": "use `rag_kernel deployment --set`",
    "migrations": "appended by `rag_kernel migrate`",
    "reconciliation_surfaces": "a list — edited by init/configure",
    "test_gate": "written by `rag_kernel tests --run` (measured, never hand-set)",
    "last_updated_utc": "stamped automatically by every governed write",
}


def coerce(key: str, value: Any) -> Any:
    """Coerce ``value`` to the declared type for ``key``. Fail loud, never guess.

    Pure. ``bool`` is rejected for ``int`` fields on purpose: ``True`` coercing to
    ``1`` is the kind of silent success that produces a wrong sequence number.
    """
    want = SETTABLE.get(key)
    if want is None:
        raise MetaSetterError(f"meta.{key} is not a declared settable scalar")
    if want is int:
        if isinstance(value, bool):
            raise MetaSetterError(
                f"meta.{key} expects an int; refusing a bool ({value!r})"
            )
        if isinstance(value, int):
            return value
        try:
            return int(str(value).strip())
        except (TypeError, ValueError) as exc:
            raise MetaSetterError(
                f"meta.{key} expects an int, got {value!r}"
            ) from exc
    if not isinstance(value, str):
        value = str(value)
    return value


def get_meta_scalar(hot: dict, key: str) -> Any:
    """Read one ``meta`` scalar out of a loaded HOT dict. Pure; missing -> ``None``."""
    meta = hot.get("meta")
    if not isinstance(meta, dict):
        return None
    return meta.get(key)


def set_meta_scalar_file(
    path: Path | str,
    key: str,
    value: Any,
    *,
    session: str,
    now: Optional[str] = None,
    dry_run: bool = False,
    touch_meta: bool = True,
) -> "tuple[Any, Any, bool]":
    """Atomically set one declared ``meta`` scalar. Returns ``(old, new, wrote)``.

    Refuses an undeclared key, a container key, and a value that will not coerce to
    the declared type. Writes nothing when the value is already correct, on
    ``dry_run``, or on any refusal — so a refused call leaves HOT == ``.bak``.
    """
    if not session:
        raise MetaSetterError("--session is required: a meta write must be attributable")
    if key in CONTAINER_KEYS:
        raise MetaSetterError(
            f"meta.{key} is a container, not a scalar — {CONTAINER_KEYS[key]}"
        )
    if key not in SETTABLE:
        raise MetaSetterError(
            f"meta.{key} is not a declared settable scalar — declared keys: "
            + ", ".join(sorted(SETTABLE))
        )

    p = Path(path)
    hot = load_hot(p)
    meta = hot.get("meta")
    if not isinstance(meta, dict):
        raise MetaSetterError("HOT has no meta object — refusing to create one here")

    new = coerce(key, value)
    old = meta.get(key)
    if old == new:
        return old, new, False
    if dry_run:
        return old, new, False

    meta[key] = new
    if touch_meta:
        _touch_meta(hot, now)
    atomic_write_json(p, hot, mirror_bak=True, guard_side_stores=True)
    return old, new, True
