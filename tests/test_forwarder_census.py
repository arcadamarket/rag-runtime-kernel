"""WHITELIST-FORWARDER-CLASS-S209 — the class S208 named, measured and gated.

S208 fixed ONE instance: ``_close_report_ns`` copied named scalars onto a new
namespace and silently dropped ``--no-auto-close-order``, which downstream read
back as its default. Nothing crashed — a dropped field became a plausible value,
and three of S207's six defects were this same mechanism. S208 deposited the
generalisation as an inbox note rather than a tracked item, precisely because it
was a hypothesis with no number behind it, and named the measurement that would
settle it: find the ``getattr(x, "name", default)`` reads, pair each with the
thing meant to supply that name, report what nothing supplies.

S209 ran it (``scripts/forwarder_census.py``): 161 defaulted reads over 78
distinct names, and ONE genuine instance — ``_session_start_phase1`` had read
``getattr(args, "no_auto_reconcile", False)`` since S184 while no argument ever
supplied it, so the carry-forward gate's DERIVED-state repair could not be
switched off by anyone. The flag now exists.

These tests keep both halves from decaying: the escape hatch stays reachable, and
the census stays at zero unexplained names. A script that must be REMEMBERED is a
hope; run from the suite, it is a gate.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

#: Names read with a default that no supplier in this tree provides, and are
#: nonetheless correct: each is an attribute of a FOREIGN object, so no
#: assignment here can ever name it. Every entry needs its reason in this list —
#: an unexplained addition is the defect this module exists to catch.
EXPLAINED_UNSUPPLIED = {
    # an `ast` node attribute, probed inside the census itself
    "attr",
    # `sys.stdout.reconfigure`, absent on some stream implementations
    "reconfigure",
}


def _census_module():
    path = REPO / "scripts" / "forwarder_census.py"
    spec = importlib.util.spec_from_file_location("forwarder_census", path)
    assert spec and spec.loader, path
    module = importlib.util.module_from_spec(spec)
    sys.modules["forwarder_census"] = module
    spec.loader.exec_module(module)
    return module


class TestTheEscapeHatchIsReachable:
    def test_no_auto_reconcile_can_actually_be_set(self):
        """The exact defect: the read existed, the flag did not."""
        from rag_kernel.__main__ import build_parser

        args = build_parser().parse_args(["session-start", "--no-auto-reconcile"])
        assert args.no_auto_reconcile is True

    def test_it_defaults_to_letting_the_gate_repair(self):
        from rag_kernel.__main__ import build_parser

        args = build_parser().parse_args(["session-start"])
        assert args.no_auto_reconcile is False, (
            "GATE-AUTO-RECONCILE stays the default; the flag only makes the "
            "documented opt-out reachable"
        )


class TestTheClassStaysMeasured:
    def test_every_unsupplied_forwarder_is_explained(self):
        """A new unexplained name here IS a new instance of the S208 class.

        The fix is either to supply the name — add the argument, pass the keyword
        — or to add it to EXPLAINED_UNSUPPLIED with the reason it is a foreign
        object's attribute. Deleting the assertion is not one of the options.
        """
        result = _census_module().census(REPO)
        unexplained = set(result["unsupplied"]) - EXPLAINED_UNSUPPLIED
        assert not unexplained, (
            "unsupplied getattr forwarder(s) with no explanation: "
            + ", ".join(f"{n} ({result['unsupplied'][n][0]})"
                        for n in sorted(unexplained))
        )

    def test_the_census_actually_scanned_something(self):
        """A census that silently scans nothing reports a clean tree."""
        result = _census_module().census(REPO)
        assert result["files_scanned"] > 30
        assert result["total_reads"] > 50

    def test_the_explained_list_does_not_rot(self):
        """An entry that is no longer unsupplied must leave the list."""
        result = _census_module().census(REPO)
        stale = EXPLAINED_UNSUPPLIED - set(result["unsupplied"])
        assert not stale, (
            f"these names are supplied now and no longer need an exemption: "
            f"{', '.join(sorted(stale))}"
        )


class TestTheCensusReadsTheSuppliers:
    @pytest.mark.parametrize("source,name,unsupplied", [
        ('import argparse\nx = getattr(a, "flag", None)\n', "flag", True),
        ('import argparse\np.add_argument("--flag")\n'
         'x = getattr(a, "flag", None)\n', "flag", False),
        ('p.add_argument("-f", dest="flag")\nx = getattr(a, "flag", None)\n',
         "flag", False),
        ('p.add_subparsers(dest="flag")\nx = getattr(a, "flag", None)\n',
         "flag", False),
        ('import argparse\nargparse.Namespace(flag=1)\n'
         'x = getattr(a, "flag", None)\n', "flag", False),
        ('a.flag = 1\nx = getattr(a, "flag", None)\n', "flag", False),
        ('setattr(a, "flag", 1)\nx = getattr(a, "flag", None)\n', "flag", False),
        ('flag = 1\nx = getattr(m, "flag", None)\n', "flag", False),
    ])
    def test_each_supplier_shape_is_recognised(self, tmp_path, source, name,
                                               unsupplied):
        pkg = tmp_path / "rag_kernel"
        pkg.mkdir()
        (pkg / "mod.py").write_text(source, encoding="utf-8")
        result = _census_module().census(tmp_path)
        assert (name in result["unsupplied"]) is unsupplied

    def test_a_two_argument_getattr_is_not_a_forwarder(self, tmp_path):
        """Without a default it RAISES on a dropped field — that is the safe form
        and the whole point of the class is that the three-argument form does not."""
        pkg = tmp_path / "rag_kernel"
        pkg.mkdir()
        (pkg / "mod.py").write_text('x = getattr(a, "flag")\n', encoding="utf-8")
        result = _census_module().census(tmp_path)
        assert result["unsupplied"] == {}
