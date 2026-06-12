"""FIX-1 (K1+K2): regression tests for the integrity-invariant family.

The eBay Session-Zero deploy produced a RAG on which ``audit --strict`` reported
"0 findings" while it carried a broken WAL, a stale backup, unsubstituted
``<ISO>`` placeholders, leaked ``_required``/``_note`` template keys, a COLD pinned
to the wrong spec version, an empty ``written_by_session`` and a negative-looking
machine-minted session id. These tests pin each new fail-loud invariant AND
dogfood them together against a synthetic reproduction of that exact defective RAG
(``test_dogfood_ebay_defective_rag_fails_loud``), which the prior auditor passed
clean. Each invariant also has a clean-case test proving it self-skips / passes a
healthy artifact (no false positives).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rag_kernel import drift_audit
from rag_kernel.drift_audit import (
    check_bak_parity,
    check_cold_hot_version,
    check_placeholder_tokens,
    check_session_id_coherence,
    check_template_keys,
    check_wal_integrity,
    check_written_by_session,
)
from rag_kernel.persistence import WAL


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _checks(findings) -> set[str]:
    return {f.check for f in findings}


def _write_json(path: Path, obj: dict, *, bom: bool = False) -> None:
    enc = "utf-8-sig" if bom else "utf-8"
    path.write_text(json.dumps(obj, indent=2), encoding=enc)


def _write_wal(path: Path, seqs: list[int]) -> None:
    lines = [json.dumps({"seq": s, "ts": "2026-06-12T00:00:00Z", "event": "X"}) for s in seqs]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# placeholder tokens (K3)
# --------------------------------------------------------------------------

def test_placeholder_whole_value_is_error():
    hot = {"meta": {"created_utc": "<ISO>"}, "sessions_recent": [{"d": "<ISO>"}]}
    f = check_placeholder_tokens(hot)
    assert len(f) == 2
    assert _checks(f) == {"placeholder_tokens"}


def test_placeholder_in_prose_is_not_flagged():
    # rule text mentioning a template token ("S<NN>") is NOT a whole-value placeholder
    hot = {"operating_protocol": {"rule": "render the heading 'Status Report (S<NN> close)'"}}
    assert check_placeholder_tokens(hot) == []


# --------------------------------------------------------------------------
# template keys (K5)
# --------------------------------------------------------------------------

def test_template_keys_leaked_is_error():
    hot = {"operating_protocol": {"_required": True, "_note": "x", "real_rule": "ok"}}
    f = check_template_keys(hot)
    assert len(f) == 2
    assert _checks(f) == {"template_keys"}


def test_template_keys_clean():
    hot = {"operating_protocol": {"tool_hierarchy": "...", "garbage_collector": "..."}}
    assert check_template_keys(hot) == []


# --------------------------------------------------------------------------
# written_by_session (K7)
# --------------------------------------------------------------------------

def test_empty_written_by_session_on_ready_rag_is_error():
    hot = {"meta": {"written_by_session": ""}, "state_machine_status": "READY"}
    f = check_written_by_session(hot)
    assert _checks(f) == {"written_by_session"}


def test_empty_written_by_session_skipped_while_booting():
    hot = {"meta": {"written_by_session": ""}, "state_machine_status": "BOOTING"}
    assert check_written_by_session(hot) == []


def test_stamped_written_by_session_clean():
    hot = {"meta": {"written_by_session": "S70"}, "state_machine_status": "READY"}
    assert check_written_by_session(hot) == []


# --------------------------------------------------------------------------
# session-id coherence (K7)
# --------------------------------------------------------------------------

def test_negative_machine_session_id_is_error():
    hot = {
        "meta": {"written_by_session": "S-12488-1781260490"},
        "sessions_recent": [{"id": "S70"}, {"id": "S-99-1"}],
    }
    f = check_session_id_coherence(hot)
    assert _checks(f) == {"session_id_coherence"}
    assert len(f) == 2  # the wbs and the bad sessions_recent id


def test_canonical_session_ids_clean():
    hot = {
        "meta": {"written_by_session": "S70"},
        "sessions_recent": [{"id": "S0"}, {"id": "S69"}, {"id": "S70"}],
    }
    assert check_session_id_coherence(hot) == []


# --------------------------------------------------------------------------
# WAL monotonicity (K1)
# --------------------------------------------------------------------------

def test_wal_duplicate_and_gap_is_error(tmp_path):
    wal = tmp_path / "WAL.jsonl"
    _write_wal(wal, [1, 2, 3, 3, 5])  # the eBay shape: dup 3, no 4
    f = check_wal_integrity(wal)
    assert _checks(f) == {"wal_integrity"}
    assert len(f) == 2  # duplicate seq 3 + gap 3->5


def test_wal_monotonic_clean(tmp_path):
    wal = tmp_path / "WAL.jsonl"
    _write_wal(wal, [1, 2, 3, 4, 5])
    assert check_wal_integrity(wal) == []


def test_wal_absent_self_skips(tmp_path):
    assert check_wal_integrity(tmp_path / "nope.jsonl") == []


def test_wal_verify_integrity_method(tmp_path):
    wal = tmp_path / "WAL.jsonl"
    _write_wal(wal, [1, 2, 4])  # gap
    assert WAL(wal).verify_integrity()  # non-empty == anomalies present
    _write_wal(wal, [1, 2, 3])
    assert WAL(wal).verify_integrity() == []


# --------------------------------------------------------------------------
# .bak parity (K6)
# --------------------------------------------------------------------------

def test_bak_stale_seq_is_error(tmp_path):
    rag = tmp_path / "RAG_MASTER.json"
    _write_json(rag, {"meta": {"last_checkpoint_seq": 3}})
    _write_json(rag.with_suffix(".json.bak"), {"meta": {"last_checkpoint_seq": 0}})
    f = check_bak_parity(rag, {"meta": {"last_checkpoint_seq": 3}})
    assert _checks(f) == {"bak_parity"}


def test_bak_parity_mirror_and_rollback_prev_clean(tmp_path):
    rag = tmp_path / "RAG_MASTER.json"
    hot = {"meta": {"last_checkpoint_seq": 7}}
    _write_json(rag, hot)
    # parity-mirror (equal)
    _write_json(rag.with_suffix(".json.bak"), {"meta": {"last_checkpoint_seq": 7}})
    assert check_bak_parity(rag, hot) == []
    # rollback-prior (one behind)
    _write_json(rag.with_suffix(".json.bak"), {"meta": {"last_checkpoint_seq": 6}})
    assert check_bak_parity(rag, hot) == []


def test_bak_absent_self_skips(tmp_path):
    rag = tmp_path / "RAG_MASTER.json"
    _write_json(rag, {"meta": {"last_checkpoint_seq": 1}})
    assert check_bak_parity(rag, {"meta": {"last_checkpoint_seq": 1}}) == []


# --------------------------------------------------------------------------
# COLD<->HOT version (K4) — incl. BOM tolerance (the production COLD has a BOM)
# --------------------------------------------------------------------------

def test_cold_version_mismatch_is_error(tmp_path):
    cold = tmp_path / "RAG_COLD.json"
    _write_json(cold, {"init_prompt_reference": {"version": "3.1.9"}}, bom=True)
    hot = {"meta": {"rag_files": {"init_prompt": "INIT_..._v3.2.2.md"}, "policy_version": "3.2.2"}}
    f = check_cold_hot_version(cold, hot)
    assert _checks(f) == {"cold_hot_version"}


def test_cold_version_match_clean(tmp_path):
    cold = tmp_path / "RAG_COLD.json"
    _write_json(cold, {"init_prompt_reference": {"version": "3.2.2"}})
    hot = {"meta": {"rag_files": {"init_prompt": "INIT_..._v3.2.2.md"}}}
    assert check_cold_hot_version(cold, hot) == []


def test_cold_absent_self_skips(tmp_path):
    hot = {"meta": {"policy_version": "3.2.2"}}
    assert check_cold_hot_version(tmp_path / "nope.json", hot) == []


# --------------------------------------------------------------------------
# DOGFOOD — the synthetic eBay-defective RAG the old auditor passed clean
# --------------------------------------------------------------------------

def _defective_rag(dirpath: Path) -> Path:
    """Reproduce the eBay Session-Zero deploy's defects (K1,K3–K7) on disk."""
    rag = dirpath / "RAG_MASTER.json"
    hot = {
        "meta": {
            "policy_version": "3.2.2",
            "written_by_session": "",                       # K7: empty
            "last_checkpoint_seq": 3,                         # K6: bak is stale at 0
            "rag_files": {"init_prompt": "INIT_..._v3.2.2.md",
                          "cold": "RAG_COLD.json", "wal": "WAL.jsonl"},
            "created_utc": "<ISO>",                           # K3: placeholder
        },
        "state_machine_status": "READY",
        "operating_protocol": {"_required": True, "_note": "scaffold", "rule_x": "ok"},  # K5
        "sessions_recent": [{"id": "S1", "d": "<ISO>"},       # K3 placeholder
                            {"id": "S-12488-1781260490"}],    # K7 negative id
        "tracked_items": [],
    }
    _write_json(rag, hot)
    _write_json(rag.with_suffix(".json.bak"), {"meta": {"last_checkpoint_seq": 0}})  # K6
    _write_json(dirpath / "RAG_COLD.json",
                {"init_prompt_reference": {"version": "3.1.9"}}, bom=True)            # K4
    _write_wal(dirpath / "WAL.jsonl", [1, 2, 3, 3, 5])                                # K1
    return rag


