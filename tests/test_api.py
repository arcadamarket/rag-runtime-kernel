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


class TestKernelAppSecretsIngestGuard:
    """SECRETS-INGEST-GUARD (P1/G2): propose() refuses a payload carrying a
    declared-secret VALUE before it can reach HOT/context."""

    # Deliberately NOT a real provider key format (no sk_live_/AKIA prefix) so
    # GitHub push-protection's secret scanner doesn't flag this test fixture; the
    # guard keys off the secret-ish KEY name ("api_key"), not the value pattern.
    SECRET = "FAKE-testonly-credential-0123456789abcdef"  # >= min_len

    def _declare_secret(self, project_dir):
        # project root == project_dir.parent (RAG is a subdir); config/ is a
        # universal default secret glob.
        cfg = project_dir.parent / "config"
        cfg.mkdir(exist_ok=True)
        (cfg / "creds.json").write_text(
            json.dumps({"api_key": self.SECRET}), encoding="utf-8"
        )

    def test_no_secret_files_is_noop(self, booted_app):
        # No config/ secret tree -> guard is a clean no-op.
        result = booted_app.propose({
            "action": "update_status",
            "payload": {"current_status": {"note": self.SECRET}},
        })
        assert result["valid"] is True

    def test_blocks_payload_carrying_secret_value(self, booted_app, project_dir):
        self._declare_secret(project_dir)
        result = booted_app.propose({
            "action": "update_status",
            "payload": {"current_status": {"leaked": self.SECRET}},
        })
        assert result["valid"] is False
        joined = " ".join(result["errors"])
        assert "Secrets-ingest violation" in joined
        assert "config/creds.json" in joined

    def test_error_is_redaction_safe(self, booted_app, project_dir):
        self._declare_secret(project_dir)
        result = booted_app.propose({
            "action": "update_status",
            "payload": {"x": self.SECRET},
        })
        assert result["valid"] is False
        # The raw secret must NEVER appear in the surfaced error; only a fingerprint.
        assert self.SECRET not in " ".join(result["errors"])
        assert "sha256:" in " ".join(result["errors"])

    def test_reference_by_path_is_allowed(self, booted_app, project_dir):
        self._declare_secret(project_dir)
        result = booted_app.propose({
            "action": "update_status",
            "payload": {"current_status": {"secret_ref": "config/creds.json"}},
        })
        assert result["valid"] is True

    def test_blocked_secret_never_reaches_hot(self, booted_app, project_dir):
        self._declare_secret(project_dir)
        booted_app.propose({
            "action": "update_status",
            "payload": {"current_status": {"leaked": self.SECRET}},
        })
        # Refused proposal is never staged, so it can never be committed into HOT.
        assert self.SECRET not in json.dumps(booted_app.get_hot())


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


class TestKernelAppPovMode:
    def test_default_pov_mode(self, booted_app):
        """Default mode is strict."""
        assert booted_app.get_pov_mode() == "strict"

    def test_set_pov_mode_advisory(self, booted_app):
        """Can set mode to advisory."""
        result = booted_app.set_pov_mode("advisory")
        assert result["updated"] is True
        assert result["new_mode"] == "advisory"
        assert booted_app.get_pov_mode() == "advisory"

    def test_set_pov_mode_silent(self, booted_app):
        """Can set mode to silent."""
        result = booted_app.set_pov_mode("silent")
        assert result["updated"] is True
        assert booted_app.get_pov_mode() == "silent"

    def test_set_pov_mode_disabled(self, booted_app):
        """Can set mode to disabled."""
        result = booted_app.set_pov_mode("disabled")
        assert result["updated"] is True
        assert booted_app.get_pov_mode() == "disabled"

    def test_set_pov_mode_invalid(self, booted_app):
        """Invalid mode rejected."""
        result = booted_app.set_pov_mode("turbo")
        assert result["updated"] is False
        assert len(result["errors"]) > 0

    def test_pov_mode_in_status(self, booted_app):
        """Status includes pov_mode."""
        status = booted_app.status()
        assert "pov_mode" in status
        assert status["pov_mode"] == "strict"

    def test_pov_mode_persists(self, project_dir):
        """POV mode change is written to disk."""
        app = KernelApp(project_dir, session_id="POV-PERSIST")
        app.boot()
        app.set_pov_mode("advisory")

        # Re-read from disk
        hot_path = project_dir / "RAG_MASTER.json"
        import json
        data = json.loads(hot_path.read_text(encoding="utf-8"))
        assert data["pov_mandate"]["mode"] == "advisory"

    def test_auto_escalate_high_risk(self, booted_app):
        """High-risk operations escalate to strict."""
        result = booted_app.check_auto_escalate("state_machine_change")
        assert result["escalated"] is True
        assert result["effective_mode"] == "strict"

    def test_auto_escalate_low_risk(self, booted_app):
        """Low-risk operations do not escalate."""
        result = booted_app.check_auto_escalate("file_read")
        assert result["escalated"] is False
        assert result["effective_mode"] == "strict"  # default is strict anyway

    def test_auto_escalate_when_silent(self, booted_app):
        """Auto-escalation overrides silent mode for high-risk ops."""
        booted_app.set_pov_mode("silent")
        result = booted_app.check_auto_escalate("persistence_change")
        assert result["escalated"] is True
        assert result["effective_mode"] == "strict"
        assert result["configured_mode"] == "silent"


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


