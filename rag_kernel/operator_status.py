"""OPERATOR-ONE-NUMBER (S192 mandate, built S200) — one line, GREEN or not.

THE MANDATE, in the operator's words at S192:

    "I am a vibe coder, I set tasks, I do not delve into the weeds of coding and
    testing what you make; whether I trust you or not does not change anything
    for me."

The correct response is not a better report. It is that he must not have to
trust a report the agent wrote. Until S200, judging this project meant reading a
nine-axis grand audit — a coder's tool, run by the agent, summarised by the
agent. Every layer of that chain is a place where a claim can be prettier than
the fact, and S192-S199 is the record of exactly that happening.

So this module composes ONE line and ONE exit code out of terms that are already
measured elsewhere. It measures nothing new and it believes nothing. Every term
resolves to True, False, or UNKNOWN, and:

    L2 (AUDIT_PROTOCOL): UNKNOWN IS BLOCKING. An unfinished measurement is not a
    pass. GREEN requires every term True.

The operator runs it. The agent does not get to be the narrator.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

#: Exit codes. 0 GREEN, 1 NOT GREEN. Nothing else, so `&&` means what it looks
#: like in a shell and the operator never has to interpret a number.
EXIT_GREEN = 0
EXIT_NOT_GREEN = 1


@dataclass(frozen=True)
class Term:
    """One measured condition. ``ok is None`` means UNKNOWN, which blocks GREEN."""

    name: str
    ok: Optional[bool]
    detail: str

    @property
    def blocking(self) -> bool:
        return self.ok is not True

    def render(self) -> str:
        mark = {True: "ok", False: "FAIL", None: "UNKNOWN"}[self.ok]
        return f"  [{mark:>7}] {self.name}: {self.detail}"


@dataclass(frozen=True)
class Verdict:
    terms: tuple[Term, ...]

    @property
    def green(self) -> bool:
        return all(t.ok is True for t in self.terms)

    @property
    def blockers(self) -> tuple[Term, ...]:
        return tuple(t for t in self.terms if t.blocking)

    def headline(self) -> str:
        """The one line. On failure it names ONE reason — the first blocker.

        Deliberately not "3 problems": a list is something to triage, and
        triage is the work the operator said he does not do. One reason is an
        instruction.
        """
        if self.green:
            return "GREEN"
        first = self.blockers[0]
        return f"NOT GREEN — {first.name}: {first.detail}"

    def exit_code(self) -> int:
        return EXIT_GREEN if self.green else EXIT_NOT_GREEN


def _git(root: Path, *args: str) -> tuple[int, str]:
    try:
        out = subprocess.run(["git", "-C", str(root), *args],
                             capture_output=True, text=True, timeout=30)
        return out.returncode, (out.stdout or "").strip()
    except (OSError, ValueError, subprocess.SubprocessError) as ex:
        return 127, f"{type(ex).__name__}: {ex}"


def term_test_gate(hot: dict, live_head: Optional[str] = None) -> Term:
    """The suite was measured, was green, and was measured at the LIVE code.

    A stamp taken at an older commit is the defect this project calls STALE: it
    proves something about code that no longer runs.
    """
    try:
        from rag_kernel import test_gate
        stamp = test_gate.read_stamp(hot)
    except Exception as ex:  # noqa: BLE001 - an unreadable stamp is UNKNOWN, not a pass
        return Term("test gate", None, f"stamp unreadable ({type(ex).__name__})")
    if not stamp:
        return Term("test gate", False, "never measured")
    try:
        from rag_kernel import __version__ as live_runtime
    except Exception:  # noqa: BLE001
        live_runtime = None
    try:
        ok, cell, reason = test_gate.verdict(
            stamp, live_head=live_head, live_runtime=live_runtime)
    except Exception as ex:  # noqa: BLE001
        return Term("test gate", None,
                    f"stamp present but not gradeable ({type(ex).__name__})")
    # `ok` is tri-state by design: None means UNMEASURED or STALE — we do not
    # know. Inheriting the last green number in that state is the S184/S185
    # failure this whole verb exists to make impossible.
    return Term("test gate", ok, cell if ok else f"{cell} — {reason}")


def term_audit(rag_path: Path, git_head: Optional[str]) -> Term:
    """The stored state is self-consistent. Zero ERRORs; warnings do not block."""
    try:
        from rag_kernel import drift_audit
        report = drift_audit.audit_file(rag_path, git_head=git_head)
    except Exception as ex:  # noqa: BLE001
        return Term("audit", None, f"audit did not complete ({type(ex).__name__}: {ex})")
    errors = report.errors
    if errors:
        return Term("audit", False,
                    f"{len(errors)} error(s), first: {errors[0].check}")
    return Term("audit", True, f"0 errors, {len(report.warnings)} warning(s)")


def term_worktree(repo_root: Optional[Path]) -> Term:
    """The running kernel exists in a commit, and that commit is pushed.

    E-109/E-123. Uncommitted work is not a smaller version of committed work; it
    is work that the next session inherits as a mystery, or loses.
    """
    if repo_root is None or not Path(repo_root).is_dir():
        return Term("worktree", None, "repo root not resolved")
    rc, dirty = _git(Path(repo_root), "status", "--porcelain")
    if rc != 0:
        return Term("worktree", None, f"git status failed: {dirty[:80]}")
    if dirty:
        n = len([ln for ln in dirty.splitlines() if ln.strip()])
        return Term("worktree", False, f"{n} uncommitted change(s)")
    rc, sb = _git(Path(repo_root), "status", "-sb")
    if rc != 0:
        return Term("worktree", None, "branch state unreadable")
    head = sb.splitlines()[0] if sb else ""
    if "ahead" in head:
        return Term("worktree", False, "committed but NOT pushed")
    if "behind" in head:
        return Term("worktree", False, "behind the remote")
    return Term("worktree", True, "clean and pushed")


def term_deploy_parity(repo_root: Optional[Path], rag_dir: Path) -> Term:
    """The kernel that RUNS is the kernel that was committed.

    The deployed package under RAG/ is what `python -m rag_kernel` imports; the
    worktree is what the tests and the reviewer see. When they diverge, every
    green measurement is about a different program than the one in use.
    """
    if repo_root is None:
        return Term("deploy parity", None, "repo root not resolved")
    src = Path(repo_root) / "rag_kernel"
    dst = Path(rag_dir) / "rag_kernel"
    if not src.is_dir() or not dst.is_dir():
        return Term("deploy parity", None, "one side of the mirror is absent")
    import filecmp
    drift: list[str] = []
    for path in sorted(src.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        mirror = dst / path.relative_to(src)
        if not mirror.is_file() or not filecmp.cmp(path, mirror, shallow=False):
            drift.append(path.name)
    if drift:
        return Term("deploy parity", False,
                    f"{len(drift)} module(s) differ, first: {drift[0]}")
    return Term("deploy parity", True, "deployed == committed")


def term_hook_layer(project_root: Optional[Path]) -> Term:
    """Declared gates are proven to have run, or are not declared at all.

    HEARTBEAT-FORGERY (S200): before this term existed, a hook layer that had
    never executed once reported full coverage, because the test suite stamped
    the liveness heartbeat the audit read. A gate nobody can prove ran is not a
    gate, and counting it is worse than having none.
    """
    if project_root is None:
        return Term("hook layer", None, "project root not resolved")
    try:
        from rag_kernel.drift_audit import ERROR, check_hook_layer_live
        findings = check_hook_layer_live(project_root)
    except Exception as ex:  # noqa: BLE001
        return Term("hook layer", None, f"clause did not run ({type(ex).__name__})")
    errs = [f for f in findings if getattr(f, "severity", "") == ERROR]
    if errs:
        return Term("hook layer", False, "declared but never proven to have run")
    if findings:
        # WARNING only — a layer formally declared inert, or one that has simply
        # gone quiet. Both are visible in every audit; neither is a false claim
        # of coverage, and it is the false claim this term exists to catch.
        return Term("hook layer", True, str(findings[0].detail)[:110])
    return Term("hook layer", True, "no unproven gates declared")


def compose(rag_path: Path | str) -> Verdict:
    """Measure every term. Never raises: a term that cannot run is UNKNOWN."""
    # Resolve FIRST. Every root below is derived from this path by walking up,
    # so a relative --rag silently pointed every term at the wrong tree — which
    # is the same class of defect as measuring a suite that is not the one that
    # ships, and it read as ok rather than as an error.
    rag_path = Path(rag_path).resolve()
    rag_dir = rag_path.parent
    project_root = rag_dir.parent

    try:
        from rag_kernel.drift_store import load_hot
        hot = load_hot(rag_path)
    except Exception:  # noqa: BLE001
        import json
        try:
            hot = json.loads(rag_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            hot = {}

    repo_root: Optional[Path] = None
    try:
        from rag_kernel import test_gate
        repo_root = Path(test_gate.resolve_repo_root(rag_path, hot))
    except Exception:  # noqa: BLE001
        repo_root = None

    git_head = None
    if repo_root is not None:
        rc, head = _git(repo_root, "rev-parse", "HEAD")
        git_head = head if rc == 0 else None

    return Verdict((
        term_test_gate(hot, git_head),
        term_audit(rag_path, git_head),
        term_worktree(repo_root),
        term_deploy_parity(repo_root, rag_dir),
        term_hook_layer(project_root),
    ))
