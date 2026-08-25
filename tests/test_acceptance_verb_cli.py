"""ACCEPTANCE-VERB-NEVER-RAN-S209 — the verb that could not execute once, ever.

``cmd_acceptance`` called ``subprocess.run`` and caught
``subprocess.TimeoutExpired`` while ``subprocess`` was never imported in its
scope. This module imports it per-function everywhere else; this one function was
missed. Both the call and the except clause raised ``NameError``, so the verb was
dead on every invocation since it was wired in S190.

WHY A GREEN SUITE NEVER SAW IT — and this is the part worth keeping. Every test
of acceptance reached ``cmd_acceptance`` or ``scripts/acceptance_check.py``
directly. Nothing went through ``main(argv)``, so nothing ever built the
namespace, dispatched, and executed the body. That is TESTS-BYPASS-THE-CLI-S208
in its purest form, and the S209 CLI-coverage census had already listed
``acceptance`` among the 19 verbs no test reached through the entry point. The
census named it; the field found it. A clone deployment hit the crash and
reported it as E-IMM-033.

So the fix is one import, and the guard is this file: a test that goes through
``main(argv)`` and would have caught it on the day it was written. It also
removes ``acceptance`` from CLI_UNTESTED_BASELINE in
tests/test_cli_coverage_census.py, which is what shrinking that debt looks like.
"""

from __future__ import annotations

import pytest

from rag_kernel.__main__ import main


class TestAcceptanceReachesItsBody:
    def test_the_verb_dispatches_without_a_name_error(self, tmp_path, capsys):
        """The regression proper: drive it through the entry point.

        A missing checker script is the cheapest way to prove the body RAN --
        that message is printed from inside ``cmd_acceptance``, after the
        argument namespace was built and dispatched. Before the fix this call
        died with NameError before reaching any branch.
        """
        rag = tmp_path / "RAG_MASTER.json"
        rag.write_text("{}", encoding="utf-8")
        rc = main(["acceptance", "--rag", str(rag),
                   "--script", str(tmp_path / "nope.py")])
        assert rc == 1
        err = capsys.readouterr().err
        assert "acceptance checker not found" in err, err
        assert "NameError" not in err

    def test_subprocess_is_in_scope_for_the_handler(self):
        """Pins the actual defect rather than only its symptom."""
        import inspect

        from rag_kernel import __main__ as m

        src = inspect.getsource(m.cmd_acceptance)
        assert "import subprocess" in src, (
            "cmd_acceptance uses subprocess.run and subprocess.TimeoutExpired; "
            "this module imports subprocess per-function, so the handler must"
        )

    def test_it_runs_a_checker_that_exists(self, tmp_path, capsys):
        """Exercise the success path end to end through the CLI."""
        script = tmp_path / "checker.py"
        script.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
        rag = tmp_path / "RAG_MASTER.json"
        rag.write_text("{}", encoding="utf-8")
        rc = main(["acceptance", "--rag", str(rag), "--script", str(script)])
        assert rc == 0

    def test_a_failing_checker_propagates_its_exit_code(self, tmp_path):
        script = tmp_path / "checker.py"
        script.write_text("import sys\nsys.exit(3)\n", encoding="utf-8")
        rag = tmp_path / "RAG_MASTER.json"
        rag.write_text("{}", encoding="utf-8")
        assert main(["acceptance", "--rag", str(rag), "--script", str(script)]) == 3


class TestTheClassNotJustTheInstance:
    @pytest.mark.parametrize("name", ["cmd_acceptance"])
    def test_every_handler_naming_subprocess_can_reach_it(self, name):
        """The same shape, asked of the whole module rather than one function.

        A handler that references ``subprocess`` must either import it in its own
        body or be covered by a module-level import. Neither existed here, and
        nothing asked. Now something does.
        """
        import inspect

        from rag_kernel import __main__ as m

        module_src = inspect.getsource(m)
        module_level = any(
            line.strip() == "import subprocess"
            for line in module_src.splitlines()
            if line and not line[0].isspace()
        )
        src = inspect.getsource(getattr(m, name))
        if "subprocess." in src:
            assert module_level or "import subprocess" in src, (
                f"{name} references subprocess with no import in scope"
            )
