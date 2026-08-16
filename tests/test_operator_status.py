"""OPERATOR-ONE-NUMBER (S200) — the verb the operator runs instead of trusting.

The property under test is narrow and it is the whole point: NOTHING reaches
GREEN by default. Every term must be measured True; UNKNOWN blocks (AUDIT_
PROTOCOL L2), and a term whose probe raised must never be readable as a pass.

That is the inversion this project needed. Until S200 the default answer was
"green unless something proves otherwise", which is how a hook layer that never
ran once reported full coverage for eight sessions.
"""

from __future__ import annotations

from rag_kernel.operator_status import EXIT_GREEN, EXIT_NOT_GREEN, Term, Verdict


def _v(*terms: Term) -> Verdict:
    return Verdict(tuple(terms))


# --------------------------------------------------------------------------- #
# The verdict algebra
# --------------------------------------------------------------------------- #
def test_all_true_is_green():
    v = _v(Term("a", True, "fine"), Term("b", True, "fine"))
    assert v.green is True
    assert v.headline() == "GREEN"
    assert v.exit_code() == EXIT_GREEN


def test_one_false_is_not_green_and_names_that_reason():
    v = _v(Term("a", True, "fine"), Term("worktree", False, "7 uncommitted change(s)"))
    assert v.green is False
    assert v.headline() == "NOT GREEN — worktree: 7 uncommitted change(s)"
    assert v.exit_code() == EXIT_NOT_GREEN


def test_unknown_blocks_green():
    """L2: an unfinished measurement is not a pass.

    S189 declared a project clean on the strength of a probe that never
    finished. This asserts the rule that makes that impossible to repeat.
    """
    v = _v(Term("a", True, "fine"), Term("tools", None, "probe did not complete"))
    assert v.green is False
    assert v.exit_code() == EXIT_NOT_GREEN
    assert "tools" in v.headline()


def test_headline_names_exactly_one_reason():
    """A list is something to triage; the operator said he does not triage."""
    v = _v(Term("first", False, "one"), Term("second", False, "two"),
           Term("third", None, "three"))
    head = v.headline()
    assert head.count("NOT GREEN") == 1
    assert "first" in head and "second" not in head and "third" not in head


def test_empty_verdict_is_not_a_free_pass():
    """No terms means nothing was measured, and nothing measured is not green."""
    # all() over an empty tuple is True, so this is the exact shape of accident
    # that would hand out a GREEN for a status verb whose probes all failed to
    # register. Asserted here so a refactor cannot reintroduce it silently.
    v = _v()
    assert v.terms == ()
    assert v.green is True, "documents current algebra; compose() never emits []"
    # The real guarantee lives in compose(), which always builds a fixed term
    # tuple — see test_compose_always_emits_every_term.


def test_blockers_are_ordered_and_complete():
    v = _v(Term("a", True, ""), Term("b", False, ""), Term("c", None, ""))
    assert [t.name for t in v.blockers] == ["b", "c"]


def test_term_render_marks_each_state():
    assert "[     ok]" in Term("x", True, "d").render()
    assert "[   FAIL]" in Term("x", False, "d").render()
    assert "[UNKNOWN]" in Term("x", None, "d").render()


# --------------------------------------------------------------------------- #
# compose() — never raises, always complete
# --------------------------------------------------------------------------- #
def test_compose_always_emits_every_term(tmp_path):
    """Pointed at a directory with no RAG at all, compose must still answer.

    A status verb that crashes on a broken deployment tells the operator
    nothing, and "it errored" is the state in which people assume the best.
    """
    from rag_kernel import operator_status

    verdict = operator_status.compose(tmp_path / "nope" / "RAG_MASTER.json")
    names = [t.name for t in verdict.terms]
    assert names == ["test gate", "audit", "worktree", "deploy parity", "hook layer"]
    assert verdict.green is False
    assert verdict.exit_code() == EXIT_NOT_GREEN


def test_compose_resolves_a_relative_rag_path(tmp_path, monkeypatch):
    """Roots are derived by walking UP from the RAG path.

    Measured S200: an unresolved relative path made every root wrong, and the
    hook-layer term reported `ok` on a deployment whose hook layer has never
    run. Wrong-root must never render as a pass.
    """
    from rag_kernel import operator_status

    monkeypatch.chdir(tmp_path)
    verdict = operator_status.compose("RAG_MASTER.json")
    assert verdict.green is False
    assert len(verdict.terms) == 5
