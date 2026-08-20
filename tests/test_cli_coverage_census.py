"""TESTS-BYPASS-THE-CLI-S208 — made countable instead of habitual.

A verb has two surfaces. ``cmd_x(args)`` is one; ``python -m rag_kernel x`` is the
other, and it is the only one an operator or an agent ever touches — it parses
arguments, builds the namespace, passes the seal guard and dispatches. A test
that imports ``cmd_x`` and hands it a hand-built namespace proves nothing about
that path. S208 marked an item RESOLVED over a build whose CLI died on an
undefined constant while 2907 tests were green, because every test of that verb
went around the entry point.

The general form is not "add a test for that verb". It is: the absence of an
entry-point test must be a NUMBER that a suite can refuse, not a habit someone
has to remember. ``scripts/cli_coverage_census.py`` produces the number by
pairing the dispatcher table against every test call that reaches ``main(argv)``
or a subprocess CLI; this module turns it into a gate.

THE BASELINE IS DEBT, NOT PERMISSION. 19 of 65 verbs were unreached when it was
measured at S209. Each line below is owed a test; the list may only shrink.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: Verbs with no test that reaches them through the entry point, measured S209.
#: THIS LIST MAY ONLY SHRINK. Adding to it is how a gate becomes a formality.
CLI_UNTESTED_BASELINE = {
    "acceptance", "adopt-preflight", "birth-adopt", "bootmap", "cite",
    "decide", "decisions", "deployment", "drain", "errlog-migrate",
    "forensics", "hook-guard", "inbox", "ingest", "migrate", "post",
    "push-check", "status", "tests",
}


def _census_module():
    path = REPO / "scripts" / "cli_coverage_census.py"
    spec = importlib.util.spec_from_file_location("cli_coverage_census", path)
    assert spec and spec.loader, path
    module = importlib.util.module_from_spec(spec)
    sys.modules["cli_coverage_census"] = module
    spec.loader.exec_module(module)
    return module


class TestTheCensusMeasuresTheRightThing:
    def test_it_finds_the_dispatcher_table(self):
        """A census that silently finds no verbs reports a clean tree."""
        verbs = _census_module().dispatcher_verbs(REPO)
        assert len(verbs) > 50, verbs
        for expected in ("audit", "session-start", "session-end", "resolve",
                         "checkpoint", "items"):
            assert expected in verbs

    def test_it_does_not_mistake_a_render_dict_for_the_dispatcher(self):
        """The first S209 run swallowed the forensics render dict and reported
        `gap_share` as an untested governed verb."""
        verbs = set(_census_module().dispatcher_verbs(REPO))
        for not_a_verb in ("gap_share", "double_sealed", "wall_seconds",
                           "session_ends", "invocations"):
            assert not_a_verb not in verbs

    def test_a_subprocess_cli_call_counts_as_coverage(self, tmp_path):
        mod = _census_module()
        (tmp_path / "rag_kernel").mkdir()
        (tmp_path / "rag_kernel" / "__main__.py").write_text(
            'commands = {' + ", ".join(f'"v{i}": cmd_v{i}' for i in range(12))
            + '}\n', encoding="utf-8")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_x.py").write_text(
            'subprocess.run([sys.executable, "-m", "rag_kernel", "v3"])\n'
            'main(["v7", "--dry-run"])\n', encoding="utf-8")
        result = mod.census(tmp_path)
        assert "v3" in result["covered"]
        assert "v7" in result["covered"]
        assert "v1" in result["uncovered"]


class TestTheDebtOnlyShrinks:
    def _uncovered(self) -> set:
        return set(_census_module().census(REPO)["uncovered"])

    def test_no_new_verb_bypasses_the_cli(self):
        """A verb added without an entry-point test turns this red.

        Repair: write one test that calls `main([...])` for it, or a subprocess
        CLI call. Do NOT add the verb to CLI_UNTESTED_BASELINE — the baseline is
        a record of debt measured once, not a place to put new debt.
        """
        new = self._uncovered() - CLI_UNTESTED_BASELINE
        assert not new, (
            "verb(s) with no test through `python -m rag_kernel`: "
            + ", ".join(sorted(new))
        )

    def test_the_baseline_does_not_rot(self):
        """A verb that gained a CLI test must leave the list."""
        stale = CLI_UNTESTED_BASELINE - self._uncovered()
        assert not stale, (
            "these verbs are CLI-tested now and must leave CLI_UNTESTED_BASELINE: "
            + ", ".join(sorted(stale))
        )

    def test_most_verbs_are_already_reached(self):
        """Guards against the census breaking open and passing vacuously."""
        result = _census_module().census(REPO)
        assert result["cli_tested"] >= 40, result["cli_tested"]
