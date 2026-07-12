"""KA-16 — atomic, resumable session close (KA-10 GOVERNANCE-DETERMINISM arc).

The eBay S4 freeze was a NON-ATOMIC close: the session checkpointed (state banked
at seq 6) but the close ritual then aborted, stranding the operator — state saved,
no handoff, and nothing on disk flagged the close as unfinished. KA-16 makes the
close a forward-progress transaction tracked by a single ``session_close`` marker:

  phase: CHECKPOINTED -> CLOSED -> COMPLETE
  transfer_ready: True only at COMPLETE (after checkpoint + ERROR_LOG fold +
                  logger close + audit all pass).

These tests assert the transaction contract — the marker is written per step, an
aborted close is left resumable, ``session-resume`` finishes it WITHOUT a second
checkpoint (idempotent), the ERROR_LOG fold is idempotent, and the session-start
carry-forward gate detects a stranded close.
"""

from __future__ import annotations

import json
from pathlib import Path

import rag_kernel.__main__ as m
from rag_kernel.__main__ import main, _carry_forward_gate
from rag_kernel.session_logger import LOG_FILE_PREFIX, LOG_FILE_EXT


def _log_path(tmp_path: Path, sid: str) -> Path:
    return tmp_path / f"{LOG_FILE_PREFIX}{sid}{LOG_FILE_EXT}"


def _write_rag(tmp_path: Path, written_by: str, seq: int = 1) -> Path:
    rag_path = tmp_path / "RAG_MASTER.json"
    rag_path.write_text(
        json.dumps({
            "meta": {"written_by_session": written_by, "last_checkpoint_seq": seq},
            "sessions_recent": [],
        }),
        encoding="utf-8",
    )
    return rag_path


def _start_logger(tmp_path: Path, sid: str) -> None:
    assert main(["session", "start", sid, "--rag-dir", str(tmp_path)]) == 0


def _marker(rag: Path) -> dict:
    return json.loads(rag.read_text(encoding="utf-8"))["session_close"]


def _set_marker(rag: Path, marker: dict) -> None:
    data = json.loads(rag.read_text(encoding="utf-8"))
    data["session_close"] = marker
    rag.write_text(json.dumps(data), encoding="utf-8")


# --- happy path: transfer_ready flips only at the end -----------------------

def test_session_end_sets_complete_transfer_ready_marker(tmp_path, monkeypatch):
    rag = _write_rag(tmp_path, "S0", seq=1)
    _start_logger(tmp_path, "S1")
    monkeypatch.setattr(m, "cmd_audit", lambda args: 0)

    rc = main(["session-end", "--rag", str(rag), "--session", "S1",
               "--summary", "did the thing"])
    assert rc == 0

    data = json.loads(rag.read_text(encoding="utf-8"))
    mk = data["session_close"]
    assert mk["session"] == "S1"
    assert mk["phase"] == "COMPLETE"
    assert mk["transfer_ready"] is True
    assert mk["steps"]["checkpoint"] and mk["steps"]["logger_close"] and mk["steps"]["audit"]
    # the checkpoint still ran (state banked) ...
    assert data["meta"]["written_by_session"] == "S1"
    assert data["meta"]["last_checkpoint_seq"] == 2
    # ... and the session_end log marker was written (KA-4 gate satisfied).
    events = [json.loads(l) for l in _log_path(tmp_path, "S1").read_text().splitlines() if l.strip()]
    assert any(e["event"] == "session_end" for e in events)


# --- S139 WIRE-CLOSE: the close MACHINE-RENDERS the canonical report ---------

