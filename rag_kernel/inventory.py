"""FLEET-INVENTORY (S180) — know what exists, here and next door.

Why this module exists
----------------------
Two measured facts, one week apart, with the same shape.

1. This kernel's baked-asset registry sat **empty for 178 sessions** while the
   runbook told every clone to register what it ships. Prior work was
   consequently re-derived by ``grep`` instead of found by ``reuse-check``.
2. The eBay clone carries **47 scripts** and a ``RAG_CONTEXT.json`` with five
   partitions and **no ``baked_assets`` partition at all**, so ``reuse-check``
   there returns CLEAR for every one of them. It also carries
   ``capability_ledger.py`` — it reinvented this registry locally, because this
   registry never reached it.

An inventory that only sees its own deployment cannot prevent the second
failure. Clone #3 rewrites what clone #1 shipped, and nothing in the toolchain
can even tell that it happened. So inventory is **fleet-scoped**, and it is
**mandatory** — a control the source operates before any clone is required to.

Design contract
---------------
* **Read-only by default.** ``scan`` and ``fleet`` never write. Only
  ``backfill`` writes, and only through the guarded ``register_asset`` store.
* **Deterministic classification.** :func:`classify` is a pure function of the
  path. Two runs over an unchanged tree produce byte-identical output, so a
  diff between them is signal rather than noise.
* **Stdlib only, no RAG import at module scope.** Usable against a deployment
  whose RAG does not exist yet, and against a sibling this kernel does not own.
* **Bounded emission.** Every listing is capped and reports what it truncated;
  a 47-script deployment must not flood a pane (Rule 17).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

__all__ = [
    "KERNEL", "TEST", "SCRIPT", "DOC", "DATA", "MIRROR", "SCRATCH", "OTHER",
    "CLASSES", "REUSABLE",
    "FileRecord", "InventoryReport", "FleetEntry",
    "classify", "scan", "unregistered", "fleet_scan", "fleet_reuse_check",
    "FLEET_PARTITION",
]

# -- classes ---------------------------------------------------------------- #
KERNEL = "kernel"    # the rag_kernel package itself — travels by file copy
TEST = "test"        # test suite
SCRIPT = "script"    # self-made executable work — THE reuse-relevant class
DOC = "doc"          # runbooks, blueprints, guides — durable instructions
REPORT = "report"    # per-session history: AUDIT_CANONICAL_REPORT_S161.md et al
DATA = "data"        # json/yaml/csv state and config
MIRROR = "mirror"    # .bak atomic-write mirrors — governed, not waste
SCRATCH = "scratch"  # genuinely disposable
OTHER = "other"

CLASSES = (KERNEL, TEST, SCRIPT, DOC, REPORT, DATA, MIRROR, SCRATCH, OTHER)

#: Classes worth registering and worth showing to a sibling deployment.
#:
#: REPORT is deliberately excluded. A session's audit report is *history* — it
#: describes what happened once and is never reused. Registering 45 of them
#: would bury the two scripts that matter and would breach the S165 operator
#: ruling on what the registry is for. The distinction is load-bearing: an
#: inventory that cannot tell capability from history produces a number nobody
#: reads, which is how this registry stayed empty for 178 sessions.
REUSABLE = (SCRIPT, DOC)

FLEET_PARTITION = "fleet_assets"
_FLEET_CONFIG_PARTITION = "fleet"

# Directories never walked. ``.git`` alone is tens of thousands of files and
# would dominate every count while meaning nothing.
_PRUNE_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "venv", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".next", "dist", "build", ".idea", ".vscode",
})

_SCRATCH_SUFFIXES = frozenset({
    ".pyc", ".pyo", ".pyd", ".tmp", ".temp", ".orig", ".rej", ".swp", ".swo",
})
_DATA_SUFFIXES = frozenset({".json", ".yaml", ".yml", ".csv", ".tsv", ".toml", ".ini"})
_DOC_SUFFIXES = frozenset({".md", ".rst", ".txt"})

_DEFAULT_LIMIT = 40


@dataclass(frozen=True)
class FileRecord:
    """One file, classified. ``rel`` is POSIX-relative to the scan root."""

    rel: str
    cls: str
    size: int

    def to_dict(self) -> dict:
        return {"path": self.rel, "class": self.cls, "size": self.size}


@dataclass
class InventoryReport:
    root: str
    files: list[FileRecord] = field(default_factory=list)
    pruned_dirs: int = 0

    @property
    def counts(self) -> dict:
        out = {c: 0 for c in CLASSES}
        for f in self.files:
            out[f.cls] += 1
        return out

    def of(self, cls: str) -> list[FileRecord]:
        return [f for f in self.files if f.cls == cls]

    def to_dict(self, limit: int = _DEFAULT_LIMIT) -> dict:
        return {
            "root": self.root,
            "total": len(self.files),
            "counts": self.counts,
            "reusable": [f.to_dict() for f in self.reusable()[:limit]],
            "truncated": max(0, len(self.reusable()) - limit),
        }

    def reusable(self) -> list[FileRecord]:
        return sorted(
            (f for f in self.files if f.cls in REUSABLE), key=lambda f: f.rel)

    def render(self, limit: int = _DEFAULT_LIMIT) -> str:
        c = self.counts
        head = (f"inventory: {len(self.files)} files under {self.root}\n"
                f"  " + "  ".join(f"{k}={c[k]}" for k in CLASSES if c[k]))
        reusable = self.reusable()
        if not reusable:
            return head + "\n  (nothing reusable — no scripts or docs found)"
        shown = reusable[:limit]
        body = "\n".join(f"  [{f.cls}] {f.rel}" for f in shown)
        tail = ""
        if len(reusable) > limit:
            tail = f"\n  … {len(reusable) - limit} more (raise --limit to see them)"
        return f"{head}\n  --- reusable ---\n{body}{tail}"


@dataclass(frozen=True)
class FleetEntry:
    """A reusable artifact belonging to some deployment — possibly not this one."""

    deployment: str
    rel: str
    cls: str
    purpose: Optional[str] = None
    registered: bool = False

    def to_dict(self) -> dict:
        return {
            "deployment": self.deployment, "path": self.rel, "class": self.cls,
            "purpose": self.purpose, "registered": self.registered,
        }


_SESSION_STAMP = __import__("re").compile(r"_S\d+(?=[._-]|$)", __import__("re").I)


def _is_session_stamped(name: str) -> bool:
    """True for a per-session artifact: ``AUDIT_CANONICAL_REPORT_S161.md``.

    A session stamp is the deterministic signal that a document records one
    session rather than instructing every session. Note the RUNBOOK is
    ``RUNBOOK_CLONE_INIT_S179.md`` — stamped, yet plainly durable instruction —
    so the stamp alone is not sufficient; see :func:`classify` for the
    instruction-name override that keeps it a DOC.
    """
    stem = name.rsplit(".", 1)[0]
    return bool(_SESSION_STAMP.search(stem))


#: Name prefixes that stay DOC even when session-stamped: they instruct future
#: sessions rather than record a past one.
_INSTRUCTION_PREFIXES = ("RUNBOOK", "BLUEPRINT", "GUIDE", "SPEC", "INIT", "README",
                         "PLAYBOOK", "PROTOCOL", "CHANGELOG", "ROADMAP")


def classify(rel: str) -> str:
    """Pure, total classification of a project-relative POSIX path.

    Ordering matters and is deliberate: ``.bak`` is checked before suffix rules
    so a governed mirror is never mistaken for scratch, and ``rag_kernel/`` wins
    over ``.py`` so the kernel is never counted as self-made work.
    """
    p = rel.replace("\\", "/")
    parts = p.split("/")
    name = parts[-1]

    if p.endswith(".bak"):
        return MIRROR
    if "rag_kernel" in parts[:-1] or name == "rag_kernel":
        return KERNEL
    if "tests" in parts[:-1] or name.startswith("test_"):
        return TEST

    suffix = ("." + name.rsplit(".", 1)[1].lower()) if "." in name[1:] else ""
    if suffix in _SCRATCH_SUFFIXES:
        return SCRATCH
    if suffix == ".py":
        return SCRIPT
    if suffix in _DOC_SUFFIXES:
        instructional = name.upper().startswith(_INSTRUCTION_PREFIXES)
        return REPORT if (_is_session_stamped(name) and not instructional) else DOC
    if suffix in _DATA_SUFFIXES:
        return DATA
    if suffix in (".jsonl", ".log"):
        # Session logs and WAL streams: governed history, not waste, but not
        # reusable capability either.
        return DATA
    return OTHER


def scan(root: Path | str) -> InventoryReport:
    """Walk ``root`` once, classifying every file. Never writes, never follows
    symlinks out of the tree."""
    root = Path(root)
    report = InventoryReport(root=str(root))
    if not root.is_dir():
        return report

    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except OSError:  # unreadable dir — record nothing, keep walking
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    if entry.name in _PRUNE_DIRS:
                        report.pruned_dirs += 1
                        continue
                    stack.append(entry)
                    continue
                rel = entry.relative_to(root).as_posix()
                report.files.append(
                    FileRecord(rel=rel, cls=classify(rel), size=entry.stat().st_size))
            except OSError:
                continue
    report.files.sort(key=lambda f: f.rel)
    return report


def unregistered(root: Path | str, rag_dir: Path | str) -> list[FileRecord]:
    """Reusable files present on disk but absent from the baked-asset registry.

    This is the backfill worklist, and the number it returns is the honest
    measure of how much shipped capability is currently invisible to
    ``reuse-check``.
    """
    report = scan(root)
    try:
        from rag_kernel.asset_registry import list_assets
        known = {rec.path.replace("\\", "/") for rec in list_assets(rag_dir)}
    except Exception:
        known = set()
    return [f for f in report.reusable() if f.rel not in known]


def _read_partition(rag_dir: Path | str, name: str) -> Optional[dict]:
    path = Path(rag_dir) / "RAG_CONTEXT.json"
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            return (json.load(fh) or {}).get(name)
    except (OSError, ValueError):
        return None


def _deployment_registry(rag_dir: Path | str) -> dict:
    """``{relpath: purpose}`` for a deployment's registered assets, or ``{}``.

    Deliberately tolerant: a sibling may be on an older kernel, may have no
    registry partition at all (the eBay case), or may be mid-write. None of
    those is this deployment's problem to fail on.
    """
    part = _read_partition(rag_dir, "baked_assets")
    if not isinstance(part, dict):
        return {}
    out = {}
    for rec in part.get("assets") or []:
        if isinstance(rec, dict) and rec.get("path"):
            out[str(rec["path"]).replace("\\", "/")] = rec.get("purpose")
    return out


def fleet_scan(deployments: Iterable[dict]) -> list[FleetEntry]:
    """Merge the reusable surface of several deployments into one view.

    ``deployments`` is an iterable of ``{"name":…, "root":…, "rag_dir":…}``.
    A deployment with no registry still contributes its scripts and docs —
    which is the entire point, since the deployment most worth learning from is
    the one that never registered anything.
    """
    entries: list[FleetEntry] = []
    for dep in deployments:
        name = str(dep.get("name") or Path(str(dep.get("root", "?"))).name)
        root = dep.get("root")
        if not root:
            continue
        registry = _deployment_registry(dep.get("rag_dir") or root)
        for rec in scan(root).reusable():
            entries.append(FleetEntry(
                deployment=name, rel=rec.rel, cls=rec.cls,
                purpose=registry.get(rec.rel),
                registered=rec.rel in registry))
    entries.sort(key=lambda e: (e.deployment, e.rel))
    return entries


def fleet_reuse_check(rag_dir: Path | str, purpose: str) -> list[FleetEntry]:
    """Search the persisted fleet view for prior art matching ``purpose``.

    Containment in either direction, mirroring ``asset_registry.reuse_check`` so
    a fleet hit and a local hit mean the same thing to the caller. Matches on
    the filename stem too, because a sibling's unregistered script has no
    purpose text — and an unregistered ``preflight.py`` next door is exactly the
    prior art this is for.
    """
    part = _read_partition(rag_dir, FLEET_PARTITION)
    if not isinstance(part, dict):
        return []
    needle = (purpose or "").strip().lower()
    if not needle:
        return []
    hits: list[FleetEntry] = []
    for rec in part.get("entries") or []:
        if not isinstance(rec, dict):
            continue
        hay = " ".join(filter(None, [
            str(rec.get("purpose") or ""),
            Path(str(rec.get("path") or "")).stem.replace("_", " "),
        ])).lower()
        if not hay:
            continue
        if needle in hay or hay in needle or any(
                tok and tok in hay for tok in needle.split() if len(tok) > 3):
            hits.append(FleetEntry(
                deployment=str(rec.get("deployment") or "?"),
                rel=str(rec.get("path") or "?"),
                cls=str(rec.get("class") or OTHER),
                purpose=rec.get("purpose"),
                registered=bool(rec.get("registered"))))
    return hits


def fleet_config(rag_dir: Path | str) -> list[dict]:
    """Declared sibling deployments from the ``fleet`` context partition."""
    part = _read_partition(rag_dir, _FLEET_CONFIG_PARTITION)
    if isinstance(part, dict):
        deps = part.get("deployments")
        if isinstance(deps, list):
            return [d for d in deps if isinstance(d, dict)]
    return []
