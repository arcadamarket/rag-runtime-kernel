"""KA-SCHEMA-MIGRATE — governed deployment-facing schema/version migration.

Covers the operator-banked design contract (S158 D1/D2):
  * version-range-general (ladder-derived terminal, no hardcoded pair in the logic)
  * reads the TARGET's meta and never assumes direction (ahead => refuse)
  * fail-loud on an unknown origin version (nothing written)
  * no-op when already current (no write, .bak untouched)
  * preserve-in-place (additive steps only; project-owned state untouched)
  * atomic write contract with .bak byte-parity
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rag_kernel.schema_migrate import (
    CURRENT_SCHEMA_VERSION,
    SCHEMA_MIGRATIONS,
    MigrationPlan,
    SchemaAheadError,
    SchemaMigrateError,
    SchemaMigration,
    UnknownSchemaVersionError,
    apply_migration,
    compare_versions,
    migrate_file,
    parse_version,
    plan_migration,
    resolve_path,
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def _hot(schema="5.3", policy="3.2.0", **extra) -> dict:
    hot = {
        "meta": {
            "schema_version": schema,
            "rag_version": "0.1.0",
            "policy_version": policy,
            "written_by_session": "S15",
        },
        "operating_protocol": {"some_rule": "deployment-owned prose"},
    }
    hot.update(extra)
    return hot


def _write(tmp_path: Path, hot: dict) -> Path:
    p = tmp_path / "RAG_MASTER.json"
    p.write_text(json.dumps(hot, indent=2), encoding="utf-8")
    return p


# --------------------------------------------------------------------------- #
# Version algebra
# --------------------------------------------------------------------------- #
class TestVersionAlgebra:
    def test_parses_dotted_numeric(self):
        assert parse_version("5.4") == (5, 4)
        assert parse_version("3.2.7") == (3, 2, 7)

    @pytest.mark.parametrize("bad", ["", "  ", None, 5.4, "5.x", "v5.4"])
    def test_fails_loud_on_unorderable(self, bad):
        with pytest.raises(SchemaMigrateError):
            parse_version(bad)

    def test_compare_zero_pads_unequal_widths(self):
        assert compare_versions("5.4", "5.4.0") == 0
        assert compare_versions("3.2.7", "3.2") == 1
        assert compare_versions("3.2", "3.2.7") == -1

    def test_compare_is_numeric_not_lexical(self):
        # the bug a string compare would introduce: "5.10" < "5.9" lexically
        assert compare_versions("5.10", "5.9") == 1


# --------------------------------------------------------------------------- #
# Ladder resolution
# --------------------------------------------------------------------------- #
class TestLadder:
    def test_terminal_node_defines_current_schema(self):
        assert CURRENT_SCHEMA_VERSION == SCHEMA_MIGRATIONS[-1].to_version

    def test_ladder_is_contiguous(self):
        for prev, nxt in zip(SCHEMA_MIGRATIONS, SCHEMA_MIGRATIONS[1:]):
            assert prev.to_version == nxt.from_version

    def test_ladder_steps_move_forward_only(self):
        for m in SCHEMA_MIGRATIONS:
            assert compare_versions(m.from_version, m.to_version) < 0

    def test_current_resolves_to_empty_path(self):
        assert resolve_path(CURRENT_SCHEMA_VERSION) == []

    def test_known_origin_resolves_to_steps(self):
        steps = resolve_path(SCHEMA_MIGRATIONS[0].from_version)
        assert [s.to_version for s in steps][-1] == CURRENT_SCHEMA_VERSION

    def test_ahead_target_is_refused_not_downgraded(self):
        ahead = f"{parse_version(CURRENT_SCHEMA_VERSION)[0] + 1}.0"
        with pytest.raises(SchemaAheadError):
            resolve_path(ahead)

    def test_unknown_origin_fails_loud(self):
        with pytest.raises(UnknownSchemaVersionError):
            resolve_path("0.1")


# --------------------------------------------------------------------------- #
# Planning
# --------------------------------------------------------------------------- #
class TestPlan:
    def test_plan_reads_target_meta(self):
        plan = plan_migration(_hot(schema="5.3"), spec_version="3.2.6")
        assert plan.schema_from == "5.3"
        assert plan.schema_to == CURRENT_SCHEMA_VERSION
        assert plan.steps

    def test_missing_schema_version_fails_loud(self):
        hot = _hot()
        del hot["meta"]["schema_version"]
        with pytest.raises(SchemaMigrateError):
            plan_migration(hot)

    def test_missing_meta_fails_loud(self):
        with pytest.raises(SchemaMigrateError):
            plan_migration({"tracked_items": []})

    def test_policy_behind_is_advanced(self):
        plan = plan_migration(_hot(policy="3.2.0"), spec_version="3.2.6")
        assert plan.policy_action == "advanced"
        assert (plan.policy_from, plan.policy_to) == ("3.2.0", "3.2.6")

    def test_policy_ahead_is_preserved_never_downgraded(self):
        # the live eBay clone case: 3.2.7 target vs 3.2.6 kernel
        plan = plan_migration(_hot(policy="3.2.7"), spec_version="3.2.6")
        assert plan.policy_action == "ahead-preserved"

    def test_policy_equal_is_unchanged(self):
        plan = plan_migration(_hot(policy="3.2.6"), spec_version="3.2.6")
        assert plan.policy_action == "unchanged"

    def test_absent_policy_is_not_fabricated(self):
        hot = _hot()
        del hot["meta"]["policy_version"]
        plan = plan_migration(hot, spec_version="3.2.6")
        assert plan.policy_action == "absent"

    def test_noop_when_fully_current(self):
        plan = plan_migration(
            _hot(schema=CURRENT_SCHEMA_VERSION, policy="3.2.6"), spec_version="3.2.6"
        )
        assert plan.is_noop

    def test_policy_advance_alone_is_not_a_noop(self):
        plan = plan_migration(
            _hot(schema=CURRENT_SCHEMA_VERSION, policy="3.2.0"), spec_version="3.2.6"
        )
        assert not plan.is_noop


# --------------------------------------------------------------------------- #
# Apply — additive / preserve-in-place
# --------------------------------------------------------------------------- #
class TestApply:
    def test_adds_missing_structural_keys(self):
        hot = _hot()
        plan = plan_migration(hot, spec_version="3.2.6")
        apply_migration(hot, plan, session="S159")
        assert hot["tracked_items"] == []
        assert "next_session_directive" in hot
        assert hot["meta"]["schema_version"] == CURRENT_SCHEMA_VERSION

    def test_preserves_existing_deployment_content(self):
        items = [{"id": "S15-REPRICE-MOTORS-13", "status": "OPEN"}]
        hot = _hot(tracked_items=list(items))
        hot["next_session_directive"] = {"session": "S15"}
        plan = plan_migration(hot, spec_version="3.2.6")
        apply_migration(hot, plan, session="S159")
        assert hot["tracked_items"] == items
        assert hot["next_session_directive"] == {"session": "S15"}
        assert hot["operating_protocol"] == {"some_rule": "deployment-owned prose"}

    def test_never_touches_project_owned_rag_version(self):
        hot = _hot()
        before = hot["meta"]["rag_version"]
        plan = plan_migration(hot, spec_version="3.2.6")
        apply_migration(hot, plan, session="S159")
        assert hot["meta"]["rag_version"] == before

    def test_refuses_to_coerce_a_non_list_tracked_items(self):
        hot = _hot(tracked_items={"not": "a list"})
        plan = plan_migration(hot, spec_version="3.2.6")
        with pytest.raises(SchemaMigrateError):
            apply_migration(hot, plan, session="S159")

    def test_writes_migration_audit_trail(self):
        hot = _hot(policy="3.2.0")
        plan = plan_migration(hot, spec_version="3.2.6")
        apply_migration(hot, plan, session="S159", now="2026-07-18T14:00:00+00:00")
        entry = hot["meta"]["migrations"][-1]
        assert entry["session"] == "S159"
        assert entry["schema_from"] == "5.3"
        assert entry["schema_to"] == CURRENT_SCHEMA_VERSION
        assert entry["policy_action"] == "advanced"
        assert entry["utc"] == "2026-07-18T14:00:00+00:00"

    def test_ahead_policy_is_kept_in_the_written_meta(self):
        hot = _hot(policy="3.2.7")
        plan = plan_migration(hot, spec_version="3.2.6")
        apply_migration(hot, plan, session="S159")
        assert hot["meta"]["policy_version"] == "3.2.7"

    def test_steps_are_idempotent(self):
        hot = _hot()
        plan = plan_migration(hot, spec_version="3.2.6")
        apply_migration(hot, plan, session="S159")
        # re-running each declared step over migrated state yields no further notes
        for step in SCHEMA_MIGRATIONS:
            assert step.apply(hot) == []


# --------------------------------------------------------------------------- #
# File-level transaction
# --------------------------------------------------------------------------- #
class TestMigrateFile:
    def test_migrates_and_mirrors_bak_to_byte_parity(self, tmp_path):
        p = _write(tmp_path, _hot(policy="3.2.0"))
        plan, wrote = migrate_file(p, session="S159", spec_version="3.2.6")
        assert wrote and not plan.is_noop
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["meta"]["schema_version"] == CURRENT_SCHEMA_VERSION
        bak = p.with_suffix(p.suffix + ".bak")
        assert bak.exists()
        assert bak.read_bytes() == p.read_bytes()

    def test_dry_run_writes_nothing(self, tmp_path):
        p = _write(tmp_path, _hot())
        before = p.read_bytes()
        plan, wrote = migrate_file(p, session="S159", spec_version="3.2.6", dry_run=True)
        assert not wrote
        assert plan.steps  # a real plan was computed
        assert p.read_bytes() == before
        assert not p.with_suffix(p.suffix + ".bak").exists()

    def test_noop_leaves_file_byte_untouched(self, tmp_path):
        p = _write(tmp_path, _hot(schema=CURRENT_SCHEMA_VERSION, policy="3.2.6"))
        before = p.read_bytes()
        plan, wrote = migrate_file(p, session="S159", spec_version="3.2.6")
        assert plan.is_noop and not wrote
        assert p.read_bytes() == before

    def test_second_run_is_a_noop(self, tmp_path):
        p = _write(tmp_path, _hot(policy="3.2.0"))
        migrate_file(p, session="S159", spec_version="3.2.6")
        after_first = p.read_bytes()
        plan, wrote = migrate_file(p, session="S160", spec_version="3.2.6")
        assert plan.is_noop and not wrote
        assert p.read_bytes() == after_first

    def test_ahead_target_writes_nothing(self, tmp_path):
        ahead = f"{parse_version(CURRENT_SCHEMA_VERSION)[0] + 1}.0"
        p = _write(tmp_path, _hot(schema=ahead))
        before = p.read_bytes()
        with pytest.raises(SchemaAheadError):
            migrate_file(p, session="S159", spec_version="3.2.6")
        assert p.read_bytes() == before

    def test_unknown_origin_writes_nothing(self, tmp_path):
        p = _write(tmp_path, _hot(schema="0.1"))
        before = p.read_bytes()
        with pytest.raises(UnknownSchemaVersionError):
            migrate_file(p, session="S159", spec_version="3.2.6")
        assert p.read_bytes() == before

    def test_missing_file_fails_loud(self, tmp_path):
        with pytest.raises(SchemaMigrateError):
            migrate_file(tmp_path / "nope.json", session="S159")

    def test_non_object_root_fails_loud(self, tmp_path):
        p = tmp_path / "RAG_MASTER.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(SchemaMigrateError):
            migrate_file(p, session="S159")


# --------------------------------------------------------------------------- #
# CLI wiring
# --------------------------------------------------------------------------- #
class TestCli:
    def _run(self, argv, capsys):
        from rag_kernel.__main__ import main

        code = main(argv)
        return code, capsys.readouterr()

    def test_migrate_verb_is_registered(self, tmp_path, capsys):
        p = _write(tmp_path, _hot(policy="3.2.0"))
        code, out = self._run(
            ["migrate", "--rag", str(p), "--session", "S159", "--dry-run"], capsys
        )
        assert code == 0
        assert "[DRY RUN] migrate would apply" in out.out
        assert "schema_version: 5.3 ->" in out.out

    def test_cli_renders_every_change_line_by_line(self, tmp_path, capsys):
        p = _write(tmp_path, _hot(policy="3.2.0"))
        code, out = self._run(
            ["migrate", "--rag", str(p), "--session", "S159",
             "--spec-version", "3.2.6"], capsys
        )
        assert code == 0
        assert "policy_version: 3.2.0 -> 3.2.6" in out.out
        assert "rag_version / tracked_items / operating_protocol: untouched" in out.out
        assert "HOT == BAK" in out.out

    def test_cli_reports_ahead_policy_as_preserved(self, tmp_path, capsys):
        p = _write(tmp_path, _hot(policy="3.2.7"))
        code, out = self._run(
            ["migrate", "--rag", str(p), "--session", "S159",
             "--spec-version", "3.2.6"], capsys
        )
        assert code == 0
        assert "AHEAD" in out.out and "PRESERVED" in out.out

    def test_cli_noop_message(self, tmp_path, capsys):
        p = _write(tmp_path, _hot(schema=CURRENT_SCHEMA_VERSION, policy="3.2.6"))
        code, out = self._run(
            ["migrate", "--rag", str(p), "--session", "S159",
             "--spec-version", "3.2.6"], capsys
        )
        assert code == 0
        assert "already current" in out.out

    def test_cli_exits_1_and_writes_nothing_on_unknown_origin(self, tmp_path, capsys):
        p = _write(tmp_path, _hot(schema="0.1"))
        before = p.read_bytes()
        code, out = self._run(
            ["migrate", "--rag", str(p), "--session", "S159"], capsys
        )
        assert code == 1
        assert "no migration declared" in out.err
        assert p.read_bytes() == before

    def test_cli_exits_1_on_ahead_target(self, tmp_path, capsys):
        ahead = f"{parse_version(CURRENT_SCHEMA_VERSION)[0] + 1}.0"
        p = _write(tmp_path, _hot(schema=ahead))
        code, out = self._run(["migrate", "--rag", str(p), "--session", "S159"], capsys)
        assert code == 1
        assert "AHEAD" in out.err


# --------------------------------------------------------------------------- #
# Manifest / health registration
# --------------------------------------------------------------------------- #
class TestRegistration:
    def test_module_is_a_kernel_module(self):
        import rag_kernel

        assert "rag_kernel.schema_migrate" in rag_kernel._KERNEL_MODULES

    def test_module_is_in_the_package_manifest(self):
        import rag_kernel

        registry = rag_kernel.discover()
        assert "schema_migrate" in registry["package"]["modules"]

    def test_health_imports_the_new_module_cleanly(self):
        import rag_kernel

        registry = rag_kernel.discover()
        # the import target is exercised by discover(); a broken module would have
        # raised or been dropped from the walked list
        assert "rag_kernel.schema_migrate" in rag_kernel._KERNEL_MODULES
        assert registry["package"]["version"] == rag_kernel.__version__


# --------------------------------------------------------------------------- #
# Extensibility — the ladder is data, not logic
# --------------------------------------------------------------------------- #
class TestExtensibility:
    def test_a_future_step_needs_no_logic_change(self, monkeypatch):
        import rag_kernel.schema_migrate as sm

        future = SchemaMigration(
            from_version=CURRENT_SCHEMA_VERSION,
            to_version="9.9",
            description="hypothetical future shape",
            apply=lambda hot: ["future step applied"],
        )
        monkeypatch.setattr(sm, "SCHEMA_MIGRATIONS", SCHEMA_MIGRATIONS + (future,))
        monkeypatch.setattr(sm, "CURRENT_SCHEMA_VERSION", "9.9")
        steps = sm.resolve_path(SCHEMA_MIGRATIONS[0].from_version)
        assert [s.to_version for s in steps][-1] == "9.9"
