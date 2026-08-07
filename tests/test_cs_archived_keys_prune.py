"""META-SETTER-GAP residue + CS-SECONDARY-PROSE-DRIFT (S187).

Two halves of one defect class: ``current_status`` is the LIVE status surface, but
the governed verbs could only reach part of it.

  1. ``refresh-current-status`` re-stamps machine-fact TOKENS; it has no way to
     remove a KEY. So 24 session-stamped snapshots — ``next_session_directive_S59``
     … ``_S147``, ``session_finding_S77_E045``, ``fv_phase3_S35`` — accreted on the
     live surface with no governed path to clear them, i.e. exactly the "requires a
     hand edit that ``tool_contract`` forbids" shape META-SETTER-GAP names. These
     tests pin the detection predicate, the auditor WARNING, and the guarded repair
     verb, including its refusal to be aimed at a live field.

  2. The version refresh reached ONE field (``rag_kernel_version``). A SECONDARY
     narrative field restating the same machine fact — ``runtime_deployment``:
     "Runtime v0.4.46 deployed to RAG/rag_kernel/" — was invisible to both guard and
     repair, and sat five releases stale through green audits. These tests pin that
     the secondary field is now refreshed from the SAME authority.
"""

from __future__ import annotations

import json

import pytest

from rag_kernel import drift_store
from rag_kernel.drift_audit import ERROR, WARNING, check_current_status_archived_keys
from rag_kernel.drift_store import (
    CURRENT_STATUS_KEY,
    CurrentStatusRefreshError,
    archived_current_status_keys,
    compute_current_status_refresh,
    prune_current_status_file,
)
from rag_kernel.__main__ import main


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _hot(**cs):
    return {
        "tracked_items": [],
        "meta": {"last_updated_utc": "2020-01-01T00:00:00Z",
                 "written_by_session": "S1"},
        CURRENT_STATUS_KEY: dict(cs),
    }


def _dirty():
    """A current_status carrying both live fields and archived snapshots."""
    return _hot(
        rag_kernel_version="v0.4.53 -- 19 capability modules",
        github_repo="https://github.com/x/y -- LATEST COMMIT abc1234",
        unit_tests="2,298 tests, all passing",
        runtime_deployment="Runtime v0.4.46 deployed to RAG/rag_kernel/",
        next_session_directive="S187: do the thing",
        next_session_directive_S133="TRANSFER FROM S132 ...",
        next_session_directive_S59="TRANSFER FROM S57 ...",
        session_finding_S77_E045="E-045 (logged ERROR_LOG.md, S77) ...",
        fv_phase3_S35="SHIPPED (commit 7d060ef)",
    )