# ===== ENH-007: SessionLogger wiring tests =====

class TestSessionLoggerWiring:
    """Verify SessionLogger is automatically wired into KernelApp lifecycle."""

    def test_logger_exists_on_app(self, app):
        """KernelApp should have a logger attribute."""
        from rag_kernel.session_logger import SessionLogger
        assert hasattr(app, "logger")
        assert isinstance(app.logger, SessionLogger)

    def test_boot_opens_logger(self, app):
        """boot() should open the session logger."""
        app.boot()
        assert app.logger.is_open
        app.close()

    def test_boot_creates_log_file(self, app, project_dir):
        """boot() should create a session log JSONL file."""
        app.boot()
        log_path = app.logger.log_path
        assert log_path.exists()
        app.close()

    def test_boot_logs_session_start_and_transition(self, app, project_dir):
        """boot() should log session_start and state_transition events."""
        app.boot()
        log_path = app.logger.log_path
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        events = [json.loads(line)["event"] for line in lines]
        assert "session_start" in events
        assert "state_transition" in events
        app.close()

    def test_commit_logs_rag_mutation(self, booted_app, project_dir):
        """commit() should log a rag_mutation event."""
        app = booted_app
        proposal = app.propose({"action": "test_write", "payload": {"test_key": "val"}})
        app.commit(proposal["proposal_id"])
        log_path = app.logger.log_path
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        events = [json.loads(line)["event"] for line in lines]
        assert "rag_mutation" in events
        app.close()

    def test_checkpoint_logs_checkpoint_event(self, booted_app, project_dir):
        """checkpoint() should log a checkpoint event."""
        app = booted_app
        app.checkpoint(force_full=True)
        log_path = app.logger.log_path
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        events = [json.loads(line)["event"] for line in lines]
        assert "checkpoint" in events
        app.close()

    def test_close_logs_session_end(self, booted_app, project_dir):
        """close() should log info and session_end events, then close logger."""
        app = booted_app
        app.close()
        log_path = app.logger.log_path
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        events = [json.loads(line)["event"] for line in lines]
        assert "session_end" in events
        assert not app.logger.is_open

    def test_full_lifecycle_log_sequence(self, app, project_dir):
        """Full boot->propose->commit->checkpoint->close produces correct log sequence."""
        app.boot()
        proposal = app.propose({"action": "lifecycle_test", "payload": {"x": 1}})
        app.commit(proposal["proposal_id"])
        app.checkpoint(force_full=True)
        app.close()

        log_path = app.logger.log_path
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        events = [json.loads(line)["event"] for line in lines]

        # Verify ordering: session_start comes first, session_end comes last
        assert events[0] == "session_start"
        assert events[-1] == "session_end"
        # All key lifecycle events present
        assert "state_transition" in events
        assert "rag_mutation" in events
        assert "checkpoint" in events
        assert "info" in events  # close logs an info event

    def test_logger_session_id_matches_app(self, app):
        """Logger session_id should match KernelApp session_id."""
        assert app.logger.session_id == app.session_id

    def test_recovery_logs_error(self, project_dir):
        """Boot into recovery should log an error event."""
        hot_path = project_dir / "RAG_MASTER.json"
        hot_path.write_text("NOT VALID JSON", encoding="utf-8")
        app = KernelApp(project_dir, session_id="TEST-RECOVERY")
        result = app.boot()
        assert result["status"] == "HOT_LOAD_FAILED"
        log_path = app.logger.log_path
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        events = [json.loads(line)["event"] for line in lines]
        assert "error" in events


# ===== ENH-009c: Tier gate enforcement (INS-012) =====

