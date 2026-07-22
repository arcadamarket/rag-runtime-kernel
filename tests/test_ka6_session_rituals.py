"""KA-6 — machine-enforced session-start / session-end rituals (KA-10 arc).

The eBay S2/S4 governance freeze was a *hand-scripted-ritual* failure: an agent
ran the opening/closing steps by hand and skipped one (it closed on
``configure``/``audit`` and never ``checkpoint``-ed, so ``meta.written_by_session``
froze). KA-4 closed the close-without-checkpoint hole; KA-6 removes the
hand-scripting surface entirely by collapsing each ritual into ONE ordered,
fail-loud command:

  session-start = carry-forward gate (fail-loud) -> gc dry-run -> open logger
  session-end   = checkpoint -> close logger (KA-4 gate) -> audit (fail-loud)

These tests assert the orchestration *contract* — order, fail-fast, and the
fail-loud exits — isolating it from the underlying verify/audit engines (which
carry their own coverage) via monkeypatch where a fully audit-clean tmp RAG is
not the unit under test.
"""

from __future__ import annotations

import json
from pathlib import Path

import rag_kernel.__main__ as m
from rag_kernel.__main__ import main, _carry_forward_gate
from rag_kernel.session_logger import LOG_FILE_PREFIX, LOG_FILE_EXT


