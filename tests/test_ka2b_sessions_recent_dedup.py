"""KA-2 increment B: the governed ``sessions_recent`` row-repair / dedup verb.

Increment A (``drift_audit.check_sessions_recent_coherence``) made the kernel FAIL
LOUD on duplicate-bootstrap rows — two ``sessions_recent`` rows sharing a checkpoint
timestamp ``d`` (the eBay Session-Zero S0/S1 signature) — but offered no governed way
to *repair* them; a hand-edit of the array is exactly the drift the project forbids.
This is the repair half: ``drift_store.dedup_sessions_recent[_file]`` plus the
``dedup-sessions`` CLI verb.

These tests pin: (1) the pure dedup removes exactly the phantom duplicate(s) and is
group-correct + idempotent + order-preserving, honoring ``keep=first|last``; (2) the
file verb is atomic and .bak-mirroring, and is a true no-op on a clean ledger; (3)
DETECT == REPAIR — the auditor reports clean on whatever the verb leaves behind,
because both consume the one shared predicate; (4) the CLI dry-run reports without
writing and the real run repairs; (5) the date coercers re-exported from drift_audit
still resolve (public surface unchanged after the single-source move).
"""

from __future__ import annotations

import json

import pytest

from rag_kernel import drift_audit
from rag_kernel.drift_audit import check_sessions_recent_coherence
from rag_kernel.drift_store import (
    DriftStoreError,
    dedup_sessions_recent,
    dedup_sessions_recent_file,
    sessions_recent_duplicate_pairs,
)
from rag_kernel.__main__ import main


def _rows(*pairs):
    return [{"id": i, "d": d, "s": f"summary {i}"} for i, d in pairs]


# --------------------------------------------------------------------------
# pure dedup: removes exactly the phantom duplicate(s)
# --------------------------------------------------------------------------

def test_dedup_removes_ebay_s0_s1_duplicate_keep_first():
    hot = {"sessions_recent": _rows(
        ("S0", "2026-06-16T10:00:00+00:00"),
        ("S1", "2026-06-16T10:00:00+00:00"),
    )}
    _, removed = dedup_sessions_recent(hot)
    assert [r["id"] for r in removed] == ["S1"]               # later phantom dropped
    assert [r["id"] for r in hot["sessions_recent"]] == ["S0"]


def test_dedup_keep_last_retains_later_row():
    hot = {"sessions_recent": _rows(
        ("S0", "2026-06-16T10:00:00+00:00"),
        ("S1", "2026-06-16T10:00:00+00:00"),
    )}
    _, removed = dedup_sessions_recent(hot, keep="last")
    assert [r["id"] for r in removed] == ["S0"]
    assert [r["id"] for r in hot["sessions_recent"]] == ["S1"]


def test_dedup_z_suffixed_and_offset_twin_collapse():
    # 14:00Z and 16:00+02:00 are the SAME instant → one duplicate group.
    hot = {"sessions_recent": _rows(
        ("S0", "2026-06-16T14:00:00Z"),
        ("S1", "2026-06-16T16:00:00+02:00"),
    )}
    _, removed = dedup_sessions_recent(hot)
    assert [r["id"] for r in removed] == ["S1"]


def test_dedup_identical_unparseable_literal():
    hot = {"sessions_recent": _rows(("S0", "bootstrap"), ("S1", "bootstrap"))}
    _, removed = dedup_sessions_recent(hot)
    assert [r["id"] for r in removed] == ["S1"]
    assert [r["id"] for r in hot["sessions_recent"]] == ["S0"]


def test_dedup_group_correct_three_in_a_group():
    hot = {"sessions_recent": _rows(
        ("S0", "2026-06-16T10:00:00+00:00"),
        ("S1", "2026-06-16T10:00:00+00:00"),
        ("S2", "2026-06-16T10:00:00+00:00"),
    )}
    _, removed = dedup_sessions_recent(hot)
    assert [r["id"] for r in removed] == ["S1", "S2"]         # keep first only
    assert [r["id"] for r in hot["sessions_recent"]] == ["S0"]


