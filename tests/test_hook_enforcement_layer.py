"""HOOK-ENFORCEMENT-LAYER (S195) — every test here traces to a logged defect.

The premise under test is not "does the code work" but "is this rule now
REFUSABLE". Each defect below was a rule the agent had loaded, could recite, and
broke anyway; the assertion is that the corresponding call now comes back denied
without the model's cooperation.

  E-081 / E-116 / E-128   polling a running command — the same defect three
                          times across three sessions.
  E-071                   a sandbox shell reaching canonical state.
  boot rule 1             a direct read of RAG_MASTER.json to load state.
  tool_contract cl.1      hand-editing canonical state instead of using a
                          governed verb.
  DEPLOY-PARITY           editing kernel source and measuring the old build.
"""
from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pytest

from rag_kernel import hook_guard
from rag_kernel.hook_guard import (
    CANONICAL_FILES,
    GATES,
    HOOK_GUARD_VERSION,
    POLL_COOLDOWN_SECONDS,
    Decision,
    decide,
    run_gate,
    selftest,
)


T0 = 1_700_000_000.0


def _poll(cmd_id="c1"):
    return {"tool_name": "mcp__tmux-mcp__get-command-result",
            "tool_input": {"commandId": cmd_id}}


def _bash(command):
    return {"tool_name": "Bash", "tool_input": {"command": command}}


def _file(tool, path):
    return {"tool_name": tool, "tool_input": {"file_path": path}}


# --------------------------------------------------------------------------- #
# contract
# --------------------------------------------------------------------------- #

def test_gate_names_are_stable():
    # S197 added `transport` (default-deny at the boundary, E-133) and
    # `post-transport-audit` (does the layer actually cover this call path).
    # The version moves with them: a layer whose policy changed without a
    # version bump is indistinguishable from one that stopped running.
    # S206 added `unbounded-wait`: a hand-rolled `while/until … sleep` wait with
    # no timeout is refused at the boundary. It survives the "why can't
    # session-start refuse this?" question below, which is the admission test for
    # this layer — the loop is typed into the Bash tool and never reaches a kernel
    # verb, so `wait-for` cannot refuse what it never sees.
    # S209 added `wait-duration` (POLL-GATE-BLIND-TO-WAIT-FANOUT-S208): the poll
    # gate could only see a wait REPEATING against one file, and the measured
    # traffic never repeated — 66 waits on 66 different files, 45% of them
    # instant, gate fired zero times. Duration is the invariant that survives
    # both shapes, and only PostToolUse can see it, so the refusal is split
    # across two events. It passes the admission test for this layer: the elapsed
    # time exists only in a tool RESPONSE, which no kernel verb ever sees.
    assert GATES == (
        "poll", "sandbox-state", "canonical-read",
        "unbounded-wait",
        "tmux-heredoc",
        "stop-status",
        "transport",
        "deploy-parity", "post-transport-audit",
        "wait-duration",
    )
    # Bumped WITH the gate. The pin and the bump were both left undone by the
    # commit that added it (d4d86c1), which is why this test was red at HEAD —
    # see RED-TESTS-COMMITTED-AT-HEAD-S206.
    assert HOOK_GUARD_VERSION == "1.9.0"  # S209: wait fan-out refused


def test_unknown_gate_is_fail_loud_not_silently_allowed():
    """A typo in settings.json must not become a silently disabled gate."""
    with pytest.raises(ValueError):
        decide("no-such-gate", _bash("ls"))


# --------------------------------------------------------------------------- #
# POLL-GUARD — E-081 / E-116 / E-128
# --------------------------------------------------------------------------- #

def test_first_query_of_a_command_is_allowed(tmp_path: Path):
    """One check after a single long wait is the sanctioned pattern."""
    assert decide("poll", _poll(), state_dir=tmp_path, now=T0).allow


