"""Baked-asset registry + reuse-before-rewrite guard (REUSE-REGISTRY-GUARD).

WHY THIS MODULE EXISTS
----------------------
Field evidence, triaged UNIVERSAL (Rule 15 lane-A): agents re-author artifacts
the project has ALREADY baked — re-writing a script that already exists on disk
(eBay S12), or, at the design level, re-deciding a settled architecture choice
(S165 / E-069, where the agent re-proposed a storage location the lean-RAG concept
had already fixed). The kernel had three gaps this module closes:

1. **No asset inventory.** There was no record of WHAT has been baked, WHERE it
   lives, and its content hash — so "does this already exist?" was unanswerable.
2. **No pre-write reuse guard.** Nothing let an agent ask, before authoring, "is
   this purpose/path already covered?" and get a fail-loud answer.
3. **No auditor.** Nothing caught a registered asset that had vanished, diverged
   from its recorded hash, or been re-created under a second id.

    LLM proposes an asset. System checks the registry. Reuse persists.

LEAN-RAG STORAGE (operator ruling S165; the fix E-069 turns into a guardrail)
----------------------------------------------------------------------------
The inventory is BULK, on-demand reference state — it must NOT bloat the HOT,
always-loaded ``RAG_MASTER.json``. Per the lean-RAG concept it lives as a
sanctioned, NON-LOADED partition (``baked_assets``) in ``RAG_CONTEXT.json``, with
its own protocol prose encapsulated alongside the records; ``RAG_MASTER.json``
carries only a concise ``operating_protocol.reuse_registry_guard`` pointer rule.
Writes go through :class:`rag_kernel.cold_manager.ProjectContextManager`
(COLD-style: atomic, NO ``.bak`` mirror — the FIX-11 contract), so an unread
registry costs zero boot tokens.

DESIGN CONTRACT
---------------
1. **Register is additive + idempotent.** Registering a NEW id appends a record.
   Re-registering the SAME id with byte-identical (path, purpose, sha256) is a
   no-op. Re-registering the same id with DIFFERENT content is fail-loud
   (:class:`DuplicateAssetError`) — an id is a stable handle, never silently
   rebound. The same PATH under a different id is fail-loud
   (:class:`AssetPathCollisionError`) — that IS the re-authoring this guard exists
   to surface.
2. **Reuse-check never writes.** It answers "what already covers this
   path/purpose?" and returns the matches; the CLI turns a non-empty result into
   a fail-loud non-zero exit so an agent reuses instead of rewrites.
3. **Hashing is content-addressed.** ``sha256`` over the file bytes; the registry
   is the source of truth for the expected hash, disk is checked against it.
4. **Paths are stored portably.** A file under ``project_root`` is stored as its
   POSIX path relative to the root; anything outside is stored absolute. Resolution
   reverses this so the same registry is valid regardless of the mount prefix.
5. **The file being registered must exist.** You cannot bake the hash of a file
   that is not there (:class:`AssetFileNotFoundError`).

The auditor lives in :mod:`rag_kernel.drift_audit` (``check_asset_registry``),
which reuses :func:`load_registry` + :func:`compute_sha256` here so the audit and
the store agree by construction; this module never imports drift_audit (no cycle).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_TS_FORMAT = "%Y-%m-%dT%H:%M:%S+00:00"

#: The sanctioned, NON-LOADED RAG_CONTEXT.json partition that holds the inventory.
PARTITION_NAME = "baked_assets"

#: Encapsulated protocol prose stored INSIDE the partition (lean-RAG: rules travel
#: with the bulk they govern). Seeded on first write; never overwritten thereafter.
DEFAULT_PROTOCOL = (
    "REUSE-BEFORE-REWRITE. Before authoring a script/asset, run "
    "`rag_kernel reuse-check --purpose \"<what it does>\"` (and/or --path); if a "
    "record already covers it, REUSE that asset instead of re-creating it. Register "
    "every baked asset with `rag_kernel register-asset <path> --purpose \"...\"`. An "
    "asset_id is a stable handle (never silently rebound); the same path under a "
    "second id is a fail-loud collision. `rag_kernel audit` fails loud when a "
    "registered asset is missing or its on-disk sha256 has diverged. This partition "
    "is lean-RAG bulk: it lives here in RAG_CONTEXT.json (non-loaded), and "
    "RAG_MASTER.json carries only the concise reuse_registry_guard pointer rule."
)


# --------------------------------------------------------------------------- #
# Errors — each is a fail-loud condition; nothing is written when raised.
# --------------------------------------------------------------------------- #
class AssetRegistryError(Exception):
    """Base: any fail-loud condition in the asset-registry path."""


class AssetFileNotFoundError(AssetRegistryError):
    """The file to register does not exist — cannot content-address a missing file."""


class DuplicateAssetError(AssetRegistryError):
    """The asset_id already exists with DIFFERENT content. An id is a stable handle;
    rebinding it is forbidden — register a new id or reuse the existing asset."""


class AssetPathCollisionError(AssetRegistryError):
    """The same path is already registered under a DIFFERENT id — the re-authoring /
    duplication this guard exists to surface. Fail-loud, never silently deduped."""


# --------------------------------------------------------------------------- #
# Record model
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class AssetRecord:
    """One baked asset: a content-addressed inventory row."""

    asset_id: str
    path: str          # POSIX, relative to project_root when under it, else absolute
    purpose: str
    sha256: str
    session: str
    registered_utc: str

    def to_dict(self) -> dict:
        return {
            "asset_id": self.asset_id,
            "path": self.path,
            "purpose": self.purpose,
            "sha256": self.sha256,
            "session": self.session,
            "registered_utc": self.registered_utc,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AssetRecord":
        return cls(
            asset_id=str(d.get("asset_id", "")),
            path=str(d.get("path", "")),
            purpose=str(d.get("purpose", "")),
            sha256=str(d.get("sha256", "")),
            session=str(d.get("session", "")),
            registered_utc=str(d.get("registered_utc", "")),
        )

    def identity_matches(self, other: "AssetRecord") -> bool:
        """True iff the reuse-relevant fields (path, purpose, sha256) are identical —
        the idempotency predicate (session/timestamp are audit metadata, not identity)."""
        return (
            self.path == other.path
            and self.purpose == other.purpose
            and self.sha256 == other.sha256
        )


# --------------------------------------------------------------------------- #
# Path helpers — portable storage / resolution
# --------------------------------------------------------------------------- #
def normalize_path(path: Path | str, project_root: Optional[Path | str]) -> str:
    """Store a file under ``project_root`` as its POSIX relative path; else absolute.

    Deterministic and mount-prefix independent: two deployments of the same tree at
    different absolute roots produce the same stored path for the same file.
    """
    p = Path(path)
    if project_root is not None:
        root = Path(project_root)
        try:
            rp = (root / p) if not p.is_absolute() else p
            return rp.resolve().relative_to(root.resolve()).as_posix()
        except (ValueError, OSError):
            pass  # outside root (or unresolvable) — fall through to absolute
    return (p if p.is_absolute() else Path(p).resolve()).as_posix()


def resolve_path(stored_path: str, project_root: Optional[Path | str]) -> Path:
    """Inverse of :func:`normalize_path`: turn a stored path back into a real Path."""
    p = Path(stored_path)
    if p.is_absolute() or project_root is None:
        return p
    return Path(project_root) / p


def _resolve_input_path(path: Path | str, project_root: Optional[Path | str]) -> Path:
    """Locate the real file for an input ``path``: try it as given (absolute or
    cwd-relative), then relative to ``project_root``. Returns the first that exists,
    else the original so :func:`compute_sha256` raises the fail-loud not-found error."""
    p = Path(path)
    if p.is_file():
        return p
    if project_root is not None and not p.is_absolute():
        candidate = Path(project_root) / p
        if candidate.is_file():
            return candidate
    return p


def compute_sha256(file_path: Path | str) -> str:
    """SHA-256 hex digest of a file's bytes. Fail-loud if the file is absent."""
    p = Path(file_path)
    if not p.is_file():
        raise AssetFileNotFoundError(f"file to register not found: {p}")
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# Registry load / list (read-only)
# --------------------------------------------------------------------------- #
def _manager(rag_dir: Path | str):
    from rag_kernel.cold_manager import ProjectContextManager

    return ProjectContextManager.default(Path(rag_dir))


