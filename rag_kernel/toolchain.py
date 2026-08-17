"""TOOLCHAIN — ONE measured answer to "which binary do we mean" (S201).

WHY THIS EXISTS. Every platform assumption in this kernel was written inline, in
prose, at the call site, and each one was true on the author's machine and false
on the operator's:

  * ``detach_run``      hardcoded ``shell="/bin/bash"``      -> RUN-DETACH-AWAIT
                        could not start a process on Windows, so the project's
                        most-repeated rule ("launch detached, block once, never
                        poll") had no working implementation. E-081 -> E-116 ->
                        E-128 are the receipts.
  * ``persistence``     assumed POSIX ``os.kill(pid, 0)``    -> every stale lock
                        read as ALIVE on Windows; a killed agent bricked the
                        deployment.
  * ``scripts/grand_audit`` spelled every probe ``python3``  -> on this host that
                        is the Microsoft Store stub, so the auditor whose first
                        law is "nothing measured below a broken transport is
                        trustworthy" WAS the broken transport, and refused every
                        boot for a reason that was its own.
  * ``grand_audit``     searched ``/home/pakhol /opt /usr/local`` for the TLC jar
                        and, finding nothing because it looked nowhere, asserted
                        "search completed, jar absent".
  * the suite itself    ran only under WSL/Python 3.12 while the kernel runs on
                        Windows/Python 3.14, so 2,818 green described a program
                        that is not the one that ships.

Five instances, one disease: **an unmeasured platform fact, written down at the
point of use, where nothing can audit it.** This module is the cure. Every
binary the project depends on is resolved HERE, measured, and written to
``toolchain/toolchain.json`` inside the project root — one file, inside the
project, under the kernel's control, auditable like any other state.

RULES THIS MODULE ENFORCES BY SHAPE
  1. No caller may hardcode an interpreter, a shell, a jar or a tool path.
     Call ``resolve()`` and use what it measured.
  2. The manifest is a CACHE of a measurement, never a second source of truth:
     ``refresh()`` re-measures from the live machine and rewrites it. A stale
     entry is detected by re-measuring, not by trusting the file.
  3. A tool that is absent is recorded as absent, with where we looked. An
     absent tool is never silently substituted.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

#: Directory name, relative to the project root. Inside the project by rule:
#: nothing this project depends on may live in a side store on someone's Desktop
#: or in a client-owned scratch dir (operator ruling, S201).
TOOLCHAIN_DIRNAME = "toolchain"
MANIFEST_NAME = "toolchain.json"


def toolchain_dir(project_root: "str | Path") -> Path:
    return Path(project_root) / TOOLCHAIN_DIRNAME


def manifest_path(project_root: "str | Path") -> Path:
    return toolchain_dir(project_root) / MANIFEST_NAME


# --------------------------------------------------------------------------- #
# probes — each returns (path_or_None, evidence)
# --------------------------------------------------------------------------- #
def _probe_python() -> tuple[str, str]:
    """The interpreter running us. Never ``which python3``.

    ``python3`` on Windows is usually the Store alias, which prints an advert
    and exits non-zero. ``sys.executable`` is the only answer that cannot be
    wrong about which interpreter is actually executing the kernel.
    """
    return sys.executable, f"sys.executable, {platform.python_version()}"


def _probe_posix_shell() -> tuple[Optional[str], str]:
    """A POSIX shell, required by the detached-run wrapper's shell syntax."""
    if os.name != "nt":
        return ("/bin/bash", "posix default") if Path("/bin/bash").exists() \
            else (shutil.which("sh"), "posix fallback")
    looked = []
    for cand in (shutil.which("bash"),
                 os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                              "Git", "bin", "bash.exe"),
                 os.path.join(os.environ.get("ProgramFiles(x86)",
                                             r"C:\Program Files (x86)"),
                              "Git", "bin", "bash.exe")):
        if not cand:
            continue
        looked.append(cand)
        if Path(cand).exists():
            return cand, "git-for-windows bash"
    return None, "not found; looked in: " + os.pathsep.join(looked)


def _probe_java() -> tuple[Optional[str], str]:
    """java, for TLC. winget's JDK does not appear on PATH until a new shell."""
    p = shutil.which("java")
    if p:
        return p, "on PATH"
    looked = []
    for base in (os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                              "Microsoft"),
                 os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                              "Java"),
                 os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                              "Eclipse Adoptium")):
        looked.append(base)
        if not Path(base).is_dir():
            continue
        for child in sorted(Path(base).iterdir(), reverse=True):
            cand = child / "bin" / ("java.exe" if os.name == "nt" else "java")
            if cand.exists():
                return str(cand), f"found under {base} (not on PATH)"
    return None, "not found; looked in: " + os.pathsep.join(looked)