def test_second_query_inside_the_cooldown_is_refused(tmp_path: Path):
    """The machine-visible signature of polling, refused at the tool layer."""
    decide("poll", _poll(), state_dir=tmp_path, now=T0)
    d = decide("poll", _poll(), state_dir=tmp_path, now=T0 + 3)
    assert not d.allow
    assert "POLL-GUARD" in d.reason
    assert "wait-for" in d.reason  # the refusal names the alternative


def test_query_after_the_cooldown_is_allowed_again(tmp_path: Path):
    decide("poll", _poll(), state_dir=tmp_path, now=T0)
    later = T0 + POLL_COOLDOWN_SECONDS + 1
    assert decide("poll", _poll(), state_dir=tmp_path, now=later).allow


def test_a_different_command_id_is_not_collateral_damage(tmp_path: Path):
    decide("poll", _poll("c1"), state_dir=tmp_path, now=T0)
    assert decide("poll", _poll("c2"), state_dir=tmp_path, now=T0 + 1).allow


def test_repeat_attempts_do_not_reset_the_window(tmp_path: Path):
    """Hammering the gate must not hand the caller a fresh allowance."""
    decide("poll", _poll(), state_dir=tmp_path, now=T0)
    for i in range(1, 5):
        d = decide("poll", _poll(), state_dir=tmp_path, now=T0 + i)
        assert not d.allow
    assert "attempt 5" in d.reason  # the count is reported, not just the verdict


def test_poll_gate_ignores_tools_it_does_not_mediate(tmp_path: Path):
    assert decide("poll", _bash("ls"), state_dir=tmp_path, now=T0).allow


# --------------------------------------------------------------------------- #
# SANDBOX-STATE — E-071
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("command", [
    "cat RAG_MASTER.json",
    "python3 -c \"import json; json.load(open('RAG_MASTER.json'))\"",
    "cp /x/RAG/RAG_MASTER.json /tmp/",
    "grep foo ./RAG_COLD.json",
])
def test_sandbox_shell_naming_canonical_state_is_refused(command):
    d = decide("sandbox-state", _bash(command))
    assert not d.allow
    assert "E-071" in d.reason


def test_refusal_names_the_sanctioned_path_not_just_the_prohibition():
    """A gate that only says no teaches nothing; the successor repeats the move."""
    d = decide("sandbox-state", _bash("cat RAG_MASTER.json"))
    assert "session-start" in d.reason and "governed verb" in d.reason


@pytest.mark.parametrize("command", [
    "ls /tmp",
    "python3 -m rag_kernel items --json",
    "echo RAG_MASTER_SUMMARY.md",       # a different file, not canonical state
    "cat rag_master.json.notes",        # not the canonical name
])
def test_ordinary_shell_work_is_untouched(command):
    assert decide("sandbox-state", _bash(command)).allow


def test_sandbox_gate_does_not_mediate_the_governed_transport():
    """tmux-mcp IS the sanctioned path — gating it would gate the cure."""
    event = {"tool_name": "mcp__tmux-mcp__execute-command",
             "tool_input": {"command": "python3 -m rag_kernel items"}}
    assert decide("sandbox-state", event).allow


# --------------------------------------------------------------------------- #
# CANONICAL-READ — boot rule 1 and tool_contract clause 1
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name", sorted(CANONICAL_FILES))
def test_reading_any_canonical_file_is_refused(name):
    d = decide("canonical-read", _file("Read", f"/x/RAG/{name}"))
    assert not d.allow


def test_read_refusal_cites_the_load_path():
    d = decide("canonical-read", _file("Read", "/x/RAG/RAG_MASTER.json"))
    assert "session-start" in d.reason and "E-071" in d.reason


@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit"])
def test_hand_editing_canonical_state_is_refused_with_the_verb_list(tool):
    """tool_contract clause 1: atomicity and the WAL are preconditions."""
    d = decide("canonical-read", _file(tool, "/x/RAG/RAG_MASTER.json"))
    assert not d.allow
    assert "governed verb" in d.reason and "WAL" in d.reason


