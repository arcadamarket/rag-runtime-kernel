#!/usr/bin/env python
"""Census of ``getattr(ns, "name", default)`` reads that nothing ever supplies.

THE CLASS THIS MEASURES (INBOX-S208-001, deposited by S208 as a HYPOTHESIS with
no number behind it — this script is the number). Wherever this kernel copies
named scalars from one namespace onto another and reads them downstream with
``getattr(x, "name", default)``, a dropped field does not crash: it becomes a
plausible value. S208 fixed one instance — ``_close_report_ns`` silently dropped
``--no-auto-close-order`` — and three of S207's six defects were this same
mechanism. Nobody had measured how many other such reads exist.

WHAT IS DECIDABLE HERE, and what is not. The script finds every three-argument
``getattr`` with a literal name, then asks whether that name is SUPPLIED anywhere
in the tree by any of the four things that can supply it:

  * an ``argparse`` ``add_argument`` — either an explicit ``dest=`` or the name
    derived from the first long option, the way argparse itself derives it;
  * a keyword in an ``argparse.Namespace(...)`` or ``Namespace(...)`` call;
  * a direct attribute assignment ``obj.name = ...``;
  * a ``setattr(obj, "name", ...)`` with a literal name.

A name no supplier mentions is an UNSUPPLIED read: the default is the only value
that branch can ever see. That is a candidate defect, NOT a proven one — the
attribute may come from a dataclass, a dict-to-object shim, or a third-party
object. The list is short enough to read by hand, which is the point: this
converts "how many are there?" from an opinion into a bounded list.

FALSE-NEGATIVE, DECLARED: a name that IS supplied somewhere but not on the
particular path that reaches this read still reads as SUPPLIED here. This census
measures NAME coverage, not path coverage — the same limit ``gate_census.py``
declares for rule references, and for the same reason: a whole-program dataflow
analysis is a different tool than a triage list.

USAGE, from the RAG directory or the worktree::

    python scripts/forwarder_census.py
    python scripts/forwarder_census.py --root ../GIT\\ WORKTREES/rag-runtime-kernel
    python scripts/forwarder_census.py --json

Exit 0 when nothing is unsupplied, 1 when something is — so it can be a gate
later without being rewritten.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

#: Directories walked for kernel sources. Tests are excluded on purpose: a test
#: builds deliberately partial namespaces, which is exactly what this looks for.
SOURCE_DIRS = ("rag_kernel", "scripts", "tools")


def _iter_sources(root: Path):
    for rel in SOURCE_DIRS:
        base = root / rel
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            yield path


def _dest_from_option(option: str) -> str | None:
    """argparse's own rule: the first long option, dashes to underscores."""
    if option.startswith("--"):
        return option[2:].replace("-", "_")
    return None


class _Visitor(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.reads: list[tuple[str, int]] = []
        self.supplied: set[str] = set()

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        func = node.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)

        if name == "getattr" and len(node.args) == 3:
            key = node.args[1]
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                self.reads.append((key.value, node.lineno))

        elif name == "setattr" and len(node.args) >= 2:
            key = node.args[1]
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                self.supplied.add(key.value)

        elif name == "add_argument":
            explicit = None
            for kw in node.keywords:
                if kw.arg == "dest" and isinstance(kw.value, ast.Constant):
                    explicit = str(kw.value.value)
            if explicit:
                self.supplied.add(explicit)
            else:
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        derived = _dest_from_option(arg.value)
                        if derived:
                            self.supplied.add(derived)
                        elif not arg.value.startswith("-"):
                            # a positional: its dest IS the name
                            self.supplied.add(arg.value.replace("-", "_"))

        elif name == "add_subparsers":
            # `add_subparsers(dest="context_action")` supplies that name on every
            # parsed namespace. Missing this read as an unsupplied forwarder in
            # the first S209 run — a false positive costs the reader's trust as
            # surely as a false negative costs the measurement.
            for kw in node.keywords:
                if kw.arg == "dest" and isinstance(kw.value, ast.Constant):
                    self.supplied.add(str(kw.value.value))

        elif name == "Namespace":
            for kw in node.keywords:
                if kw.arg:
                    self.supplied.add(kw.arg)

        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        for target in node.targets:
            if isinstance(target, ast.Attribute):
                self.supplied.add(target.attr)
        self.generic_visit(node)


def _declared_names(tree: ast.Module) -> set[str]:
    """Names a module itself defines: module globals, functions, classes, and
    class-body fields (which is how a dataclass declares its attributes).

    Without this, ``getattr(mod, "__version__", None)`` and
    ``getattr(finding, "severity", "")`` read as unsupplied forwarders. They are
    not: the first is a module global, the second a dataclass field. A census
    that cries wolf on thirteen entries to find one is not a triage list.
    """
    found: set[str] = set()

    def _from_body(body) -> None:
        for node in body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        found.add(target.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                found.add(node.target.id)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.add(node.name)
            elif isinstance(node, ast.ClassDef):
                found.add(node.name)
                _from_body(node.body)

    _from_body(tree.body)
    return found


def census(root: Path) -> dict:
    reads: dict[str, list[str]] = {}
    supplied: set[str] = set()
    files = 0
    for path in _iter_sources(root):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            continue
        files += 1
        visitor = _Visitor(path)
        visitor.visit(tree)
        supplied |= visitor.supplied
        supplied |= _declared_names(tree)
        for key, line in visitor.reads:
            reads.setdefault(key, []).append(
                f"{path.relative_to(root).as_posix()}:{line}"
            )

    unsupplied = {k: v for k, v in sorted(reads.items()) if k not in supplied}
    return {
        "files_scanned": files,
        "distinct_names_read_with_a_default": len(reads),
        "total_reads": sum(len(v) for v in reads.values()),
        "distinct_names_supplied": len(supplied),
        "unsupplied": unsupplied,
    }


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent,
                    help="tree to walk (default: the parent of this script's directory)")
    ap.add_argument("--json", dest="as_json", action="store_true",
                    help="emit the census as JSON")
    args = ap.parse_args(argv)

    result = census(Path(args.root))
    if args.as_json:
        print(json.dumps(result, indent=2))
        return 1 if result["unsupplied"] else 0

    print(f"FORWARDER CENSUS over {args.root}")
    print(f"  files scanned                 : {result['files_scanned']}")
    print(f"  getattr(..., name, default)   : {result['total_reads']} read(s), "
          f"{result['distinct_names_read_with_a_default']} distinct name(s)")
    print(f"  names supplied somewhere      : {result['distinct_names_supplied']}")
    print(f"  UNSUPPLIED names              : {len(result['unsupplied'])}")
    for name, sites in result["unsupplied"].items():
        print(f"    {name}")
        for site in sites[:6]:
            print(f"      read at {site}")
        if len(sites) > 6:
            print(f"      … and {len(sites) - 6} more read(s)")
    if not result["unsupplied"]:
        print("  every name read with a default is supplied by something.")
    return 1 if result["unsupplied"] else 0


if __name__ == "__main__":
    sys.exit(main())