class TestWebTierGate:
    """Tests for web access tier enforcement in proposal validation."""

    def test_valid_tier_1_no_justification(self, booted_app):
        """Tier 1 (script) does not require justification."""
        result = booted_app.propose({
            "action": "web_fetch",
            "payload": {"url": "https://example.com"},
            "web_tier": "script",
        })
        assert result["valid"] is True

    def test_tier_2_requires_justification(self, booted_app):
        """Tier 2 (fetch) without justification is rejected."""
        result = booted_app.propose({
            "action": "web_fetch",
            "payload": {"url": "https://example.com"},
            "web_tier": "fetch",
        })
        assert result["valid"] is False
        assert any("tier_justification" in e for e in result["errors"])

    def test_tier_2_with_justification_passes(self, booted_app):
        """Tier 2 (fetch) with justification is accepted."""
        result = booted_app.propose({
            "action": "web_fetch",
            "payload": {"url": "https://example.com"},
            "web_tier": "fetch",
            "tier_justification": "One-off page read to inspect structure before building scraper",
        })
        assert result["valid"] is True

    def test_tier_3_requires_justification(self, booted_app):
        """Tier 3 (search) without justification is rejected."""
        result = booted_app.propose({
            "action": "web_search",
            "payload": {"query": "test"},
            "web_tier": "search",
        })
        assert result["valid"] is False
        assert any("tier_justification" in e for e in result["errors"])

    def test_tier_3_with_justification_passes(self, booted_app):
        """Tier 3 (search) with justification passes (when no scripts exist)."""
        result = booted_app.propose({
            "action": "web_search",
            "payload": {"query": "test"},
            "web_tier": "search",
            "tier_justification": "Reconnaissance — target URL unknown",
        })
        assert result["valid"] is True

    def test_unknown_tier_rejected(self, booted_app):
        """Unknown tier names are rejected."""
        result = booted_app.propose({
            "action": "web_fetch",
            "payload": {"url": "https://example.com"},
            "web_tier": "magic",
        })
        assert result["valid"] is False
        assert any("Unknown web_tier" in e for e in result["errors"])

    def test_no_web_tier_field_passes(self, booted_app):
        """Proposals without web_tier field skip tier validation entirely."""
        result = booted_app.propose({
            "action": "update_status",
            "payload": {"status": "READY"},
        })
        assert result["valid"] is True

    def test_tier_3_blocked_when_scraper_exists(self, booted_app, project_dir):
        """Tier 3 rejected when scraper scripts exist in project."""
        # Create a scraper script in the parent of the RAG dir
        parent = project_dir.parent
        scraper = parent / "scrape_data.py"
        scraper.write_text("# scraper", encoding="utf-8")
        try:
            result = booted_app.propose({
                "action": "web_search",
                "payload": {"query": "test"},
                "web_tier": "search",
                "tier_justification": "Need data",
            })
            assert result["valid"] is False
            assert any("Tier violation" in e for e in result["errors"])
        finally:
            scraper.unlink(missing_ok=True)

    def test_case_insensitive_tier(self, booted_app):
        """Tier names are case-insensitive."""
        result = booted_app.propose({
            "action": "web_fetch",
            "payload": {"url": "https://example.com"},
            "web_tier": "Script",
        })
        assert result["valid"] is True


# ===== ENH-009d: Echo-back enforcement (INS-015) =====

class TestEchoBackEnforcement:
    """Tests for echo-back validation in proposal pipeline."""

    def test_no_user_input_passes(self, booted_app):
        """Proposals not consuming user input skip echo-back check."""
        result = booted_app.propose({
            "action": "update_status",
            "payload": {"status": "WORKING"},
        })
        assert result["valid"] is True

    def test_user_input_without_echo_rejected(self, booted_app):
        """Consuming user input without echo_value is rejected."""
        result = booted_app.propose({
            "action": "configure_api_key",
            "payload": {"key": "sk-..."},
            "user_input_consumed": True,
        })
        assert result["valid"] is False
        assert any("Echo-back violation" in e for e in result["errors"])

    def test_user_input_with_echo_passes(self, booted_app):
        """Consuming user input with echo_value is accepted."""
        result = booted_app.propose({
            "action": "configure_api_key",
            "payload": {"key": "sk-..."},
            "user_input_consumed": True,
            "echo_value": "API key = sk-***. Confirmed by user.",
        })
        assert result["valid"] is True

    def test_user_input_false_passes(self, booted_app):
        """user_input_consumed=False skips the check."""
        result = booted_app.propose({
            "action": "update_config",
            "payload": {"setting": "value"},
            "user_input_consumed": False,
        })
        assert result["valid"] is True

    def test_echo_value_without_flag_passes(self, booted_app):
        """echo_value without user_input_consumed is harmless."""
        result = booted_app.propose({
            "action": "update_config",
            "payload": {"setting": "value"},
            "echo_value": "some value",
        })
        assert result["valid"] is True
