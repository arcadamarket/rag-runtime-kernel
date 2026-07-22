"""Deterministic domain BOOT-MAP (ROOT-FILE-MANIFEST / bootmap milestone S168).

WHY THIS MODULE EXISTS
----------------------
Boot-blindness is a recurring failure class in this project: an agent opening a
fresh session cannot SEE the real-world file domain it inherited, so it invents a
story about a file it cannot observe (S165 fabricated an account of a file it had
no map of), or a silent swap (CLEANUP v3) goes UNNOTICED because nothing diffs the
domain across sessions. The RAG is the single source of truth for GOVERNED STATE,
but it deliberately holds no per-file inventory of the physical root -- so the map
of "what actually exists on disk right now" was never captured anywhere.

This module is the deterministic answer, ratified by the operator in
DESIGN_DOMAIN_MANIFEST_S166.md (v2):

- A single deterministic walk of the project root returns the real-world map into
  context at session-open (kills boot-blindness: the agent always knows exactly
  what it holds and where), fed by the SAME traversal the session-start GC walk
  already performs -- near-zero extra cost.
- The map is persisted to ONE machine-generated sidecar beside the RAG under the
  same ``.bak`` parity contract, so a silent baseline drift is itself impossible;
  the RAG holds at most a one-line pointer (like ``meta.rag_files``), NOT per-file
  prose.
- The next boot diffs the current walk against that persisted baseline and emits
  ``Domain map: N files; since S<last>: +new / ~changed / -deleted`` -- a
  DETERMINISTIC change flag, computed by script, never left to the LLM to eyeball
  against narrative.

DESIGN CONTRACT (DESIGN_DOMAIN_MANIFEST_S166.md)
------------------------------------------------
1. Fold into the existing boot walk. Same exclude set as the GC walk plus the dev
   ``GIT WORKTREES/`` tree; per file emit ``{path, sha256, size, mtime, class,
   owner}``.
2. Deterministic. Entries are sorted by path; the diff is computed by content hash
   (never mtime); the same disk state always produces the same map and diff.
3. Persisted baseline. ``session-end`` refreshes the sealed baseline (so the next
   boot has its prior) through the FIX-4 ``tmp -> verify -> .bak parity -> rename``
   path (:func:`rag_kernel.persistence.atomic_write_json`, ``mirror_bak=True``).
4. ``owner=operator`` files (e.g. ``CLEANUP.ps1``) are MAPPED for change-detection
   but flagged operator-owned, so a change is recorded without the kernel claiming
   authorship (closes F3, the S165 mis-registration).
5. One fail-loud auditor invariant, :func:`check_map_coverage`, runs in ``audit``:
   every mapped entry resolves to a real file, and every governed file on disk is
   mapped or explicitly out-of-scope. Self-skips clean until a baseline exists.
   Presence-only (never content) and the per-session churn classes are excluded,
   so a sealed baseline stays audit-clean across normal session cadence.
6. No per-boot GitHub read. Sync/release records remote facts at sync time; the
   local map covers GitHub transitively (Correction 1).

@rag-kernel-manifest
{
  "module": "bootmap",
  "capability": "Deterministic domain boot-map: session-open walk of the project root emitting {path,sha256,size,mtime,class,owner} per file, persisted to a machine-generated sidecar under .bak parity with a one-line meta pointer, diffed against the prior-session baseline into a since-S<last> +new/~changed/-deleted boot line; check_map_coverage fail-loud coverage invariant in audit; owner=operator classification (ROOT-FILE-MANIFEST / bootmap milestone S168)",
  "never_bypass": false
}
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from rag_kernel.persistence import atomic_write_json

SCHEMA_VERSION = "1.0"
MANIFEST_NAME = "BOOTMAP_MANIFEST.json"
META_POINTER_KEY = "bootmap_manifest"

# Directories never mapped: the GC exclude set plus the dev worktree tree. Matched
# by directory *name* during the walk (``GIT WORKTREES`` is the top-level dev tree).
_EXCLUDE_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", ".playwright-mcp",
    "GIT WORKTREES",
}
# Files never mapped: compiled artifacts, crash/parity copies, temp writes, and
# the map sidecar itself (machine-regenerated; mapping it would be circular and
# would churn the coverage check).
_EXCLUDE_SUFFIXES = (".pyc", ".bak", ".tmp")
_EXCLUDE_NAMES = {MANIFEST_NAME}

# owner=operator: files the operator authored/owns -- mapped for change-detection
# but never claimed by the kernel. Deterministic by suffix (S166 F3).
_OPERATOR_SUFFIXES = (".ps1", ".bat", ".cmd")

# Content classes that legitimately appear/append every session. Excluded from the
# UNCOVERED direction of :func:`check_map_coverage` so the normal cadence of new
# session logs / audit reports is never a coverage defect (their PHANTOM direction
# still applies -- a mapped log/report that VANISHED is real tampering).
_CHURN_CLASSES = {"session_log", "audit_report"}


def _is_excluded_file(name: str) -> bool:
    return name in _EXCLUDE_NAMES or name.endswith(_EXCLUDE_SUFFIXES)


def classify(rel_path: str) -> str:
    """Deterministic content class for a mapped file. Total -- never raises."""
    p = rel_path.replace("\\", "/")
    base = p.rsplit("/", 1)[-1]
    if base in ("RAG_MASTER.json", "RAG_COLD.json"):
        return "rag_state"
    if f"/{p}".find("/rag_kernel/") != -1 and base.endswith(".py"):
        return "kernel"
    if base.startswith("AUDIT_") and base.endswith(".md"):
        return "audit_report"
    if base.startswith("session_log_") and base.endswith(".jsonl"):
        return "session_log"
    if base == "ERROR_LOG.md":
        return "error_log"
    if base.startswith("INIT_UNIVERSAL_RUNTIME_KERNEL_") and base.endswith(".md"):
        return "init_prompt"
    if base.startswith("DESIGN_") and base.endswith(".md"):
        return "design"
    if base.endswith(_OPERATOR_SUFFIXES):
        return "script"
    if base.endswith(".py"):
        return "code"
    if base.endswith((".md", ".txt", ".rst")):
        return "doc"
    if base.endswith((".json", ".jsonl")):
        return "data"
    return "other"


def owner_of(rel_path: str) -> str:
    """``operator`` for operator-owned files (mapped, never kernel-claimed); else ``kernel``."""
    base = rel_path.replace("\\", "/").rsplit("/", 1)[-1]
    return "operator" if base.endswith(_OPERATOR_SUFFIXES) else "kernel"


@dataclass(frozen=True)
class FileEntry:
    """One mapped file: identity (path/sha), size/mtime, and classification."""

    path: str
    sha256: str
    size: int
    mtime: int
    cls: str
    owner: str

    def to_dict(self) -> dict:
        return {
            "path": self.path, "sha256": self.sha256, "size": self.size,
            "mtime": self.mtime, "class": self.cls, "owner": self.owner,
        }


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def walk_domain(root: Path | str) -> list[FileEntry]:
    """Walk ``root`` with the GC exclude set; return entries sorted by path.

    Deterministic: directory pruning plus a final sort by path make the output a
    pure function of the on-disk state. An unreadable file fails loud (the OSError
    propagates) rather than silently dropping from the map -- a dropped file is the
    very boot-blindness this module exists to kill.
    """
    root = Path(root).resolve()
    entries: list[FileEntry] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _EXCLUDE_DIRS]
        for name in filenames:
            if _is_excluded_file(name):
                continue
            fpath = Path(dirpath) / name
            rel = fpath.relative_to(root).as_posix()
            st = fpath.stat()
            entries.append(FileEntry(
                path=rel, sha256=_sha256_file(fpath), size=st.st_size,
                mtime=int(st.st_mtime), cls=classify(rel), owner=owner_of(rel),
            ))
    entries.sort(key=lambda e: e.path)
    return entries


def build_manifest(root: Path | str, session: str) -> dict:
    """Build the full map dict for ``root`` stamped with the authoring session."""
    entries = walk_domain(root)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "session": session,
        "root": str(Path(root).resolve()),
        "count": len(entries),
        "files": [e.to_dict() for e in entries],
    }


def manifest_path(rag_dir: Path | str) -> Path:
    return Path(rag_dir) / MANIFEST_NAME


def read_manifest(rag_dir: Path | str) -> Optional[dict]:
    """Load the persisted baseline map, or ``None`` if absent/malformed."""
    p = manifest_path(rag_dir)
    if not p.exists():
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_manifest(rag_dir: Path | str, manifest: dict) -> Path:
    """Persist the map atomically with ``.bak`` parity (FIX-4)."""
    p = manifest_path(rag_dir)
    atomic_write_json(p, manifest, mirror_bak=True)
    return p


def _index(manifest: Optional[dict]) -> dict[str, str]:
    """``path -> sha256`` index of a manifest (empty for ``None``/malformed)."""
    if not manifest or not isinstance(manifest.get("files"), list):
        return {}
    out: dict[str, str] = {}
    for e in manifest["files"]:
        if isinstance(e, dict) and "path" in e:
            out[e["path"]] = e.get("sha256", "")
    return out


def diff_maps(prior: Optional[dict], current: dict) -> dict:
    """Deterministic content diff of two maps: sorted new/changed/deleted paths."""
    pi, ci = _index(prior), _index(current)
    return {
        "new": sorted(p for p in ci if p not in pi),
        "deleted": sorted(p for p in pi if p not in ci),
        "changed": sorted(p for p in ci if p in pi and ci[p] != pi[p]),
    }


def boot_line(prior: Optional[dict], current: dict) -> str:
    """The boot-visible one-liner: total count + since-last-session change flags."""
    n = current.get("count", len(current.get("files", [])))
    if not prior:
        return f"Domain map: {n} files; no prior baseline (first map sealed this session)."
    last = prior.get("session", "?")
    d = diff_maps(prior, current)
    return (
        f"Domain map: {n} files; since {last}: "
        f"+{len(d['new'])} new / ~{len(d['changed'])} changed / -{len(d['deleted'])} deleted"
    )


def session_start_line(root: Path | str, rag_dir: Path | str) -> str:
    """Boot-visible surface: compute the current map, diff it against the sealed
    baseline, and return the one-line summary. READ-ONLY -- the seal at
    session-end owns writing the baseline, so a boot never mutates it."""
    prior = read_manifest(rag_dir)
    current = build_manifest(root, session=(prior or {}).get("session", "?"))
    return boot_line(prior, current)


def refresh_baseline(root: Path | str, rag_dir: Path | str, session: str) -> Path:
    """session-end: rebuild the map and seal it as the new baseline (``.bak``
    parity). Returns the sidecar path so the caller can set the meta pointer."""
    manifest = build_manifest(root, session=session)
    return write_manifest(rag_dir, manifest)


def ensure_meta_pointer(rag_path: Path | str) -> bool:
    """Idempotently set ``meta.rag_files.bootmap_manifest`` -> the sidecar name.

    One-line discoverability pointer (like the other ``meta.rag_files`` entries).
    Returns True if it wrote (pointer was absent/wrong), False if already correct
    -- so it is a clean no-op after the first session. Preserves all other RAG
    fields and keeps ``.bak`` parity via the canonical atomic writer.
    """
    rag_path = Path(rag_path)
    with open(rag_path, "r", encoding="utf-8-sig") as f:
        hot = json.load(f)
    rag_files = hot.setdefault("meta", {}).setdefault("rag_files", {})
    if rag_files.get(META_POINTER_KEY) == MANIFEST_NAME:
        return False
    rag_files[META_POINTER_KEY] = MANIFEST_NAME
    atomic_write_json(rag_path, hot, mirror_bak=True)
    return True


def check_map_coverage(hot: dict, root: Path | str, rag_dir: Path | str) -> list:
    """Fail-loud coverage invariant (runs in ``audit``).

    Two directions over the SEALED baseline vs the live disk:
      * PHANTOM   -- a mapped entry that no longer resolves to a real file.
      * UNCOVERED -- a governed file on disk the map does not contain and that is
        not in the declared out-of-scope set.

    Self-skips clean (returns ``[]``) until a baseline exists -- a fresh deploy has
    no map yet and must not fail its first audit. Presence-only (paths), never
    content: sha/mtime changes are the DIFF's job, not a coverage defect, so the
    check stays clean across normal per-session churn once the baseline is sealed.
    The per-session churn classes (session logs, audit reports) are exempt from the
    UNCOVERED direction so the normal cadence of new logs/reports is never a gap.

    ``hot`` is accepted for a uniform ``check_*(...)`` signature and future use; the
    invariant is over the filesystem + sealed baseline, not the in-memory RAG.
    """
    from rag_kernel.drift_audit import AuditFinding  # lazy: avoid an import cycle

    manifest = read_manifest(rag_dir)
    if manifest is None:
        return []
    mapped = set(_index(manifest))
    on_disk = {e.path for e in walk_domain(root)}
    findings = []
    for p in sorted(mapped - on_disk):
        findings.append(AuditFinding(
            check="map_coverage", severity="error",
            detail=f"mapped file no longer on disk (stale map entry): {p}",
            item_id=None))
    for p in sorted(on_disk - mapped):
        if classify(p) in _CHURN_CLASSES:
            continue
        findings.append(AuditFinding(
            check="map_coverage", severity="error",
            detail=f"governed file on disk is not in the boot-map (coverage gap): {p}",
            item_id=None))
    return findings