def test_dedup_preserves_order_and_other_groups():
    hot = {"sessions_recent": _rows(
        ("S0", "2026-06-16T09:00:00+00:00"),
        ("S1", "2026-06-16T10:00:00+00:00"),
        ("S2", "2026-06-16T10:00:00+00:00"),   # dup of S1
        ("S3", "2026-06-16T11:00:00+00:00"),
    )}
    _, removed = dedup_sessions_recent(hot)
    assert [r["id"] for r in removed] == ["S2"]
    assert [r["id"] for r in hot["sessions_recent"]] == ["S0", "S1", "S3"]


# --------------------------------------------------------------------------
# no-op on clean / untouchable input
# --------------------------------------------------------------------------

def test_dedup_clean_ledger_is_noop():
    hot = {"sessions_recent": _rows(
        ("S0", "2026-06-16T09:00:00+00:00"),
        ("S1", "2026-06-16T10:00:00+00:00"),
    )}
    before = [dict(r) for r in hot["sessions_recent"]]
    _, removed = dedup_sessions_recent(hot)
    assert removed == []
    assert hot["sessions_recent"] == before


def test_dedup_legit_multi_checkpoint_pair_kept():
    # this kernel's own S95/S95 pattern: same id, DIFFERENT instants → not duplicates.
    hot = {"sessions_recent": _rows(
        ("S95", "2026-06-20T14:12:52+00:00"),
        ("S95", "2026-06-20T14:13:22+00:00"),
    )}
    _, removed = dedup_sessions_recent(hot)
    assert removed == []
    assert len(hot["sessions_recent"]) == 2


def test_dedup_never_removes_rows_with_blank_or_missing_d():
    hot = {"sessions_recent": [
        {"id": "S0"},                       # no d
        {"id": "S1", "d": "  "},            # blank d
        {"id": "S2", "d": "2026-06-16T10:00:00+00:00"},
        {"id": "S3", "d": "2026-06-16T10:00:00+00:00"},  # dup of S2
    ]}
    _, removed = dedup_sessions_recent(hot)
    assert [r["id"] for r in removed] == ["S3"]
    assert [r["id"] for r in hot["sessions_recent"]] == ["S0", "S1", "S2"]


def test_dedup_idempotent():
    hot = {"sessions_recent": _rows(
        ("S0", "2026-06-16T10:00:00+00:00"),
        ("S1", "2026-06-16T10:00:00+00:00"),
    )}
    dedup_sessions_recent(hot)
    _, removed_again = dedup_sessions_recent(hot)
    assert removed_again == []


def test_dedup_short_or_absent_ledger_is_noop():
    assert dedup_sessions_recent({})[1] == []
    assert dedup_sessions_recent({"sessions_recent": []})[1] == []
    assert dedup_sessions_recent({"sessions_recent": [{"id": "S1", "d": "x"}]})[1] == []


def test_dedup_rejects_bad_keep():
    with pytest.raises(DriftStoreError):
        dedup_sessions_recent({"sessions_recent": []}, keep="middle")


# --------------------------------------------------------------------------
# DETECT == REPAIR: the auditor is clean on whatever the verb leaves
# --------------------------------------------------------------------------

def test_auditor_clean_after_dedup():
    hot = {"sessions_recent": _rows(
        ("S0", "2026-06-16T10:00:00+00:00"),
        ("S1", "2026-06-16T10:00:00+00:00"),
        ("S2", "2026-06-16T10:00:00+00:00"),
    )}
    assert check_sessions_recent_coherence(hot)               # flagged before
    dedup_sessions_recent(hot)
    assert check_sessions_recent_coherence(hot) == []         # clean after


def test_shared_predicate_pairs_match_auditor_count():
    sr = _rows(
        ("S0", "2026-06-16T10:00:00+00:00"),
        ("S1", "2026-06-16T10:00:00+00:00"),
        ("S2", "2026-06-16T10:00:00+00:00"),
    )
    pairs = sessions_recent_duplicate_pairs(sr)
    assert [(p[0], p[1]) for p in pairs] == [(0, 1), (0, 2)]
    assert len(pairs) == len(check_sessions_recent_coherence({"sessions_recent": sr}))


