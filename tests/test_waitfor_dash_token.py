"""WAITFOR-LEADING-DASH-ARGV (S198): the wait verb accepts dash-leading tokens.

``wait-for`` is the sanctioned replacement for polling. At the S198 boot the
obvious invocation --

    rag_kernel wait-for .boot/session_start_S198.log --contains "--attest"

-- died in argparse with "expected one argument", because ``--attest`` looks
like a flag. The agent fell back to polling, in a project whose most-repeated
error class (E-081 / E-116 / E-128) is polling. A primitive that cannot express
the token you most need to wait for does not replace anything.

These tests pin the fold at the argv layer, where the fix lives, so they hold
regardless of which subcommand later needs the same treatment.
"""

from __future__ import annotations

import pytest

from rag_kernel.__main__ import _fold_dash_values


def test_folds_dash_leading_contains_value():
    assert _fold_dash_values(
        ["wait-for", "f.log", "--contains", "--attest"]
    ) == ["wait-for", "f.log", "--contains=--attest"]


def test_folds_single_dash_value_too():
    assert _fold_dash_values(["wait-for", "f.log", "--contains", "-x"]) == [
        "wait-for", "f.log", "--contains=-x",
    ]


def test_leaves_ordinary_values_alone():
    argv = ["wait-for", "f.log", "--contains", "DONE", "--emit", "20"]
    assert _fold_dash_values(argv) == argv


def test_leaves_the_explicit_equals_form_alone():
    argv = ["wait-for", "f.log", "--contains=--attest"]
    assert _fold_dash_values(argv) == argv


def test_does_not_swallow_a_trailing_option():
    """``--contains`` with nothing after it must still be argparse's error."""
    argv = ["wait-for", "f.log", "--contains"]
    assert _fold_dash_values(argv) == argv


def test_does_not_touch_other_options():
    """Only options whose value may legitimately start with a dash are folded."""
    argv = ["wait-for", "f.log", "--timeout", "--emit", "20"]
    assert _fold_dash_values(argv) == argv


@pytest.mark.parametrize("argv", [None, []])
def test_empty_argv_is_a_noop(argv):
    assert _fold_dash_values(argv) == argv


def test_parser_accepts_the_folded_form():
    """End-to-end through the real parser, not just the helper."""
    from rag_kernel.__main__ import build_parser

    args = build_parser().parse_args(
        _fold_dash_values(["wait-for", "f.log", "--timeout", "5", "--contains", "--attest"])
    )
    assert args.contains == "--attest"
    assert args.timeout == 5.0


def test_parser_still_rejects_the_unfolded_form():
    """Proof the fold is load-bearing rather than decorative."""
    from rag_kernel.__main__ import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["wait-for", "f.log", "--timeout", "5", "--contains", "--attest"]
        )
