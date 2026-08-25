"""HANDLER-USES-AN-UNIMPORTED-MODULE-S209 — the class behind the dead verb.

``cmd_acceptance`` referenced ``subprocess.run`` and ``subprocess.TimeoutExpired``
with no ``import subprocess`` anywhere in scope. This module imports stdlib
modules PER FUNCTION — a deliberate style, and a fragile one: the import and its
use are separated by nothing but the author's attention, and one function was
missed. The verb was dead on every invocation from the day it was wired.

WHY NOTHING HERE CAUGHT IT, stated exactly, because the answer is the lesson:

* ``rag_kernel audit`` reads canonical STATE. It never invokes a verb, so a verb
  that dies on invocation is invisible to it by construction.
* ``scripts/grand_audit.py`` is the same shape one level up — file and state
  probes, not executions.
* The suite was green over it because every test of that verb imported the
  handler directly and hand-built a namespace. Nothing went through
  ``main(argv)``. That is TESTS-BYPASS-THE-CLI-S208.
* The S209 CLI-coverage census had ALREADY NAMED this verb: 65 verbs, 46 reached
  through the entry point, 19 not, and ``acceptance`` was one of the 19. The
  number was published and treated as the deliverable. A census converts an
  unknown into a KNOWN unknown; it does not execute anything. The defect lived
  in the gap between *counted* and *exercised*, and a clone deployment found it
  by doing the one thing that closes that gap: running it.

So this file asks the question statically, for EVERY handler at once, which is
cheaper than executing them and catches the defect at the moment it is typed: a
function that references ``<module>.<attr>`` must have that module importable in
its own scope — module-level, or imported inside the function.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

SOURCE = Path(inspect.getsourcefile(__import__("rag_kernel.__main__",
                                               fromlist=["x"]))).resolve()

#: Stdlib modules this kernel actually uses. Restricting to a named set is what
#: keeps the predicate decidable: ``args.rag`` is an attribute access too, and a
#: rule that flagged every ``X.y`` would flag the whole file and be switched off.
KNOWN_MODULES = {
    "argparse", "ast", "base64", "collections", "csv", "datetime", "difflib",
    "glob", "hashlib", "importlib", "io", "itertools", "json", "math", "os",
    "pathlib", "platform", "random", "re", "shutil", "socket", "sqlite3",
    "subprocess", "sys", "tempfile", "textwrap", "time", "traceback", "types",
    "urllib", "uuid", "zipfile",
}


def _module_level_bindings(tree: ast.Module) -> set[str]:
    out: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                out.add(alias.asname or alias.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out.add(t.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
    return out


def _bound_inside(fn: ast.AST) -> set[str]:
    """Everything the function itself binds: imports, assignments, args, loops."""
    out: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                out.add(alias.asname or alias.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            out.add(node.id)
        elif isinstance(node, ast.arg):
            out.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            out.add(node.name)
        elif isinstance(node, (ast.withitem,)) and isinstance(
                getattr(node, "optional_vars", None), ast.Name):
            out.add(node.optional_vars.id)
    return out


def _used_modules(fn: ast.AST) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id in KNOWN_MODULES:
                out.add(node.value.id)
    return out


def _offenders(path: "Path | None" = None) -> "list[tuple[str, str, int, str]]":
    target = Path(path) if path else SOURCE
    tree = ast.parse(target.read_text(encoding="utf-8"), filename=str(target))
    module_level = _module_level_bindings(tree)
    bad: list[tuple[str, str, int, str]] = []
    # Methods count too: a class body is not a scope its methods inherit names
    # from, so a method using an unimported module is the same defect.
    scopes: list[ast.AST] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scopes.append(node)
        elif isinstance(node, ast.ClassDef):
            scopes.extend(n for n in node.body
                          if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
    for node in scopes:
        local = _bound_inside(node)
        for mod in sorted(_used_modules(node)):
            if mod not in module_level and mod not in local:
                bad.append((node.name, mod, node.lineno, target.name))
    return bad


def _kernel_modules() -> "list[Path]":
    pkg = SOURCE.parent
    return sorted(p for p in pkg.glob("*.py") if "__pycache__" not in p.parts)


class TestEveryHandlerCanReachWhatItUses:
    def test_no_function_uses_a_module_it_cannot_import(self):
        """The generalisation of the dead-verb bug, asked of the whole module.

        REPAIR when this goes red: add ``import <module>`` inside the function
        (this file's house style) or at module level. Do NOT silence it — the
        function it names cannot execute, and no state audit will ever say so.
        """
        bad = _offenders()
        assert not bad, (
            "function(s) referencing a module with no import in scope — each one "
            "raises NameError on its first execution:\n"
            + "\n".join(f"  {f}:{ln}  {fn}() uses {mod}"
                        for fn, mod, ln, f in bad)
        )

    def test_no_kernel_module_anywhere_uses_an_unimported_module(self):
        """The same question asked of the WHOLE package, not just the CLI module.

        Scoping a guard to the file where the defect happened to surface is how a
        class comes back in the module next door. The S209 instance was in
        __main__.py; nothing made that the only possible home.
        """
        bad: list = []
        for path in _kernel_modules():
            bad.extend(_offenders(path))
        assert not bad, (
            "unimported-module reference(s) across rag_kernel/:\n"
            + "\n".join(f"  {f}:{ln}  {fn}() uses {mod}"
                        for fn, mod, ln, f in bad)
        )

    def test_the_package_scan_actually_scanned_something(self):
        mods = _kernel_modules()
        assert len(mods) > 10, [p.name for p in mods]

    def test_the_predicate_finds_a_planted_offender(self, tmp_path):
        """Guards against the scan silently matching nothing at all."""
        src = tmp_path / "m.py"
        src.write_text(
            "import json\n"
            "def cmd_good(a):\n"
            "    import subprocess\n"
            "    return subprocess.run(a)\n"
            "def cmd_bad(a):\n"
            "    return subprocess.run(a)\n"
            "def cmd_fine(a):\n"
            "    return json.dumps(a)\n", encoding="utf-8")
        tree = ast.parse(src.read_text(encoding="utf-8"))
        module_level = _module_level_bindings(tree)
        found = []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                local = _bound_inside(node)
                for mod in _used_modules(node):
                    if mod not in module_level and mod not in local:
                        found.append((node.name, mod))
        assert found == [("cmd_bad", "subprocess")], found

    def test_a_local_variable_shadowing_a_module_name_is_not_flagged(self, tmp_path):
        """GATE-FALSE-POSITIVE-ON-PROSE-S201, applied to code: a binding is a
        binding, whether it came from an import or an assignment."""
        src = tmp_path / "m.py"
        src.write_text(
            "def cmd_x(a):\n"
            "    time = a.clock\n"
            "    return time.now\n", encoding="utf-8")
        tree = ast.parse(src.read_text(encoding="utf-8"))
        node = tree.body[0]
        assert "time" in _bound_inside(node)

    @pytest.mark.parametrize("verb", ["acceptance"])
    def test_the_verb_the_field_found_is_covered_now(self, verb):
        """The instance that started it: the clone hit it, not this suite."""
        from rag_kernel import __main__ as m

        src = inspect.getsource(getattr(m, f"cmd_{verb}"))
        assert "import subprocess" in src
