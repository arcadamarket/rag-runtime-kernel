"""REPORT-VERB-FIDELITY (S139): the report verb renders the canonical spec faithfully.

Covers the three S136 divergences fixed in S139:

  (a) section 2 is a planned-vs-actual ``# | Increment | Plan | Status | RAG |
      Commit-S`` table scoped to the CURRENT build's ``increments``, not a dump of
      every historical milestone/release;
  (b) the at-a-glance milestone cell names the milestone shipped THIS session
      instead of falling back to the bare "(no active milestone)";
  (c) a deployed package (no ``formal/RAGKernel.tla`` beside it) can reach a
      GREEN verdict by self-verifying its baked guard tables, so a genuinely-green
      released build no longer reads a false AMBER "unverified".

The honesty invariant is preserved throughout: an UNKNOWN gate still pulls to
AMBER (Rule 14). GREEN requires positive drift evidence — here, baked self-proof.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from rag_kernel import generated_guards as g
from rag_kernel import guardgen
from rag_kernel.drift_control import Increment, ItemKind, ItemStatus, TrackedItem
from rag_kernel.drift_render import _overall_rag, render_status_report
from rag_kernel.__main__ import _drift_gate_ok


_TLA = Path(guardgen.__file__).resolve().parent.parent / "formal" / "RAGKernel.tla"


# ---------------------------------------------------------------------------
# (c) baked self-verification — the false-AMBER fix
# ---------------------------------------------------------------------------

def test_committed_guards_self_verify():
    """The committed generated_guards.py proves its own guard integrity."""
    assert g.GUARDS_SELF_SHA256
    assert g.verify_self() is True


def test_self_hash_matches_generator_canonical_payload():
    """guardgen and the emitted runtime build byte-identical canonical payloads."""
    model = guardgen._load_model(_TLA)
    payload = guardgen.canonical_guard_payload(
        model.states,
        model.terminal_states,
        model.transitions,
        [(a.name, bool(a.takes_target)) for a in model.actions],
    )
    assert hashlib.sha256(payload.encode("utf-8")).hexdigest() == g.GUARDS_SELF_SHA256
    # and the runtime payload equals the generator payload verbatim
    assert g._guards_payload() == payload


def test_drift_gate_baked_path_true_without_tla(monkeypatch, tmp_path):
    """No reachable .tla: the gate self-verifies from baked provenance -> True."""
    # empty SOURCE_SHA256 forces the source-recompute path to be skipped, so only
    # the baked verify_self() evidence remains (the deployed-package condition).
    monkeypatch.setattr(g, "SOURCE_SHA256", "")
    rag = tmp_path / "RAG_MASTER.json"
    rag.write_text("{}", encoding="utf-8")
    assert _drift_gate_ok(rag) is True


def test_drift_gate_baked_path_false_on_tamper(monkeypatch, tmp_path):
    """A tampered deployed package fails the baked check -> False (never a fake GREEN)."""
    monkeypatch.setattr(g, "SOURCE_SHA256", "")
    monkeypatch.setattr(g, "verify_self", lambda: False)
    rag = tmp_path / "RAG_MASTER.json"
    rag.write_text("{}", encoding="utf-8")
    assert _drift_gate_ok(rag) is False


def test_drift_gate_source_path_still_true_in_worktree(tmp_path):
    """With the .tla reachable (dev worktree), the source-recompute path returns True."""
    assert _drift_gate_ok(tmp_path / "sub" / "RAG_MASTER.json") is True


# ---------------------------------------------------------------------------
# (a) section 2 — planned vs actual from the milestone's increments
# ---------------------------------------------------------------------------

def _milestone_with_increments():
    return TrackedItem(
        id="REPORT-VERB",
        title="Report verb fidelity",
        status=ItemStatus.IN_PROGRESS,
        kind=ItemKind.MILESTONE,
        session="S139",
        increments=(
            Increment(n="1", plan="self-hash guards", status="DONE",
                      rag="211", commit="aaa1111-S139"),
            Increment(n="2", plan="section 2 increments", status="in-progress",
                      rag="", commit=""),
        ),
    )


def test_section2_renders_increments_table():
    report = render_status_report([_milestone_with_increments()], session="S139")
    assert "### 2 · Build (planned vs actual)" in report
    assert "| # | Increment | Plan | Status | RAG | Commit-S |" in report
    assert "| 1 | 1 | self-hash guards | DONE | 211 | aaa1111-S139 |" in report
    # an empty increment field renders n/a, never blank (honesty)
    assert "| 2 | 2 | section 2 increments | in-progress | n/a | n/a |" in report
    # NOT the old historical dump header
    assert "Build (milestones & releases)" not in report


def test_section2_fallback_names_build_without_historical_dump():
    """A build with no increments is named plainly, not dumped as all history."""
    ms = TrackedItem(id="MS-X", title="Build X", status=ItemStatus.IN_PROGRESS,
                     kind=ItemKind.MILESTONE, session="S139")
    other = TrackedItem(id="MS-OLD", title="Old build", status=ItemStatus.RESOLVED,
                        kind=ItemKind.MILESTONE, session="S10")
    report = render_status_report([ms, other], session="S139")
    assert "Current build: MS-X — Build X" in report
    assert "no increments recorded" in report
    # the resolved historical milestone is NOT enumerated in section 2
    assert "MS-OLD" not in report.split("### 3")[0]


# ---------------------------------------------------------------------------
# (b) milestone cell fallback — name the shipped-this-session build
# ---------------------------------------------------------------------------

def test_milestone_cell_names_shipped_this_session():
    shipped = TrackedItem(id="MS-SHIP", title="Shipped build", status=ItemStatus.RESOLVED,
                          kind=ItemKind.MILESTONE, session="S139")
    report = render_status_report([shipped], session="S139")
    glance = report.split("### 2")[0]
    assert "MS-SHIP — Shipped build (shipped S139)" in glance
    assert "(no active milestone)" not in glance


def test_milestone_cell_bare_phrase_when_nothing_to_name():
    """No milestone/release at all still degrades honestly to the plain phrase."""
    task = TrackedItem(id="T", title="a task", status=ItemStatus.OPEN, session="S139")
    report = render_status_report([task], session="S139")
    assert "(no active milestone)" in report.split("### 2")[0]


# ---------------------------------------------------------------------------
# GREEN reachability + honesty invariant
# ---------------------------------------------------------------------------

def test_overall_green_when_drift_self_verified():
    assert _overall_rag(tests_ok=True, health_ok=True, drift_ok=True,
                        claims_ok=True, released=True) == "GREEN"


def test_overall_amber_when_drift_unknown():
    # the pre-fix false-AMBER condition: drift_ok None (no evidence) -> AMBER
    assert _overall_rag(tests_ok=True, health_ok=True, drift_ok=None,
                        claims_ok=True, released=True) == "AMBER"


def test_released_green_report_reads_green_end_to_end():
    report = render_status_report(
        [_milestone_with_increments()],
        session="S139",
        tests="1,720 green", tests_ok=True, health="20/20", health_ok=True,
        drift_sha="268149294421", drift_ok=True, released=True,
        release_ref="runtime-v0.4.31", claims_ok=True,
    )
    assert "🟢 GREEN" in report
    assert "GREEN — released" in report
