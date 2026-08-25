"""AUDIT-ROOT-HARDCODED-TO-ONE-DEPLOYMENT-S209 — a released kernel must not know
where it lives.

WHAT HAPPENED, measured, not imagined. ``scripts/grand_audit.py`` declared

    ap.add_argument("--root", default="/mnt/c/Users/pakhol/Desktop/GitHub Project (RAG Runtime Kernel)")

and ``_close_order_prepare`` invoked it with ``--session`` but no ``--root``. The
close audit is MANDATORY, so every deployment that adopted this release audited
the authoring project instead of itself. On 2026-08-25 the ``_MY U.S. IMM PROJ``
clone ran its close and its own report reads ``root=GitHub Project (RAG Runtime
Kernel)``: it scanned that project's ``RAG_MASTER.json``, ``RAG_CONTEXT.json``,
``BOOTMAP_MANIFEST.json`` and ``toolchain/``, ran the ``gc`` verb three times
inside it, and appended three ``caller=auditor`` records to a session log that
had already been SEALED — under that session's own id.

Nothing was deleted and no canonical file was written; the damage was a false
audit and a foreign write into a sealed log. The damage that MATTERS is that a
clone cannot trust its own close, which is the one gate the whole ritual rests on.

WHY A TEST AND NOT JUST A FIX. Three separate files carried a baked absolute
path, and the one that bit had carried it for many sessions with a full suite
green over it, because nothing ever asked. A fix repairs three files; this asks
the question on every run. The predicate is decidable: a string literal naming a
REAL user home is a hardcoded deployment path. Placeholders in documentation
(``/mnt/c/Users/x``) are not, and are recognised as such.

REPAIR when this goes red: derive the path from ``__file__`` (the kernel lives at
``<root>/<rag-dir>/...``, so the root is two or three levels up depending on the
file), or take it from the CALLER, which usually already knows it — in the S209
incident ``cwd`` was correct in the very frame that failed to pass ``--root``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

#: Trees that ship. Tests are excluded: a test may legitimately pin a path.
SHIPPED = ("rag_kernel", "scripts", "tools")

#: A path naming somebody's home directory, POSIX or Windows-escaped.
HOME_PATH = re.compile(
    r"/mnt/[a-z]/Users/([A-Za-z0-9_.\-]+)/"
    r"|[A-Za-z]:\\{1,2}Users\\{1,2}([A-Za-z0-9_.\-]+)\\{1,2}"
)

#: Names that are obviously stand-ins in documentation, not a real account.
PLACEHOLDERS = {"x", "y", "user", "username", "USER", "me", "you", "someone",
                "alice", "bob", "foo", "<user>", "youruser"}

#: Files allowed to name a real deployment path, each with the reason. A new
#: entry needs a reason that survives the question "why can this not be derived?"
EXEMPT = {
    # A one-off S183 migration script whose SUBJECT is one named deployment:
    # the path is the parameter, not the environment. The garbage collector
    # already lists it as a session-stamped one-off.
    "scripts/ingest_blueprint_s183.py",
}


def _offenders() -> "list[tuple[str, int, str]]":
    out: list[tuple[str, int, str]] = []
    for tree in SHIPPED:
        base = REPO / tree
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(REPO).as_posix()
            if rel in EXEMPT:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for n, line in enumerate(text.splitlines(), 1):
                for m in HOME_PATH.finditer(line):
                    who = m.group(1) or m.group(2) or ""
                    if who in PLACEHOLDERS:
                        continue
                    out.append((rel, n, line.strip()[:120]))
    return out


class TestNoBakedDeploymentPath:
    def test_no_shipped_module_names_a_real_home_directory(self):
        bad = _offenders()
        assert not bad, (
            "hardcoded deployment path(s) — a clone adopting this release would "
            "act on somebody else's project:\n"
            + "\n".join(f"  {f}:{n}  {t}" for f, n, t in bad)
        )

    def test_the_exemption_list_does_not_rot(self):
        """An exempt file that no longer needs the exemption must leave the list."""
        for rel in sorted(EXEMPT):
            path = REPO / rel
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            hits = [m for m in HOME_PATH.finditer(text)
                    if (m.group(1) or m.group(2) or "") not in PLACEHOLDERS]
            assert hits, (
                f"{rel} carries no deployment path any more and must leave EXEMPT"
            )

    def test_documentation_placeholders_are_not_flagged(self):
        """The predicate must not fire on prose — GATE-FALSE-POSITIVE-ON-PROSE-S201."""
        for sample in ("'/mnt/c/Users/x/p' -> 'C:\\\\Users\\\\x\\\\p'",
                       "e.g. /mnt/c/Users/user/Desktop/Proj",
                       "C:\\Users\\<user>\\Desktop"):
            hits = [m for m in HOME_PATH.finditer(sample)
                    if (m.group(1) or m.group(2) or "") not in PLACEHOLDERS]
            assert not hits, sample

    def test_a_real_path_is_flagged(self):
        """Guards against the regex silently matching nothing at all."""
        sample = 'default="/mnt/c/Users/pakhol/Desktop/GitHub Project (RAG)"'
        hits = [m for m in HOME_PATH.finditer(sample)
                if (m.group(1) or m.group(2) or "") not in PLACEHOLDERS]
        assert hits


class TestTheAuditorLocatesItself:
    def test_grand_audit_root_default_is_not_baked(self):
        text = (REPO / "scripts" / "grand_audit.py").read_text(
            encoding="utf-8", errors="replace")
        assert 'ap.add_argument("--root",default=None' in text.replace(" ", "") \
            or '"--root",default=None' in text.replace(" ", ""), \
            "grand_audit --root must default to None and be derived at runtime"

    def test_grand_audit_derives_the_root_from_its_own_location(self):
        text = (REPO / "scripts" / "grand_audit.py").read_text(
            encoding="utf-8", errors="replace")
        assert "abspath(__file__)" in text, \
            "the auditor must locate itself rather than be told where it is"

    @staticmethod
    def _derive(script: Path, cwd: Path) -> Path:
        """The derivation as grand_audit performs it — structure, not arithmetic."""
        import os

        def root_of(d: str) -> Path:
            # a source checkout is its own root; a deployed store dir is not
            return Path(d) if os.path.isdir(os.path.join(d, "tests")) \
                else Path(os.path.dirname(d))

        ragdir = os.path.dirname(os.path.dirname(os.path.abspath(str(script))))
        cwd_s = os.path.abspath(str(cwd))
        if os.path.isfile(os.path.join(ragdir, "RAG_MASTER.json")):
            return root_of(ragdir)
        if os.path.isfile(os.path.join(cwd_s, "RAG_MASTER.json")):
            return root_of(cwd_s)
        return Path(cwd_s)

    @pytest.mark.parametrize("rag_dir_name", ["RAG", "_RAG", "RAG_KERNEL"])
    def test_a_deployment_resolves_to_its_own_root(self, tmp_path, rag_dir_name):
        """The clone that was mis-audited names its RAG dir ``_RAG``."""
        rag = tmp_path / "proj" / rag_dir_name
        (rag / "scripts").mkdir(parents=True)
        (rag / "RAG_MASTER.json").write_text("{}", encoding="utf-8")
        script = rag / "scripts" / "grand_audit.py"
        script.write_text("# stub\n", encoding="utf-8")
        assert self._derive(script, tmp_path) == tmp_path / "proj"

    def test_a_source_checkout_never_escapes_into_another_tree(self, tmp_path):
        """Counting levels was the first S209 attempt and it was wrong at once.

        Run from the git worktree — ``<root>/GIT WORKTREES/<repo>/scripts`` — a
        fixed level count yields ``<root>/GIT WORKTREES``. Whatever the fallback
        decides, it must stay inside the tree the caller stands in and must never
        name a sibling deployment.
        """
        wt = tmp_path / "proj" / "GIT WORKTREES" / "repo"
        (wt / "scripts").mkdir(parents=True)
        (wt / "tests").mkdir()
        (wt / "RAG_MASTER.json").write_text("{}", encoding="utf-8")
        script = wt / "scripts" / "grand_audit.py"
        script.write_text("# stub\n", encoding="utf-8")
        derived = self._derive(script, wt)
        assert derived == wt, (
            "a source checkout is its own root, not the directory containing it"
        )
        assert str(derived).startswith(str(tmp_path))

    def test_invocation_from_a_rag_dir_resolves_to_that_root(self, tmp_path):
        rag = tmp_path / "proj" / "RAG"
        (rag / "scripts").mkdir(parents=True)
        (rag / "RAG_MASTER.json").write_text("{}", encoding="utf-8")
        stray = tmp_path / "elsewhere" / "scripts"
        stray.mkdir(parents=True)
        script = stray / "grand_audit.py"
        script.write_text("# stub\n", encoding="utf-8")
        assert self._derive(script, rag) == tmp_path / "proj"

    def test_the_close_passes_the_root_explicitly(self):
        """The caller knew the answer and did not say it — that was the defect."""
        import inspect

        from rag_kernel import __main__ as m

        src = inspect.getsource(m._close_order_prepare)
        assert "grand_audit.py" in src
        head = src.split("grand_audit.py", 1)[1]
        assert '"--root"' in head, (
            "_close_order_prepare must pass --root to the auditor; relying on "
            "the script's default is what sent a clone into another project"
        )
