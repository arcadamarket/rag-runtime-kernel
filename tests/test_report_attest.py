"""REPORT-RENDER-ATTEST (E-062, recurrence of E-060) — report provenance guard.

`report` appends a `report-attest: sha256(body)` token; `report --verify` recomputes
it and fails loud on mismatch/absence. A hand-typed / re-prosed / summarized report
cannot carry a matching token, so re-prosing becomes machine-detectable — the exact
drift that recurred as E-060 (S136) and E-062 (S149).
"""

from __future__ import annotations

import json

import pytest

from rag_kernel.__main__ import (
    main,
    _report_attest_token,
    _append_report_attest,
    _verify_report_attest,
    _normalize_report_body,
    _REPORT_ATTEST_SEP,
)


# --- token + normalization ----------------------------------------------------

def test_token_is_deterministic():
    body = "### 1 · At a glance\nsome | table | row"
    assert _report_attest_token(body) == _report_attest_token(body)
    assert _report_attest_token(body).startswith("sha256:")


def test_normalization_tolerates_trailing_ws_not_content():
    a = "line one\nline two"
    b = "line one   \nline two\n\n"        # trailing ws + blank lines
    c = "line one\nline TWO"               # content change
    assert _report_attest_token(a) == _report_attest_token(b)
    assert _report_attest_token(a) != _report_attest_token(c)
    assert _normalize_report_body(b) == "line one\nline two"


# --- append + verify round-trip ----------------------------------------------

def test_append_then_verify_passes():
    body = "### canonical body\n- item"
    text = _append_report_attest(body)
    ok, detail = _verify_report_attest(text)
    assert ok is True
    assert "AUTHENTIC" in detail


def test_verify_fails_on_tampered_body():
    body = "### canonical body\n- item ONE"
    text = _append_report_attest(body)
    tampered = text.replace("item ONE", "item TWO")   # re-prose one word
    ok, detail = _verify_report_attest(tampered)
    assert ok is False
    assert "does not match" in detail


def test_verify_fails_on_missing_token():
    ok, detail = _verify_report_attest("### a report with no token\n- item")
    assert ok is False
    assert "no report-attest token" in detail


def test_verify_fails_on_stripped_token_line():
    body = "### body\n- x"
    text = _append_report_attest(body)
    # keep the body, drop the attest trailer entirely (what a re-prose looks like)
    stripped = text.split(_REPORT_ATTEST_SEP)[0]
    ok, _ = _verify_report_attest(stripped)
    assert ok is False


# --- CLI round-trip -----------------------------------------------------------

@pytest.fixture
def rag_file(tmp_path):
    hot = {
        "meta": {"last_checkpoint_seq": 234, "written_by_session": "S149",
                 "rag_version": "1.8.0", "last_updated_utc": "2026-07-16T00:00:00Z"},
        "inference_ledger": [{"id": "INS-2", "disposition": "OPEN", "summary": "x"}],
        "tracked_items": [
            {"id": "OPEN-1", "title": "open one", "status": "OPEN", "kind": "TASK",
             "session": "S40", "note": "", "superseded_by": None, "history": []},
        ],
    }
    p = tmp_path / "RAG_MASTER.json"
    p.write_text(json.dumps(hot, indent=2), encoding="utf-8")
    return p


def test_cli_render_carries_verifiable_token(tmp_path, rag_file, capsys):
    rc = main(["report", "--session", "S149", "--rag", str(rag_file), "--no-live"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "report-attest: sha256:" in out
    # a verbatim capture verifies OK
    f = tmp_path / "rendered.md"
    f.write_text(out, encoding="utf-8")
    rc_v = main(["report", "--verify", str(f)])
    assert rc_v == 0
    assert "OK — AUTHENTIC" in capsys.readouterr().out


def test_cli_verify_rejects_reprosed(tmp_path, rag_file, capsys):
    main(["report", "--session", "S149", "--rag", str(rag_file), "--no-live"])
    out = capsys.readouterr().out
    reprosed = out.replace("OPEN-1", "OPEN-1 (my hand-edited note)")
    f = tmp_path / "reprosed.md"
    f.write_text(reprosed, encoding="utf-8")
    rc = main(["report", "--verify", str(f)])
    assert rc == 1
    assert "FAIL" in capsys.readouterr().out


def test_cli_verify_rejects_no_token(tmp_path, capsys):
    f = tmp_path / "handauthored.md"
    f.write_text("## RAG Runtime Kernel — Status Report (S149 close)\nI typed this myself.\n",
                 encoding="utf-8")
    rc = main(["report", "--verify", str(f)])
    assert rc == 1
    assert "FAIL" in capsys.readouterr().out