def _events(log_path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _log_path(tmp_path: Path, sid: str) -> Path:
    return tmp_path / f"{LOG_FILE_PREFIX}{sid}{LOG_FILE_EXT}"


def _write_rag(tmp_path: Path, written_by: str, seq: int = 1) -> Path:
    """A minimal RAG with the meta a real checkpoint stamps + reads."""
    rag_path = tmp_path / "RAG_MASTER.json"
    rag_path.write_text(
        json.dumps({
            "meta": {"written_by_session": written_by, "last_checkpoint_seq": seq},
            "sessions_recent": [],
        }),
        encoding="utf-8",
    )
    return rag_path


# --- the carry-forward gate predicate, in isolation -----------------------

def test_gate_fails_when_rag_missing(tmp_path):
    ok, findings = _carry_forward_gate(tmp_path / "RAG_MASTER.json")
    assert ok is False
    assert any("not found" in f for f in findings)


def test_gate_fails_when_rag_unreadable(tmp_path):
    (tmp_path / "RAG_MASTER.json").write_text("{ not valid json", encoding="utf-8")
    ok, findings = _carry_forward_gate(tmp_path / "RAG_MASTER.json")
    assert ok is False
    # A corrupt RAG must surface as a finding, never as an exception.
    assert findings


def test_gate_never_raises_returns_findings(tmp_path):
    # Contract: the gate converts any fault into a fail-loud finding list.
    ok, findings = _carry_forward_gate(tmp_path / "nope" / "RAG_MASTER.json")
    assert ok is False and isinstance(findings, list) and findings


# --- session-start orchestration ------------------------------------------

def test_session_start_refuses_on_failed_gate_and_opens_no_log(tmp_path, monkeypatch):
    rag = _write_rag(tmp_path, "S0")
    monkeypatch.setattr(m, "_carry_forward_gate", lambda *a, **k: (False, ["boom"]))
    rc = main(["session-start", "S1", "--rag", str(rag), "--no-gc"])
    assert rc == 1
    assert not _log_path(tmp_path, "S1").exists()  # no session opened on a red gate


def test_session_start_force_bypasses_failed_gate(tmp_path, monkeypatch):
    # --force gets past a red gate; the legacy one-shot open (--no-attest-gate)
    # then opens the logger without the KA-14 attestation handshake.
    rag = _write_rag(tmp_path, "S0")
    monkeypatch.setattr(m, "_carry_forward_gate", lambda *a, **k: (False, ["boom"]))
    rc = main(["session-start", "S1", "--rag", str(rag), "--no-gc",
               "--force", "--no-attest-gate"])
    assert rc == 0
    assert _log_path(tmp_path, "S1").exists()


def test_session_start_clean_gate_requires_attestation_not_logger(tmp_path, monkeypatch, capsys):
    # KA-14: a clean gate no longer opens the logger in one shot. Phase 1 renders
    # the rule digest and demands attestation; the session is NOT yet READY.
    rag = _write_rag(tmp_path, "S0")
    monkeypatch.setattr(m, "_carry_forward_gate", lambda *a, **k: (True, []))
    rc = main(["session-start", "S1", "--rag", str(rag), "--no-gc"])
    assert rc == 0
    assert not _log_path(tmp_path, "S1").exists()  # logger NOT opened in phase 1
    out = capsys.readouterr().out
    assert "Attestation REQUIRED" in out and "--attest" in out


def test_session_start_no_attest_gate_opens_one_shot(tmp_path, monkeypatch):
    # The escape hatch still opens the logger in one shot on a clean gate.
    rag = _write_rag(tmp_path, "S0")
    monkeypatch.setattr(m, "_carry_forward_gate", lambda *a, **k: (True, []))
    rc = main(["session-start", "S1", "--rag", str(rag), "--no-gc", "--no-attest-gate"])
    assert rc == 0
    evs = _events(_log_path(tmp_path, "S1"))
    assert any(e["event"] == "session_start" for e in evs)


def test_session_start_runs_gc_dry_run(tmp_path, monkeypatch, capsys):
    rag = _write_rag(tmp_path, "S0")
    monkeypatch.setattr(m, "_carry_forward_gate", lambda *a, **k: (True, []))
    rc = main(["session-start", "S1", "--rag", str(rag), "--gc-path", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "GC (dry-run)" in out and "DRY RUN" in out


def test_session_start_no_gc_skips_scan(tmp_path, monkeypatch, capsys):
    rag = _write_rag(tmp_path, "S0")
    monkeypatch.setattr(m, "_carry_forward_gate", lambda *a, **k: (True, []))
    main(["session-start", "S1", "--rag", str(rag), "--no-gc"])
    assert "GC: skipped" in capsys.readouterr().out


def test_session_start_bootmap_root_is_project_root_not_gcpath(tmp_path, monkeypatch, capsys):
    # BOOTMAP-BOOTROOT-FIX (S170, E-074): the domain boot-map must diff the live
    # tree against the PROJECT ROOT (rag_dir.parent) — the same root session-end
    # seals the baseline against — and NEVER against --gc-path/CWD. The regression:
    # run per governance_runtime from RAG/ (so --gc-path/CWD = the RAG subdir), the
    # prior code keyed boot_root off --gc-path, walked RAG/, and reported every
    # project-root-keyed baseline path as DELETED — a spurious full turnover on an
    # unchanged tree. This pins boot_root to rag_dir.parent, so --gc-path only
    # steers the GC scan, never the boot-map root.
    from rag_kernel import bootmap

    proj = tmp_path
    rag_dir = proj / "RAG"
    rag_dir.mkdir()
    (proj / "README.md").write_text("hi", encoding="utf-8")  # a governed root file
    rag = _write_rag(rag_dir, "S0")
    # Seal the baseline against the PROJECT ROOT, exactly like session-end does.
    bootmap.refresh_baseline(proj, rag_dir, "S0")

    monkeypatch.setattr(m, "_carry_forward_gate", lambda *a, **k: (True, []))
    # --gc-path points at the RAG subdir — what the documented `cd RAG/` invocation
    # makes CWD. The boot-map root must NOT follow it.
    rc = main(["session-start", "S1", "--rag", str(rag),
               "--gc-path", str(rag_dir), "--no-attest-gate"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Domain map:" in out and "since S0:" in out
    # The baseline is intact on disk, so nothing is deleted. The bug reported the
    # whole project-root-keyed baseline as deleted (>=2 here).
    assert "-0 deleted" in out


# --- session-end orchestration --------------------------------------------

def _start_logger(tmp_path: Path, sid: str) -> None:
    assert main(["session", "start", sid, "--rag-dir", str(tmp_path)]) == 0


def test_session_end_checkpoints_then_closes_then_audits(tmp_path, monkeypatch):
    rag = _write_rag(tmp_path, "S0", seq=1)  # prior session — S1 not yet banked
    _start_logger(tmp_path, "S1")
    seen = {}
    monkeypatch.setattr(m, "cmd_audit", lambda args: (seen.__setitem__("audit", True), 0)[1])

    rc = main(["session-end", "--rag", str(rag), "--session", "S1",
               "--summary", "did the thing"])
    assert rc == 0

    data = json.loads(rag.read_text(encoding="utf-8"))
    # (1) checkpoint stamped this session + bumped the seq...
    assert data["meta"]["written_by_session"] == "S1"
    assert data["meta"]["last_checkpoint_seq"] == 2
    # (2) ...so the KA-4 close gate passed and a session_end marker was written...
    assert any(e["event"] == "session_end" for e in _events(_log_path(tmp_path, "S1")))
    # (3) ...and the audit ran last.
    assert seen.get("audit") is True


def test_session_end_aborts_before_close_when_checkpoint_fails(tmp_path, monkeypatch):
    # No RAG file -> checkpoint returns non-zero -> ritual aborts before close/audit.
    _start_logger(tmp_path, "S1")
    called = {"audit": False}
    monkeypatch.setattr(m, "cmd_audit", lambda args: called.__setitem__("audit", True) or 0)

    rc = main(["session-end", "--rag", str(tmp_path / "missing.json"),
               "--session", "S1", "--summary", "x"])
    assert rc != 0
    assert called["audit"] is False  # audit never reached
    assert not any(e["event"] == "session_end" for e in _events(_log_path(tmp_path, "S1")))


def test_session_end_fails_loud_when_audit_fails(tmp_path, monkeypatch):
    rag = _write_rag(tmp_path, "S0", seq=1)
    _start_logger(tmp_path, "S1")
    monkeypatch.setattr(m, "cmd_audit", lambda args: 1)  # audit fails

    rc = main(["session-end", "--rag", str(rag), "--session", "S1", "--summary", "x"])
    assert rc != 0  # a red audit fails the whole ritual
    # checkpoint + close still happened (audit is the last, verifying step)
    assert json.loads(rag.read_text(encoding="utf-8"))["meta"]["written_by_session"] == "S1"