def test_coercers_still_re_exported_from_drift_audit():
    # the single-source move kept drift_audit's public surface intact.
    assert drift_audit._coerce_utc_instant("2026-06-20T23:59:00Z") is not None
    assert drift_audit._coerce_utc_date("2026-06-20") is not None


# --------------------------------------------------------------------------
# file verb: atomic + .bak parity + true no-op when clean
# --------------------------------------------------------------------------

def _write_rag(path, sr):
    hot = {
        "meta": {"last_updated_utc": "2026-06-16T00:00:00Z"},
        "sessions_recent": sr,
        "tracked_items": [],
    }
    path.write_text(json.dumps(hot), encoding="utf-8")
    return path


def test_file_verb_repairs_and_mirrors_bak(tmp_path):
    p = _write_rag(tmp_path / "RAG_MASTER.json", _rows(
        ("S0", "2026-06-16T10:00:00+00:00"),
        ("S1", "2026-06-16T10:00:00+00:00"),
    ))
    _, removed = dedup_sessions_recent_file(p, now="2026-06-20T12:00:00Z")
    assert [r["id"] for r in removed] == ["S1"]
    on_disk = json.loads(p.read_text(encoding="utf-8"))
    assert [r["id"] for r in on_disk["sessions_recent"]] == ["S0"]
    assert on_disk["meta"]["last_updated_utc"] == "2026-06-20T12:00:00Z"
    bak = tmp_path / "RAG_MASTER.json.bak"
    assert bak.exists()
    assert bak.read_bytes() == p.read_bytes()                 # byte-parity (FIX-4)


def test_file_verb_clean_ledger_writes_nothing(tmp_path):
    p = _write_rag(tmp_path / "RAG_MASTER.json", _rows(
        ("S0", "2026-06-16T09:00:00+00:00"),
        ("S1", "2026-06-16T10:00:00+00:00"),
    ))
    before = p.read_bytes()
    _, removed = dedup_sessions_recent_file(p)
    assert removed == []
    assert p.read_bytes() == before                           # untouched
    assert not (tmp_path / "RAG_MASTER.json.bak").exists()    # no spurious .bak churn


# --------------------------------------------------------------------------
# CLI: dry-run reports without writing; real run repairs; clean → nothing to do
# --------------------------------------------------------------------------

def test_cli_dry_run_reports_without_writing(tmp_path, capsys):
    p = _write_rag(tmp_path / "RAG_MASTER.json", _rows(
        ("S0", "2026-06-16T10:00:00+00:00"),
        ("S1", "2026-06-16T10:00:00+00:00"),
    ))
    before = p.read_bytes()
    rc = main(["dedup-sessions", "--rag", str(p), "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "would remove 1" in out and "S1" in out
    assert p.read_bytes() == before


def test_cli_real_run_repairs(tmp_path, capsys):
    p = _write_rag(tmp_path / "RAG_MASTER.json", _rows(
        ("S0", "2026-06-16T10:00:00+00:00"),
        ("S1", "2026-06-16T10:00:00+00:00"),
    ))
    rc = main(["dedup-sessions", "--rag", str(p), "--session", "S97"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "removed 1" in out
    on_disk = json.loads(p.read_text(encoding="utf-8"))
    assert [r["id"] for r in on_disk["sessions_recent"]] == ["S0"]


def test_cli_clean_ledger_reports_nothing(tmp_path, capsys):
    p = _write_rag(tmp_path / "RAG_MASTER.json", _rows(
        ("S0", "2026-06-16T09:00:00+00:00"),
        ("S1", "2026-06-16T10:00:00+00:00"),
    ))
    rc = main(["dedup-sessions", "--rag", str(p)])
    assert rc == 0
    assert "nothing to repair" in capsys.readouterr().out


def test_cli_missing_rag_file_errors(tmp_path, capsys):
    rc = main(["dedup-sessions", "--rag", str(tmp_path / "absent.json")])
    assert rc == 1
    assert "not found" in capsys.readouterr().err
