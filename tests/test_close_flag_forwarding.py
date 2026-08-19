"""CLOSE-FLAG FORWARDING — a flag the parser accepts must reach the code that reads it.

CLOSE-ESCAPE-HATCH-IS-INERT-S207, measured twice in a row: a close refused by the
grand audit printed "re-run the close with --no-auto-close-order and say in the
handoff which axis was accepted red and why". The re-run with that exact flag
rendered CLAUDE.md, committed a new HEAD, re-measured the suite and refused on the
identical axes -- the close order had not been skipped at all.

ROOT CAUSE, and it is the reusable part: `_close_report_ns` is a hand-maintained
WHITELIST that copies named scalars from the parsed args onto the namespace the
close actually consumes. `--no-auto-close-order` was registered on the parser and
never added to that list, so `_close_order_prepare` read it with a `getattr(...,
False)` default and ran the close order regardless. argparse accepted the flag,
`--help` advertised it, and nothing anywhere disagreed.

WHY THIS CLASS DESERVES A TEST RATHER THAN A CAREFUL AUTHOR: the failure is silent
and it is on the RECOVERY path, so it surfaces only when something has already gone
wrong -- and it removes the one action the refusal told the operator to take. Since
the axis that refusal pointed around was red by construction, nothing an agent or the
operator could type would seal the session. A defaulted `getattr` turns a dropped
field into a plausible value instead of a crash, which is why review does not catch it.

Applying the project's standard: could a tired agent add a tenth close flag, wire it
into the parser, read it downstream and ship it inert? Before this test, yes -- that
is the measured history. The list below is the current set of behaviour-changing
close flags; each one is asserted to survive the hop.
"""
from __future__ import annotations

import argparse

import pytest

from rag_kernel.__main__ import _close_report_ns

#: Flags that CHANGE WHAT THE CLOSE DOES, as opposed to what it prints. Each must
#: survive the hop from parsed args onto the close namespace. Add a flag here in the
#: same commit that adds it to the parser -- that is the whole point of this file.
BEHAVIOUR_FLAGS = (
    "no_auto_close_order",
    "accept_conduct",
    "no_errors",
    "no_report",
    "force",
)


@pytest.mark.parametrize("flag", BEHAVIOUR_FLAGS)
def test_behaviour_flag_survives_the_hop(flag: str) -> None:
    args = argparse.Namespace(**{f: True for f in BEHAVIOUR_FLAGS})
    ns = _close_report_ns("S999", args)
    assert hasattr(ns, flag), (
        f"_close_report_ns dropped {flag!r}: it is a hand-maintained whitelist and "
        f"this flag is not on it. Downstream reads it with a getattr default, so the "
        f"flag is accepted by the parser, advertised by --help, and INERT."
    )
    assert getattr(ns, flag) is True, (
        f"_close_report_ns carried {flag!r} but not its value "
        f"({getattr(ns, flag)!r}) -- the flag reaches the close as a default."
    )


@pytest.mark.parametrize("flag", BEHAVIOUR_FLAGS)
def test_behaviour_flag_defaults_are_not_invented(flag: str) -> None:
    """An UNSET flag must not arrive as True: a silent opt-in is the mirror defect."""
    ns = _close_report_ns("S999", argparse.Namespace())
    assert getattr(ns, flag, None) is not True, (
        f"{flag!r} defaults to True when the caller never passed it -- the close "
        f"would take the escape path nobody asked for."
    )


def test_no_auto_close_order_is_the_regression_case() -> None:
    """The measured S207 case, pinned by name so a rename cannot quietly drop it."""
    on = _close_report_ns("S999", argparse.Namespace(no_auto_close_order=True))
    off = _close_report_ns("S999", argparse.Namespace(no_auto_close_order=False))
    assert on.no_auto_close_order is True and off.no_auto_close_order is False, (
        "the close order escape hatch does not reach _close_order_prepare; a grand "
        "audit red by construction then leaves NO reachable seal for agent or operator"
    )
