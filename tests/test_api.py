"""Tests for the RAG Runtime Kernel HTTP API.

Coverage targets:
- KernelApp: boot, status, hot, cold, propose, commit, reject, checkpoint,
  recover, close, WAL access
- KernelHTTPHandler: all GET/POST routes, error codes, JSON parsing
- Integration: full boot -> propose -> commit -> checkpoint -> close cycle
- Error handling: corrupt HOT, locked project, missing proposals
"""

import json
import threading
import time
import urllib.request
import urllib.error

import pytest

from rag_kernel.api import (
    DEFAULT_PORT,
    KernelApp,
    KernelHTTPServer,
    create_server,
)
from rag_kernel.state_machine import State


# ===== Helpers =====

SAMPLE_HOT = {
    "meta": {
        "session_id": "S8",
        "state_hash": "",
        "last_checkpoint_seq": 0,
    },
    "current_status": {"phase": "idle"},
}


@pytest.fixture
def project_dir(tmp_path):
    """Project directory with sample HOT file."""
    d = tmp_path / "RAG"
    d.mkdir()
    hot_path = d / "RAG_MASTER.json"
    hot_path.write_text(json.dumps(SAMPLE_HOT), encoding="utf-8")
    # Create an empty COLD file
    cold_path = d / "RAG_COLD.json"
    cold_path.write_text(
        json.dumps({"meta": {"type": "RAG_COLD"}, "inventory": {"files": []}}),
        encoding="utf-8",
    )
    return d


@pytest.fixture
def app(project_dir):
    """KernelApp instance (not booted)."""
    return KernelApp(project_dir, session_id="TEST-S9")


@pytest.fixture
def booted_app(app):
    """KernelApp that has been booted."""
    app.boot()
    return app


# ===== KernelApp unit tests =====

class TestKernelAppBoot:
    def test_boot_success(self, app):
        result = app.boot()
        assert result["status"] == "OK"
        assert result["state"] == "READY"
        assert result["session_id"] == "TEST-S9"

    def test_boot_creates_wal(self, app, project_dir):
        app.boot()
        assert (project_dir / "WAL.jsonl").exists()

    def test_boot_creates_lock(self, app, project_dir):
        app.boot()
        assert app.lock.is_locked

    def test_boot_no_hot_file(self, tmp_path):
        d = tmp_path / "empty"
        d.mkdir()
        app = KernelApp(d, session_id="TEST")
        result = app.boot()
        assert result["status"] == "OK"

    def test_boot_corrupt_hot(self, project_dir):
        (project_dir / "RAG_MASTER.json").write_text("{bad", encoding="utf-8")
        app = KernelApp(project_dir, session_id="TEST")
        result = app.boot()
        assert result["status"] == "HOT_LOAD_FAILED"
        assert result["state"] == "RECOVERY"

    def test_boot_locked_by_other(self, project_dir):
        # Pre-acquire lock as different session with this PID (alive)
        import os
        lock_data = {
            "session_id": "OTHER",
            "pid": os.getpid(),
            "acquired_at": "2026-01-01T00:00:00Z",
        }
        (project_dir / ".rag_kernel.lock").write_text(
            json.dumps(lock_data), encoding="utf-8"
        )
        app = KernelApp(project_dir, session_id="TEST")
        result = app.boot()
        assert result["status"] == "LOCKED"


class TestKernelAppStatus:
    def test_status_after_boot(self, booted_app):
        status = booted_app.status()
        assert status["state"] == "READY"
        assert status["session_id"] == "TEST-S9"
        assert "seq" in status
        assert "wal_seq" in status
        assert status["lock_held"] is True

    def test_status_before_boot(self, app):
        status = app.status()
        assert status["state"] == "BOOTING"


class TestKernelAppHot:
    def test_get_hot(self, booted_app):
        hot = booted_app.get_hot()
        assert "meta" in hot
        assert "current_status" in hot

    def test_hot_has_session_id(self, booted_app):
        hot = booted_app.get_hot()
        assert hot["meta"]["session_id"] == "TEST-S9"