def test_session_end_machine_renders_close_report(tmp_path, monkeypatch, capsys):
    """The close emits the deterministic 7-section report verbatim (Rule 12).

    Rendering it from the just-checkpointed RAG is what makes hand-authoring
    impossible (the S136 close-drift root cause) — and the render itself IS the
    report_rendered attestation, no --report-rendered flag needed.
    """
    rag = _write_rag(tmp_path, "S0", seq=1)
    _start_logger(tmp_path, "S1")
    monkeypatch.setattr(m, "cmd_audit", lambda args: 0)

    rc = main(["session-end", "--rag", str(rag), "--session", "S1",
               "--summary", "x", "--released", "--release-ref", "runtime-vX",
               "--tests", "1,731 green", "--claims-ok"])
    assert rc == 0

    out = capsys.readouterr().out
    assert "=== Canonical status report (Rule 12 — machine-rendered at close) ===" in out
    assert "### 1 · At a glance" in out
    assert "### 7 · Verification & handoff" in out
    # the machine render satisfies the attestation step
    assert _marker(rag)["steps"]["report_rendered"] is True


def test_session_end_no_report_suppresses_render(tmp_path, monkeypatch, capsys):
    """--no-report opts out of the machine render (kept for exceptional cases)."""
    rag = _write_rag(tmp_path, "S0", seq=1)
    _start_logger(tmp_path, "S1")
    monkeypatch.setattr(m, "cmd_audit", lambda args: 0)

    rc = main(["session-end", "--rag", str(rag), "--session", "S1",
               "--summary", "x", "--no-report"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "machine-rendered at close" not in out


# --- ERROR_LOG fold is part of the governed call, and idempotent ------------

def test_session_end_folds_error_log_entry(tmp_path, monkeypatch):
    rag = _write_rag(tmp_path, "S0", seq=1)
    _start_logger(tmp_path, "S1")
    monkeypatch.setattr(m, "cmd_audit", lambda args: 0)
    entry = "### E-099: a folded close incident"

    rc = main(["session-end", "--rag", str(rag), "--session", "S1", "--summary", "x",
               "--error-log-entry", entry, "--error-log-id", "E-099"])
    assert rc == 0
    text = (tmp_path / "ERROR_LOG.md").read_text(encoding="utf-8")
    assert entry in text
    assert text.count("<!-- close-log-id: E-099 -->") == 1
    assert _marker(rag)["steps"]["error_log"] is True


def test_checkpoint_error_log_fold_is_idempotent(tmp_path):
    rag = _write_rag(tmp_path, "S0", seq=1)
    el = tmp_path / "ERROR_LOG.md"
    for _ in range(2):
        rc = main(["checkpoint", "--rag", str(rag), "--session", "S1", "--summary", "x",
                   "--error-log-entry", "### E-1 dup-guard", "--error-log-id", "E-1",
                   "--no-require-session-log"])  # KA-18 guard orthogonal to fold-idempotency
        assert rc == 0
    text = el.read_text(encoding="utf-8")
    assert text.count("<!-- close-log-id: E-1 -->") == 1   # appended exactly once
    assert text.count("### E-1 dup-guard") == 1


# --- an aborted close is left resumable, not silently stranded ---------------

def test_audit_failure_leaves_resumable_closed_marker(tmp_path, monkeypatch):
    rag = _write_rag(tmp_path, "S0", seq=1)
    _start_logger(tmp_path, "S1")
    monkeypatch.setattr(m, "cmd_audit", lambda args: 1)   # red audit

    rc = main(["session-end", "--rag", str(rag), "--session", "S1", "--summary", "x"])
    assert rc != 0
    mk = _marker(rag)
    assert mk["transfer_ready"] is False          # NOT handed off
    assert mk["phase"] == "CLOSED"                # resumable record on disk
    assert mk["steps"]["checkpoint"] and mk["steps"]["logger_close"]
    assert mk["steps"]["audit"] is False


def test_session_resume_completes_without_second_checkpoint(tmp_path, monkeypatch):
    rag = _write_rag(tmp_path, "S0", seq=1)
    _start_logger(tmp_path, "S1")
    monkeypatch.setattr(m, "cmd_audit", lambda args: 1)
    assert main(["session-end", "--rag", str(rag), "--session", "S1", "--summary", "x"]) != 0
    seq_after_abort = json.loads(rag.read_text())["meta"]["last_checkpoint_seq"]

    # audit now green — resume should finish the close and NOT re-checkpoint.
    monkeypatch.setattr(m, "cmd_audit", lambda args: 0)
    rc = main(["session-resume", "--rag", str(rag)])
    assert rc == 0
    data = json.loads(rag.read_text())
    assert data["session_close"]["transfer_ready"] is True
    assert data["session_close"]["phase"] == "COMPLETE"
    # idempotent resume: no double seq-increment
    assert data["meta"]["last_checkpoint_seq"] == seq_after_abort


# --- resume no-ops -----------------------------------------------------------

def test_session_resume_noop_when_complete(tmp_path, monkeypatch, capsys):
    rag = _write_rag(tmp_path, "S0", seq=1)
    _start_logger(tmp_path, "S1")
    monkeypatch.setattr(m, "cmd_audit", lambda args: 0)
    assert main(["session-end", "--rag", str(rag), "--session", "S1", "--summary", "x"]) == 0
    capsys.readouterr()
    assert main(["session-resume", "--rag", str(rag)]) == 0
    assert "No incomplete close" in capsys.readouterr().out


def test_session_resume_noop_when_no_marker(tmp_path, capsys):
    rag = _write_rag(tmp_path, "S0", seq=1)
    assert main(["session-resume", "--rag", str(rag)]) == 0
    assert "No incomplete close" in capsys.readouterr().out


def test_session_resume_requires_summary_before_checkpoint(tmp_path):
    rag = _write_rag(tmp_path, "S0", seq=1)
    _set_marker(rag, {
        "session": "S1", "phase": "CHECKPOINTED", "transfer_ready": False,
        "started_utc": "t", "completed_utc": None, "steps": {"checkpoint": False},
    })
    # checkpoint never landed + no --summary -> fail-loud, do not guess.
    assert main(["session-resume", "--rag", str(rag)]) == 1


# --- carry-forward gate detects a stranded close ----------------------------

def test_carry_forward_gate_flags_incomplete_close(tmp_path):
    rag = _write_rag(tmp_path, "S0", seq=1)
    _set_marker(rag, {
        "session": "S0", "phase": "CLOSED", "transfer_ready": False,
        "started_utc": "t", "completed_utc": None, "steps": {"checkpoint": True},
    })
    ok, findings = _carry_forward_gate(rag)
    assert ok is False
    assert any("incomplete close" in f for f in findings)


def test_carry_forward_gate_silent_when_close_complete(tmp_path):
    rag = _write_rag(tmp_path, "S0", seq=1)
    _set_marker(rag, {
        "session": "S0", "phase": "COMPLETE", "transfer_ready": True,
        "started_utc": "t", "completed_utc": "t2", "steps": {"checkpoint": True},
    })
    _, findings = _carry_forward_gate(rag)
    assert not any("incomplete close" in f for f in findings)


# --- a different session's pending close blocks a new session-end ------------

def test_session_end_refuses_when_other_session_close_pending(tmp_path, monkeypatch):
    rag = _write_rag(tmp_path, "S0", seq=1)
    _set_marker(rag, {
        "session": "S5", "phase": "CHECKPOINTED", "transfer_ready": False,
        "started_utc": "t", "completed_utc": None, "steps": {"checkpoint": True},
    })
    _start_logger(tmp_path, "S6")
    called = {"audit": False}
    monkeypatch.setattr(m, "cmd_audit", lambda args: called.__setitem__("audit", True) or 0)

    rc = main(["session-end", "--rag", str(rag), "--session", "S6", "--summary", "x"])
    assert rc == 1
    assert called["audit"] is False   # never started the close


# --- optional report attestation --------------------------------------------

def test_report_rendered_attestation_recorded(tmp_path, monkeypatch):
    rag = _write_rag(tmp_path, "S0", seq=1)
    _start_logger(tmp_path, "S1")
    monkeypatch.setattr(m, "cmd_audit", lambda args: 0)
    rc = main(["session-end", "--rag", str(rag), "--session", "S1", "--summary", "x",
               "--report-rendered"])
    assert rc == 0
    assert _marker(rag)["steps"]["report_rendered"] is True