def test_ordinary_files_are_untouched():
    assert decide("canonical-read", _file("Read", "/x/README.md")).allow
    assert decide("canonical-read", _file("Edit", "/x/rag_kernel/api.py")).allow


def test_a_relative_path_cannot_step_around_the_guard():
    """Basename matching — otherwise ./RAG_MASTER.json defeats the whole gate."""
    assert not decide("canonical-read", _file("Read", "./RAG_MASTER.json")).allow
    assert not decide("canonical-read", _file("Read", "RAG_MASTER.json")).allow


# --------------------------------------------------------------------------- #
# DEPLOY-PARITY — non-blocking notice at the edit
# --------------------------------------------------------------------------- #

def _project(tmp_path: Path, *, same: bool):
    root = tmp_path / "proj"
    wt = root / "GIT WORKTREES" / "kernel" / "rag_kernel"
    dep = root / "RAG" / "rag_kernel"
    wt.mkdir(parents=True)
    dep.mkdir(parents=True)
    (root / "RAG").mkdir(exist_ok=True)
    (wt / "api.py").write_text("edited\n", encoding="utf-8")
    (dep / "api.py").write_text("edited\n" if same else "deployed\n", encoding="utf-8")
    return root, wt / "api.py"


def test_parity_notice_fires_when_the_deployed_copy_differs(tmp_path: Path):
    root, edited = _project(tmp_path, same=False)
    d = decide("deploy-parity", _file("Edit", str(edited)), project_root=root)
    assert d.allow                      # never blocks — the edit is the job
    assert "DEPLOY-PARITY" in d.context
    assert "not the kernel you just edited" in d.context


def test_parity_is_silent_when_the_copies_agree(tmp_path: Path):
    root, edited = _project(tmp_path, same=True)
    assert decide("deploy-parity", _file("Edit", str(edited)),
                  project_root=root).context == ""


def test_parity_ignores_non_kernel_edits(tmp_path: Path):
    root, _ = _project(tmp_path, same=False)
    d = decide("deploy-parity", _file("Edit", str(root / "README.md")),
               project_root=root)
    assert d.allow and d.context == ""


# --------------------------------------------------------------------------- #
# transport contract — what Claude Code actually receives
# --------------------------------------------------------------------------- #

