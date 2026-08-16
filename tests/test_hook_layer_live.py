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
    # source is now explicit: this test simulates a REAL client invocation, and
    # after S200 that has to be said out loud rather than inherited by default.
    record_heartbeat("poll", state_dir=state, source="hook_entry")
    assert check_hook_layer_live(tmp_path, state_dir=state) == []


def test_stale_heartbeat_warns(tmp_path):
    _declare_hooks(tmp_path)
    state = tmp_path / "state"
    record_heartbeat("poll", state_dir=state, now=time.time() - 10 * 86400,
                     source="hook_entry")
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


# --------------------------------------------------------------------------- #
# HEARTBEAT-PROVENANCE (S200) — the forgery, and the refusal that ends it
# --------------------------------------------------------------------------- #
def test_test_written_heartbeat_is_not_liveness(tmp_path):
    """The exact S200 defect: a heartbeat the SUITE wrote must not read as live.

    Until S200 this returned []. The heartbeat on the operator's disk said
    gate=canonical-read, matching tests/test_hook_enforcement_layer.py's payload
    byte for byte, and the audit reported the hook layer live in a client that
    had never run a hook. The suite was manufacturing the evidence the audit
    consumed.
    """
    _declare_hooks(tmp_path)
    state = tmp_path / "state"
    record_heartbeat("canonical-read", state_dir=state, source="test")
    findings = check_hook_layer_live(tmp_path, state_dir=state)
    assert len(findings) == 1
    assert findings[0].severity == ERROR
    assert "not stamped by a real client invocation" in findings[0].detail


def test_pytest_is_detected_without_an_explicit_source(tmp_path):
    """Defence in depth: under pytest, an unqualified stamp labels itself.

    The conftest fixture already keeps the suite away from the production path.
    This asserts the second lock: even a heartbeat that lands somewhere it
    should not cannot claim to be a real invocation, because the stamp itself
    records who wrote it.
    """
    _declare_hooks(tmp_path)
    state = tmp_path / "state"
    record_heartbeat("poll", state_dir=state)  # no source -> detected as test
    payload = json.loads(heartbeat_path(state).read_text(encoding="utf-8"))
    assert payload["source"] == "test"
    findings = check_hook_layer_live(tmp_path, state_dir=state)
    assert len(findings) == 1 and findings[0].severity == ERROR


def test_unlabelled_heartbeat_is_not_liveness(tmp_path):
    """A pre-S200 heartbeat has no source key — the shape the forged one had."""
    _declare_hooks(tmp_path)
    state = tmp_path / "state"
    hb = heartbeat_path(state)
    hb.parent.mkdir(parents=True, exist_ok=True)
    hb.write_text(json.dumps({"last_utc": time.time(), "gate": "poll",
                              "pid": 1}), encoding="utf-8")
    findings = check_hook_layer_live(tmp_path, state_dir=state)
    assert len(findings) == 1 and findings[0].severity == ERROR
    assert "pre-S200 artifact" in findings[0].detail


def test_suite_cannot_reach_the_production_heartbeat():
    """The conftest fixture is the structural lock; assert it is actually on.

    L4 of the audit protocol: if a check cannot be scripted it is not a check,
    it is a hope. This is that check for the isolation itself.
    """
    import os
    from pathlib import Path

    assert os.environ.get("RAG_HOOK_STATE_DIR"), \
        "conftest must pin RAG_HOOK_STATE_DIR for the whole session"
    resolved = heartbeat_path()
    assert Path.home() not in resolved.parents, \
        f"suite would write the operator's real hook state at {resolved}"


# --------------------------------------------------------------------------- #
# DECLARED-INERT (S200) — withdrawing a coverage claim, explicitly
# --------------------------------------------------------------------------- #
def _declare_inert(root, session="S200", reason="client does not read the file"):
    p = root / ".claude" / "settings.json"
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["$hook_layer_status"] = {"reachable": False, "declared_session": session,
                                 "reason": reason}
    p.write_text(json.dumps(doc), encoding="utf-8")


def test_declared_inert_downgrades_the_error_to_a_visible_warning(tmp_path):
    """The only non-invocation way out of the error, and it costs the coverage.

    A layer that cannot be reached in the client actually in use is not a defect
    to re-report forever; it is a claim to withdraw. The withdrawal is a
    WARNING, not silence, so it stays visible in every audit — "we know about
    it" living only in an agent's memory is how the gap survived S197-S199.
    """
    _declare_hooks(tmp_path)
    _declare_inert(tmp_path)
    findings = check_hook_layer_live(tmp_path, state_dir=tmp_path / "state")
    assert len(findings) == 1
    assert findings[0].severity == WARNING
    assert "DECLARED INERT by S200" in findings[0].detail
    assert "claim NO coverage" in findings[0].detail


def test_declared_inert_needs_reachable_false_not_merely_present(tmp_path):
    """A malformed or half-written declaration must not buy silence."""
    _declare_hooks(tmp_path)
    p = tmp_path / ".claude" / "settings.json"
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["$hook_layer_status"] = {"declared_session": "S200"}  # no reachable key
    p.write_text(json.dumps(doc), encoding="utf-8")
    findings = check_hook_layer_live(tmp_path, state_dir=tmp_path / "state")
    assert len(findings) == 1 and findings[0].severity == ERROR


def test_removing_the_declaration_restores_the_hard_error(tmp_path):
    """The withdrawal is revocable, and revoking it re-arms the gate."""
    _declare_hooks(tmp_path)
    _declare_inert(tmp_path)
    assert check_hook_layer_live(tmp_path, state_dir=tmp_path / "state")[0].severity == WARNING
    p = tmp_path / ".claude" / "settings.json"
    doc = json.loads(p.read_text(encoding="utf-8"))
    del doc["$hook_layer_status"]
    p.write_text(json.dumps(doc), encoding="utf-8")
    assert check_hook_layer_live(tmp_path, state_dir=tmp_path / "state")[0].severity == ERROR
