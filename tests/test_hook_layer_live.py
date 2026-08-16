"""HOOKS-INERT-IN-CLIENT — proving the hook layer RAN, not that it would pass.

The defect this closes, measured in S199 and true for at least three sessions
before it: `hook-guard --selftest` passed every gate, including
`poll: mcp__tmux-mcp__get-command-result -> DENY`, while ~40 consecutive polls
of single command ids went through unrefused in the same session. Both facts
hold because the wiring lives in `.claude/settings.json`, which the vendor
client in use does not read. A gate that is built, tested, deployed and
unreachable is counted as coverage while enforcing nothing — the exact shape
GATE-OR-HOPE-PRINCIPLE names.

Selftest answers "would this gate refuse a known-bad payload".
Only a heartbeat can answer "did this gate ever run".
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from rag_kernel.drift_audit import ERROR, WARNING, check_hook_layer_live
from rag_kernel.hook_guard import heartbeat_path, record_heartbeat, run_gate


def _declare_hooks(root: Path, entries: int = 2) -> Path:
    d = root / ".claude"
    d.mkdir(parents=True, exist_ok=True)
    hooks = {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command",
                                                        "command": f"cmd{i}"}]}
                            for i in range(entries)]}
    p = d / "settings.json"
    p.write_text(json.dumps({"hooks": hooks}), encoding="utf-8")
    return p


# --------------------------------------------------------------------------- #
# The clause
# --------------------------------------------------------------------------- #
def test_declared_but_never_fired_is_an_error(tmp_path):
    _declare_hooks(tmp_path)
    state = tmp_path / "state"
    findings = check_hook_layer_live(tmp_path, state_dir=state)
    assert len(findings) == 1
    f = findings[0]
    assert f.check == "hook_layer_live" and f.severity == ERROR
    assert "never" in f.detail.lower() or "absent" in f.detail.lower()


def test_no_declared_hooks_self_skips(tmp_path):
    # A deployment that never wired a hook layer is not failing to run one.
    assert check_hook_layer_live(tmp_path, state_dir=tmp_path / "state") == []
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text('{"$comment": "no hooks"}',
                                                        encoding="utf-8")
    assert check_hook_layer_live(tmp_path, state_dir=tmp_path / "state") == []


def test_fresh_heartbeat_is_clean(tmp_path):
    _declare_hooks(tmp_path)
    state = tmp_path / "state"
    record_heartbeat("poll", state_dir=state)
    assert check_hook_layer_live(tmp_path, state_dir=state) == []


def test_stale_heartbeat_warns(tmp_path):
    _declare_hooks(tmp_path)
    state = tmp_path / "state"
    record_heartbeat("poll", state_dir=state, now=time.time() - 10 * 86400)
    findings = check_hook_layer_live(tmp_path, state_dir=state, max_age_days=3.0)
    assert len(findings) == 1 and findings[0].severity == WARNING
    assert "days ago" in findings[0].detail


def test_corrupt_heartbeat_counts_as_inert(tmp_path):
    _declare_hooks(tmp_path)
    state = tmp_path / "state"
    hb = heartbeat_path(state)
    hb.parent.mkdir(parents=True, exist_ok=True)
    hb.write_text("{not json", encoding="utf-8")
    findings = check_hook_layer_live(tmp_path, state_dir=state)
    assert len(findings) == 1 and findings[0].severity == ERROR


# --------------------------------------------------------------------------- #
# The heartbeat itself
# --------------------------------------------------------------------------- #
def test_run_gate_stamps_even_on_a_malformed_payload(tmp_path, capsys):
    # THE COUNTERFACTUAL THAT MATTERS. A heartbeat written only on the success
    # path cannot distinguish a DEAD layer from one receiving garbage — and the
    # dead layer is the whole point of the clause.
    state = tmp_path / "state"
    rc = run_gate("poll", "{not json", state_dir=state)
    assert rc == 0                       # fail-open is declared behaviour
    assert heartbeat_path(state).is_file()
    payload = json.loads(heartbeat_path(state).read_text(encoding="utf-8"))
    assert payload["gate"] == "poll" and payload["pid"] > 0


def test_record_heartbeat_never_raises(tmp_path):
    # It runs on every matched tool call; an exception here would fire on every
    # call and block nothing, which is the failure mode hook_entry documents.
    record_heartbeat("poll", state_dir=tmp_path / "nested" / "deep" / "state")
    assert heartbeat_path(tmp_path / "nested" / "deep" / "state").is_file()
    # An unwritable location degrades silently rather than taking the call down.
    record_heartbeat("poll", state_dir=Path("/proc/definitely/not/writable"))


def test_wiring_is_asserted_on_the_audit_entrypoint():
    # A clause nobody calls is the defect it was written to catch.
    from rag_kernel import drift_audit as _da
    assert "check_hook_layer_live" in _da.audit_file.__code__.co_names