class TestKernelAppCold:
    def test_get_cold_full(self, booted_app):
        cold = booted_app.get_cold()
        assert "meta" in cold
        assert "inventory" in cold

    def test_get_cold_partition(self, booted_app):
        inv = booted_app.get_cold("inventory")
        assert "files" in inv

    def test_get_cold_missing_partition(self, booted_app):
        from rag_kernel.cold_manager import PartitionNotFoundError
        with pytest.raises(PartitionNotFoundError):
            booted_app.get_cold("nonexistent")


class TestKernelAppPropose:
    def test_propose_valid(self, booted_app):
        result = booted_app.propose({
            "action": "update_status",
            "payload": {"current_status": {"phase": "working"}},
        })
        assert result["valid"] is True
        assert "proposal_id" in result

    def test_propose_missing_action(self, booted_app):
        result = booted_app.propose({"payload": {}})
        assert result["valid"] is False
        assert "Missing 'action'" in result["errors"][0]

    def test_propose_missing_payload(self, booted_app):
        result = booted_app.propose({"action": "test"})
        assert result["valid"] is False

    def test_propose_wrong_state(self, app):
        # Not booted — still in BOOTING
        result = app.propose({"action": "test", "payload": {}})
        assert result["valid"] is False
        assert "BOOTING" in result["errors"][0]


class TestKernelAppCommit:
    def test_commit_valid_proposal(self, booted_app, project_dir):
        prop = booted_app.propose({
            "action": "update_status",
            "payload": {"current_status": {"phase": "committed"}},
        })
        result = booted_app.commit(prop["proposal_id"])
        assert result["committed"] is True
        assert "state_hash" in result

        # Verify written to disk
        on_disk = json.loads(
            (project_dir / "RAG_MASTER.json").read_text(encoding="utf-8")
        )
        assert on_disk["current_status"]["phase"] == "committed"

    def test_commit_nonexistent(self, booted_app):
        result = booted_app.commit("FAKE-ID")
        assert result["committed"] is False

    def test_double_commit(self, booted_app):
        prop = booted_app.propose({
            "action": "test",
            "payload": {"x": 1},
        })
        booted_app.commit(prop["proposal_id"])
        result = booted_app.commit(prop["proposal_id"])
        assert result["committed"] is False


class TestKernelAppReject:
    def test_reject_valid(self, booted_app):
        prop = booted_app.propose({
            "action": "test",
            "payload": {"x": 1},
        })
        result = booted_app.reject(prop["proposal_id"])
        assert result["rejected"] is True

    def test_reject_nonexistent(self, booted_app):
        result = booted_app.reject("FAKE")
        assert result["rejected"] is False


class TestKernelAppCheckpoint:
    def test_checkpoint_from_ready(self, booted_app):
        result = booted_app.checkpoint()
        assert result["checkpointed"] is True
        assert "state_hash" in result
        assert booted_app.state_machine.current == State.READY

    def test_checkpoint_from_booting_fails(self, app):
        result = app.checkpoint()
        assert result["checkpointed"] is False


class TestKernelAppWAL:
    def test_get_wal_after_boot(self, booted_app):
        entries = booted_app.get_wal()
        assert len(entries) >= 1
        events = [e["event"] for e in entries]
        assert "BOOT_COMPLETE" in events

    def test_get_wal_since(self, booted_app):
        entries_all = booted_app.get_wal(since=0)
        if len(entries_all) > 1:
            first_seq = entries_all[0]["seq"]
            entries_after = booted_app.get_wal(since=first_seq)
            assert len(entries_after) < len(entries_all)


class TestKernelAppRecover:
    def test_recover_with_bak(self, project_dir):
        # Create a .bak file
        bak = project_dir / "RAG_MASTER.json.bak"
        bak.write_text(json.dumps(SAMPLE_HOT), encoding="utf-8")
        # Corrupt the main file
        (project_dir / "RAG_MASTER.json").write_text("{bad", encoding="utf-8")
        app = KernelApp(project_dir, session_id="TEST")
        app.boot()  # will go to RECOVERY
        result = app.recover()
        assert result["recovered"] is True
        assert result["method"] == "bak"

    def test_recover_no_bak(self, booted_app):
        result = booted_app.recover()
        assert result["recovered"] is False


