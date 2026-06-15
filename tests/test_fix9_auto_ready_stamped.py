"""FIX-9 (U1) — ``init --auto-ready`` must yield a STAMPED, audit-clean RAG.

The guide tells every deploy to run ``init … --auto-ready``. That used to flip
``BOOTING -> READY`` with a *bare* state assignment, leaving
``meta.written_by_session=""`` and ``meta.last_checkpoint_seq=0``. Once the
machine is READY, ``drift_audit.check_written_by_session`` fails loud (it
self-skips only while BOOTING) — so the very first auditor run on the prescribed
clean-deploy path failed *by construction*. This is the K7 residual that FIX-3
(S73) did not close: FIX-3 wired checkpoint-stamping, but the ``--auto-ready``
shortcut bypassed the checkpoint pipeline entirely.

FIX-9 routes the ``--auto-ready`` transition through the first session-stamping
checkpoint: it stamps ``written_by_session``, bumps ``last_checkpoint_seq`` to 1,
appends the session record, and mirrors ``.bak`` to byte-parity
(``mirror_bak=True``, matching ``api.checkpoint`` do_full and the standalone
``checkpoint`` verb). A fresh ``init --spec … --auto-ready`` is therefore
``audit --strict`` clean with zero manual workarounds.
"""
import argparse
import json
from pathlib import Path

import pytest

from rag_kernel.__main__ import cmd_init
from rag_kernel.drift_audit import (
    audit_file,
    check_bak_parity,
    check_written_by_session,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _latest_spec() -> Path:
    specs = sorted(REPO_ROOT.glob("INIT_UNIVERSAL_RUNTIME_KERNEL_v*.md"))
    if not specs:
        pytest.skip("no INIT_UNIVERSAL_RUNTIME_KERNEL spec found in repo root")
    return specs[-1]


def _init_args(output: Path, **kw) -> argparse.Namespace:
    ns = argparse.Namespace(
        spec=_latest_spec(),
        output=output,
        root_project="",
        root_deliverables="",
        root_rag="",
        project_name="",
        dry_run=False,
        auto_ready=False,
        session="S0",
        path_style="auto",
        requirements=None,
        allow_void=False,
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _run_init(tmp_path: Path, **kw) -> tuple[Path, dict]:
    """Run cmd_init into tmp_path/RAG and return (hot_path, hot_dict)."""
    out = tmp_path / "RAG"
    cmd_init(_init_args(out, **kw))
    hot_path = out / "RAG_MASTER.json"
    hot = json.loads(hot_path.read_text(encoding="utf-8"))
    return hot_path, hot


def test_auto_ready_stamps_session_seq_and_state(tmp_path):
    """The core of FIX-9: --auto-ready stamps wbs (default S0), seq -> 1, READY."""
    _, hot = _run_init(tmp_path, auto_ready=True)
    assert hot["state_machine_status"] == "READY"
    assert hot["meta"]["written_by_session"] == "S0"
    assert hot["meta"]["session_id"] == "S0"
    assert hot["meta"]["last_checkpoint_seq"] == 1
    # A session record is appended so the lineage is complete.
    assert hot["sessions_recent"][-1]["id"] == "S0"


def test_auto_ready_honors_custom_session_id(tmp_path):
    """--session overrides the default S0 stamp."""
    _, hot = _run_init(tmp_path, auto_ready=True, session="S7")
    assert hot["meta"]["written_by_session"] == "S7"
    assert hot["sessions_recent"][-1]["id"] == "S7"


def test_auto_ready_mirrors_bak_to_byte_parity(tmp_path):
    """The stamped checkpoint write mirrors .bak byte-for-byte (FIX-4 / K6)."""
    hot_path, hot = _run_init(tmp_path, auto_ready=True)
    bak = hot_path.with_suffix(hot_path.suffix + ".bak")
    assert bak.exists()
    assert bak.read_bytes() == hot_path.read_bytes()
    assert check_bak_parity(hot_path, hot) == []


def test_auto_ready_passes_written_by_session_audit(tmp_path):
    """The exact auditor that used to fail loud (K7) is now clean."""
    _, hot = _run_init(tmp_path, auto_ready=True)
    assert check_written_by_session(hot) == []


def test_auto_ready_full_audit_strict_clean(tmp_path):
    """Headline regression: a fresh `init --spec … --auto-ready` passes
    `audit --strict` with zero findings — no checkpoint workaround needed."""
    hot_path, _ = _run_init(tmp_path, auto_ready=True)
    report = audit_file(hot_path)
    assert report.is_clean(strict=True), report.render()


def test_without_auto_ready_stays_booting_and_unstamped(tmp_path):
    """No regression to the default path: without --auto-ready the RAG stays
    BOOTING and unstamped, and check_written_by_session self-skips (BOOTING)."""
    _, hot = _run_init(tmp_path, auto_ready=False)
    assert hot["state_machine_status"] == "BOOTING"
    assert hot["meta"]["written_by_session"] == ""
    assert hot["meta"]["last_checkpoint_seq"] == 0
    # The BOOTING self-skip is still load-bearing for the non-auto-ready path.
    assert check_written_by_session(hot) == []
