"""Tests for the BIRTH-ADOPT verb (S181).

The behaviours worth pinning are the ones the verb exists to guarantee:
  * direction is DECIDED, with a stated reason, or REFUSED — never guessed;
  * the spec resolves direction for keys with no provenance (the eBay 23 case);
  * `update` refuses to clobber a value changed outside the governed path;
  * the exit predicate is decidable, not asserted.
"""
from __future__ import annotations

import json

import pytest

from rag_kernel.birth_adopt import (
    Direction,
    StaleProvenanceError,
    UndecidableDirectionError,
    adoption_complete,
    apply_adopt,
    apply_update,
    backfill_provenance,
    diff_rules,
    read_provenance,
    record_spec_pointer,
    render_diff,
    stamp_provenance,
    value_sha,
)

UNIVERSAL = {"alpha", "beta", "gamma", "delta"}


def _rag(rules: dict, provenance: dict | None = None) -> dict:
    rag = {"meta": {"policy_version": "3.2.8"}, "operating_protocol": dict(rules)}
    if provenance:
        rag["meta"]["rule_provenance"] = dict(provenance)
    return rag


def _prov(session: str, value: object, origin: str = "hardened") -> dict:
    return {"session": session, "utc": "2026-07-29T00:00:00+00:00",
            "sha256": value_sha(value), "origin": origin}


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #
def test_value_sha_is_stable_across_dict_key_order():
    assert value_sha({"a": 1, "b": 2}) == value_sha({"b": 2, "a": 1})


def test_backfill_marks_verbatim_spec_values_as_spec_not_unknown():
    spec_op = {"alpha": "SPEC TEXT", "beta": "SPEC BETA"}
    rag = _rag({"alpha": "SPEC TEXT", "beta": "hardened beta", "local": "x"})
    as_spec, unknown = backfill_provenance(rag, spec_op, session="S181")
    prov = read_provenance(rag)
    assert (as_spec, unknown) == (1, 2)
    assert prov["alpha"]["origin"] == "spec"
    assert prov["beta"]["origin"] == "unknown"
    assert prov["local"]["origin"] == "unknown"


def test_backfill_never_overwrites_an_existing_record():
    rag = _rag({"alpha": "v"}, {"alpha": _prov("S100", "v")})
    backfill_provenance(rag, {"alpha": "v"}, session="S181")
    assert read_provenance(rag)["alpha"]["session"] == "S100"


def test_stamp_records_hash_of_the_value_written():
    rag = _rag({"alpha": "v"})
    rec = stamp_provenance(rag, "alpha", session="S181", origin="hardened", value="v2")
    assert rec["sha256"] == value_sha("v2")
    assert rec["origin"] == "hardened"


def test_spec_pointer_records_file_version_and_hash(tmp_path):
    spec = tmp_path / "INIT_UNIVERSAL_RUNTIME_KERNEL_v3.2.8.md"
    spec.write_text("# spec", encoding="utf-8")
    rag = _rag({})
    pointer = record_spec_pointer(rag, spec, "3.2.8", session="S181")
    assert pointer["file"] == spec.name
    assert pointer["version"] == "3.2.8"
    assert rag["meta"]["init_spec"] == pointer


# --------------------------------------------------------------------------- #
# Diff — direction resolution
# --------------------------------------------------------------------------- #
def test_absent_from_target_is_an_addition():
    diff = diff_rules(_rag({"alpha": "a"}), _rag({}), universal_keys={"alpha"})
    assert diff.entries[0].direction is Direction.ADD_TO_TARGET


def test_absent_from_source_is_reported_as_back_flow_not_ignored():
    diff = diff_rules(_rag({}), _rag({"alpha": "a"}), universal_keys={"alpha"})
    assert diff.entries[0].direction is Direction.ADD_TO_SOURCE


def test_identical_values_are_a_noop():
    diff = diff_rules(_rag({"alpha": "a"}), _rag({"alpha": "a"}),
                      universal_keys={"alpha"})
    assert diff.entries[0].direction is Direction.IDENTICAL
    assert diff.is_noop


