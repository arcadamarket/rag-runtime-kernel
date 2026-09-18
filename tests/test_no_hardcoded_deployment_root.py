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
        """The derivation as grand_audit performs it — structure, not arithmetic.

        S211: this used to REIMPLEMENT ``root_of`` inline, which is the
        self-certifying shape SELF-CERTIFYING-EVIDENCE-GATE-S201 names — the copy
        stayed green while the original was wrong for a whole class of
        deployment. It now calls the shipped function.
        """
        import os

        root_of = _grand_audit_module()._root_of
        ragdir = os.path.dirname(os.path.dirname(os.path.abspath(str(script))))
        cwd_s = os.path.abspath(str(cwd))
        if os.path.isfile(os.path.join(ragdir, "RAG_MASTER.json")):
            return Path(root_of(ragdir))
        if os.path.isfile(os.path.join(cwd_s, "RAG_MASTER.json")):
            return Path(root_of(cwd_s))
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


def _grand_audit_module():
    """Import the real auditor, so these tests measure IT and not a copy of it.

    The class above derives the root with a REIMPLEMENTATION of grand_audit's
    logic. That is a self-certifying shape (SELF-CERTIFYING-EVIDENCE-GATE-S201):
    the copy can stay green while the original rots. The store-dir tests below
    import the shipped function instead.
    """
    import importlib.util

    path = REPO / "scripts" / "grand_audit.py"
    spec = importlib.util.spec_from_file_location("grand_audit_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # main() is behind a __main__ guard
    return mod


class TestTheAuditorResolvesTheStoreDirName:
    """AUDIT-STORE-DIR-NAME-HARDCODED-S211 — the half S209 did not finish.

    S209 made the ROOT derivable for "any deployment and any rag-dir name (`RAG`
    here, `_RAG` in that clone)" — and the constructor then threw it away on the
    next line with ``os.path.join(root, "RAG")``. Passing ``--root`` therefore
    only ever worked for deployments that spell the store ``RAG``, and passing
    ``--root`` is exactly what the mandatory close does.

    MEASURED: the ``_MY U.S. IMM PROJ`` clone spells its store ``_RAG`` and
    patched this line locally on 2026-08-25, leaving a comment asking for this
    release — "RETIRE when a release resolves the store dir instead of assuming
    its name". Its copy of the auditor has been AHEAD of upstream since, which
    also means the S210 cutover advice ("the cutover is a FILE COPY of
    scripts/grand_audit.py") would have overwritten that patch and re-broken the
    deployment it was meant to fix. A fix that lives in one clone is a fix the
    fleet does not have.
    """

    @staticmethod
    def _deployment(tmp_path, store_name):
        rag = tmp_path / "proj" / store_name
        (rag / "scripts").mkdir(parents=True)
        (rag / "RAG_MASTER.json").write_text("{}", encoding="utf-8")
        return tmp_path / "proj", rag

    @pytest.mark.parametrize("store", ["RAG", "_RAG", "RAG_KERNEL", "store"])
    def test_any_store_name_resolves(self, tmp_path, store):
        root, rag = self._deployment(tmp_path, store)
        got = _grand_audit_module()._store_dir(str(root))
        assert Path(got) == rag, (
            f"a deployment spelling its store {store!r} must be audited at its "
            f"own store, not at <root>/RAG"
        )

    def test_a_source_checkout_is_its_own_store(self, tmp_path):
        """The kernel repo holds RAG_MASTER.json at its top level."""
        wt = tmp_path / "proj" / "GIT WORKTREES" / "repo"
        (wt / "scripts").mkdir(parents=True)
        (wt / "RAG_MASTER.json").write_text("{}", encoding="utf-8")
        got = _grand_audit_module()._store_dir(str(wt))
        assert Path(got) == wt

    def test_the_conventional_name_wins_when_two_stores_exist(self, tmp_path):
        """A stray copy must not silently outrank the real store."""
        root = tmp_path / "proj"
        for name in ("RAG", "_RAG", "aaa_decoy"):
            d = root / name
            d.mkdir(parents=True)
            (d / "RAG_MASTER.json").write_text("{}", encoding="utf-8")
        got = _grand_audit_module()._store_dir(str(root))
        assert Path(got) == root / "RAG"

    def test_an_empty_root_still_names_a_concrete_path(self, tmp_path):
        """The fallback exists so a failure says WHERE it looked, not ''."""
        root = tmp_path / "proj"
        root.mkdir()
        got = _grand_audit_module()._store_dir(str(root))
        assert Path(got) == root / "RAG"

    def test_the_constructor_no_longer_assumes_the_name(self):
        text = (REPO / "scripts" / "grand_audit.py").read_text(
            encoding="utf-8", errors="replace")
        assert 'self.ragd=_store_dir(root)' in text.replace(" ", ""), (
            "Grand.__init__ must RESOLVE the store dir; assuming os.path.join("
            "root, 'RAG') is the defect this class exists for"
        )

    def test_the_real_deployment_of_this_repo_resolves(self):
        """Not a fixture: the store this very checkout ships beside."""
        mod = _grand_audit_module()
        got = Path(mod._store_dir(str(REPO)))
        assert (got / "RAG_MASTER.json").is_file(), got


class TestADeploymentThatMirrorsTestsIsStillADeployment:
    """AUDIT-ROOT-READS-A-MIRRORED-DEPLOYMENT-AS-A-CHECKOUT-S211.

    S209 discriminated a source checkout from a deployed store on ``tests/``:
    "the kernel repo also carries tests/; a deployment never does". This project
    then instructed the opposite. E-IMM-020 settled that "a deployment is runtime
    + tests + formal + EVERY pinned spec version", after 31 tests failed in the
    _MY U.S. IMM PROJ clone on missing artifacts — so that clone mirrored
    ``tests/`` into its store exactly as told, and the auditor started reading its
    store as a checkout.

    THE MEASURED CONSEQUENCE, 2026-09-17, running that clone's own boot gate:

        GRAND AUDIT   root=_RAG   session=-
        [????] toolchain manifest  L1: rag_kernel.toolchain not importable
        RESULT: 0 PASS  0 FAIL  1 UNKNOWN   VERDICT: NOT GREEN

    against ``15 PASS / GREEN`` for a deployment whose store happens not to carry
    tests/. The root resolved to the STORE, the store dir under that root did not
    exist, and every probe running with ``cwd=ragd`` failed. The close's grand
    audit is MANDATORY, so that deployment's close could not pass at all — which
    is GRAND-AUDIT-IN-CLOSE-CANNOT-PASS-S209 arriving by a second road.
    """

    @staticmethod
    def _clone(tmp_path, store_name="_RAG", *, mirror_tests=True, lived=True):
        root = tmp_path / "IMM CASE"
        store = root / store_name
        (store / "scripts").mkdir(parents=True)
        (store / "RAG_MASTER.json").write_text("{}", encoding="utf-8")
        if mirror_tests:
            (store / "tests").mkdir()
        if lived:
            (store / "session_log_S21.jsonl").write_text("", encoding="utf-8")
            (store / "AUDIT_CANONICAL_REPORT_S21.md").write_text("", encoding="utf-8")
        return root, store

    def test_the_clone_resolves_to_its_project_root_not_its_store(self, tmp_path):
        root, store = self._clone(tmp_path)
        got = Path(_grand_audit_module()._root_of(str(store)))
        assert got == root, (
            "a deployment that mirrored tests/ as instructed must still resolve "
            "to its PROJECT root; resolving to the store is what made its "
            "mandatory close audit unpassable"
        )

    def test_and_then_the_store_dir_still_resolves_under_it(self, tmp_path):
        """The two halves compose: right root, right store, any store name."""
        root, store = self._clone(tmp_path)
        got = Path(_grand_audit_module()._store_dir(str(root)))
        assert got == store

    def test_a_checkout_under_git_worktrees_is_still_its_own_root(self, tmp_path):
        wt = tmp_path / "proj" / "GIT WORKTREES" / "rag-runtime-kernel"
        (wt / "tests").mkdir(parents=True)
        (wt / "RAG_MASTER.json").write_text("{}", encoding="utf-8")
        assert Path(_grand_audit_module()._root_of(str(wt))) == wt

    def test_a_store_that_never_ran_still_resolves_to_its_root(self, tmp_path):
        """A freshly deployed clone has no logs yet and must not be misread."""
        root, store = self._clone(tmp_path, mirror_tests=False, lived=False)
        assert Path(_grand_audit_module()._root_of(str(store))) == root

    def test_the_live_store_predicate_does_not_rest_on_the_boot_map(self):
        """BOOTMAP_MANIFEST.json separates nothing — the worktree carries one."""
        import inspect

        src = inspect.getsource(_grand_audit_module()._live_store)
        assert "BOOTMAP_MANIFEST" not in src.split('"""')[2], (
            "the predicate must not key on an artifact both shapes carry"
        )

    def test_root_and_store_round_trip_in_whatever_layout_this_is(self):
        """Measured against the REAL tree this copy sits in — any layout.

        S211, second pass. The first version of this test asserted
        ``_root_of(REPO) == REPO`` and ``_store_dir(REPO.parent.parent)``, which
        hardcodes the AUTHORING project's two-tree layout — inside the very file
        whose subject is that a released kernel must not know where it lives. It
        passed here and failed immediately in the _MY U.S. IMM PROJ deployment,
        where the repo IS the store. The irony is the finding: an assertion about
        layout independence must itself be layout independent.

        What actually holds everywhere is a ROUND TRIP: resolve the root of this
        tree, resolve the store under that root, and the store must resolve back
        to the same root. Both real layouts on this machine satisfy it, and so
        does any deployment that is coherent at all.
        """
        mod = _grand_audit_module()
        root = Path(mod._root_of(str(REPO)))
        store = Path(mod._store_dir(str(root)))
        assert (store / "RAG_MASTER.json").is_file(), (
            f"resolved root {root} has no store under it (found {store})"
        )
        assert Path(mod._root_of(str(store))) == root, (
            f"store {store} resolves to a different root than {root}"
        )


class TestTheSealProbeCanFailOnItsOwnSubject:
    """GRAND-AUDIT-SEAL-PROBE-CANNOT-READ-ZERO-S211.

    The probe named "session sealed cleanly" tested `PASS if s>0`. So one seal
    passed — and TWO passed just as well, which is CLOSE-DOUBLE-SEAL-S187, the
    precise failure it exists to catch. A probe that cannot fail on its own
    subject is decoration.

    The second half is why the close could never satisfy it: session_forensics
    printed no SEALS line at all before a seal landed, the regex matched nothing,
    the probe returned UNKNOWN, and L2 makes an UNKNOWN block GREEN. The close
    runs this audit BEFORE writing its marker, so the MANDATORY audit was
    unanswerable at the only moment it actually runs — one of the two roads into
    GRAND-AUDIT-IN-CLOSE-CANNOT-PASS-S209.
    """

    @staticmethod
    def _verdict(seals, in_close=False):
        return _grand_audit_module().seal_verdict(seals, in_close)

    def test_two_seals_fail_and_are_named_as_the_double_seal(self):
        status, evidence = self._verdict(2)
        mod = _grand_audit_module()
        assert status == mod.FAIL, "a double seal passed this probe until S211"
        assert "DOUBLE SEAL" in evidence and "S187" in evidence

    def test_seven_seals_also_fail(self):
        """`s>0` passed every count above one, not just two."""
        assert self._verdict(7)[0] == _grand_audit_module().FAIL

    def test_exactly_one_seal_passes(self):
        assert self._verdict(1)[0] == _grand_audit_module().PASS

    def test_zero_seals_inside_a_close_is_correct(self):
        status, evidence = self._verdict(0, in_close=True)
        assert status == _grand_audit_module().PASS
        assert "INSIDE the close" in evidence

    def test_zero_seals_outside_a_close_is_a_failure(self):
        status, evidence = self._verdict(0, in_close=False)
        assert status == _grand_audit_module().FAIL
        assert "unsealed" in evidence

    def test_an_unreadable_count_stays_unknown_not_invented(self):
        assert self._verdict(None)[0] == _grand_audit_module().UNK

    def test_the_renderer_always_prints_a_number(self):
        """The probe can only read zero if the renderer is willing to write it."""
        from rag_kernel.session_forensics import analyze_log, render_text

        out = render_text(analyze_log([{
            "seq": 1, "ts": "2026-01-01T00:00:00+00:00", "sid": "S1",
            "event": "tool_invocation", "msg": "cli audit",
            "data": {"command": "audit", "exit_code": 0},
        }]))
        assert "SEALS            : 0 (not closed yet)" in out, out

    def test_the_close_declares_the_phase_rather_than_the_probe_guessing_it(self):
        import inspect

        from rag_kernel import __main__ as m

        src = inspect.getsource(m._close_order_prepare)
        head = src.split("grand_audit.py", 1)[1]
        assert '"--in-close"' in head, (
            "the close must declare that it runs before the marker; a probe that "
            "infers its own phase is guessing"
        )


class TestTheAuditorDoesNotAssumeTwoTrees:
    """AUDIT-ASSUMES-A-TWO-TREE-DEPLOYMENT-S211.

    Axis 1 probed ``<root>/GIT WORKTREES/rag-runtime-kernel/{tests,formal}`` and
    nothing else — the layout of the project that wrote the auditor. That is one
    valid shape, not the only one.

    MEASURED in the _MY U.S. IMM PROJ clone on 2026-09-17: its store ``_RAG`` is
    at once the store, the git repository, and the holder of ``tests/`` and
    ``formal/`` (51 files), while ``GIT WORKTREES/rag-runtime-kernel/`` holds
    exactly one file — a rendered CLAUDE.md — and is not a repository. The audit
    reported ``tree formal/`` and ``tree tests/`` as FAILURES against a
    deployment that has both, and aimed the TLC probe at a directory that does
    not exist, which surfaced as a truncated ``[Errno 2]`` instead of a finding.
    """

    @staticmethod
    def _two_tree(tmp_path):
        root = tmp_path / "proj"
        wt = root / "GIT WORKTREES" / "rag-runtime-kernel"
        for d in ("tests", "formal", "rag_kernel"):
            (wt / d).mkdir(parents=True)
        store = root / "RAG"
        store.mkdir()
        return root, wt, store

    @staticmethod
    def _single_tree(tmp_path):
        """The IMM shape: everything in the store, a decoy worktree beside it."""
        root = tmp_path / "IMM CASE"
        store = root / "_RAG"
        for d in ("tests", "formal", "rag_kernel"):
            (store / d).mkdir(parents=True)
        wt = root / "GIT WORKTREES" / "rag-runtime-kernel"
        wt.mkdir(parents=True)
        (wt / "CLAUDE.md").write_text("# rendered\n", encoding="utf-8")
        return root, wt, store

    def test_a_two_tree_deployment_still_uses_its_worktree(self, tmp_path):
        _, wt, store = self._two_tree(tmp_path)
        got = _grand_audit_module()._kernel_tree(str(wt), str(store))
        assert Path(got) == wt

    def test_a_single_tree_deployment_uses_its_store(self, tmp_path):
        _, wt, store = self._single_tree(tmp_path)
        got = _grand_audit_module()._kernel_tree(str(wt), str(store))
        assert Path(got) == store, (
            "a deployment holding tests/ and formal/ in its store must not be "
            "reported as missing them"
        )

    def test_a_decoy_worktree_does_not_win_on_existence_alone(self, tmp_path):
        """The IMM worktree EXISTS; it just holds nothing. Existence is not fitness."""
        _, wt, store = self._single_tree(tmp_path)
        assert wt.is_dir()
        assert Path(_grand_audit_module()._kernel_tree(str(wt), str(store))) != wt

    def test_neither_tree_falls_back_to_the_conventional_place(self, tmp_path):
        """A genuine absence must still name where it looked."""
        root = tmp_path / "proj"
        wt = root / "GIT WORKTREES" / "rag-runtime-kernel"
        store = root / "RAG"
        wt.mkdir(parents=True)
        store.mkdir()
        got = _grand_audit_module()._kernel_tree(str(wt), str(store))
        assert Path(got) == wt

    def test_axis_one_probes_the_resolved_tree_not_the_worktree(self):
        """Pins the call sites, so a new probe cannot quietly re-assume."""
        text = (REPO / "scripts" / "grand_audit.py").read_text(
            encoding="utf-8", errors="replace")
        flat = text.replace(" ", "")
        assert 'self.have(A,"treeformal/",os.path.join(self.ktree,"formal")' in flat
        assert 'self.have(A,"treetests/",os.path.join(self.ktree,"tests")' in flat
        assert 'fd=os.path.join(self.ktree,"formal")' in flat, \
            "axis 8 must model-check the formal/ this deployment actually has"

    def test_a_missing_formal_dir_is_a_finding_not_an_exception(self):
        text = (REPO / "scripts" / "grand_audit.py").read_text(
            encoding="utf-8", errors="replace")
        assert "nothing to model-check" in text, (
            "pointing a probe at a missing directory produced a truncated "
            "[Errno 2]; it must ask first and report a sentence"
        )
