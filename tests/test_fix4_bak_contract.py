"""FIX-4 (K6) — the parity-mirror ``.bak`` contract.

The operator settled the ``.bak`` semantics as a **parity-mirror**: after a clean
checkpoint / session close, the ``.bak`` MUST be a byte-identical copy of HOT so
recovery restores the *exact* known-good state (not the previous one). The eBay
Session-Zero deploy shipped a ``.bak`` three checkpoints stale (HOT seq 3 / .bak
seq 0, different md5) — a backup that could not actually restore.

Two halves, both tested here:
  * ENFORCE — the canonical RAG-state writers (full checkpoint / close,
    drift_store mutations, drift_render apply) pass ``mirror_bak=True`` to
    ``atomic_write_json`` so ``.bak`` is refreshed to byte-parity with the
    just-committed HOT.
  * AUDIT — ``drift_audit.check_bak_parity`` asserts byte-parity (the rollback-prev
    one-seq-behind allowance from FIX-1 is gone).

The generic ``atomic_write`` keeps its prior-file crash backup by default (that
N-1 copy is the write-window crash safety); only opt-in callers mirror.
"""
import json
from pathlib import Path

import pytest

from rag_kernel.persistence import atomic_write, atomic_write_json
from rag_kernel.drift_audit import check_bak_parity
from rag_kernel.drift_store import (
    add_items_file,
    migrate_backlog_file,
    set_note_in_file,
)
from rag_kernel.drift_render import apply_renders_file
from rag_kernel.api import KernelApp


def _bak(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".bak")


def _checks(findings) -> set[str]:
    return {f.check for f in findings}


def _make_rag(tmp_path, items=None) -> Path:
    rag = tmp_path / "RAG_MASTER.json"
    rag.write_text(json.dumps({
        "meta": {"last_checkpoint_seq": 1, "written_by_session": "T"},
        "tracked_items": items or [],
    }), encoding="utf-8")
    return rag


# --------------------------------------------------------------------------
# persistence primitive — opt-in parity-mirror, default prior-file crash copy
# --------------------------------------------------------------------------

def test_atomic_write_default_keeps_prior_file_bak(tmp_path):
    # Crash-safety contract is unchanged: default .bak holds the PRIOR file.
    p = tmp_path / "f.json"
    atomic_write(p, b"v1")
    atomic_write(p, b"v2")
    assert p.read_bytes() == b"v2"
    assert _bak(p).read_bytes() == b"v1"


def test_atomic_write_mirror_bak_is_byte_parity(tmp_path):
    p = tmp_path / "f.json"
    atomic_write(p, b"v1")
    atomic_write(p, b"v2", mirror_bak=True)
    assert p.read_bytes() == b"v2"
    assert _bak(p).read_bytes() == b"v2"  # parity-mirror, not N-1


def test_atomic_write_json_mirror_bak_is_byte_parity(tmp_path):
    p = tmp_path / "f.json"
    atomic_write_json(p, {"a": 1})
    atomic_write_json(p, {"a": 2, "b": 3}, mirror_bak=True)
    assert _bak(p).read_bytes() == p.read_bytes()


# --------------------------------------------------------------------------
# ENFORCE — canonical drift writers leave .bak at byte-parity
# --------------------------------------------------------------------------

def test_add_items_file_mirrors_bak(tmp_path):
    rag = _make_rag(tmp_path)
    add_items_file(rag, [{"id": "FIX-X", "title": "t", "status": "OPEN", "session": "S74"}])
    assert _bak(rag).read_bytes() == rag.read_bytes()


def test_set_note_in_file_mirrors_bak(tmp_path):
    rag = _make_rag(tmp_path, items=[{
        "id": "A", "title": "t", "status": "OPEN", "kind": "TASK",
        "history": [], "note": "",
    }])
    set_note_in_file(rag, "A", "a fresh note", session="S74")
    assert _bak(rag).read_bytes() == rag.read_bytes()