def load_registry(rag_dir: Path | str) -> dict:
    """Return the ``baked_assets`` partition as ``{"_protocol": str, "assets": [...]}``.

    A store or partition that does not exist yet returns the fresh default shape;
    reads lazy-load a single partition so an unread registry costs zero boot tokens.
    """
    mgr = _manager(rag_dir)
    if not mgr.has_partition(PARTITION_NAME):
        return {"_protocol": DEFAULT_PROTOCOL, "assets": []}
    data = mgr.get(PARTITION_NAME)
    if not isinstance(data, dict):
        raise AssetRegistryError(
            f"{PARTITION_NAME} partition is not an object — registry corrupt"
        )
    assets = data.get("assets")
    if not isinstance(assets, list):
        data = {**data, "assets": []}
    data.setdefault("_protocol", DEFAULT_PROTOCOL)
    return data


def list_assets(rag_dir: Path | str) -> list[AssetRecord]:
    """All registered assets as records, in registration order."""
    reg = load_registry(rag_dir)
    return [AssetRecord.from_dict(a) for a in reg.get("assets", []) if isinstance(a, dict)]


# --------------------------------------------------------------------------- #
# Register — additive, idempotent, fail-loud on rebind / path collision
# --------------------------------------------------------------------------- #
def register_asset(
    rag_dir: Path | str,
    *,
    asset_id: str,
    path: Path | str,
    purpose: str,
    session: str,
    project_root: Optional[Path | str] = None,
    dry_run: bool = False,
    now: Optional[str] = None,
) -> tuple[AssetRecord, str]:
    """Register ``path`` under ``asset_id``. Returns ``(record, action)``.

    ``action`` is ``"created"`` (a new row was appended / would be) or ``"idempotent"``
    (an identical row already exists — no write). Raises :class:`DuplicateAssetError`
    (id exists, different content), :class:`AssetPathCollisionError` (path exists under
    another id), or :class:`AssetFileNotFoundError` (file missing). Nothing is written
    on any raise, nor under ``dry_run``.
    """
    asset_id = (asset_id or "").strip()
    if not asset_id:
        raise AssetRegistryError("asset_id must be a non-empty string")
    purpose = (purpose or "").strip()
    if not purpose:
        raise AssetRegistryError("purpose must be a non-empty string")

    sha = compute_sha256(_resolve_input_path(path, project_root))  # fail-loud if absent
    stored = normalize_path(path, project_root)
    stamp = now or datetime.now(timezone.utc).strftime(_TS_FORMAT)
    candidate = AssetRecord(
        asset_id=asset_id, path=stored, purpose=purpose,
        sha256=sha, session=session, registered_utc=stamp,
    )

    reg = load_registry(rag_dir)
    existing = [AssetRecord.from_dict(a) for a in reg.get("assets", []) if isinstance(a, dict)]

    for rec in existing:
        if rec.asset_id == asset_id:
            if rec.identity_matches(candidate):
                return rec, "idempotent"  # same id, same content — no-op
            raise DuplicateAssetError(
                f"asset_id {asset_id!r} already registered with different content "
                f"(stored path={rec.path!r} sha256={rec.sha256[:12]}…; incoming "
                f"path={candidate.path!r} sha256={sha[:12]}…) — an id is a stable "
                f"handle; register a new id or reuse the existing asset"
            )
        if rec.path == stored and rec.asset_id != asset_id:
            raise AssetPathCollisionError(
                f"path {stored!r} is already registered under id {rec.asset_id!r} — "
                f"refusing to register it a second time under {asset_id!r}; reuse the "
                f"existing asset (this is the re-authoring REUSE-REGISTRY-GUARD exists "
                f"to catch)"
            )

    if dry_run:
        return candidate, "created"

    reg = {**reg, "assets": [*reg.get("assets", []), candidate.to_dict()]}
    reg.setdefault("_protocol", DEFAULT_PROTOCOL)
    mgr = _manager(rag_dir)
    mgr.path.parent.mkdir(parents=True, exist_ok=True)
    mgr.update_partition(PARTITION_NAME, reg)
    return candidate, "created"