def _write(tmp_path, hot):
    p = tmp_path / "RAG_MASTER.json"
    p.write_text(json.dumps(hot, indent=2), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# the predicate — structural, and it cannot reach a live field
# ---------------------------------------------------------------------------

def test_predicate_selects_only_session_stamped_keys():
    assert archived_current_status_keys(_dirty()) == [
        "fv_phase3_S35",
        "next_session_directive_S133",
        "next_session_directive_S59",
        "session_finding_S77_E045",
    ]


def test_predicate_never_matches_the_live_unsuffixed_directive():
    """The LIVE key is unsuffixed — the one the boot briefing actually reads."""
    assert "next_session_directive" not in archived_current_status_keys(_dirty())


@pytest.mark.parametrize("key", [
    "rag_kernel_version", "github_repo", "unit_tests", "runtime_deployment",
    "security", "readme", "logo", "funding", "developer_name",
    "git_worktree_path", "deferred_tracked", "init_prompt",
])
def test_predicate_spares_every_real_live_field(key):
    assert archived_current_status_keys(_hot(**{key: "x"})) == []


def test_predicate_tolerates_absent_current_status():
    assert archived_current_status_keys({"tracked_items": []}) == []
    assert archived_current_status_keys({CURRENT_STATUS_KEY: "not a dict"}) == []


# ---------------------------------------------------------------------------
# auditor: WARNING, single-sourced with the repair verb
# ---------------------------------------------------------------------------

def test_audit_warns_and_names_the_repair_verb():
    findings = check_current_status_archived_keys(_dirty())
    assert len(findings) == 1
    f = findings[0]
    assert f.check == "current_status_archived_keys"
    assert f.severity == WARNING and f.severity != ERROR
    assert "prune-current-status" in f.detail
    assert "4 archived" in f.detail


def test_audit_clean_when_no_archived_keys():
    assert check_current_status_archived_keys(_hot(unit_tests="2,298 tests")) == []


def test_detection_and_repair_share_one_predicate():
    """The DRY invariant: the auditor imports the store's function, not a copy."""
    assert (check_current_status_archived_keys.__module__
            == "rag_kernel.drift_audit")
    assert drift_store.archived_current_status_keys is archived_current_status_keys


# ---------------------------------------------------------------------------
# the repair verb
# ---------------------------------------------------------------------------

def test_prune_removes_archived_and_preserves_live(tmp_path):
    p = _write(tmp_path, _dirty())
    removed, wrote = prune_current_status_file(p)
    assert wrote is True
    assert len(removed) == 4
    cs = json.loads(p.read_text(encoding="utf-8"))[CURRENT_STATUS_KEY]
    assert archived_current_status_keys({CURRENT_STATUS_KEY: cs}) == []
    # every live field survives untouched, including the unsuffixed directive
    for live in ("rag_kernel_version", "github_repo", "unit_tests",
                 "runtime_deployment", "next_session_directive"):
        assert live in cs


def test_prune_is_idempotent(tmp_path):
    p = _write(tmp_path, _dirty())
    prune_current_status_file(p)
    removed, wrote = prune_current_status_file(p)
    assert removed == [] and wrote is False


def test_prune_dry_run_writes_nothing(tmp_path):
    p = _write(tmp_path, _dirty())
    before = p.read_text(encoding="utf-8")
    removed, wrote = prune_current_status_file(p, dry_run=True)
    assert len(removed) == 4 and wrote is False
    assert p.read_text(encoding="utf-8") == before


def test_prune_refuses_a_live_field(tmp_path):
    """The whole point of routing the edit through a governed verb."""
    p = _write(tmp_path, _dirty())
    with pytest.raises(CurrentStatusRefreshError) as ex:
        prune_current_status_file(p, keys=["rag_kernel_version"])
    assert "refusing to prune" in str(ex.value)
    assert "rag_kernel_version" in str(ex.value)
    # and it wrote nothing
    assert "rag_kernel_version" in json.loads(p.read_text(encoding="utf-8"))[CURRENT_STATUS_KEY]


def test_prune_subset_removes_only_the_named_key(tmp_path):
    p = _write(tmp_path, _dirty())
    removed, wrote = prune_current_status_file(p, keys=["fv_phase3_S35"])
    assert removed == ["fv_phase3_S35"] and wrote is True
    cs = json.loads(p.read_text(encoding="utf-8"))[CURRENT_STATUS_KEY]
    assert "fv_phase3_S35" not in cs
    assert "next_session_directive_S133" in cs  # untouched


def test_prune_mirrors_bak_to_byte_parity(tmp_path):
    p = _write(tmp_path, _dirty())
    prune_current_status_file(p)
    bak = p.with_suffix(p.suffix + ".bak")
    assert bak.exists()
    assert bak.read_bytes() == p.read_bytes()


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------

class TestPruneCLI:
    def test_list_reports_without_writing(self, tmp_path, capsys):
        p = _write(tmp_path, _dirty())
        before = p.read_text(encoding="utf-8")
        rc = main(["prune-current-status", "--rag", str(p), "--session", "S187", "--list"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "archived keys (4)" in out
        assert "session_finding_S77_E045" in out
        assert p.read_text(encoding="utf-8") == before

    def test_apply_exits_zero_and_cleans(self, tmp_path, capsys):
        p = _write(tmp_path, _dirty())
        rc = main(["prune-current-status", "--rag", str(p), "--session", "S187"])
        assert rc == 0
        assert "current_status pruned" in capsys.readouterr().out
        assert archived_current_status_keys(
            json.loads(p.read_text(encoding="utf-8"))) == []

    def test_refusal_exits_one(self, tmp_path, capsys):
        p = _write(tmp_path, _dirty())
        rc = main(["prune-current-status", "--rag", str(p), "--session", "S187",
                   "--keys", "unit_tests"])
        assert rc == 1
        assert "refusing to prune" in capsys.readouterr().err

    def test_missing_rag_exits_one(self, tmp_path, capsys):
        rc = main(["prune-current-status", "--rag", str(tmp_path / "nope.json"),
                   "--session", "S187"])
        assert rc == 1
        assert "not found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# CS-SECONDARY-PROSE-DRIFT: the secondary version restatement
# ---------------------------------------------------------------------------

def test_secondary_version_field_is_refreshed_from_the_same_authority():
    hot = _dirty()
    new_cs, changes = compute_current_status_refresh(hot, version="0.4.53")
    by_field = {c["field"]: c for c in changes}
    assert by_field["rag_kernel_version"]["action"] == "unchanged"
    sec = by_field["runtime_deployment"]
    assert sec["action"] == "updated"
    assert sec["old"] == "0.4.46" and sec["new"] == "0.4.53"
    assert "v0.4.53 deployed to RAG/rag_kernel/" in new_cs["runtime_deployment"]


def test_secondary_refresh_preserves_surrounding_narrative():
    hot = _hot(rag_kernel_version="v0.4.53",
               runtime_deployment="Runtime v0.4.46 deployed to RAG/rag_kernel/ (19 modules)")
    new_cs, _ = compute_current_status_refresh(hot, version="0.4.53")
    assert new_cs["runtime_deployment"] == (
        "Runtime v0.4.53 deployed to RAG/rag_kernel/ (19 modules)"
    )


def test_secondary_field_absent_is_not_a_repair_failure():
    """A secondary restatement is optional — its absence must not plan a skip."""
    hot = _hot(rag_kernel_version="v0.4.53")
    _, changes = compute_current_status_refresh(hot, version="0.4.53", strict=True)
    assert "runtime_deployment" not in {c["field"] for c in changes}


def test_secondary_field_without_a_version_token_is_left_alone():
    hot = _hot(rag_kernel_version="v0.4.53",
               runtime_deployment="deployed to RAG/rag_kernel/")
    new_cs, changes = compute_current_status_refresh(hot, version="0.4.53")
    assert "runtime_deployment" not in {c["field"] for c in changes}
    assert new_cs["runtime_deployment"] == "deployed to RAG/rag_kernel/"


# ---------------------------------------------------------------------------
# STATE-MACHINE-STATUS-INVALID (S187)
# ---------------------------------------------------------------------------
# The legal state set was enforced ONLY by spec_parser.SpecParser.validate, which
# runs on init/configure and never on audit. This kernel's own RAG carried
# state_machine_status = "COMPLETE" — a value no transition produces — through
# every green session-end audit. The set is now a module constant both the parser
# and the auditor import, and the auditor ERRORs on a non-state.

from rag_kernel.drift_audit import check_state_machine_status
from rag_kernel.spec_parser import VALID_STATE_MACHINE_STATUS


@pytest.mark.parametrize("state", sorted(VALID_STATE_MACHINE_STATUS))
def test_every_legal_state_audits_clean(state):
    assert check_state_machine_status({"state_machine_status": state}) == []


@pytest.mark.parametrize("state", ["COMPLETE", "DONE", "ready", "FINISHED", None, 7])
def test_a_non_state_is_a_hard_error(state):
    findings = check_state_machine_status({"state_machine_status": state})
    assert len(findings) == 1
    assert findings[0].check == "state_machine_status"
    assert findings[0].severity == ERROR


def test_the_reported_defect_is_the_regression_case():
    """S187's live finding, verbatim."""
    findings = check_state_machine_status({"state_machine_status": "COMPLETE"})
    assert "'COMPLETE' is not a legal state" in findings[0].detail
    assert "checkpoint --status" in findings[0].detail  # names the governed repair


def test_absent_key_self_skips():
    """An un-migrated RAG declares no state machine — not a violation."""
    assert check_state_machine_status({"meta": {}}) == []
    assert check_state_machine_status("not a dict") == []


def test_parser_and_auditor_share_one_set():
    """The DRY invariant: the literal no longer lives inside validate_rag."""
    import inspect
    from rag_kernel.spec_parser import SpecParser
    src = inspect.getsource(SpecParser.validate_rag)
    assert "VALID_STATE_MACHINE_STATUS" in src
    assert '"CHECKPOINTING"' not in src  # the literal set is gone from the method


def test_parser_still_rejects_a_non_state():
    from rag_kernel.spec_parser import SpecParser
    errs = SpecParser.validate_rag({"state_machine_status": "COMPLETE"})
    assert any("Invalid state_machine_status" in e for e in errs)


def test_parser_accepts_every_legal_state():
    from rag_kernel.spec_parser import SpecParser
    for state in VALID_STATE_MACHINE_STATUS:
        errs = SpecParser.validate_rag({"state_machine_status": state})
        assert not any("Invalid state_machine_status" in e for e in errs), state


class TestCheckpointStatusWriteGuard:
    """STATE-MACHINE-STATUS-INVALID: refuse a non-state at the WRITE site.

    Detecting the bad value downstream is not enough — `checkpoint --status` was a
    free-text passthrough straight into canonical state, which is how "COMPLETE"
    got written in the first place.
    """

    def _rag_file(self, tmp_path):
        p = tmp_path / "RAG_MASTER.json"
        p.write_text(json.dumps({
            "meta": {"last_updated_utc": "2020-01-01T00:00:00Z",
                     "written_by_session": "S1", "last_checkpoint_seq": 1},
            "tracked_items": [], "sessions_recent": [],
            "state_machine_status": "READY",
        }, indent=2), encoding="utf-8")
        return p

    def test_illegal_status_is_refused(self, tmp_path, capsys):
        from rag_kernel.__main__ import main
        p = self._rag_file(tmp_path)
        rc = main(["checkpoint", "--rag", str(p), "--session", "S9",
                   "--summary", "x", "--status", "COMPLETE", "--dry-run",
                   "--no-require-session-log"])
        assert rc == 1
        assert "not a legal state_machine_status" in capsys.readouterr().err

    def test_legal_status_is_accepted(self, tmp_path, capsys):
        from rag_kernel.__main__ import main
        p = self._rag_file(tmp_path)
        rc = main(["checkpoint", "--rag", str(p), "--session", "S9",
                   "--summary", "x", "--status", "WORKING", "--dry-run",
                   "--no-require-session-log"])
        assert rc == 0
        assert "READY -> WORKING" in capsys.readouterr().out


def test_audit_hot_surfaces_the_finding():
    """Wired into the real audit, not just callable in isolation."""
    from rag_kernel.drift_audit import audit_hot
    hot = _hot(unit_tests="2,363 tests")
    hot["state_machine_status"] = "COMPLETE"
    report = audit_hot(hot)
    assert any(f.check == "state_machine_status" for f in report.errors)
