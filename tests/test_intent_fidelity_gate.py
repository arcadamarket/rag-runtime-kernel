"""KA-INTENT-FIDELITY inc1 — session-end handoff-persistence gate (integration).

Two behaviours, driven through the CLI:

1. ``checkpoint --handoff X`` persists X VERBATIM into a structured
   ``next_session_directive`` (decision-of-record); no handoff -> no field;
   ``--dry-run`` writes nothing.
2. The session-end SEAL GATE refuses to advance toward ``transfer_ready`` when a
   stated handoff is not persisted verbatim — the E-055 / S146 guard. The refusal
   fires at step 1b, BEFORE the audit step, so it needs no audit-clean fixture.
"""

from __future__ import annotations

import json
from pathlib import Path

from rag_kernel.__main__ import main


def _write(p: Path, obj: dict) -> None:
    p.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def _load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _minimal_rag(sid: str = "S1") -> dict:
    return {"meta": {"session_id": sid, "written_by_session": sid, "last_checkpoint_seq": 1}}


def _checkpointed_marker(sid: str = "S1") -> dict:
    return {
        "session": sid,
        "phase": "CHECKPOINTED",
        "transfer_ready": False,
        "started_utc": "2026-01-01T00:00:00+00:00",
        "completed_utc": None,
        "steps": {
            "checkpoint": True, "error_log": False, "logger_close": False,
            "audit": False, "report_rendered": False,
        },
    }


# --- checkpoint persistence ---------------------------------------------------

def test_checkpoint_persists_directive_verbatim(tmp_path):
    p = tmp_path / "RAG_MASTER.json"
    _write(p, _minimal_rag("S1"))
    handoff = "Ship KA-INTENT-FIDELITY inc1 first, then inc2"
    rc = main([
        "checkpoint", "--rag", str(p), "--session", "S1",
        "--summary", "work", "--handoff", handoff, "--no-require-session-log",
    ])
    assert rc == 0
    nsd = _load(p).get("next_session_directive")
    assert isinstance(nsd, dict)
    assert nsd["directive"] == handoff          # VERBATIM, byte-for-byte
    assert nsd["session"] == "S1"
    assert nsd["for_session"] == "S2"           # trailing-int increment
    assert "authored_utc" in nsd


def test_checkpoint_without_handoff_leaves_no_directive(tmp_path):
    p = tmp_path / "RAG_MASTER.json"
    _write(p, _minimal_rag("S1"))
    rc = main([
        "checkpoint", "--rag", str(p), "--session", "S1",
        "--summary", "work", "--no-require-session-log",
    ])
    assert rc == 0
    assert "next_session_directive" not in _load(p)


def test_checkpoint_dry_run_persists_nothing(tmp_path):
    p = tmp_path / "RAG_MASTER.json"
    _write(p, _minimal_rag("S1"))
    before = p.read_text(encoding="utf-8")
    rc = main([
        "checkpoint", "--rag", str(p), "--session", "S1", "--summary", "work",
        "--handoff", "would persist this", "--no-require-session-log", "--dry-run",
    ])
    assert rc == 0
    assert p.read_text(encoding="utf-8") == before  # untouched


# --- session-end seal gate ----------------------------------------------------

# SESSION-END-RESUME-HANDOFF (S207). These two cases used to assert a REFUSAL on
# the resume path, and that assertion encoded the defect rather than the contract:
# the handoff is persisted by exactly one code path (cmd_checkpoint in step 1/4),
# which a resumed close skips as "already banked", so a handoff revised after a
# failed first attempt had nowhere to land while the gate kept comparing it to the
# stale stored text. No number of retries could satisfy that — it is what left S206
# unsealable. The contract is now: on resume the stated handoff is RE-BANKED, and
# the gate then verifies the write landed. Teeth are preserved and proved by
# test_close_still_refuses_when_rebank_does_not_land below.

def test_close_rebanks_revised_handoff_on_resume(tmp_path):
    p = tmp_path / "RAG_MASTER.json"
    rag = _minimal_rag("S1")
    rag["next_session_directive"] = {
        "session": "S1", "for_session": "S2", "directive": "OLD stored directive",
    }
    rag["session_close"] = _checkpointed_marker("S1")
    _write(p, rag)
    revised = "REVISED stated directive"
    main([
        "session-end", "--rag", str(p), "--session", "S1",
        "--summary", "x", "--handoff", revised, "--no-report",
    ])
    nsd = _load(p).get("next_session_directive")
    assert isinstance(nsd, dict)
    assert nsd["directive"] == revised   # re-banked VERBATIM, not left stale


def test_close_persists_absent_directive_on_resume(tmp_path):
    # The S146 scenario, resumed: a handoff is STATED and nothing had persisted it.
    # Persisting it serves S146's intent (the successor actually receives the
    # directive) strictly better than refusing and carrying nothing forward.
    p = tmp_path / "RAG_MASTER.json"
    rag = _minimal_rag("S1")
    rag["session_close"] = _checkpointed_marker("S1")
    _write(p, rag)
    main([
        "session-end", "--rag", str(p), "--session", "S1",
        "--summary", "x", "--handoff", "carry this forward", "--no-report",
    ])
    nsd = _load(p).get("next_session_directive")
    assert isinstance(nsd, dict)
    assert nsd["directive"] == "carry this forward"


def test_close_still_refuses_when_rebank_does_not_land(tmp_path, monkeypatch):
    # The gate keeps its teeth. With persistence neutered, the re-bank reports
    # success but banks nothing, and step 1b must still refuse the seal — this is
    # the "persisted lossily / not at all" half of E-055 that survives the fix.
    import rag_kernel.__main__ as m
    p = tmp_path / "RAG_MASTER.json"
    rag = _minimal_rag("S1")
    rag["session_close"] = _checkpointed_marker("S1")
    _write(p, rag)
    monkeypatch.setattr(m, "cmd_checkpoint", lambda ns: 0)  # banks nothing
    rc = m.main([
        "session-end", "--rag", str(p), "--session", "S1",
        "--summary", "x", "--handoff", "carry this forward", "--no-report",
    ])
    assert rc == 1
    assert _load(p)["session_close"]["transfer_ready"] is False
    assert "next_session_directive" not in _load(p)
