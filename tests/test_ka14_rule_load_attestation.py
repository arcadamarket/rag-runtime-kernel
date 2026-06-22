"""KA-14 — session-start rule-load attestation gate.

The fresh-deploy root cause (eBay S0/S105): the HOT operating_protocol rule bodies
sit on disk in the RAG but a fresh agent never loaded them into cognition — it ran
the ritual blind to its own rules. KA-14 makes rule-load a two-phase, token-attested
handshake so READY is unreachable without the rules in context:

    phase 1  session-start <sid>            -> render digest + rule_load(attested=false)
                                               + token; logger NOT opened
    phase 2  session-start <sid> --attest T -> verify T == live digest token,
                                               attested=true, open logger (READY)

These tests assert the handshake contract: the digest content, the marker lifecycle,
the deterministic token, and every fail-loud rejection. The carry-forward gate engine
(verify/audit) carries its own coverage and is monkeypatched green here so the unit
under test is the attestation gate alone.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import rag_kernel.__main__ as m
from rag_kernel.__main__ import main, _compute_rule_digest, _rule_summary
from rag_kernel.session_logger import LOG_FILE_PREFIX, LOG_FILE_EXT


_OP = {
    "tool_hierarchy": {
        "file_read_write_list": "File tools first.",
        "file_copy_move_git_shell": "tmux first.",
        "pytest_and_testing": "tmux primary.",
    },
    "circuit_breaker": "Rule 5. (1) Pre-state before 3+ tool calls toward one goal. "
                       "(2) Two-Strike Rule. (3) Edit-First.",
    "strict_obey": "Rule 16. Obey EXACTLY what the operator instructs; no scope creep.",
}


def _write_rag(tmp_path: Path, op: "dict | None" = None) -> Path:
    rag_path = tmp_path / "RAG_MASTER.json"
    rag_path.write_text(
        json.dumps({
            "meta": {"written_by_session": "S0", "last_checkpoint_seq": 1},
            "operating_protocol": _OP if op is None else op,
            "sessions_recent": [],
        }),
        encoding="utf-8",
    )
    return rag_path


def _log_path(tmp_path: Path, sid: str) -> Path:
    return tmp_path / f"{LOG_FILE_PREFIX}{sid}{LOG_FILE_EXT}"


def _marker(rag_path: Path) -> dict:
    return json.loads(rag_path.read_text(encoding="utf-8"))["rule_load"]


def _green_gate(monkeypatch):
    monkeypatch.setattr(m, "_carry_forward_gate", lambda *a, **k: (True, []))


def _token_from_stdout(out: str) -> str:
    mt = re.search(r"--attest\s+([0-9a-f]{12})", out)
    assert mt, f"no attestation token in phase-1 output:\n{out}"
    return mt.group(1)


# --- digest + token primitives --------------------------------------------

def test_digest_lists_every_rule_key():
    rag = {"operating_protocol": _OP}
    lines, _ = _compute_rule_digest(rag)
    assert [k for k, _ in lines] == list(_OP.keys())


def test_dict_rule_summarized_as_sub_rules():
    summ = _rule_summary(_OP["tool_hierarchy"])
    assert "3 sub-rules" in summ and "file_read_write_list" in summ


def test_long_string_rule_truncated():
    summ = _rule_summary("x" * 500)
    assert summ.endswith("…") and len(summ) <= 112


def test_token_is_deterministic_and_12_hex():
    rag = {"operating_protocol": _OP}
    _, t1 = _compute_rule_digest(rag)
    _, t2 = _compute_rule_digest(rag)
    assert t1 == t2 and re.fullmatch(r"[0-9a-f]{12}", t1)


def test_token_changes_when_rules_change():
    _, t_before = _compute_rule_digest({"operating_protocol": _OP})
    mutated = dict(_OP, new_rule="Rule N. A freshly added rule.")
    _, t_after = _compute_rule_digest({"operating_protocol": mutated})
    assert t_before != t_after


def test_empty_operating_protocol_yields_stable_token():
    lines, token = _compute_rule_digest({})
    assert lines == [] and re.fullmatch(r"[0-9a-f]{12}", token)


# --- phase 1: render + marker, no logger -----------------------------------

def test_phase1_renders_digest_writes_marker_no_logger(tmp_path, monkeypatch, capsys):
    rag = _write_rag(tmp_path)
    _green_gate(monkeypatch)
    rc = main(["session-start", "S1", "--rag", str(rag), "--no-gc"])
    assert rc == 0
    out = capsys.readouterr().out
    # every rule key is rendered into context
    for key in _OP:
        assert key in out
    assert "Attestation REQUIRED" in out
    # logger NOT opened
    assert not _log_path(tmp_path, "S1").exists()
    # marker recorded, attested=false, token present
    mk = _marker(rag)
    assert mk["session"] == "S1" and mk["attested"] is False
    assert re.fullmatch(r"[0-9a-f]{12}", mk["token"])
    assert mk["rule_count"] == len(_OP) and mk["attested_utc"] is None


# --- phase 2: attestation handshake ----------------------------------------

def test_phase2_correct_token_opens_logger_and_marks_attested(tmp_path, monkeypatch, capsys):
    rag = _write_rag(tmp_path)
    _green_gate(monkeypatch)
    main(["session-start", "S1", "--rag", str(rag), "--no-gc"])
    token = _token_from_stdout(capsys.readouterr().out)

    rc = main(["session-start", "S1", "--rag", str(rag), "--attest", token])
    assert rc == 0
    assert _log_path(tmp_path, "S1").exists()
    evs = [json.loads(l) for l in _log_path(tmp_path, "S1").read_text().splitlines() if l.strip()]
    assert any(e["event"] == "session_start" for e in evs)
    mk = _marker(rag)
    assert mk["attested"] is True and mk["attested_utc"] is not None


def test_phase2_wrong_token_fails_loud_no_logger(tmp_path, monkeypatch):
    rag = _write_rag(tmp_path)
    _green_gate(monkeypatch)
    main(["session-start", "S1", "--rag", str(rag), "--no-gc"])
    rc = main(["session-start", "S1", "--rag", str(rag), "--attest", "deadbeef0000"])
    assert rc == 1
    assert not _log_path(tmp_path, "S1").exists()
    assert _marker(rag)["attested"] is False  # unchanged


def test_phase2_without_phase1_marker_fails(tmp_path, monkeypatch):
    rag = _write_rag(tmp_path)
    _green_gate(monkeypatch)
    # no phase 1 run -> no rule_load marker
    rc = main(["session-start", "S1", "--rag", str(rag), "--attest", "000000000000"])
    assert rc == 1
    assert not _log_path(tmp_path, "S1").exists()


def test_phase2_session_mismatch_fails(tmp_path, monkeypatch, capsys):
    rag = _write_rag(tmp_path)
    _green_gate(monkeypatch)
    main(["session-start", "S1", "--rag", str(rag), "--no-gc"])
    token = _token_from_stdout(capsys.readouterr().out)
    # attest a DIFFERENT session id than the one phase 1 opened
    rc = main(["session-start", "S2", "--rag", str(rag), "--attest", token])
    assert rc == 1
    assert not _log_path(tmp_path, "S2").exists()


def test_stale_token_after_rules_change_is_rejected(tmp_path, monkeypatch, capsys):
    rag = _write_rag(tmp_path)
    _green_gate(monkeypatch)
    main(["session-start", "S1", "--rag", str(rag), "--no-gc"])
    stale = _token_from_stdout(capsys.readouterr().out)
    # rules change between phase 1 and attestation
    data = json.loads(rag.read_text(encoding="utf-8"))
    data["operating_protocol"]["new_rule"] = "Rule N. Added after phase 1."
    rag.write_text(json.dumps(data), encoding="utf-8")
    rc = main(["session-start", "S1", "--rag", str(rag), "--attest", stale])
    assert rc == 1  # token no longer matches the live digest
    assert not _log_path(tmp_path, "S1").exists()


def test_attest_after_rules_change_with_fresh_token_succeeds(tmp_path, monkeypatch, capsys):
    rag = _write_rag(tmp_path)
    _green_gate(monkeypatch)
    main(["session-start", "S1", "--rag", str(rag), "--no-gc"])
    capsys.readouterr()
    # rules change; re-run phase 1 to get the CURRENT token
    data = json.loads(rag.read_text(encoding="utf-8"))
    data["operating_protocol"]["new_rule"] = "Rule N. Added after phase 1."
    rag.write_text(json.dumps(data), encoding="utf-8")
    main(["session-start", "S1", "--rag", str(rag), "--no-gc"])
    fresh = _token_from_stdout(capsys.readouterr().out)
    rc = main(["session-start", "S1", "--rag", str(rag), "--attest", fresh])
    assert rc == 0 and _log_path(tmp_path, "S1").exists()


def test_phase1_preserves_bak_parity(tmp_path, monkeypatch):
    rag = _write_rag(tmp_path)
    _green_gate(monkeypatch)
    main(["session-start", "S1", "--rag", str(rag), "--no-gc"])
    bak = rag.parent / "RAG_MASTER.json.bak"
    assert bak.exists()
    assert bak.read_text(encoding="utf-8") == rag.read_text(encoding="utf-8")
