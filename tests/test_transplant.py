"""TRANSPLANT-CLASSIFY-AUTHORITY — governed scaffold-rule transplant (Authority A).

Covers the design contract (docs/DESIGN_SCAFFOLD_TRANSPLANT.md §3) and the §5 test
obligations:
  * classification: spec-listed rule => universal; target-only rule => untouched
  * collision on differing content => fail loud, nothing written
  * additive: target's project-specific rules byte-identical after a real run
  * idempotence: second run is a no-op
  * dry-run writes nothing; .bak never created
  * target ahead on spec => refused (and source incomplete => refused)
  * audit-trail entry shape
  * CLI registration + line-by-line render
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rag_kernel.__main__ import main
from rag_kernel.transplant import (
    SourceIncompleteError,
    SpecUnavailableError,
    TargetAheadError,
    TransplantCollisionError,
    TransplantError,
    apply_transplant,
    plan_transplant,
    transplant_file,
    universal_keys_from_spec,
)

# A minimal, self-contained INIT spec: title carries the version token, and one
# rag-config JSON fence declares the operating_protocol whose keys ARE the universal
# set (Authority A). Two universal rules: rule_alpha, rule_beta.
MINISPEC = """# TEST SPEC v3.9.9

```rag-config
{
  "meta": {"schema_version": "5.4", "policy_version": "3.9.9"},
  "operating_protocol": {"rule_alpha": "canonical alpha", "rule_beta": "canonical beta"}
}
```
"""


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #
def _spec(tmp_path: Path) -> Path:
    p = tmp_path / "INIT_TEST_v3.9.9.md"
    p.write_text(MINISPEC, encoding="utf-8")
    return p


def _rag(policy: str = "3.9.9", op: dict | None = None, schema: str = "5.4") -> dict:
    return {
        "meta": {
            "schema_version": schema,
            "policy_version": policy,
            "rag_version": "0.1.0",
            "written_by_session": "S1",
        },
        "operating_protocol": {} if op is None else dict(op),
    }


def _write(tmp_path: Path, name: str, d: dict) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(d, indent=2), encoding="utf-8")
    return p


def _source(op: dict | None = None) -> dict:
    return _rag(op={"rule_alpha": "canonical alpha", "rule_beta": "canonical beta"}
               if op is None else op)


# --------------------------------------------------------------------------- #
# Classification authority (Authority A — spec-derived)
# --------------------------------------------------------------------------- #
class TestClassificationAuthority:
    def test_universal_set_is_the_specs_operating_protocol_keys(self, tmp_path):
        keys, ver = universal_keys_from_spec(_spec(tmp_path))
        assert keys == {"rule_alpha", "rule_beta"}
        assert ver == "3.9.9"

    def test_missing_spec_fails_loud(self, tmp_path):
        with pytest.raises(SpecUnavailableError):
            universal_keys_from_spec(tmp_path / "does_not_exist.md")

    def test_spec_listed_is_universal_target_only_is_invisible(self):
        # target has one universal rule (identical) + one PROJECT-SPECIFIC rule.
        target = _rag(op={"rule_beta": "canonical beta", "ebay_reprice": "clone-owned"})
        source = _source()
        plan = plan_transplant(
            target, source, universal_keys={"rule_alpha", "rule_beta"},
            spec_version="3.9.9",
        )
        # rule_alpha is universal + missing => an addition; rule_beta identical => skip.
        assert plan.additions == [("rule_alpha", "canonical alpha")]
        assert plan.present_identical == ["rule_beta"]
        assert plan.collisions == []
        # the project-specific rule is invisible: it appears in NO plan bucket.
        assert "ebay_reprice" not in dict(plan.additions)
        assert "ebay_reprice" not in plan.present_identical


# --------------------------------------------------------------------------- #
# Collision — fail-loud, nothing written
# --------------------------------------------------------------------------- #
class TestCollisionFailLoud:
    def test_plan_collects_collision_on_differing_content(self):
        target = _rag(op={"rule_alpha": "LOCALLY AMENDED"})
        plan = plan_transplant(
            target, _source(), universal_keys={"rule_alpha", "rule_beta"},
            spec_version="3.9.9",
        )
        assert [k for k, _t, _s in plan.collisions] == ["rule_alpha"]
        assert plan.collisions[0][1] == "LOCALLY AMENDED"
        assert plan.collisions[0][2] == "canonical alpha"
        assert not plan.is_noop  # a collision is a halt, never a no-op

    def test_real_run_raises_and_writes_nothing(self, tmp_path):
        target = _rag(op={"rule_alpha": "LOCALLY AMENDED"})
        tp = _write(tmp_path, "RAG_MASTER.json", target)
        sp = _write(tmp_path, "src_RAG.json", _source())
        before = tp.read_bytes()
        with pytest.raises(TransplantCollisionError):
            transplant_file(tp, sp, _spec(tmp_path), session="S162")
        assert tp.read_bytes() == before  # byte-identical: nothing written
        assert not (tmp_path / "RAG_MASTER.json.bak").exists()


# --------------------------------------------------------------------------- #
# Additive real run + preservation + audit trail
# --------------------------------------------------------------------------- #
class TestAdditiveRun:
    def _run(self, tmp_path):
        target = _rag(op={"rule_beta": "canonical beta", "ebay_reprice": "clone-owned"})
        tp = _write(tmp_path, "RAG_MASTER.json", target)
        sp = _write(tmp_path, "src_RAG.json", _source())
        plan, wrote = transplant_file(tp, sp, _spec(tmp_path), session="S162")
        return tp, plan, wrote

    def test_adds_missing_universal_and_preserves_project_rules(self, tmp_path):
        tp, plan, wrote = self._run(tmp_path)
        assert wrote is True
        assert plan.additions == [("rule_alpha", "canonical alpha")]
        reloaded = json.loads(tp.read_text(encoding="utf-8"))
        op = reloaded["operating_protocol"]
        assert op["rule_alpha"] == "canonical alpha"        # added
        assert op["rule_beta"] == "canonical beta"          # untouched
        assert op["ebay_reprice"] == "clone-owned"          # project rule preserved

    def test_bak_refreshed_to_byte_parity(self, tmp_path):
        tp, _plan, _wrote = self._run(tmp_path)
        bak = tmp_path / "RAG_MASTER.json.bak"
        assert bak.exists()
        assert bak.read_bytes() == tp.read_bytes()          # HOT == BAK

    def test_audit_trail_entry_shape(self, tmp_path):
        tp, _plan, _wrote = self._run(tmp_path)
        reloaded = json.loads(tp.read_text(encoding="utf-8"))
        trail = reloaded["meta"]["transplants"]
        assert isinstance(trail, list) and len(trail) == 1
        entry = trail[0]
        assert set(entry) == {
            "utc", "session", "runtime", "source_kernel_version",
            "spec_version", "spec_file", "rules_added", "collisions_skipped",
        }
        assert entry["session"] == "S162"
        assert entry["spec_version"] == "3.9.9"
        assert entry["rules_added"] == ["rule_alpha"]
        assert entry["collisions_skipped"] == []
        assert entry["spec_file"].endswith(".md")

    def test_idempotent_second_run_is_noop(self, tmp_path):
        tp, _plan, _wrote = self._run(tmp_path)
        after_first = tp.read_bytes()
        sp = tmp_path / "src_RAG.json"
        plan2, wrote2 = transplant_file(tp, sp, _spec(tmp_path), session="S163")
        assert wrote2 is False
        assert plan2.is_noop is True
        assert tp.read_bytes() == after_first             # no second write


# --------------------------------------------------------------------------- #
# Dry-run writes nothing
# --------------------------------------------------------------------------- #
class TestDryRun:
    def test_dry_run_writes_nothing_and_creates_no_bak(self, tmp_path):
        target = _rag(op={"rule_beta": "canonical beta"})
        tp = _write(tmp_path, "RAG_MASTER.json", target)
        sp = _write(tmp_path, "src_RAG.json", _source())
        before = tp.read_bytes()
        plan, wrote = transplant_file(tp, sp, _spec(tmp_path), session="S162", dry_run=True)
        assert wrote is False
        assert plan.additions == [("rule_alpha", "canonical alpha")]   # plan still computed
        assert tp.read_bytes() == before
        assert not (tmp_path / "RAG_MASTER.json.bak").exists()


# --------------------------------------------------------------------------- #
# Direction / integrity guards — refuse rather than corrupt
# --------------------------------------------------------------------------- #
class TestGuards:
    def test_target_ahead_of_spec_is_refused(self):
        target = _rag(policy="9.9.9")
        with pytest.raises(TargetAheadError):
            plan_transplant(
                target, _source(), universal_keys={"rule_alpha", "rule_beta"},
                spec_version="3.9.9",
            )

    def test_target_ahead_refused_even_in_dry_run(self, tmp_path):
        target = _rag(policy="9.9.9")
        tp = _write(tmp_path, "RAG_MASTER.json", target)
        sp = _write(tmp_path, "src_RAG.json", _source())
        with pytest.raises(TargetAheadError):
            transplant_file(tp, sp, _spec(tmp_path), session="S162", dry_run=True)

    def test_source_missing_a_universal_rule_is_refused(self):
        source = _rag(op={"rule_alpha": "canonical alpha"})   # missing rule_beta
        with pytest.raises(SourceIncompleteError):
            plan_transplant(
                _rag(), source, universal_keys={"rule_alpha", "rule_beta"},
                spec_version="3.9.9",
            )

    def test_apply_refuses_when_collisions_present(self):
        # defensive contract: apply never writes over a collision even if reached directly
        target = _rag(op={"rule_alpha": "LOCALLY AMENDED"})
        plan = plan_transplant(
            target, _source(), universal_keys={"rule_alpha", "rule_beta"},
            spec_version="3.9.9",
        )
        with pytest.raises(TransplantCollisionError):
            apply_transplant(target, plan, session="S162")


# --------------------------------------------------------------------------- #
# CLI registration + line-by-line render
# --------------------------------------------------------------------------- #
class TestCLI:
    def _files(self, tmp_path, target_op):
        tp = _write(tmp_path, "RAG_MASTER.json", _rag(op=target_op))
        sp = _write(tmp_path, "src_RAG.json", _source())
        return tp, sp, _spec(tmp_path)

    def test_cli_applies_and_renders_each_addition(self, tmp_path, capsys):
        tp, sp, spec = self._files(tmp_path, {"rule_beta": "canonical beta",
                                              "ebay_reprice": "clone-owned"})
        rc = main(["transplant", "--rag", str(tp), "--source", str(sp),
                   "--spec", str(spec), "--session", "S162"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "transplant applied" in out
        assert "authority: A (spec-derived)" in out
        assert "+ rule_alpha" in out          # line-by-line, not a bare count
        # applied for real:
        assert json.loads(tp.read_text())["operating_protocol"]["rule_alpha"] == "canonical alpha"

    def test_cli_collision_dry_run_halts_with_exit_1(self, tmp_path, capsys):
        tp, sp, spec = self._files(tmp_path, {"rule_alpha": "LOCALLY AMENDED"})
        rc = main(["transplant", "--rag", str(tp), "--source", str(sp),
                   "--spec", str(spec), "--session", "S162", "--dry-run"])
        captured = capsys.readouterr()
        assert rc == 1
        assert "COLLISIONS" in captured.out
        assert "! rule_alpha" in captured.out

    def test_cli_help_registered(self):
        # subcommand is registered iff argparse exits 0 on its --help
        with pytest.raises(SystemExit) as ex:
            main(["transplant", "--help"])
        assert ex.value.code == 0
