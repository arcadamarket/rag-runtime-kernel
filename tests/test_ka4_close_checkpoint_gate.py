"""KA-4 — checkpoint-to-close enforcement (governance-freeze guard).

The eBay S4 ``ran-but-never-checkpointed`` freeze happened because the CLI
``session close`` wrote a ``session_end`` marker without ever requiring a
checkpoint, leaving ``meta.written_by_session`` stale across sessions. KA-4
makes the kernel *refuse* to close a started session unless that session
checkpointed first — a checkpoint is what stamps ``meta.written_by_session``
with the session id, so its absence is the precise freeze signature.

The programmatic ``KernelApp.close()`` already force-checkpoints on close
(ENH-006); this regression set covers the standalone CLI ``session close``
path, which the CLI-driven deploy used to freeze on. The S89 prose-only guide
fix did not prevent the freeze — enforcement must be code, not prose.
"""

from __future__ import annotations

import json
from pathlib import Path

from rag_kernel.__main__ import main, _session_checkpoint_gate
from rag_kernel.session_logger import LOG_FILE_PREFIX, LOG_FILE_EXT


def _events(log_path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _log_path(tmp_path: Path, sid: str) -> Path:
    return tmp_path / f"{LOG_FILE_PREFIX}{sid}{LOG_FILE_EXT}"


def _write_rag(tmp_path: Path, written_by: str) -> None:
    """Emulate the meta stamp that a real ``checkpoint`` leaves behind."""
    (tmp_path / "RAG_MASTER.json").write_text(
        json.dumps({"meta": {"written_by_session": written_by, "last_checkpoint_seq": 1}}),
        encoding="utf-8",
    )


# --- the gate predicate, in isolation -------------------------------------

def test_gate_ok_when_written_by_matches(tmp_path):
    _write_rag(tmp_path, "S1")
    ok, reason = _session_checkpoint_gate(tmp_path / "RAG_MASTER.json", "S1")
    assert ok is True
    assert "checkpoint present" in reason


def test_gate_fails_when_written_by_stale(tmp_path):
    _write_rag(tmp_path, "S0")  # a prior session — the freeze signature
    ok, reason = _session_checkpoint_gate(tmp_path / "RAG_MASTER.json", "S1")
    assert ok is False
    assert "no checkpoint by this session" in reason


def test_gate_fails_when_rag_missing(tmp_path):
    ok, reason = _session_checkpoint_gate(tmp_path / "RAG_MASTER.json", "S1")
    assert ok is False
    assert "not found" in reason


def test_gate_fails_when_rag_unreadable(tmp_path):
    (tmp_path / "RAG_MASTER.json").write_text("{ not valid json", encoding="utf-8")
    ok, reason = _session_checkpoint_gate(tmp_path / "RAG_MASTER.json", "S1")
    assert ok is False
    assert "unreadable" in reason


# --- end-to-end via the CLI -----------------------------------------------

def test_close_refused_without_checkpoint(tmp_path):
    assert main(["session", "start", "S1", "--rag-dir", str(tmp_path)]) == 0
    # No checkpoint / no RAG stamp -> refuse, non-zero exit, no session_end.
    assert main(["session", "close", "S1", "--rag-dir", str(tmp_path)]) == 1
    evs = _events(_log_path(tmp_path, "S1"))
    assert not any(e["event"] == "session_end" for e in evs)


def test_close_refused_when_written_by_stale(tmp_path):
    assert main(["session", "start", "S1", "--rag-dir", str(tmp_path)]) == 0
    _write_rag(tmp_path, "S0")  # prior session stamped — S1 never checkpointed
    assert main(["session", "close", "S1", "--rag-dir", str(tmp_path)]) == 1
    assert not any(e["event"] == "session_end" for e in _events(_log_path(tmp_path, "S1")))


def test_close_allowed_after_checkpoint(tmp_path):
    assert main(["session", "start", "S1", "--rag-dir", str(tmp_path)]) == 0
    _write_rag(tmp_path, "S1")  # checkpoint by this exact session
    assert main(["session", "close", "S1", "--rag-dir", str(tmp_path)]) == 0
    assert any(e["event"] == "session_end" for e in _events(_log_path(tmp_path, "S1")))


def test_force_overrides_gate(tmp_path):
    assert main(["session", "start", "S1", "--rag-dir", str(tmp_path)]) == 0
    # No checkpoint, but --force closes anyway (UNSAFE escape hatch — sanctioned
    # so a blocked agent does not resort to an unsanctioned scratch script).
    assert main(["session", "close", "S1", "--rag-dir", str(tmp_path), "--force"]) == 0
    assert any(e["event"] == "session_end" for e in _events(_log_path(tmp_path, "S1")))


def test_no_log_file_is_noop_not_refusal(tmp_path):
    # Nothing started => nothing to close => harmless no-op, even with no
    # checkpoint. The gate guards real closes, not no-ops.
    assert main(["session", "close", "S_NONE", "--rag-dir", str(tmp_path)]) == 0
