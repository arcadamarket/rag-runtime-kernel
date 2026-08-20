#!/usr/bin/env python
"""Which governed verbs are tested THROUGH the CLI, and which only by import.

TESTS-BYPASS-THE-CLI-S208, in its general form rather than as one fix. A verb has
two surfaces: the function ``cmd_x(args)``, and the path an operator or an agent
actually takes — ``python -m rag_kernel x ...``, which parses arguments, builds a
namespace, passes the seal guard and dispatches. A test that imports ``cmd_x``
and hands it a hand-built namespace exercises the first and says nothing about
the second. S208 marked an item RESOLVED over a build whose CLI died on an
undefined constant, and a 2907-green suite did not show it, because every test of
that verb went around the entry point.

WHAT THIS MEASURES, decidably. It reads the dispatcher table in
``rag_kernel/__main__.py`` — the single dict that maps every verb to its handler,
so the verb list cannot drift from reality — and then reads every test module for
calls that go through the entry point:

  * ``main([...])`` / ``__main__.main([...])`` with a literal first element;
  * ``subprocess.run([..., "-m", "rag_kernel", "<verb>", ...])`` and the same
    shape via ``check_output`` / ``Popen``;
  * ``runpy``-style invocations naming the module and a literal verb.

A verb no such call names is reported as CLI-UNTESTED. That is not a claim the
verb is broken; it is a claim that nothing would notice if it were.

DECLARED LIMIT, in the manner of gate_census.py: this measures whether the ENTRY
POINT is exercised for a verb, not whether the exercise is meaningful. A test
that calls ``main(["audit", "--help"])`` counts. Depth is a reviewer's judgement;
presence is a machine's, and presence is what was missing.

USAGE, from the worktree or the RAG::

    python scripts/cli_coverage_census.py
    python scripts/cli_coverage_census.py --json
    python scripts/cli_coverage_census.py --covered      # the other half

Exit 0 when every verb is reached through the CLI, 1 otherwise, so it can become
a gate without being rewritten.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

#: The module whose dispatcher table defines the verb set.
DISPATCH_MODULE = ("rag_kernel", "__main__.py")

#: Names that, when called, mean "this went through the entry point".
_ENTRY_CALLS = {"main", "run", "check_output", "check_call", "Popen", "call"}


def _repo_root(start: Path) -> Path:
    """The tree holding both rag_kernel/ and tests/, from either checkout."""
    for base in [start, *start.parents]:
        if (base / DISPATCH_MODULE[0] / DISPATCH_MODULE[1]).is_file() and \
                (base / "tests").is_dir():
            return base
    return start


def dispatcher_verbs(root: Path) -> "list[str]":
    """Every key of the dispatcher dict in ``main()`` — the real verb set."""
    path = root / DISPATCH_MODULE[0] / DISPATCH_MODULE[1]
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    verbs: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = [k for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)]
        # The dispatcher is the one dict whose every value is a BARE callable
        # name — `cmd_audit`, `_cmd_run_detach_await` — and whose keys are verb
        # strings. Attribute values are deliberately excluded: the first S209 run
        # allowed them and swallowed the forensics render dict, whose values are
        # `facts.gap_share` and friends, reporting `gap_share` and ten other
        # field names as untested governed verbs. A census that reports things
        # that are not verbs teaches the reader to skim it.
        if len(keys) != len(node.keys) or len(keys) < 10:
            continue
        if not all(isinstance(v, ast.Name) for v in node.values):
            continue
        verbs |= {k.value for k in keys}
    return sorted(verbs)


def _literal_strings(node: ast.AST) -> "list[str]":
    if isinstance(node, (ast.List, ast.Tuple)):
        return [e.value for e in node.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    return []


def cli_invoked_verbs(root: Path, verbs: "set[str]") -> "dict[str, list[str]]":
    """verb -> the test files that reach it through the entry point."""
    hits: dict[str, list[str]] = {}
    for path in sorted((root / "tests").rglob("test_*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            continue
        rel = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name not in _ENTRY_CALLS or not node.args:
                continue
            argv = _literal_strings(node.args[0])
            if not argv:
                continue
            # `main(["audit", ...])` puts the verb first; a subprocess argv puts
            # it after the interpreter and -m module, so scan the whole list and
            # take every element that IS a verb. A false match would need a test
            # to pass a verb name as an unrelated literal in the same argv.
            for token in argv:
                if token in verbs:
                    hits.setdefault(token, [])
                    if rel not in hits[token]:
                        hits[token].append(rel)
    return hits


def census(root: Path) -> dict:
    verbs = dispatcher_verbs(root)
    hits = cli_invoked_verbs(root, set(verbs))
    uncovered = [v for v in verbs if v not in hits]
    return {
        "root": str(root),
        "verbs": len(verbs),
        "cli_tested": len(hits),
        "uncovered": uncovered,
        "covered": {v: hits[v] for v in sorted(hits)},
    }


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=None,
                    help="tree holding rag_kernel/ and tests/ (default: resolved "
                         "upward from this script)")
    ap.add_argument("--json", dest="as_json", action="store_true")
    ap.add_argument("--covered", action="store_true",
                    help="list the verbs that ARE reached, with their test files")
    args = ap.parse_args(argv)

    root = Path(args.root) if args.root else _repo_root(Path(__file__).resolve().parent)
    result = census(root)

    if args.as_json:
        print(json.dumps(result, indent=2))
        return 1 if result["uncovered"] else 0

    print(f"CLI COVERAGE CENSUS over {result['root']}")
    print(f"  governed verbs in the dispatcher : {result['verbs']}")
    print(f"  reached through the entry point  : {result['cli_tested']}")
    print(f"  CLI-UNTESTED                     : {len(result['uncovered'])}")
    if args.covered:
        for verb, files in result["covered"].items():
            print(f"    {verb:<24} {', '.join(files[:3])}")
    else:
        for verb in result["uncovered"]:
            print(f"    {verb}")
    if not result["uncovered"]:
        print("  every governed verb is exercised through the CLI.")
    return 1 if result["uncovered"] else 0


if __name__ == "__main__":
    sys.exit(main())