def test_identical_but_both_verbatim_spec_is_flagged_as_agreement_by_absence():
    """The S179 'identical=8' false win: agreement because neither hardened."""
    diff = diff_rules(
        _rag({"alpha": "SPEC"}), _rag({"alpha": "SPEC"}),
        universal_keys={"alpha"}, spec_op={"alpha": "SPEC"},
    )
    assert diff.entries[0].direction is Direction.IDENTICAL
    assert "agreement by absence" in diff.entries[0].reason


def test_spec_decides_direction_when_target_holds_boilerplate():
    """The eBay 23 case: no provenance on either side, still decidable."""
    diff = diff_rules(
        _rag({"alpha": "HARDENED"}), _rag({"alpha": "SPEC"}),
        universal_keys={"alpha"}, spec_op={"alpha": "SPEC"},
    )
    entry = diff.entries[0]
    assert entry.direction is Direction.SOURCE_TO_TARGET
    assert "verbatim spec text" in entry.reason


def test_spec_decides_the_reverse_direction_too():
    diff = diff_rules(
        _rag({"alpha": "SPEC"}), _rag({"alpha": "HARDENED"}),
        universal_keys={"alpha"}, spec_op={"alpha": "SPEC"},
    )
    assert diff.entries[0].direction is Direction.TARGET_TO_SOURCE


def test_provenance_breaks_the_tie_when_neither_matches_the_spec():
    diff = diff_rules(
        _rag({"alpha": "newer"}, {"alpha": _prov("S181", "newer")}),
        _rag({"alpha": "older"}, {"alpha": _prov("S168", "older")}),
        universal_keys={"alpha"}, spec_op={"alpha": "SPEC"},
    )
    entry = diff.entries[0]
    assert entry.direction is Direction.SOURCE_TO_TARGET
    assert "S181" in entry.reason and "S168" in entry.reason


def test_a_side_with_provenance_beats_a_side_without():
    diff = diff_rules(
        _rag({"alpha": "a"}, {"alpha": _prov("S181", "a")}),
        _rag({"alpha": "b"}),
        universal_keys={"alpha"}, spec_op={"alpha": "SPEC"},
    )
    assert diff.entries[0].direction is Direction.SOURCE_TO_TARGET


def test_true_tie_is_diverged_never_guessed():
    diff = diff_rules(
        _rag({"alpha": "a"}), _rag({"alpha": "b"}),
        universal_keys={"alpha"}, spec_op={"alpha": "SPEC"},
    )
    assert diff.entries[0].direction is Direction.DIVERGED
    assert diff.undecidable == ["alpha"]


def test_equal_session_ordinals_do_not_break_the_tie():
    diff = diff_rules(
        _rag({"alpha": "a"}, {"alpha": _prov("S170", "a")}),
        _rag({"alpha": "b"}, {"alpha": _prov("S170", "b")}),
        universal_keys={"alpha"}, spec_op={"alpha": "SPEC"},
    )
    assert diff.entries[0].direction is Direction.DIVERGED


def test_keys_absent_from_both_sides_are_skipped_entirely():
    diff = diff_rules(_rag({}), _rag({}), universal_keys={"ghost"})
    assert diff.entries == []


def test_counts_cover_every_direction():
    diff = diff_rules(
        _rag({"alpha": "a", "beta": "b"}), _rag({"alpha": "a"}),
        universal_keys={"alpha", "beta"},
    )
    counts = diff.counts
    assert counts[Direction.IDENTICAL.value] == 1
    assert counts[Direction.ADD_TO_TARGET.value] == 1


# --------------------------------------------------------------------------- #
# Adopt
# --------------------------------------------------------------------------- #
def test_adopt_applies_additions_and_source_ahead_moves():
    source = _rag({"alpha": "HARDENED", "beta": "new"})
    target = _rag({"alpha": "SPEC"})
    diff = diff_rules(source, target, universal_keys={"alpha", "beta"},
                      spec_op={"alpha": "SPEC"})
    result = apply_adopt(target, diff, session="S181")
    assert target["operating_protocol"] == {"alpha": "HARDENED", "beta": "new"}
    assert {k for k, _ in result.applied} == {"alpha", "beta"}