def test_dogfood_ebay_defective_rag_fails_loud(tmp_path):
    ragdir = tmp_path / "RAG"
    ragdir.mkdir()
    rag = _defective_rag(ragdir)

    report = drift_audit.audit_file(rag, scan_root=False)

    assert not report.ok, "auditor must FAIL LOUD on the eBay-defective RAG"
    got = _checks(report.errors)
    # every FIX-1 invariant must fire on this artifact
    for expected in (
        "placeholder_tokens",
        "template_keys",
        "written_by_session",
        "session_id_coherence",
        "wal_integrity",
        "bak_parity",
        "cold_hot_version",
    ):
        assert expected in got, f"{expected} did not fire on the defective RAG (got {got})"


def test_dogfood_clean_rag_passes(tmp_path):
    """A healthy RAG with no WAL/COLD/.bak siblings audits clean (no false positives)."""
    ragdir = tmp_path / "RAG"
    ragdir.mkdir()
    rag = ragdir / "RAG_MASTER.json"
    _write_json(rag, {
        "meta": {"policy_version": "3.2.2", "written_by_session": "S70",
                 "last_checkpoint_seq": 5, "rag_files": {"init_prompt": "INIT_..._v3.2.2.md"}},
        "state_machine_status": "READY",
        "operating_protocol": {"tool_hierarchy": "..."},
        "sessions_recent": [{"id": "S70"}],
        "tracked_items": [],
    })
    report = drift_audit.audit_file(rag, scan_root=False)
    assert report.ok, report.summary()
