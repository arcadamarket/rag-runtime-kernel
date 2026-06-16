"""Regression tests for FIX-12 / U4 — bootstrap session log captures real events.

The SessionLogger class was always capable; the gap was the CLI bootstrap path:
each verb ran as a separate one-shot process that never appended to the ongoing
session log, so a CLI-driven deploy produced a near-empty observability artifact
(only start/end markers). FIX-12 adds attach/detach (append without re-emitting
lifecycle markers) plus a central dispatch wrapper that appends a real
tool_invocation for every verb (comprehensive scope), and fixes `session close`
which previously wrote a spurious SECOND session_start before closing.
"""

from __future__ import annotations

import argparse
import json
import os
import time as _time
from pathlib import Path

import pytest

from rag_kernel.session_logger import (
    SessionLogger,
    LOG_FILE_PREFIX,
    LOG_FILE_EXT,
)
from rag_kernel import __main__ as cli


def _events(log_path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _ns(**kw) -> argparse.Namespace:
    return argparse.Namespace(**kw)


def _start_log(tmp_path: Path, sid: str = "S1") -> Path:
    """Create an ongoing bootstrap log (session_start written, no session_end)."""
    lg = SessionLogger(sid, log_dir=tmp_path)
    lg.open()
    lg.detach()
    return lg.log_path


# --- inc1: attach / detach -------------------------------------------------

def test_open_close_still_emit_markers(tmp_path):
    lg = SessionLogger("S1", log_dir=tmp_path)
    lg.open()
    lg.info("hello")
    lg.close()
    evs = _events(lg.log_path)
    assert evs[0]["event"] == "session_start"
    assert evs[-1]["event"] == "session_end"
    assert any(e["event"] == "info" for e in evs)


def test_attach_does_not_emit_session_start(tmp_path):
    lg = SessionLogger("S1", log_dir=tmp_path)
    lg.open()
    lg.info("first")
    lg.detach()  # close WITHOUT session_end

    # A fresh logger (simulating a new short-lived CLI process) attaches.
    lg2 = SessionLogger("S1", log_dir=tmp_path)
    lg2.attach()
    lg2.info("second")
    lg2.detach()

    evs = _events(lg.log_path)
    starts = [e for e in evs if e["event"] == "session_start"]
    ends = [e for e in evs if e["event"] == "session_end"]
    assert len(starts) == 1  # only the original open()
    assert len(ends) == 0  # detach never writes session_end

    seqs = [e["seq"] for e in evs]
    assert seqs == sorted(seqs)  # monotonic across processes
    assert len(set(seqs)) == len(seqs)  # no duplicate seq


def test_detach_does_not_emit_session_end(tmp_path):
    lg = SessionLogger("S1", log_dir=tmp_path)
    lg.open()
    lg.detach()
    assert not any(e["event"] == "session_end" for e in _events(lg.log_path))


def test_attach_equiv_open_emit_start_false(tmp_path):
    lg = SessionLogger("S1", log_dir=tmp_path)
    lg.open(emit_start=False)
    lg.close(emit_end=False)
    assert _events(lg.log_path) == []  # nothing written either way


# --- session close fix -----------------------------------------------------

def test_cmd_session_close_no_duplicate_start(tmp_path):
    cli.cmd_session(_ns(session_action="start", session_id="S1", rag_dir=tmp_path))
    log = tmp_path / f"{LOG_FILE_PREFIX}S1{LOG_FILE_EXT}"
    assert log.exists()

    cli.cmd_session(_ns(session_action="close", session_id="S1", rag_dir=tmp_path))
    evs = _events(log)
    starts = [e for e in evs if e["event"] == "session_start"]
    ends = [e for e in evs if e["event"] == "session_end"]
    assert len(starts) == 1  # FIX-12: no spurious second session_start
    assert len(ends) == 1


# --- discovery -------------------------------------------------------------

def test_active_session_log_none_when_empty(tmp_path):
    assert cli._active_session_log(tmp_path) is None


def test_active_session_log_picks_most_recent(tmp_path):
    a = tmp_path / f"{LOG_FILE_PREFIX}S1{LOG_FILE_EXT}"
    b = tmp_path / f"{LOG_FILE_PREFIX}S2{LOG_FILE_EXT}"
    a.write_text("", encoding="utf-8")
    b.write_text("", encoding="utf-8")
    now = _time.time()
    os.utime(a, (now - 100, now - 100))
    os.utime(b, (now, now))
    assert cli._active_session_log(tmp_path) == b


# --- central wrapper (comprehensive instrumentation) -----------------------

def test_wrapper_appends_tool_invocation_on_success(tmp_path):
    log = _start_log(tmp_path)
    calls = []
    rc = cli._dispatch_with_bootstrap_log(
        "audit", lambda a: calls.append(1) or 0, _ns(rag_dir=tmp_path)
    )
    assert rc == 0
    assert calls == [1]
    ti = [e for e in _events(log) if e["event"] == "tool_invocation"]
    assert len(ti) == 1
    assert ti[0]["data"]["command"] == "audit"
    assert ti[0]["data"]["success"] is True


def test_wrapper_noop_when_no_active_log(tmp_path):
    ran = []
    rc = cli._dispatch_with_bootstrap_log(
        "verify", lambda a: ran.append(1) or 0, _ns(rag_dir=tmp_path)
    )
    assert rc == 0
    assert ran == [1]
    assert cli._active_session_log(tmp_path) is None  # wrapper created no log


def test_wrapper_logs_failure_and_propagates(tmp_path):
    log = _start_log(tmp_path)

    class Boom(RuntimeError):
        pass

    def handler(args):
        raise Boom("kaboom")

    with pytest.raises(Boom):
        cli._dispatch_with_bootstrap_log("checkpoint", handler, _ns(rag_dir=tmp_path))

    ti = [e for e in _events(log) if e["event"] == "tool_invocation"]
    assert len(ti) == 1
    assert ti[0]["data"]["success"] is False
    assert ti[0]["data"].get("error_type") == "Boom"
    assert ti[0]["data"]["command"] == "checkpoint"


def test_wrapper_excludes_session_serve_mcp(tmp_path):
    log = _start_log(tmp_path)
    for verb in ("session", "serve", "mcp"):
        cli._dispatch_with_bootstrap_log(verb, lambda a: 0, _ns(rag_dir=tmp_path))
    assert not any(e["event"] == "tool_invocation" for e in _events(log))


def test_wrapper_uses_rag_file_parent(tmp_path):
    log = _start_log(tmp_path)
    rag_file = tmp_path / "RAG_MASTER.json"
    rag_file.write_text("{}", encoding="utf-8")
    cli._dispatch_with_bootstrap_log("items", lambda a: 0, _ns(rag=str(rag_file)))
    assert any(
        e["event"] == "tool_invocation" and e["data"]["command"] == "items"
        for e in _events(log)
    )


def test_wrapper_systemexit_zero_is_success(tmp_path):
    log = _start_log(tmp_path)

    def handler(args):
        raise SystemExit(0)

    with pytest.raises(SystemExit):
        cli._dispatch_with_bootstrap_log("verify", handler, _ns(rag_dir=tmp_path))

    ti = [e for e in _events(log) if e["event"] == "tool_invocation"]
    assert len(ti) == 1
    assert ti[0]["data"]["success"] is True


def test_wrapper_systemexit_nonzero_is_failure(tmp_path):
    log = _start_log(tmp_path)

    def handler(args):
        raise SystemExit(2)

    with pytest.raises(SystemExit):
        cli._dispatch_with_bootstrap_log("init", handler, _ns(rag_dir=tmp_path))

    ti = [e for e in _events(log) if e["event"] == "tool_invocation"]
    assert len(ti) == 1
    assert ti[0]["data"]["success"] is False
    assert ti[0]["data"]["command"] == "init"


def test_comprehensive_readonly_and_mutating_both_logged(tmp_path):
    """Comprehensive scope: read-only AND mutating verbs both land in the log."""
    log = _start_log(tmp_path)
    for verb in ("health", "verify", "audit", "items", "checkpoint", "render"):
        cli._dispatch_with_bootstrap_log(verb, lambda a: 0, _ns(rag_dir=tmp_path))
    logged = {
        e["data"]["command"]
        for e in _events(log)
        if e["event"] == "tool_invocation"
    }
    assert {"health", "verify", "audit", "items", "checkpoint", "render"} <= logged