class TestKernelAppClose:
    def test_close(self, booted_app):
        result = booted_app.close()
        assert result["closed"] is True
        assert result["state"] == "CLOSING"
        assert not booted_app.lock.is_locked


class TestKernelAppFullCycle:
    def test_full_lifecycle(self, project_dir):
        """boot -> propose -> commit -> checkpoint -> close."""
        app = KernelApp(project_dir, session_id="LIFECYCLE")

        # Boot
        boot = app.boot()
        assert boot["status"] == "OK"

        # Propose
        prop = app.propose({
            "action": "update_status",
            "payload": {"current_status": {"phase": "testing"}},
        })
        assert prop["valid"]

        # Commit
        commit = app.commit(prop["proposal_id"])
        assert commit["committed"]

        # Checkpoint
        cp = app.checkpoint()
        assert cp["checkpointed"]

        # Close
        close = app.close()
        assert close["closed"]
        assert app.state_machine.current == State.CLOSING


# ===== HTTP integration tests =====

@pytest.fixture
def server(project_dir):
    """Start an HTTP server on a random port, yield it, then shut down."""
    srv = create_server(project_dir, port=0, session_id="HTTP-TEST")
    srv.app.boot()
    thread = threading.Thread(target=srv.serve_forever)
    thread.daemon = True
    thread.start()
    yield srv
    srv.shutdown()


def _get(server, path):
    """Helper: GET request to the test server."""
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}{path}"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _post(server, path, body=None):
    """Helper: POST request to the test server."""
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(body or {}).encode("utf-8") if body else b"{}"
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


class TestHTTPRoutes:
    def test_get_status(self, server):
        code, data = _get(server, "/status")
        assert code == 200
        assert data["state"] == "READY"

    def test_get_hot(self, server):
        code, data = _get(server, "/hot")
        assert code == 200
        assert "meta" in data

    def test_get_cold(self, server):
        code, data = _get(server, "/cold")
        assert code == 200
        assert "meta" in data

    def test_get_cold_partition(self, server):
        code, data = _get(server, "/cold/inventory")
        assert code == 200
        assert "files" in data

    def test_get_cold_missing_partition(self, server):
        code, data = _get(server, "/cold/nonexistent")
        assert code == 404

    def test_get_wal(self, server):
        code, data = _get(server, "/wal")
        assert code == 200
        assert isinstance(data, list)

    def test_get_not_found(self, server):
        code, data = _get(server, "/nope")
        assert code == 404

    def test_post_propose(self, server):
        code, data = _post(server, "/propose", {
            "action": "test",
            "payload": {"x": 1},
        })
        assert code == 200
        assert data["valid"] is True

    def test_post_propose_invalid(self, server):
        code, data = _post(server, "/propose", {"no_action": True})
        assert code == 400
        assert data["valid"] is False

    def test_post_commit(self, server):
        _, prop = _post(server, "/propose", {
            "action": "test",
            "payload": {"test_key": "test_val"},
        })
        code, data = _post(server, f"/commit/{prop['proposal_id']}")
        assert code == 200
        assert data["committed"] is True

    def test_post_commit_not_found(self, server):
        code, data = _post(server, "/commit/FAKE")
        assert code == 404

    def test_post_reject(self, server):
        _, prop = _post(server, "/propose", {
            "action": "test", "payload": {},
        })
        code, data = _post(server, f"/reject/{prop['proposal_id']}")
        assert code == 200
        assert data["rejected"] is True

    def test_post_checkpoint(self, server):
        code, data = _post(server, "/checkpoint")
        assert code == 200
        assert data["checkpointed"] is True

    def test_post_recover(self, server):
        code, data = _post(server, "/recover")
        # No .bak file, so recovery fails
        assert code == 500

    def test_post_not_found(self, server):
        code, data = _post(server, "/nope")
        assert code == 404

    def test_wal_since_filter(self, server):
        code, data = _get(server, "/wal?since=999999")
        assert code == 200
        assert data == []
