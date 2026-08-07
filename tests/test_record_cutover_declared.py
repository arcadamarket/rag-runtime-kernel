"""INFERENCE-KIND-LATENT-COUPLING (S169 opened, S188 defused).

``check_record_coverage`` gates itself per record kind, and that gate used to be
purely implicit: the FIRST non-retired item of a kind switched coverage on for EVERY
legacy record of that kind. Banking one honest ``kind=ERROR`` finding therefore
demanded canonical items for all 44 legacy ``E-###`` headings at once and turned a
green audit red — which is why the S187 handoff had to carry a prose warning telling
the next session to bank findings as ``kind=TASK`` instead. A rule you must remember
not to trip is a trap, not a rule.

The cutover is now DECLARABLE via ``meta.record_cutover``. These tests pin both
halves: the declaration wins where present, and its ABSENCE preserves the historical
implicit behaviour byte-for-byte, so no existing deployment changes verdict.
"""

from __future__ import annotations

import pytest

from rag_kernel.drift_audit import ERROR, check_record_coverage


# check_record_coverage matches MARKDOWN HEADINGS (``^#+\\s*E-\\d+``), so the fixture
# uses that shape deliberately — testing the gate against a form the scanner cannot
# see would pin nothing at all.
ERRLOG = """# Error Log

## E-001 (S1): first thing -- happened

## E-002 (S2): second thing -- also happened
"""


@pytest.fixture()
def errlog(tmp_path):
    p = tmp_path / "ERROR_LOG.md"
    p.write_text(ERRLOG, encoding="utf-8")
    return p


def _hot(items=(), cutover=None):
    hot = {
        "tracked_items": [
            {"id": i, "title": i, "status": "OPEN", "kind": k, "session": "S1",
             "note": "", "superseded_by": None, "history": []}
            for i, k in items
        ],
        "meta": {"written_by_session": "S188"},
        "inference_ledger": [],
    }
    if cutover is not None:
        hot["meta"]["record_cutover"] = cutover
    return hot


class TestImplicitFallback:
    """Absence of a declaration must behave exactly as before."""

    def test_no_error_items_means_no_coverage_enforcement(self, errlog):
        assert check_record_coverage(_hot(), error_log_path=errlog) == []

    def test_one_error_item_still_latches_the_gate_when_undeclared(self, errlog):
        """The historical behaviour, preserved: this is the trap, undeclared."""
        f = check_record_coverage(
            _hot([("E-001", "ERROR")]), error_log_path=errlog
        )
        assert [x.item_id for x in f] == ["E-002"]
        assert f[0].severity == ERROR


class TestDeclaredCutover:
    def test_declared_false_keeps_the_gate_shut_despite_an_error_item(self, errlog):
        """The fix: banking one ERROR finding no longer indicts 44 legacy records."""
        f = check_record_coverage(
            _hot([("E-001", "ERROR")], cutover={"ERROR": False}),
            error_log_path=errlog,
        )
        assert f == []

    def test_declared_true_enforces_even_with_no_error_items_yet(self, errlog):
        """A deployment that says migration is done is held to it immediately."""
        f = check_record_coverage(
            _hot(cutover={"ERROR": True}), error_log_path=errlog
        )
        assert sorted(x.item_id for x in f) == ["E-001", "E-002"]

    def test_declared_true_and_fully_migrated_is_clean(self, errlog):
        f = check_record_coverage(
            _hot([("E-001", "ERROR"), ("E-002", "ERROR")], cutover={"ERROR": True}),
            error_log_path=errlog,
        )
        assert f == []

    def test_kinds_are_independent(self, errlog):
        """Declaring ERROR must not move the INFERENCE gate, or vice versa."""
        hot = _hot([("E-001", "ERROR")], cutover={"ERROR": False})
        hot["inference_ledger"] = [{"id": "INS-1"}]
        hot["tracked_items"].append(
            {"id": "INS-2", "title": "x", "status": "OPEN", "kind": "INFERENCE",
             "session": "S1", "note": "", "superseded_by": None, "history": []}
        )
        f = check_record_coverage(hot, error_log_path=errlog)
        assert [x.item_id for x in f] == ["INS-1"], \
            "the INFERENCE gate is still implicit and still fires"

    def test_malformed_declaration_falls_back_to_implicit(self, errlog):
        f = check_record_coverage(
            _hot([("E-001", "ERROR")], cutover="yes please"),
            error_log_path=errlog,
        )
        assert [x.item_id for x in f] == ["E-002"]

    def test_declaration_for_another_kind_does_not_leak(self, errlog):
        f = check_record_coverage(
            _hot([("E-001", "ERROR")], cutover={"INFERENCE": False}),
            error_log_path=errlog,
        )
        assert [x.item_id for x in f] == ["E-002"]