def test_adopt_stamps_provenance_as_adopted_on_every_key_it_writes():
    source = _rag({"alpha": "HARDENED"})
    target = _rag({"alpha": "SPEC"})
    diff = diff_rules(source, target, universal_keys={"alpha"},
                      spec_op={"alpha": "SPEC"})
    apply_adopt(target, diff, session="S181")
    rec = read_provenance(target)["alpha"]
    assert rec["origin"] == "adopted"
    assert rec["session"] == "S181"
    assert rec["sha256"] == value_sha("HARDENED")


def test_adopt_refuses_when_a_key_is_undecidable():
    source, target = _rag({"alpha": "a"}), _rag({"alpha": "b"})
    diff = diff_rules(source, target, universal_keys={"alpha"},
                      spec_op={"alpha": "SPEC"})
    with pytest.raises(UndecidableDirectionError) as ex:
        apply_adopt(target, diff, session="S181")
    assert "alpha" in str(ex.value)
    assert target["operating_protocol"]["alpha"] == "b", "nothing written on refusal"


def test_adopt_honours_an_explicit_operator_ruling_for_source():
    source, target = _rag({"alpha": "a"}), _rag({"alpha": "b"})
    diff = diff_rules(source, target, universal_keys={"alpha"},
                      spec_op={"alpha": "SPEC"})
    apply_adopt(target, diff, session="S181", decisions={"alpha": "source"})
    assert target["operating_protocol"]["alpha"] == "a"


def test_adopt_honours_an_explicit_operator_ruling_for_target():
    source, target = _rag({"alpha": "a"}), _rag({"alpha": "b"})
    diff = diff_rules(source, target, universal_keys={"alpha"},
                      spec_op={"alpha": "SPEC"})
    result = apply_adopt(target, diff, session="S181", decisions={"alpha": "target"})
    assert target["operating_protocol"]["alpha"] == "b"
    assert result.applied == []


def test_adopt_never_moves_a_back_flow_key_onto_the_target():
    source = _rag({"alpha": "SPEC"})
    target = _rag({"alpha": "HARDENED"})
    diff = diff_rules(source, target, universal_keys={"alpha"},
                      spec_op={"alpha": "SPEC"})
    apply_adopt(target, diff, session="S181")
    assert target["operating_protocol"]["alpha"] == "HARDENED"


def test_adopt_is_idempotent():
    source = _rag({"alpha": "HARDENED"})
    target = _rag({"alpha": "SPEC"})
    keys, spec = {"alpha"}, {"alpha": "SPEC"}
    apply_adopt(target, diff_rules(source, target, universal_keys=keys, spec_op=spec),
                session="S181")
    second = diff_rules(source, target, universal_keys=keys, spec_op=spec)
    result = apply_adopt(target, second, session="S182")
    assert result.applied == []
    assert second.is_noop


# --------------------------------------------------------------------------- #
# Update — the hole that leaves a running clone frozen
# --------------------------------------------------------------------------- #
def test_update_propagates_an_improved_value_of_an_existing_rule():
    source = _rag({"alpha": "v2"}, {"alpha": _prov("S181", "v2")})
    target = _rag({"alpha": "v1"}, {"alpha": _prov("S168", "v1")})
    diff = diff_rules(source, target, universal_keys={"alpha"}, spec_op={"alpha": "S"})
    result = apply_update(target, diff, session="S181")
    assert target["operating_protocol"]["alpha"] == "v2"
    assert [k for k, _ in result.applied] == ["alpha"]


def test_update_restricted_to_named_keys_leaves_others_alone():
    source = _rag({"alpha": "a2", "beta": "b2"},
                  {"alpha": _prov("S181", "a2"), "beta": _prov("S181", "b2")})
    target = _rag({"alpha": "a1", "beta": "b1"},
                  {"alpha": _prov("S168", "a1"), "beta": _prov("S168", "b1")})
    diff = diff_rules(source, target, universal_keys={"alpha", "beta"},
                      spec_op={"alpha": "S", "beta": "S"})
    apply_update(target, diff, session="S181", keys=["alpha"])
    assert target["operating_protocol"] == {"alpha": "a2", "beta": "b1"}


