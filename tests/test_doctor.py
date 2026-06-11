"""Tests for the `doctor` preflight command (ENV-NORM increment 1).

Covers the pure fail-closed lock decision across every verdict, plus the doctor
command end-to-end: env summary (env probe monkeypatched for determinism), the
stale-lock --fix path (clears only when provably safe), the LIVE-lock refusal,
and --emit-runner. doctor adds NO new module — it is a CLI-only capability — so
health stays 20/20 and the drift gate is untouched.
"""
import json
import time

import pytest

from rag_kernel.__main__ import (
    build_env_audit,
    cmd_doctor,
    diagnose_index_lock,
    main,
)
import rag_kernel.__main__ as M


# ----------------------------------------------------------------------------
# Pure decision: diagnose_index_lock (no files, no processes)
# ----------------------------------------------------------------------------

def test_lock_absent():
    d = diagnose_index_lock(False, False, None)
    assert d == {"present": False, "verdict": "absent", "clearable": False,
                 "reason": "no .git/index.lock present"}


def test_lock_live_is_never_clearable():
    d = diagnose_index_lock(True, True, 9999.0)
    assert d["verdict"] == "live"
    assert d["clearable"] is False


def test_lock_unknown_age_fails_closed():
    d = diagnose_index_lock(True, False, None)
    assert d["verdict"] == "unknown"
    assert d["clearable"] is False


def test_lock_stale_is_clearable():
    d = diagnose_index_lock(True, False, 120.0, stale_after=60.0)
    assert d["verdict"] == "stale"
    assert d["clearable"] is True


def test_lock_fresh_is_refused():
    d = diagnose_index_lock(True, False, 5.0, stale_after=60.0)
    assert d["verdict"] == "fresh"
    assert d["clearable"] is False


# ----------------------------------------------------------------------------
# build_env_audit reuse contract (doctor renders the SAME probe as audit-env)
# ----------------------------------------------------------------------------

def test_build_env_audit_shape(tmp_path):
    audit = build_env_audit(tmp_path)
    for key in ("python_versions", "pip_variants", "package_managers",
                "tooling", "project_env", "platform"):
        assert key in audit
    # tooling enumerates the canonical fetch/VCS/shell set with a present flag
    names = {t["name"] for t in audit["tooling"]}
    assert {"curl", "git", "gh", "jq"} <= names
    for t in audit["tooling"]:
        assert "present" in t


# ----------------------------------------------------------------------------
# doctor end-to-end (env probe stubbed for determinism)
# ----------------------------------------------------------------------------

_FAKE_AUDIT = {
    "python_versions": [
        {"command": "python3", "version": "3.13.0", "pip_works": True, "pip_version": "24.0", "path": "/usr/bin/python3"},
        {"command": "python3.14", "version": "3.14.0", "pip_works": False, "pip_version": "", "path": "x"},
    ],
    "pip_variants": [],
    "package_managers": [],
    "tooling": [
        {"name": "curl", "present": True, "version": "8", "path": "/usr/bin/curl"},
        {"name": "git", "present": True, "version": "2.4", "path": "/usr/bin/git"},
        {"name": "jq", "present": False, "version": "", "path": None},
    ],
    "project_env": {},
    "platform": {"system": "Linux", "release": "x", "machine": "y", "python_default": "3.13.0", "python_path": "/usr/bin/python3"},
}


@pytest.fixture
def stub_env(monkeypatch):
    monkeypatch.setattr(M, "build_env_audit", lambda root: _FAKE_AUDIT)


def test_doctor_clean_no_lock(tmp_path, stub_env, monkeypatch, capsys):
    monkeypatch.setattr(M, "_git_process_running", lambda: False)
    rc = main(["doctor", "--path", str(tmp_path), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["lock"]["verdict"] == "absent"
    assert out["env"]["best_python"].startswith("python3 3.13")
    assert "python3.14 3.14.0" in out["env"]["broken_pip"]
    assert "jq" in out["env"]["tooling_missing"]
    assert out["blocking"] == []


def test_doctor_clears_stale_lock_with_fix(tmp_path, stub_env, monkeypatch, capsys):
    monkeypatch.setattr(M, "_git_process_running", lambda: False)
    git = tmp_path / ".git"
    git.mkdir()
    lock = git / "index.lock"
    lock.write_text("")
    old = time.time() - 600
    import os
    os.utime(lock, (old, old))
    rc = main(["doctor", "--path", str(tmp_path), "--fix", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["lock"]["verdict"] == "stale"
    assert out["lock"].get("cleared") is True
    assert not lock.exists()


def test_doctor_refuses_live_lock(tmp_path, stub_env, monkeypatch, capsys):
    monkeypatch.setattr(M, "_git_process_running", lambda: True)
    git = tmp_path / ".git"
    git.mkdir()
    lock = git / "index.lock"
    lock.write_text("")
    rc = main(["doctor", "--path", str(tmp_path), "--fix", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["lock"]["verdict"] == "live"
    assert lock.exists()  # LIVE lock must never be touched


def test_doctor_emit_runner(tmp_path, capsys):
    dest = tmp_path / "run_in_project.sh"
    rc = main(["doctor", "--emit-runner", str(dest)])
    assert rc == 0
    body = dest.read_text(encoding="utf-8")
    assert body.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in body


def test_doctor_renders_shell_rule_from_rag(tmp_path, stub_env, monkeypatch, capsys):
    monkeypatch.setattr(M, "_git_process_running", lambda: False)
    rag = tmp_path / "RAG_MASTER.json"
    rag.write_text(json.dumps({
        "operating_protocol": {"session_start_shell_rule": "tmux first, always."}
    }), encoding="utf-8")
    rc = main(["doctor", "--path", str(tmp_path), "--rag", str(rag), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert out["shell"]["rag_rule_present"] is True
    assert "tmux-mcp" in out["shell"]["first_move"]
