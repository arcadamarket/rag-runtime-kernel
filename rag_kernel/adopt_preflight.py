"""ADOPTION PRE-FLIGHT -- refuse an upgrade that would delete local work (S186).

WHY THIS MODULE EXISTS
----------------------
The upgrade path is: re-deploy the byte-identical pinned package into a target
RAG/rag_kernel/. For any deployment carrying local kernel work, byte-identity IS
a deletion mechanism. On 2026-08-04 a child deployment adopted v0.4.50 and lost
register-asset --update, a verb it had authored at its own S5, which left it
unable to seal a session. The parent had certified that adoption SOUND using
parent invariants -- tag present, suite passing, package byte-identical to
source -- every one of which held true while the deletion happened.

The lesson is not to check harder. The check was aimed at the wrong object: it
verified the SOURCE was intact and never asked what the TARGET was about to
lose.

WHAT IS COMPARED
----------------
Two surfaces, because the loss that actually happened was invisible at file
level -- the module survived, one CLI flag did not:

1. FILE surface -- modules present in the target and absent from the source.
2. CLI surface  -- subcommands and option strings present in the target and
   absent from the source, recovered by importing each package in its own
   subprocess and walking its parser tree.

REFUSE-BY-DEFAULT: any divergence refuses. accept_local_loss is the only way
past, and it exists to be passed deliberately by someone who read the list.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

PROBE_SOURCE = """import json, sys
sys.path.insert(0, sys.argv[1])
import rag_kernel
_origin = str(getattr(rag_kernel, '__file__', '') or '')
if not _origin.startswith(str(sys.argv[1])):
    sys.stderr.write('probe resolved rag_kernel from ' + _origin + ' not from the package under test; provenance unverified')
    raise SystemExit(3)
from rag_kernel.__main__ import build_parser

parser = build_parser()
out = {}
for action in parser._actions:
    choices = getattr(action, "choices", None)
    if not isinstance(choices, dict):
        continue
    for name, sub in choices.items():
        opts = set()
        for act in getattr(sub, "_actions", []):
            opts.update(act.option_strings or [])
        out[name] = sorted(opts)
print(json.dumps(out))
"""


class PreflightError(Exception):
    """Base for pre-flight refusals."""


class ProbeFailedError(PreflightError):
    """A package could not be introspected, so its surface is UNKNOWN.

    Unknown is a refusal, never an empty set: an un-probed target would report
    zero losses, which is precisely the silent pass this module exists to stop.
    """


class LocalDivergenceError(PreflightError):
    """The target holds work the incoming package does not. Refuse."""


@dataclass(frozen=True)
class Divergence:
    """What the TARGET would lose. Empty on every axis is the only pass."""

    files: tuple[str, ...] = ()
    commands: tuple[str, ...] = ()
    options: tuple[str, ...] = ()

    @property
    def clean(self) -> bool:
        return not (self.files or self.commands or self.options)

    def render(self) -> str:
        if self.clean:
            return "no local divergence: the target loses nothing"
        lines = []
        if self.files:
            lines.append("  MODULES only in target: " + ", ".join(self.files))
        if self.commands:
            lines.append("  VERBS only in target:   " + ", ".join(self.commands))
        if self.options:
            lines.append("  FLAGS only in target:   " + ", ".join(self.options))
        return chr(10).join(lines)


def file_surface(package_dir: Path | str) -> set[str]:
    """Names of the .py modules in a package directory."""
    return {p.name for p in Path(package_dir).glob("*.py")}


def cli_surface(package_parent: Path | str) -> dict[str, list[str]]:
    """Map subcommand -> sorted option strings, by probing the package.

    package_parent is the directory CONTAINING rag_kernel/, so the probe imports
    the package under test rather than the one running this code.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as fh:
        fh.write(PROBE_SOURCE)
        probe_path = fh.name
    try:
        proc = subprocess.run(
            [sys.executable, probe_path, str(package_parent)],
            capture_output=True, text=True, timeout=120,
        )
    finally:
        Path(probe_path).unlink(missing_ok=True)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise ProbeFailedError(
            f"could not introspect the package at {package_parent}: "
            f"{(proc.stderr or proc.stdout).strip()[-400:]}"
        )
    return json.loads(proc.stdout)


def preflight(target_parent: Path | str, source_parent: Path | str) -> Divergence:
    """Enumerate what target holds and source does not.

    Both arguments are directories CONTAINING a rag_kernel package. Nothing is
    written and nothing is copied: this only decides.
    """
    lost_files = sorted(
        file_surface(Path(target_parent) / "rag_kernel")
        - file_surface(Path(source_parent) / "rag_kernel")
    )
    tgt_cli = cli_surface(target_parent)
    src_cli = cli_surface(source_parent)
    lost_cmds = sorted(set(tgt_cli) - set(src_cli))
    lost_opts = []
    for cmd in sorted(set(tgt_cli) & set(src_cli)):
        for opt in sorted(set(tgt_cli[cmd]) - set(src_cli[cmd])):
            lost_opts.append(f"{cmd} {opt}")
    return Divergence(tuple(lost_files), tuple(lost_cmds), tuple(lost_opts))


def assert_safe_to_adopt(
    target_parent: Path | str,
    source_parent: Path | str,
    *,
    accept_local_loss: bool = False,
) -> Divergence:
    """Refuse unless the target loses nothing, or the loss is accepted aloud."""
    div = preflight(target_parent, source_parent)
    if div.clean or accept_local_loss:
        return div
    raise LocalDivergenceError(
        chr(10).join([
            "ADOPTION REFUSED -- the target holds work the incoming"
            " package does not:",
            div.render(),
            "Promote it upstream and re-release, or pass"
            " --accept-local-loss to delete it deliberately."
            " Byte-identity is a deletion mechanism.",
        ])
    )