def test_migrate_backlog_file_mirrors_bak(tmp_path):
    rag = _make_rag(tmp_path)  # empty tracked_items — migrate seeds it
    migrate_backlog_file(rag, [{"id": "M1", "title": "t", "status": "OPEN"}])
    assert _bak(rag).read_bytes() == rag.read_bytes()


def test_apply_renders_file_mirrors_bak(tmp_path):
    rag = _make_rag(tmp_path, items=[{
        "id": "A", "title": "t", "status": "OPEN", "kind": "TASK",
        "history": [], "note": "",
    }])
    apply_renders_file(rag)
    assert _bak(rag).read_bytes() == rag.read_bytes()


# --------------------------------------------------------------------------
# ENFORCE — KernelApp full checkpoint / close mirror .bak (the close half of K6)
# --------------------------------------------------------------------------

@pytest.fixture
def project_dir(tmp_path):
    d = tmp_path / "RAG"
    d.mkdir()
    (d / "RAG_MASTER.json").write_text(json.dumps({
        "meta": {"session_id": "S8", "state_hash": "", "last_checkpoint_seq": 0},
        "current_status": {"phase": "idle"},
    }), encoding="utf-8")
    (d / "RAG_COLD.json").write_text(
        json.dumps({"meta": {"type": "RAG_COLD"}, "inventory": {"files": []}}),
        encoding="utf-8")
    return d


def test_full_checkpoint_mirrors_bak(project_dir):
    app = KernelApp(project_dir, session_id="S74")
    app.boot()
    res = app.checkpoint(force_full=True)
    assert res["checkpointed"] and res["checkpoint_type"] == "full"
    hot = project_dir / "RAG_MASTER.json"
    assert _bak(hot).read_bytes() == hot.read_bytes()


def test_closing_checkpoint_mirrors_bak(project_dir):
    app = KernelApp(project_dir, session_id="S74")
    app.boot()
    app.checkpoint(is_closing=True)
    hot = project_dir / "RAG_MASTER.json"
    assert _bak(hot).read_bytes() == hot.read_bytes()


def test_checkpointed_rag_passes_bak_parity_audit(project_dir):
    app = KernelApp(project_dir, session_id="S74")
    app.boot()
    app.checkpoint(force_full=True)
    hot = project_dir / "RAG_MASTER.json"
    data = json.loads(hot.read_text(encoding="utf-8"))
    assert check_bak_parity(hot, data) == []


# --------------------------------------------------------------------------
# AUDIT — byte-parity clean, any divergence fails loud, absent self-skips
# --------------------------------------------------------------------------

def test_audit_clean_when_byte_parity(tmp_path):
    rag = _make_rag(tmp_path)
    add_items_file(rag, [{"id": "Z", "title": "t", "status": "OPEN", "session": "S74"}])
    data = json.loads(rag.read_text(encoding="utf-8"))
    assert check_bak_parity(rag, data) == []


def test_audit_fires_when_bak_drifts_from_hot(tmp_path):
    rag = _make_rag(tmp_path)
    add_items_file(rag, [{"id": "Z", "title": "t", "status": "OPEN", "session": "S74"}])
    # Simulate a stale backup (the eBay K6 defect): HOT moves on, .bak does not.
    set_note_in_file_noop = json.loads(rag.read_text(encoding="utf-8"))
    set_note_in_file_noop["meta"]["last_checkpoint_seq"] = 99
    rag.write_text(json.dumps(set_note_in_file_noop), encoding="utf-8")  # raw write, no mirror
    data = json.loads(rag.read_text(encoding="utf-8"))
    assert _checks(check_bak_parity(rag, data)) == {"bak_parity"}


def test_audit_self_skips_without_bak(tmp_path):
    rag = _make_rag(tmp_path)
    data = json.loads(rag.read_text(encoding="utf-8"))
    assert check_bak_parity(rag, data) == []


def test_audit_flags_unparseable_bak(tmp_path):
    rag = _make_rag(tmp_path)
    _bak(rag).write_text("{not json", encoding="utf-8")
    data = json.loads(rag.read_text(encoding="utf-8"))
    assert _checks(check_bak_parity(rag, data)) == {"bak_parity"}