# --------------------------------------------------------------------------- #
# Reuse-check — the pre-write guard (never writes)
# --------------------------------------------------------------------------- #
def reuse_check(
    rag_dir: Path | str,
    *,
    path: Optional[Path | str] = None,
    purpose: Optional[str] = None,
    project_root: Optional[Path | str] = None,
) -> list[AssetRecord]:
    """Return registered assets that already cover ``path`` and/or ``purpose``.

    Matching: a ``path`` matches by normalized-path equality; a ``purpose`` matches
    by case-insensitive containment in EITHER direction (so "make thumbnail" hits a
    stored "generate video thumbnail" and vice-versa). At least one of the two must
    be given. Empty result == nothing baked yet; a non-empty result is the signal to
    reuse rather than rewrite (the CLI renders it as a fail-loud non-zero exit).
    """
    if path is None and not (purpose and purpose.strip()):
        raise AssetRegistryError("reuse_check needs at least one of path or purpose")

    want_path = normalize_path(path, project_root) if path is not None else None
    want_purpose = (purpose or "").strip().lower()

    hits: list[AssetRecord] = []
    for rec in list_assets(rag_dir):
        if want_path is not None and rec.path == want_path:
            hits.append(rec)
            continue
        if want_purpose:
            sp = rec.purpose.strip().lower()
            if sp and (want_purpose in sp or sp in want_purpose):
                hits.append(rec)
    return hits
