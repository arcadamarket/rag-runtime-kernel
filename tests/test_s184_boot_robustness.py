"""S184 — boot robustness: tee, single-writer lock, auto-reconcile, full directive.

Every test here traces to a FIELD failure, not a hypothetical:

  BOOT-LOG-TEE            clone ERR-S3-DUP-SESSION-START — the transport dropped
                          phase-1 output, so the agent re-ran a state-touching
                          governed verb to recover the attestation token.
  SINGLE-WRITER LOCK      S183 — parent and clone wrote one RAG concurrently and
                          wedged a 9p RPC in uninterruptible D state.
  GATE-AUTO-RECONCILE     S183/clone-S2 — every carry-forward refusal in a whole
                          birth was mechanically repairable, and every one was
                          handed to the operator as a command to paste.
  DIRECTIVE-NO-TRUNCATE   the carried directive was clipped at 300 chars, so the
                          one field a successor must obey was unreadable in full.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from rag_kernel import persistence
from rag_kernel.persistence import (
    ConcurrentWriterError,
    atomic_write_json,
    single_writer,
)
from rag_kernel.__main__ import _BootTee, _finding_is_repairable


# --------------------------------------------------------------------------- #
# SINGLE-WRITER LOCK
# --------------------------------------------------------------------------- #
def test_lock_is_taken_and_released_around_a_canonical_write(tmp_path: Path):
    rag = tmp_path / "RAG_MASTER.json"
    atomic_write_json(rag, {"a": 1}, mirror_bak=True)
    # released after the write — a lock that outlives its writer bricks the deployment
    assert not (tmp_path / "RAG_MASTER.json.lock").exists()
    assert json.loads(rag.read_text(encoding="utf-8")) == {"a": 1}


def test_live_foreign_holder_is_refused_and_named(tmp_path: Path):
    rag = tmp_path / "RAG_MASTER.json"
    rag.write_text("{}", encoding="utf-8")
    lock = tmp_path / "RAG_MASTER.json.lock"
    # a LIVE pid that is not us: our own parent-ish — use os.getpid() of this proc
    # via a separate key so re-entry does not mask the refusal.
    lock.write_text(json.dumps({"pid": os.getpid(), "host": "other",
                                "owner": "sibling-agent", "utc": "now"}),
                    encoding="utf-8")
    with pytest.raises(ConcurrentWriterError) as ei:
        with single_writer(rag):
            pass
    msg = str(ei.value)
    assert "another writer holds this RAG" in msg
    assert "sibling-agent" in msg          # refusal NAMES the holder
    assert "ONE AGENT PER RAG" in msg


def test_stale_lock_from_a_dead_pid_is_broken_not_honoured(tmp_path: Path):
    """A killed agent must never brick the deployment — worse than the race."""
    rag = tmp_path / "RAG_MASTER.json"
    rag.write_text("{}", encoding="utf-8")
    lock = tmp_path / "RAG_MASTER.json.lock"
    lock.write_text(json.dumps({"pid": 2 ** 31 - 1, "host": "ghost",
                                "owner": "dead", "utc": "then"}),
                    encoding="utf-8")
    with single_writer(rag):
        pass                                # acquired: stale lock broken
    assert not lock.exists()


def test_lock_is_reentrant_within_one_process(tmp_path: Path):
    """One governed verb legitimately writes several times in a row."""
    rag = tmp_path / "RAG_MASTER.json"
    rag.write_text("{}", encoding="utf-8")
    with single_writer(rag):
        with single_writer(rag):
            atomic_write_json(rag, {"n": 2}, mirror_bak=True)
    assert not (tmp_path / "RAG_MASTER.json.lock").exists()


def test_generic_writes_are_not_locked(tmp_path: Path):
    """COLD/sidecar writes stay lock-free; the lock is for canonical state."""
    p = tmp_path / "RAG_COLD.json"
    lock = tmp_path / "RAG_COLD.json.lock"
    lock.write_text(json.dumps({"pid": os.getpid(), "host": "h",
                                "owner": "someone", "utc": "now"}),
                    encoding="utf-8")
    atomic_write_json(p, {"cold": True})     # no mirror_bak -> no lock -> no refusal
    assert json.loads(p.read_text(encoding="utf-8")) == {"cold": True}


# --------------------------------------------------------------------------- #
# BOOT-LOG-TEE
# --------------------------------------------------------------------------- #
def test_boot_tee_mirrors_to_file_and_passes_through(tmp_path: Path, capsys):
    log = tmp_path / ".boot" / "session_start_S9.log"
    tee = _BootTee(sys.stdout, log)
    real, sys.stdout = sys.stdout, tee
    try:
        print("token 1234abcd")
    finally:
        sys.stdout = real
        tee.close()
    assert "token 1234abcd" in log.read_text(encoding="utf-8")
    assert "token 1234abcd" in capsys.readouterr().out


def test_boot_tee_never_blocks_a_boot_when_the_log_cannot_be_opened(tmp_path: Path):
    """A diagnostic aid must not become a new gate."""
    blocked = tmp_path / "file_not_dir"
    blocked.write_text("x", encoding="utf-8")
    tee = _BootTee(sys.stdout, blocked / "nested" / "boot.log")
    tee.write("")            # must not raise
    tee.flush()
    tee.close()


# --------------------------------------------------------------------------- #
# GATE-AUTO-RECONCILE — the classifier is the whole safety argument
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("finding", [
    "audit: 1 error(s) |   - ERROR map_coverage: governed file on disk is not in the boot-map (coverage gap): docs/ROADMAP.md",
    "audit: 1 error(s) |   - ERROR render_parity: persisted open_tasks does not match the render of tracked_items",
    "audit: 1 error(s) |   - ERROR current_status_freshness: current_status.github_repo states HEAD abc but live git HEAD is def",
])
def test_derived_findings_are_repairable(finding: str):
    assert _finding_is_repairable(finding) is True


@pytest.mark.parametrize("finding", [
    "verify: HOT/COLD version skew",
    "audit: - ERROR asset_registry: registered asset 'x.md' diverged: recorded sha256 aaa != on-disk bbb",
    "audit: - WARNING note_status_contradiction [X]: status is OPEN but note claims closed",
    "audit: - ERROR side store: MEMORY.md present in project root",
    "incomplete close: session S5 left at phase CLOSING (transfer_ready=false)",
    "unsealed prior session: S7 ran but was never sealed",
    "audit: - ERROR spec_completeness: rule absent from canonical",
])
def test_asserted_findings_are_never_repairable(finding: str):
    """Repairing these would FABRICATE or ERASE a fact. Especially the last two."""
    assert _finding_is_repairable(finding) is False


def test_unknown_finding_text_fails_closed():
    """A class nobody has classified yet must not be silently auto-repaired."""
    assert _finding_is_repairable("audit: - ERROR brand_new_invariant: something") is False


def test_auto_reconcile_refuses_a_mixed_batch(tmp_path: Path):
    """Half-reconciling a broken RAG and reporting the rest invites acting on it."""
    from rag_kernel.__main__ import _auto_reconcile_gate
    rag = tmp_path / "RAG_MASTER.json"
    rag.write_text("{}", encoding="utf-8")
    findings = [
        "audit: - ERROR map_coverage: coverage gap: a.md",           # derived
        "audit: - ERROR asset_registry: 'b.md' diverged",            # asserted
    ]
    repairs, ok, out = _auto_reconcile_gate(rag, tmp_path, "S9", findings)
    assert repairs == []
    assert ok is False
    assert out == findings


# --------------------------------------------------------------------------- #
# DIRECTIVE-NO-TRUNCATE
# --------------------------------------------------------------------------- #
def test_boot_briefing_renders_the_whole_directive(tmp_path: Path):
    from rag_kernel.__main__ import _render_boot_briefing
    long_directive = "STEP ONE do the thing. " * 60          # ~1380 chars
    rag = {
        "inference_ledger": [],
        "priority_actions": [], "open_tasks": [], "deferred_items": [],
        "next_session_directive": {
            "session": "S1", "for_session": "S2", "directive": long_directive,
        },
    }
    out = _render_boot_briefing(rag)
    assert "…" not in out                     # nothing clipped
    flat = " ".join(out.split())
    assert " ".join(long_directive.split()) in flat