def test_denied_call_emits_the_pretooluse_deny_contract(tmp_path: Path):
    run_gate("poll", json.dumps(_poll()), state_dir=tmp_path, out=io.StringIO(),
             now=T0)
    out = io.StringIO()
    rc = run_gate("poll", json.dumps(_poll()), state_dir=tmp_path, out=out,
                  now=T0 + 2)
    payload = json.loads(out.getvalue())
    assert rc == 0                      # the hook exits clean; the JSON decides
    hso = payload["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    assert "POLL-GUARD" in hso["permissionDecisionReason"]


def test_allowed_call_emits_nothing(tmp_path: Path):
    out = io.StringIO()
    run_gate("canonical-read", json.dumps(_file("Read", "/x/ok.md")),
             state_dir=tmp_path, out=out)
    assert out.getvalue() == ""


def test_malformed_payload_fails_open_and_says_so(tmp_path: Path):
    """Declared trade: a crashing monitor must not brick the session.

    The honesty requirement is that the failure is LOUD — a silent fail-open is
    indistinguishable from a gate that was never installed.
    """
    out, err = io.StringIO(), io.StringIO()
    rc = run_gate("poll", "{not json", state_dir=tmp_path, out=out, err=err)
    assert rc == 0 and out.getvalue() == ""
    assert "FAILED OPEN" in err.getvalue()


def test_selftest_reports_every_gate_refusing(tmp_path: Path):
    """The measurement that keeps the layer's one hope from going unmeasured."""
    failures, lines = selftest(state_dir=tmp_path)
    assert failures == 0
    assert any("poll" in ln and "DENY" in ln for ln in lines)
    assert lines[0].startswith("hook_guard selftest")


def test_host_entry_point_allows_when_given_no_gate(capsys):
    """The wiring may fail; it may not take the session with it."""
    from rag_kernel.hook_entry import main
    assert main([]) == 0
    assert "no --gate" in capsys.readouterr().err


def test_host_entry_point_routes_to_the_named_gate(monkeypatch, capsys):
    from rag_kernel import hook_entry
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        _file("Read", "/x/RAG/RAG_MASTER.json"))))
    assert hook_entry.main(["--gate", "canonical-read"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_settings_wiring_names_only_real_gates():
    """A matcher pointing at a gate that does not exist is a disabled gate.

    The settings file lives outside the package (it is deployment wiring), so
    this test reads it where it sits and refuses to let the two drift.
    """
    root = Path(__file__).resolve().parents[3]
    settings = root / ".claude" / "settings.json"
    if not settings.exists():          # a clone that has not wired hooks yet
        pytest.skip("no .claude/settings.json in this deployment")
    text = settings.read_text(encoding="utf-8")
    used = set(re.findall(r"--gate ([A-Za-z-]+)", text))
    assert used, "settings.json wires no gate at all"
    assert used <= set(GATES), f"settings.json names unknown gate(s): {used - set(GATES)}"


def test_disable_switch_is_explicit_and_reported(monkeypatch, tmp_path: Path):
    """If the layer can be turned off, turning it off must leave a trace."""
    monkeypatch.setenv("RAG_HOOK_GUARD_DISABLED", "1")
    d = decide("canonical-read", _file("Read", "/x/RAG/RAG_MASTER.json"))
    assert d.allow and "layer disabled" in d.reason


# --------------------------------------------------------------------------- #
# TMUX-HEREDOC-HANGS-THE-SHELL-S206
# --------------------------------------------------------------------------- #

def _tmux(command):
    return {"tool_name": "mcp__tmux-mcp__execute-command",
            "tool_input": {"command": command}}


def test_heredoc_through_tmux_is_refused():
    """tmux-mcp appends its completion echo to the delimiter line, so the
    here-document never closes and the pane hangs. Measured twice in S206; the
    only recovery found was a new window."""
    d = decide("tmux-heredoc", _tmux("cat > f <<EOF\nbody\nEOF"))

    assert d.allow is False
    assert "TMUX-HEREDOC-HANGS-THE-SHELL-S206" in d.reason


def test_the_refusal_names_the_substitute_not_just_the_prohibition():
    """A gate that only says no teaches nothing and the successor repeats it."""
    d = decide("tmux-heredoc", _tmux("git commit -F - <<MSG\nx\nMSG"))

    assert "git commit -F" in d.reason
    assert "Write/Edit" in d.reason
    # and it must not push the agent onto the undeclared transport
    assert "Do NOT retry this on the Bash tool" in d.reason


def test_single_line_tmux_commands_are_untouched():
    assert decide("tmux-heredoc", _tmux("python .boot/patch.py")).allow
    assert decide("tmux-heredoc", _tmux("git commit -F .boot/msg.txt")).allow


def test_here_string_is_not_a_here_document():
    """`<<<` is single-line: it cannot hang, so it must not be refused."""
    assert decide("tmux-heredoc", _tmux("grep x <<< \"$VAR\"")).allow


def test_the_gate_does_not_mediate_other_transports():
    """PowerShell and wsl-exec do not wrap the way tmux-mcp does."""
    assert decide("tmux-heredoc",
                  {"tool_name": "PowerShell",
                   "tool_input": {"command": "x <<EOF\ny\nEOF"}}).allow


# --------------------------------------------------------------------------- #
# UNCOMMITTED-WORK-HAS-NO-GATE-S206
# --------------------------------------------------------------------------- #

def test_uncommitted_context_is_silent_below_the_threshold(monkeypatch):
    """Two edits in flight is a working set, not a warning. A gate that fires on
    normal work is a gate that gets tuned out."""
    monkeypatch.setattr(hook_guard, "_uncommitted_count", lambda wt: 2)

    assert hook_guard._uncommitted_context(Path("/wt"), Path("/wt/RAG")) == ""


def test_uncommitted_context_warns_at_the_threshold(monkeypatch):
    monkeypatch.setattr(hook_guard, "_uncommitted_count", lambda wt: 3)

    out = hook_guard._uncommitted_context(Path("/wt"), Path("/wt/RAG"))
    assert "3 uncommitted change(s)" in out
    assert "E-109/E-123" in out
    assert "git add -A" not in out, "the command is reserved for the loud tier"


def test_uncommitted_context_names_the_exact_command_when_loud(monkeypatch):
    """Past the loud threshold it stops describing and starts instructing —
    Rule 43: an instruction the reader cannot execute is a wall."""
    monkeypatch.setattr(hook_guard, "_uncommitted_count", lambda wt: 11)

    out = hook_guard._uncommitted_context(Path("/wt"), Path("/wt/RAG"))
    assert "git add -A && git commit -F" in out
    assert "commit_msg.txt" in out
    # and it must not send the reader into the transport that hangs
    assert "TMUX-HEREDOC-HANGS-THE-SHELL-S206" in out


def test_unmeasurable_git_is_silent_not_reported_clean(monkeypatch):
    """SELF-CERTIFYING-EVIDENCE-GATE-S201: a probe that cannot measure must not
    report success. None is not zero."""
    monkeypatch.setattr(hook_guard, "_uncommitted_count", lambda wt: None)

    assert hook_guard._uncommitted_context(Path("/wt"), Path("/wt/RAG")) == ""


# --------------------------------------------------------------------------- #
# AGENT-STOPS-WITHOUT-A-STATUS-S206
# --------------------------------------------------------------------------- #

def _mkproject(tmp_path):
    (tmp_path / "RAG" / ".boot").mkdir(parents=True)
    wt = tmp_path / "GIT WORKTREES" / "k"
    (wt / ".git").mkdir(parents=True)
    return tmp_path, wt


def test_a_clean_stop_is_not_nagged(tmp_path, monkeypatch):
    """A gate that fires on every quiet stop is a gate that gets tuned out."""
    root, _ = _mkproject(tmp_path)
    monkeypatch.setattr(hook_guard, "_uncommitted_count", lambda wt: 0)

    assert decide("stop-status", {}, project_root=root).context == ""


def test_stopping_with_a_job_in_flight_injects_the_checklist(tmp_path, monkeypatch):
    root, _ = _mkproject(tmp_path)
    (root / "RAG" / ".boot" / "s206_suite.txt").write_text("running...", encoding="utf-8")
    monkeypatch.setattr(hook_guard, "_uncommitted_count", lambda wt: 0)

    ctx = decide("stop-status", {}, project_root=root).context
    assert "AGENT-STOPS-WITHOUT-A-STATUS-S206" in ctx
    assert "s206_suite.txt" in ctx
    assert "NEXT ACTION" in ctx
    assert "never instead of it" in ctx


def test_a_finished_job_is_not_in_flight(tmp_path, monkeypatch):
    """The sentinel is the completion fact; a finished job must not nag."""
    root, _ = _mkproject(tmp_path)
    (root / "RAG" / ".boot" / "s206_suite.txt").write_text(
        "....\nQQ_SUITE_DONE_QQ rc=0\n", encoding="utf-8")
    monkeypatch.setattr(hook_guard, "_uncommitted_count", lambda wt: 0)

    assert decide("stop-status", {}, project_root=root).context == ""


def test_stopping_with_uncommitted_work_says_it_is_at_risk(tmp_path, monkeypatch):
    root, _ = _mkproject(tmp_path)
    monkeypatch.setattr(hook_guard, "_uncommitted_count", lambda wt: 4)

    ctx = decide("stop-status", {}, project_root=root).context
    assert "4 uncommitted change(s)" in ctx
    assert "AT RISK" in ctx


def test_stop_never_refuses():
    """Stop cannot carry a permission decision; a deny here refuses nothing."""
    assert decide("stop-status", {}).allow is True


def test_an_unsealed_session_cannot_be_declared_transferable(tmp_path, monkeypatch):
    """MEASURED S206: the agent announced readiness to transfer without calling
    session-end. transfer_ready was false in the marker the whole time and no
    gate compared that field against the claim, because the claim was prose."""
    root, _ = _mkproject(tmp_path)
    (root / "RAG" / "RAG_MASTER.json").write_text(
        '{"session_close": {"session": "S206", "transfer_ready": false}}',
        encoding="utf-8")
    monkeypatch.setattr(hook_guard, "_uncommitted_count", lambda wt: 0)

    ctx = decide("stop-status", {}, project_root=root).context
    assert "NOT SEALED" in ctx
    assert "session-end" in ctx


def test_a_sealed_session_is_quiet(tmp_path, monkeypatch):
    root, _ = _mkproject(tmp_path)
    (root / "RAG" / "RAG_MASTER.json").write_text(
        '{"session_close": {"session": "S206", "transfer_ready": true}}',
        encoding="utf-8")
    monkeypatch.setattr(hook_guard, "_uncommitted_count", lambda wt: 0)

    assert decide("stop-status", {}, project_root=root).context == ""


def test_rule_45_is_delivered_by_a_refused_boot():
    """The rule every deliverable is measured against was ORPHAN and absent from
    the boot-critical set, so a refused boot delivered every rule about HOW to
    work and not the one about WHAT the agent is."""
    from rag_kernel.__main__ import BOOT_CRITICAL_RULES

    assert "agent_role_boundary" in BOOT_CRITICAL_RULES


# --------------------------------------------------------------------------- #
# WAIT-FOR-USED-AS-A-POLL-S198 — the poll gate now covers the kernel's own wait
# --------------------------------------------------------------------------- #

def _wait(path="/x/RAG/.boot/job.txt"):
    return {"tool_name": "mcp__rag-kernel__rag_wait", "tool_input": {"path": path}}


def test_first_wait_on_a_file_is_allowed(tmp_path):
    assert decide("poll", _wait(), state_dir=tmp_path, now=T0).allow


def test_reissuing_a_wait_on_the_same_file_is_refused(tmp_path):
    """MEASURED on S207: 34 of 64 waits returned in under a second and were
    re-issued. Median 0.0s. Forensics scored that session 'no repeat bursts',
    because this gate could not see the kernel's own verb."""
    decide("poll", _wait(), state_dir=tmp_path, now=T0)
    d = decide("poll", _wait(), state_dir=tmp_path, now=T0 + 2)

    assert not d.allow
    assert "POLL-GUARD" in d.reason
    # The existing refusal already names the cure: the CLI wait-for, in a SECOND
    # pane, read ONCE. No wait-specific wording was added -- an earlier attempt
    # to inject one broke the implicit f-string concatenation, and a refusal
    # message is not worth risking the gate that carries it.
    assert "wait-for" in d.reason
    assert "SECOND pane" in d.reason


def test_a_wait_on_a_different_file_is_not_collateral_damage(tmp_path):
    decide("poll", _wait("/x/a.txt"), state_dir=tmp_path, now=T0)
    assert decide("poll", _wait("/x/b.txt"), state_dir=tmp_path, now=T0 + 1).allow


def test_a_capped_wait_chain_is_untouched(tmp_path):
    """Over MCP rag_wait is capped at ~30s, so a long job legitimately chains
    waits. Those are far outside the cooldown and must pass."""
    decide("poll", _wait(), state_dir=tmp_path, now=T0)
    later = T0 + POLL_COOLDOWN_SECONDS + 1
    assert decide("poll", _wait(), state_dir=tmp_path, now=later).allow
