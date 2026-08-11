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


# --- INSPECTED-COUNT-DISCLOSURE (S195, E-129 / E-130 / E-131) ---------------

def _rag_with_items(items):
    from rag_kernel.drift_store import TRACKED_ITEMS_KEY
    return {"inference_ledger": [], "priority_actions": [],
            TRACKED_ITEMS_KEY: items}


def _it(item_id, status, kind="TASK", priority=None):
    row = {"id": item_id, "title": f"title for {item_id}", "status": status,
           "kind": kind, "session": "S194"}
    if priority:
        row["priority_group"] = priority
    return row


def test_briefing_names_p1_errors_not_just_p1_tasks():
    """E-129 at the surface the successor actually reads.

    The boot agenda named 7 P1 items while 6 more — every one a kind=ERROR —
    existed and were invisible. A briefing that cannot name the top of its own
    backlog is not a briefing.
    """
    rag = _rag_with_items([
        _it("E-129", "OPEN", kind="ERROR", priority="P1"),
        _it("HOOK-ENFORCEMENT-LAYER", "OPEN", priority="P1"),
        _it("E-900", "OPEN", kind="ERROR", priority="P3"),
    ])
    out = _render_boot_briefing(rag, current_sid="S195")
    assert "E-129" in out
    assert "HOOK-ENFORCEMENT-LAYER" in out
    assert "E-900" not in out  # P3 is not the agenda


def test_briefing_states_the_set_it_inspected():
    """E-130 prevention: report the denominator, not only the verdict.

    A count with no stated denominator is exactly what let a truncated terminal
    listing pass for the whole backlog.
    """
    rag = _rag_with_items([
        _it("A-OPEN", "OPEN", priority="P1"),
        _it("B-PROG", "IN_PROGRESS", priority="P2"),
        _it("C-DONE", "RESOLVED", priority="P1"),
        _it("E-1", "OPEN", kind="ERROR", priority="P1"),
    ])
    out = _render_boot_briefing(rag, current_sid="S195")
    assert "INSPECTED: 3 live item(s)" in out  # RESOLVED is not live
    assert "of 4 tracked" in out
    assert "ERROR" in out and "TASK" in out  # the kinds it walked, named


def test_briefing_warns_when_persisted_agenda_omits_a_live_p1():
    """A stale projection must be announced at boot, not left to the auditor."""
    rag = _rag_with_items([_it("E-129", "OPEN", kind="ERROR", priority="P1")])
    rag["priority_actions"] = []
    out = _render_boot_briefing(rag, current_sid="S195")
    assert "WARNING" in out and "E-129" in out
    assert "render --apply" in out


def test_briefing_survives_a_store_it_cannot_parse():
    """The briefing may never be the reason a boot dies."""
    from rag_kernel.drift_store import TRACKED_ITEMS_KEY
    rag = {"inference_ledger": [], "priority_actions": ["X [P1 · OPEN · S1]: t"],
           TRACKED_ITEMS_KEY: [{"nonsense": True}]}
    out = _render_boot_briefing(rag, current_sid="S195")
    assert isinstance(out, str) and "inference_ledger" in out


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
