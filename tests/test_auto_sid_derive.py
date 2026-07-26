"""AUTO-SID-DERIVE — session-start derives the next session id from
``meta.written_by_session`` so the agent supplies (and reads) nothing at boot.

This is the kernel half of BOOT-PROSE-TO-SCRIPT: with the id auto-derived, the
Project Instructions drop the whole "work out the next SID / read
written_by_session" prose (and its Cowork-sandbox-read bait). The increment rule
is single-sourced through ``_next_session_id`` (the same helper the
next-session-directive machinery already used), so a directive's ``for_session``
and a boot's derived id can never disagree.

Contract pinned here:
  * ``_derive_next_sid`` returns ``increment(written_by_session)`` for a normal RAG,
  * it fails SAFE (``None``) when the RAG is unreadable or ``written_by_session``
    is unset/empty — the caller must then require an explicit id, never guess,
  * zero-pad width and non-``S`` prefixes are preserved (delegated to
    ``_next_session_id``),
  * the CLI positional is OPTIONAL (``nargs='?'``): absent -> ``None`` (derive),
    present -> verbatim override.
"""
from __future__ import annotations

import json
from pathlib import Path

import rag_kernel.__main__ as m


def _write_rag(tmp_path: Path, written_by: object) -> Path:
    rag = {"meta": {"written_by_session": written_by}} if written_by is not None else {"meta": {}}
    p = tmp_path / "RAG_MASTER.json"
    p.write_text(json.dumps(rag), encoding="utf-8")
    return p


def test_derive_increments_written_by_session(tmp_path):
    p = _write_rag(tmp_path, "S172")
    assert m._derive_next_sid(p) == "S173"


def test_derive_preserves_zero_pad_and_prefix(tmp_path):
    assert m._derive_next_sid(_write_rag(tmp_path, "S009")) == "S010"
    assert m._derive_next_sid(_write_rag(tmp_path, "SESS0099")) == "SESS0100"


def test_derive_fails_safe_on_missing_file(tmp_path):
    assert m._derive_next_sid(tmp_path / "nope.json") is None


def test_derive_fails_safe_on_unset_written_by(tmp_path):
    assert m._derive_next_sid(_write_rag(tmp_path, "")) is None
    assert m._derive_next_sid(_write_rag(tmp_path, "   ")) is None
    assert m._derive_next_sid(_write_rag(tmp_path, None)) is None


def test_derive_fails_safe_on_corrupt_json(tmp_path):
    p = tmp_path / "RAG_MASTER.json"
    p.write_text("{ not json", encoding="utf-8")
    assert m._derive_next_sid(p) is None


def test_cli_positional_is_optional_and_overridable():
    parser = m.build_parser()
    absent = parser.parse_args(["session-start", "--rag", "/tmp/x.json"])
    explicit = parser.parse_args(["session-start", "S999", "--rag", "/tmp/x.json"])
    assert absent.session_id is None
    assert explicit.session_id == "S999"
