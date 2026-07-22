"""KA-20 — BOOT-GUARD-FIRST-ACTION (S172).

Root cause (E-071/072/073/075/076, five consecutive fresh boots): at "hello" a
cold-booting agent reads RAG_MASTER.json via the PERMANENTLY-BANNED Cowork sandbox
to brief the operator, BEFORE the governed ritual — a rule in the RAG cannot bind
because the agent breaks it while loading it. The kernel cannot observe a sandbox
read from inside, so BOOT-GUARD does not claim to: it removes the TRIGGER (renders
the canonical boot-state briefing so there is no reason to read the RAG directly),
records the first-action PROOF (a ``boot_guard`` marker), and prints an E-071-class
notice. These tests assert that contract.

The carry-forward gate engine (verify/audit) carries its own coverage and is
monkeypatched green here so the unit under test is the boot-guard behaviour alone.
"""

from __future__ import annotations

import json
from pathlib import Path

import rag_kernel.__main__ as m
from rag_kernel.__main__ import main, _render_boot_briefing
from rag_kernel.session_logger import LOG_FILE_PREFIX, LOG_FILE_EXT


_OP = {
    "tool_hierarchy": {"file_read_write_list": "File tools first."},
    "strict_obey": "Rule 16. Obey EXACTLY what the operator instructs.",
}


def _write_rag(tmp_path: Path, *, ledger=None, nsd=None, current_meta_sid="S171") -> Path:
    rag_path = tmp_path / "RAG_MASTER.json"
    rag_path.write_text(
        json.dumps({
            "meta": {"written_by_session": current_meta_sid, "last_checkpoint_seq": 1},
            "operating_protocol": _OP,
            "inference_ledger": ledger if ledger is not None else [],
            "next_session_directive": nsd,
            "priority_actions": ["PA-1 stub"],
            "open_tasks": ["T-1 stub", "T-2 stub", "T-3 stub"],
            "deferred_items": ["D-1 stub"],
            "sessions_recent": [],
        }),
        encoding="utf-8",
    )
    return rag_path


def _green_gate(monkeypatch):
    monkeypatch.setattr(m, "_carry_forward_gate", lambda *a, **k: (True, []))


def _boot_marker(rag_path: Path) -> dict:
    return json.loads(rag_path.read_text(encoding="utf-8"))["boot_guard"]


def _log_path(tmp_path: Path, sid: str) -> Path:
    return tmp_path / f"{LOG_FILE_PREFIX}{sid}{LOG_FILE_EXT}"


# --- briefing render primitive ---------------------------------------------

def test_briefing_counts_open_ledger_and_backlog():
    rag = {
        "inference_ledger": [
            {"id": "INS-1", "disposition": "OPEN", "session": "S170"},
            {"id": "INS-2", "disposition": "SCHEDULED", "session": "S169"},
        ],
        "next_session_directive": {"session": "S171", "for_session": "S172",
                                   "directive": "ship the boot spine"},
        "priority_actions": ["a"],
        "open_tasks": ["a", "b"],
        "deferred_items": ["a", "b", "c"],
    }
    out = _render_boot_briefing(rag, current_sid="S172")
    assert "1 OPEN of 2 total" in out
    assert "for S172" in out and "ship the boot spine" in out
    assert "priority_actions=1" in out
    assert "open_tasks=2" in out
    assert "deferred_items=3" in out


def test_briefing_flags_overdue_open_items():
    # OPEN item from S168 is >2 sessions behind current S172 -> OVERDUE.
    rag = {"inference_ledger": [{"id": "INS-1", "disposition": "OPEN", "session": "S168"}]}
    out = _render_boot_briefing(rag, current_sid="S172")
    assert "OVERDUE" in out


def test_briefing_recent_open_item_not_overdue():
    rag = {"inference_ledger": [{"id": "INS-1", "disposition": "OPEN", "session": "S171"}]}
    out = _render_boot_briefing(rag, current_sid="S172")
    assert "OVERDUE" not in out


def test_briefing_handles_missing_directive():
    out = _render_boot_briefing({"inference_ledger": []}, current_sid="S172")
    assert "next_session_directive: (none)" in out


# --- phase-1 wiring: briefing + marker + notice ----------------------------

def test_phase1_renders_briefing_and_notice(tmp_path, monkeypatch, capsys):
    rag = _write_rag(
        tmp_path,
        ledger=[{"id": "INS-1", "disposition": "OPEN", "session": "S170"}],
        nsd={"session": "S171", "for_session": "S172", "directive": "ship boot spine"},
    )
    _green_gate(monkeypatch)
    rc = main(["session-start", "S172", "--rag", str(rag), "--no-gc"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Boot-state briefing" in out
    assert "1 OPEN of 1 total" in out
    assert "BOOT-GUARD" in out
    # the E-071-class notice names the sandbox read as a violation
    assert "E-071" in out
    assert "Do NOT read RAG_MASTER.json" in out


def test_phase1_writes_boot_guard_marker(tmp_path, monkeypatch, capsys):
    rag = _write_rag(tmp_path)
    _green_gate(monkeypatch)
    main(["session-start", "S172", "--rag", str(rag), "--no-gc"])
    mk = _boot_marker(rag)
    assert mk["session"] == "S172"
    assert mk["briefing_rendered"] is True
    assert mk["first_action_utc"]
    assert "governed" in mk["source"]


def test_phase1_briefing_does_not_open_logger(tmp_path, monkeypatch, capsys):
    # BOOT-GUARD lives in phase 1; the logger stays closed until attestation.
    rag = _write_rag(tmp_path)
    _green_gate(monkeypatch)
    main(["session-start", "S172", "--rag", str(rag), "--no-gc"])
    assert not _log_path(tmp_path, "S172").exists()