def _probe_tla_jar(project_root: Path) -> tuple[Optional[str], str]:
    """tla2tools.jar. Project-local FIRST: the toolchain dir is its home."""
    looked = []
    for cand in (toolchain_dir(project_root) / "tla2tools.jar",
                 project_root / "GIT WORKTREES" / "rag-runtime-kernel" / "formal"
                 / "tla2tools.jar",
                 Path.home() / "tla2tools.jar",
                 Path.home() / "bin" / "tla2tools.jar"):
        looked.append(str(cand))
        if cand.exists():
            return str(cand), "project-local" if TOOLCHAIN_DIRNAME in str(cand) \
                else "outside the toolchain dir — run toolchain --refresh to adopt"
    return None, "not found; looked in: " + os.pathsep.join(looked)


def _probe_tmux() -> tuple[Optional[str], str]:
    """The PRIMARY declared transport. On Windows it lives inside WSL.

    Probing the Windows PATH for a POSIX program answers the wrong question and
    reported FAIL on a host where the transport genuinely works through
    ``mcp__tmux-mcp__``.
    """
    p = shutil.which("tmux")
    if p:
        return p, "on PATH"
    if os.name != "nt":
        return None, "not on PATH"
    try:
        r = subprocess.run(["wsl", "-e", "tmux", "-V"],
                           capture_output=True, text=True, timeout=60)
    except Exception as exc:                                  # noqa: BLE001
        return None, f"WSL probe did not finish ({str(exc)[:60]})"
    if r.returncode == 0 and r.stdout.strip():
        return "wsl:tmux", f"via WSL: {r.stdout.strip()[:60]}"
    return None, "absent on PATH and not reachable through WSL"


def _probe_simple(name: str) -> tuple[Optional[str], str]:
    p = shutil.which(name)
    return (p, "on PATH") if p else (None, "not on PATH")


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def measure(project_root: "str | Path") -> dict[str, Any]:
    """Measure the toolchain from the live machine. Never reads the manifest."""
    # ABSOLUTE, always. A measured tool path is consumed by callers running in
    # other working directories — the TLC probe runs from formal/ — so a
    # relative answer is a path that is true only where it was measured. S201:
    # the jar resolved to "..\toolchain\tla2tools.jar" and TLC could not open it.
    root = Path(project_root).resolve()
    py, py_ev = _probe_python()
    shell, shell_ev = _probe_posix_shell()
    java, java_ev = _probe_java()
    jar, jar_ev = _probe_tla_jar(root)
    tmux, tmux_ev = _probe_tmux()

    tools: dict[str, Any] = {
        "python": {"path": py, "evidence": py_ev},
        "posix_shell": {"path": shell, "evidence": shell_ev},
        "java": {"path": java, "evidence": java_ev},
        "tla2tools_jar": {"path": jar, "evidence": jar_ev},
        "tmux": {"path": tmux, "evidence": tmux_ev},
    }
    for name in ("git", "node", "npm", "curl", "rg", "wsl"):
        p, ev = _probe_simple(name)
        tools[name] = {"path": p, "evidence": ev}

    return {
        "$schema_note": "Measured cache of live machine facts. Never hand-edit; "
                        "run `rag_kernel toolchain --refresh`.",
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python_version": platform.python_version(),
        },
        "tools": tools,
        "missing": sorted(k for k, v in tools.items() if not v["path"]),
    }


def refresh(project_root: "str | Path", *, session: Optional[str] = None) -> dict[str, Any]:
    """Re-measure and persist. Returns the manifest that was written."""
    root = Path(project_root)
    doc = measure(root)
    if session:
        doc["measured_by_session"] = session
    d = toolchain_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    manifest_path(root).write_text(
        json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return doc


def resolve(project_root: "str | Path", tool: str, *,
            required: bool = False) -> Optional[str]:
    """The path for ``tool``, measured live.

    Deliberately measures rather than reading the manifest: a cache that can go
    stale without anyone noticing is how this project got here. The manifest is
    for the operator and the auditor to READ; callers get the live answer.
    """
    doc = measure(project_root)
    entry = doc["tools"].get(tool)
    path = entry["path"] if entry else None
    if required and not path:
        raise ToolchainError(
            f"toolchain: '{tool}' is required and was not found. "
            f"{entry['evidence'] if entry else 'unknown tool'}. "
            f"Install it, then run `rag_kernel toolchain --refresh`."
        )
    return path


class ToolchainError(RuntimeError):
    """A required tool is absent. Never silently substitute another one."""
