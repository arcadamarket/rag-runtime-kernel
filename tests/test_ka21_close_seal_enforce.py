"""KA-21 — CLOSE-SEAL-ENFORCE (S172).

The carry-forward gate's step 3 (KA-16) catches a close that STARTED and aborted
(``session_close`` marker with ``transfer_ready=false``). It does NOT catch the
complementary hole: a prior session that never ran ``session-end`` AT ALL, so no
close event and no ``AUDIT_CANONICAL_REPORT`` ever existed — the S157 gap,
independently reproduced as the eBay S14 CLOSE-GAP (UNIVERSAL per Rule 15). This
gate refuses to open a new session forward over such an unsealed predecessor.

A session counts as SEALED iff its ``session_close`` marker reached
``transfer_ready`` AND its ``AUDIT_CANONICAL_REPORT_<sid>.md`` transfer surface
exists on disk (Rule 23). The check is gated on the close protocol being in use
(a ``session_close`` marker exists), so a legacy/un-migrated RAG is untouched.
"""

from __future__ import annotations

import json
from pathlib import Path

import rag_kernel.__main__ as m
from rag_kernel.__main__ import (
    main, _unsealed_prior_session, _last_sealed_session,
    CLOSE_REPORT_PREFIX, CLOSE_REPORT_EXT,
)
from rag_kernel.session_logger import LOG_FILE_PREFIX, LOG_FILE_EXT


def _rag_with(tmp_path: Path, close_marker=None) -> Path:
    rag_path = tmp_path / "RAG_MASTER.json"
    body = {
        "meta": {"written_by_session": "S5", "last_checkpoint_seq": 1},
        "operating_protocol": {"strict_obey": "Rule 16."},
        "inference_ledger": [],
        "sessions_recent": [],
    }
    if close_marker is not None:
        body["session_close"] = close_marker
    rag_path.write_text(json.dumps(body), encoding="utf-8")
    return rag_path


def _log(tmp_path: Path, sid: str) -> None:
    (tmp_path / f"{LOG_FILE_PREFIX}{sid}{LOG_FILE_EXT}").write_text(
        json.dumps({"event": "session_start", "session": sid}) + "\n", encoding="utf-8"
    )


def _report(tmp_path: Path, sid: str) -> None:
    (tmp_path / f"{CLOSE_REPORT_PREFIX}{sid}{CLOSE_REPORT_EXT}").write_text(
        "canonical report\n", encoding="utf-8"
    )


def _sealed(sid: str) -> dict:
    return {"session": sid, "phase": "COMPLETE", "transfer_ready": True}


# --- _last_sealed_session ---------------------------------------------------

def test_last_sealed_requires_transfer_ready_and_report(tmp_path):
    rag = _rag_with(tmp_path, _sealed("S5"))
    _report(tmp_path, "S5")
    assert _last_sealed_session(rag, tmp_path) == "S5"


def test_last_sealed_none_when_report_missing(tmp_path):
    rag = _rag_with(tmp_path, _sealed("S5"))  # no report artifact on disk
    assert _last_sealed_session(rag, tmp_path) is None


def test_last_sealed_none_when_not_transfer_ready(tmp_path):
    rag = _rag_with(tmp_path, {"session": "S5", "phase": "CLOSED", "transfer_ready": False})
    _report(tmp_path, "S5")
    assert _last_sealed_session(rag, tmp_path) is None


# --- _unsealed_prior_session ------------------------------------------------

def test_clean_when_latest_prior_is_sealed(tmp_path):
    rag = _rag_with(tmp_path, _sealed("S5"))
    _report(tmp_path, "S5")
    _log(tmp_path, "S5")
    # starting S6; the only prior log (S5) is sealed
    assert _unsealed_prior_session(rag, tmp_path, "S6") is None


def test_flags_prior_session_that_ran_but_never_sealed(tmp_path):
    # S4 is the last sealed; S5 ran (log present) but never sealed. Start S6.
    rag = _rag_with(tmp_path, _sealed("S4"))
    _report(tmp_path, "S4")
    _log(tmp_path, "S4")
    _log(tmp_path, "S5")
    assert _unsealed_prior_session(rag, tmp_path, "S6") == "S5"


def test_new_session_own_log_is_excluded(tmp_path):
    # Even if S6's log already exists, starting S6 must not flag itself.
    rag = _rag_with(tmp_path, _sealed("S5"))
    _report(tmp_path, "S5")
    _log(tmp_path, "S5")
    _log(tmp_path, "S6")
    assert _unsealed_prior_session(rag, tmp_path, "S6") is None


def test_legacy_rag_without_close_marker_is_skipped(tmp_path):
    # No session_close marker at all -> close protocol not in use -> no enforcement.
    rag = _rag_with(tmp_path, None)
    _log(tmp_path, "S4")
    _log(tmp_path, "S5")
    assert _unsealed_prior_session(rag, tmp_path, "S6") is None


def test_picks_highest_unsealed_when_several(tmp_path):
    rag = _rag_with(tmp_path, _sealed("S4"))
    _report(tmp_path, "S4")
    for s in ("S4", "S5", "S6", "S7"):
        _log(tmp_path, s)
    assert _unsealed_prior_session(rag, tmp_path, "S8") == "S7"


# --- carry-forward gate integration ----------------------------------------

class _CleanReport:
    def is_clean(self, strict=False):
        return True

    def summary(self):
        return ""


def _stub_verify_audit(monkeypatch):
    import rag_kernel.spec_parser as sp
    import rag_kernel.drift_audit as da
    monkeypatch.setattr(sp.SpecParser, "verify_coherence",
                        staticmethod(lambda *a, **k: []))
    monkeypatch.setattr(da, "audit_file", lambda *a, **k: _CleanReport())


def test_gate_reports_unsealed_prior_finding(tmp_path, monkeypatch):
    _stub_verify_audit(monkeypatch)
    rag = _rag_with(tmp_path, _sealed("S4"))
    _report(tmp_path, "S4")
    _log(tmp_path, "S4")
    _log(tmp_path, "S5")
    ok, findings = m._carry_forward_gate(rag, rag_dir=tmp_path, new_sid="S6")
    assert ok is False
    assert any("unsealed prior session: S5" in f for f in findings)


def test_gate_clean_when_prior_sealed(tmp_path, monkeypatch):
    _stub_verify_audit(monkeypatch)
    rag = _rag_with(tmp_path, _sealed("S5"))
    _report(tmp_path, "S5")
    _log(tmp_path, "S5")
    ok, findings = m._carry_forward_gate(rag, rag_dir=tmp_path, new_sid="S6")
    assert ok is True and findings == []


# --- end-to-end via session-start refusal + --force override ---------------

def test_session_start_refuses_over_unsealed_prior(tmp_path, monkeypatch, capsys):
    _stub_verify_audit(monkeypatch)
    rag = _rag_with(tmp_path, _sealed("S4"))
    _report(tmp_path, "S4")
    _log(tmp_path, "S4")
    _log(tmp_path, "S5")
    rc = main(["session-start", "S6", "--rag", str(rag), "--no-gc"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "unsealed prior session: S5" in err


def test_force_overrides_unsealed_prior(tmp_path, monkeypatch, capsys):
    _stub_verify_audit(monkeypatch)
    rag = _rag_with(tmp_path, _sealed("S4"))
    _report(tmp_path, "S4")
    _log(tmp_path, "S4")
    _log(tmp_path, "S5")
    rc = main(["session-start", "S6", "--rag", str(rag), "--no-gc", "--force"])
    assert rc == 0