def test_update_refuses_when_the_target_changed_outside_the_governed_path():
    source = _rag({"alpha": "v2"}, {"alpha": _prov("S181", "v2")})
    # provenance records "v1" but the live value is "hand-edited"
    target = _rag({"alpha": "hand-edited"}, {"alpha": _prov("S168", "v1")})
    diff = diff_rules(source, target, universal_keys={"alpha"}, spec_op={"alpha": "S"})
    with pytest.raises(StaleProvenanceError):
        apply_update(target, diff, session="S181")
    assert target["operating_protocol"]["alpha"] == "hand-edited"


def test_update_force_overrides_the_staleness_guard():
    source = _rag({"alpha": "v2"}, {"alpha": _prov("S181", "v2")})
    target = _rag({"alpha": "hand-edited"}, {"alpha": _prov("S168", "v1")})
    diff = diff_rules(source, target, universal_keys={"alpha"}, spec_op={"alpha": "S"})
    apply_update(target, diff, session="S181", force=True)
    assert target["operating_protocol"]["alpha"] == "v2"


def test_update_reports_a_named_key_that_has_nothing_to_propagate():
    source = _rag({"alpha": "a"})
    target = _rag({"alpha": "a"})
    diff = diff_rules(source, target, universal_keys={"alpha"})
    result = apply_update(target, diff, session="S181", keys=["alpha"])
    assert result.applied == []
    assert result.refused and result.refused[0][0] == "alpha"


# --------------------------------------------------------------------------- #
# Exit predicate + render
# --------------------------------------------------------------------------- #
def test_exit_predicate_fails_while_an_adoptable_move_remains():
    diff = diff_rules(_rag({"alpha": "a"}), _rag({}), universal_keys={"alpha"})
    ok, verdict = adoption_complete(diff)
    assert not ok and "INCOMPLETE" in verdict


def test_exit_predicate_holds_once_everything_is_identical():
    diff = diff_rules(_rag({"alpha": "a"}), _rag({"alpha": "a"}),
                      universal_keys={"alpha"})
    ok, verdict = adoption_complete(diff)
    assert ok and "COMPLETE" in verdict


def test_exit_predicate_fails_on_an_undecidable_even_with_nothing_to_move():
    diff = diff_rules(_rag({"alpha": "a"}), _rag({"alpha": "b"}),
                      universal_keys={"alpha"}, spec_op={"alpha": "SPEC"})
    ok, _ = adoption_complete(diff)
    assert not ok


def test_render_is_bounded_by_limit():
    source = _rag({f"k{i}": f"v{i}" for i in range(10)})
    diff = diff_rules(source, _rag({}), universal_keys={f"k{i}" for i in range(10)})
    text = render_diff(diff, limit=3)
    assert "more (raise --limit)" in text


# --------------------------------------------------------------------------- #
# File-level orchestration
# --------------------------------------------------------------------------- #
def _write(path, rag):
    path.write_text(json.dumps(rag), encoding="utf-8")
    return path


def test_adopt_file_diff_mode_never_writes(tmp_path):
    from rag_kernel.birth_adopt import adopt_file

    spec = tmp_path / "INIT_UNIVERSAL_RUNTIME_KERNEL_v3.2.8.md"
    spec.write_text("# spec", encoding="utf-8")
    src = _write(tmp_path / "src.json", _rag({"alpha": "a"}))
    tgt = _write(tmp_path / "tgt.json", _rag({}))
    before = tgt.read_text(encoding="utf-8")

    import rag_kernel.birth_adopt as ba

    ba.universal_keys_from_spec = lambda p: ({"alpha"}, "3.2.8")  # type: ignore
    try:
        _diff, result, wrote = adopt_file(
            tgt, src, spec, session="S181", mode="diff"
        )
    finally:
        import importlib
        importlib.reload(ba)
    assert result is None and wrote is False
    assert tgt.read_text(encoding="utf-8") == before
