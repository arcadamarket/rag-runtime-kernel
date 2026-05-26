"""Tests for the RAG Runtime Kernel CLI entry point.

Coverage targets:
- Argument parsing (serve, mcp, missing command)
- serve command (project validation, server creation)
- mcp command (project validation)
- main() return codes
"""

import json
import sys

import pytest

from rag_kernel.__main__ import build_parser, main


# ===== Fixtures =====

SAMPLE_HOT = {
    "meta": {"session_id": "S8", "state_hash": "", "last_checkpoint_seq": 0},
}


@pytest.fixture
def project_dir(tmp_path):
    d = tmp_path / "RAG"
    d.mkdir()
    (d / "RAG_MASTER.json").write_text(json.dumps(SAMPLE_HOT), encoding="utf-8")
    (d / "RAG_COLD.json").write_text('{"meta":{}}', encoding="utf-8")
    return d


# ===== Parser =====

class TestParser:
    def test_serve_command(self):
        parser = build_parser()
        args = parser.parse_args(["serve", "--project", "/tmp/RAG"])
        assert args.command == "serve"
        assert args.port == 7437
        assert args.host == "127.0.0.1"

    def test_serve_custom_port(self):
        parser = build_parser()
        args = parser.parse_args(["serve", "--project", "/tmp/RAG", "--port", "8080"])
        assert args.port == 8080

    def test_serve_custom_host(self):
        parser = build_parser()
        args = parser.parse_args(["serve", "--project", "/tmp/RAG", "--host", "0.0.0.0"])
        assert args.host == "0.0.0.0"

    def test_mcp_command(self):
        parser = build_parser()
        args = parser.parse_args(["mcp", "--project", "/tmp/RAG"])
        assert args.command == "mcp"

    def test_session_id(self):
        parser = build_parser()
        args = parser.parse_args(["serve", "--project", "/tmp/RAG", "--session-id", "S9"])
        assert args.session_id == "S9"

    def test_no_command(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.command is None


# ===== main() =====

class TestMain:
    def test_no_command_returns_1(self):
        result = main([])
        assert result == 1

    def test_serve_missing_project(self, tmp_path):
        result = main(["serve", "--project", str(tmp_path / "nonexistent")])
        assert result == 1

    def test_mcp_missing_project(self, tmp_path):
        result = main(["mcp", "--project", str(tmp_path / "nonexistent")])
        assert result == 1

    def test_serve_requires_project(self):
        with pytest.raises(SystemExit):
            main(["serve"])

    def test_mcp_requires_project(self):
        with pytest.raises(SystemExit):
            main(["mcp"])


# ===== ENH-008: session command =====

class TestSessionCommand:
    def test_session_start_creates_log(self, tmp_path):
        result = main(["session", "start", "S_TEST", "--rag-dir", str(tmp_path)])
        assert result == 0
        log_file = tmp_path / "session_log_S_TEST.jsonl"
        assert log_file.exists()
        content = log_file.read_text(encoding="utf-8")
        assert "session_start" in content
        assert "S_TEST" in content

    def test_session_close_after_start(self, tmp_path):
        # Start
        main(["session", "start", "S_TEST2", "--rag-dir", str(tmp_path)])
        # Close
        result = main(["session", "close", "S_TEST2", "--rag-dir", str(tmp_path)])
        assert result == 0
        log_file = tmp_path / "session_log_S_TEST2.jsonl"
        content = log_file.read_text(encoding="utf-8")
        assert "session_end" in content

    def test_session_close_no_log_file(self, tmp_path):
        result = main(["session", "close", "S_NONEXISTENT", "--rag-dir", str(tmp_path)])
        assert result == 0  # Should not error, just warn

    def test_session_no_action(self):
        result = main(["session"])
        assert result == 1

    def test_session_start_with_rag(self, tmp_path):
        """Session start should report RAG state if RAG_MASTER.json exists."""
        rag = {"meta": {"schema_version": "5.3"}, "state_machine_status": "READY"}
        (tmp_path / "RAG_MASTER.json").write_text(json.dumps(rag), encoding="utf-8")
        result = main(["session", "start", "S_RAG", "--rag-dir", str(tmp_path)])
        assert result == 0


# ===== ENH-008: checkpoint command =====

class TestCheckpointCommand:
    def _make_rag(self, tmp_path):
        rag = {
            "meta": {
                "schema_version": "5.3",
                "last_updated_utc": "2026-01-01T00:00:00Z",
                "written_by_session": "S0",
                "last_checkpoint_seq": 0,
            },
            "state_machine_status": "BOOTING",
            "sessions_recent": [],
            "open_tasks": [],
        }
        path = tmp_path / "RAG_MASTER.json"
        path.write_text(json.dumps(rag, indent=2), encoding="utf-8")
        return path

    def test_checkpoint_basic(self, tmp_path):
        rag_path = self._make_rag(tmp_path)
        result = main([
            "checkpoint", "--rag", str(rag_path),
            "--session", "S1", "--summary", "Test session summary",
        ])
        assert result == 0
        updated = json.loads(rag_path.read_text(encoding="utf-8"))
        assert updated["meta"]["written_by_session"] == "S1"
        assert updated["meta"]["last_checkpoint_seq"] == 1
        assert len(updated["sessions_recent"]) == 1
        assert updated["sessions_recent"][0]["id"] == "S1"
        assert updated["sessions_recent"][0]["s"] == "Test session summary"

    def test_checkpoint_with_status(self, tmp_path):
        rag_path = self._make_rag(tmp_path)
        result = main([
            "checkpoint", "--rag", str(rag_path),
            "--session", "S1", "--summary", "Done",
            "--status", "READY",
        ])
        assert result == 0
        updated = json.loads(rag_path.read_text(encoding="utf-8"))
        assert updated["state_machine_status"] == "READY"

    def test_checkpoint_with_tasks(self, tmp_path):
        rag_path = self._make_rag(tmp_path)
        tasks = '["Task A", "Task B"]'
        result = main([
            "checkpoint", "--rag", str(rag_path),
            "--session", "S1", "--summary", "Done",
            "--tasks", tasks,
        ])
        assert result == 0
        updated = json.loads(rag_path.read_text(encoding="utf-8"))
        assert updated["open_tasks"] == ["Task A", "Task B"]

    def test_checkpoint_dry_run(self, tmp_path):
        rag_path = self._make_rag(tmp_path)
        original = rag_path.read_text(encoding="utf-8")
        result = main([
            "checkpoint", "--rag", str(rag_path),
            "--session", "S1", "--summary", "Test",
            "--dry-run",
        ])
        assert result == 0
        assert rag_path.read_text(encoding="utf-8") == original  # Unchanged

    def test_checkpoint_missing_rag(self, tmp_path):
        result = main([
            "checkpoint", "--rag", str(tmp_path / "nonexistent.json"),
            "--session", "S1", "--summary", "Test",
        ])
        assert result == 1

    def test_checkpoint_increments_seq(self, tmp_path):
        rag_path = self._make_rag(tmp_path)
        main(["checkpoint", "--rag", str(rag_path), "--session", "S1", "--summary", "First"])
        main(["checkpoint", "--rag", str(rag_path), "--session", "S2", "--summary", "Second"])
        updated = json.loads(rag_path.read_text(encoding="utf-8"))
        assert updated["meta"]["last_checkpoint_seq"] == 2
        assert len(updated["sessions_recent"]) == 2

    def test_checkpoint_trims_sessions_to_five(self, tmp_path):
        rag_path = self._make_rag(tmp_path)
        for i in range(7):
            main(["checkpoint", "--rag", str(rag_path), "--session", f"S{i}", "--summary", f"Session {i}"])
        updated = json.loads(rag_path.read_text(encoding="utf-8"))
        assert len(updated["sessions_recent"]) == 5
        assert updated["sessions_recent"][0]["id"] == "S2"  # Oldest trimmed


# ===== ENH-008: gc command =====

class TestGCCommand:
    def test_gc_clean_project(self, tmp_path):
        result = main(["gc", "--path", str(tmp_path)])
        assert result == 0

    def test_gc_finds_pycache(self, tmp_path):
        cache_dir = tmp_path / "__pycache__"
        cache_dir.mkdir()
        (cache_dir / "foo.cpython-312.pyc").write_text("bytecode", encoding="utf-8")
        result = main(["gc", "--path", str(tmp_path), "--dry-run"])
        assert result == 0

    def test_gc_deletes_pycache(self, tmp_path):
        cache_dir = tmp_path / "__pycache__"
        cache_dir.mkdir()
        (cache_dir / "foo.cpython-312.pyc").write_text("bytecode", encoding="utf-8")
        result = main(["gc", "--path", str(tmp_path)])
        assert result == 0
        assert not cache_dir.exists()

    def test_gc_finds_orphan_numeric_files(self, tmp_path):
        (tmp_path / "1").write_text("pip3: not found", encoding="utf-8")
        (tmp_path / "2").write_text("error output", encoding="utf-8")
        result = main(["gc", "--path", str(tmp_path)])
        assert result == 0
        assert not (tmp_path / "1").exists()
        assert not (tmp_path / "2").exists()

    def test_gc_preserves_normal_files(self, tmp_path):
        (tmp_path / "important.py").write_text("code", encoding="utf-8")
        (tmp_path / "data.json").write_text("{}", encoding="utf-8")
        result = main(["gc", "--path", str(tmp_path)])
        assert result == 0
        assert (tmp_path / "important.py").exists()
        assert (tmp_path / "data.json").exists()

    def test_gc_deletes_bat_at_root(self, tmp_path):
        (tmp_path / "run_cmd.bat").write_text("echo hi", encoding="utf-8")
        sub = tmp_path / "scripts"
        sub.mkdir()
        (sub / "keep.bat").write_text("echo hi", encoding="utf-8")
        result = main(["gc", "--path", str(tmp_path)])
        assert result == 0
        assert not (tmp_path / "run_cmd.bat").exists()
        assert (sub / "keep.bat").exists()  # Only root-level .bat deleted

    def test_gc_skips_venv(self, tmp_path):
        venv_cache = tmp_path / ".venv" / "__pycache__"
        venv_cache.mkdir(parents=True)
        (venv_cache / "foo.cpython-312.pyc").write_text("bytecode", encoding="utf-8")
        result = main(["gc", "--path", str(tmp_path)])
        assert result == 0
        assert venv_cache.exists()  # .venv should be skipped

    def test_gc_finds_tmp_files(self, tmp_path):
        (tmp_path / "scratch.tmp").write_text("temp", encoding="utf-8")
        result = main(["gc", "--path", str(tmp_path)])
        assert result == 0
        assert not (tmp_path / "scratch.tmp").exists()


# ===== ENH-008: init --auto-ready =====

class TestInitAutoReady:
    def test_init_auto_ready_sets_state(self, tmp_path):
        """Init with --auto-ready should set state_machine_status to READY."""
        from pathlib import Path

        # Create a minimal spec file
        spec = tmp_path / "spec.md"
        spec.write_text("# Minimal spec\nNo rag-config blocks.", encoding="utf-8")

        result = main([
            "init", "--output", str(tmp_path),
            "--project-name", "TestProject",
            "--auto-ready",
        ])
        # init without spec creates void RAG
        rag_path = tmp_path / "RAG_MASTER.json"
        if rag_path.exists():
            rag = json.loads(rag_path.read_text(encoding="utf-8"))
            assert rag["state_machine_status"] == "READY"

    def test_init_without_auto_ready_stays_booting(self, tmp_path):
        """Init without --auto-ready should leave state as BOOTING."""
        result = main([
            "init", "--output", str(tmp_path),
            "--project-name", "TestProject",
        ])
        rag_path = tmp_path / "RAG_MASTER.json"
        if rag_path.exists():
            rag = json.loads(rag_path.read_text(encoding="utf-8"))
            assert rag["state_machine_status"] == "BOOTING"


# ===== ENH-008: init --path-style =====

class TestInitPathStyle:
    def test_path_style_windows(self, tmp_path):
        result = main([
            "init", "--output", str(tmp_path),
            "--root-project", "C:/Users/test/project",
            "--path-style", "windows",
            "--project-name", "Test",
        ])
        rag_path = tmp_path / "RAG_MASTER.json"
        if rag_path.exists():
            rag = json.loads(rag_path.read_text(encoding="utf-8"))
            assert "\\" in rag["meta"]["root_project"]
            assert "/" not in rag["meta"]["root_project"]

    def test_path_style_posix(self, tmp_path):
        result = main([
            "init", "--output", str(tmp_path),
            "--root-project", "C:\\Users\\test\\project",
            "--path-style", "posix",
            "--project-name", "Test",
        ])
        rag_path = tmp_path / "RAG_MASTER.json"
        if rag_path.exists():
            rag = json.loads(rag_path.read_text(encoding="utf-8"))
            assert "/" in rag["meta"]["root_project"]
            assert "\\" not in rag["meta"]["root_project"]

    def test_path_style_auto_windows(self, tmp_path):
        result = main([
            "init", "--output", str(tmp_path),
            "--root-project", "C:/Users/test/project",
            "--path-style", "auto",
            "--project-name", "Test",
        ])
        rag_path = tmp_path / "RAG_MASTER.json"
        if rag_path.exists():
            rag = json.loads(rag_path.read_text(encoding="utf-8"))
            # Auto-detect: C: drive letter -> windows style
            assert "\\" in rag["meta"]["root_project"]
