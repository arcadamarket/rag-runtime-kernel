"""TRANSPORT-ALLOWLIST + universal hook coverage (E-133, S197).

What this suite is for
----------------------
S195's hook layer was four DENYLISTS bound to tool names someone enumerated in
advance. S197 walked straight through it: an agent read the host MCP client
config via ``mcp__Desktop_Commander__read_file``, which matched no matcher, so
``hook_guard`` was never invoked. The layer did not fail open. It was never
called. The operator was the detector.

The lesson the operator drew — and it is the right one — is that extending the
matcher list after each incident produces a layer that only ever covers defects
already paid for. So the shape changed: default-deny before every call,
compliance check after every call, and coverage at each turn boundary.

The tests below pin the properties that make that real rather than nominal:

  * DEFAULT-DENY — an unknown tool is refused because it is unknown, not
    because it is known-bad. The S197 tool is a named regression case.
  * THE RAG IS THE AUTHORITY — the projection is a cache. A projection that
    disagrees with the rule is drift and must be detectable; a projection that
    is missing or corrupt must fail SOFT to the built-in list, because a gate
    that bricks the session is a gate that gets switched off.
  * UNDER-EXTRACTION IS THE DANGEROUS BUG — the first renderer silently dropped
    every parenthesised pattern, narrowing the allowlist from six entries to
    three without a word. The declared-count assertion exists for that, and is
    tested directly.
  * POST CANNOT REFUSE — only PreToolUse carries a permission decision. Every
    other event injects context. Rendering a deny on a post-event would be a
    refusal that refuses nothing, and the envelope must reflect that.
  * THE LAYER REPORTS ITS OWN BLIND SPOT — if an undeclared tool ran anyway,
    post-compliance says so, because that means the pre-gate is not covering
    that call path at all.
"""

from __future__ import annotations

import json

import pytest

from rag_kernel import hook_guard
from rag_kernel.hook_guard import (
    DEFAULT_TRANSPORT_ALLOWLIST,
    GATES,
    TRANSPORT_ALLOWLIST_PROJECTION,
    Decision,
    decide,
    selftest,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _ev(name: str, **kw) -> dict:
    return {"tool_name": name, "tool_input": kw.get("tool_input", {}), **kw}


def _write_projection(root, patterns) -> None:
    d = root / ".claude"
    d.mkdir(parents=True, exist_ok=True)
    (d / "transport_allowlist.json").write_text(
        json.dumps({"allowlist": list(patterns)}), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# default-deny
# ---------------------------------------------------------------------------

def test_the_s197_breach_tool_is_refused():
    """Named regression: this exact tool got through the layer once."""
    d = decide("transport", _ev("mcp__Desktop_Commander__read_file"))

    assert d.allow is False
    assert "E-133" in d.reason


def test_an_entirely_unknown_tool_is_refused_for_being_unknown():
    d = decide("transport", _ev("mcp__some_server_nobody_declared__do_thing"))

    assert d.allow is False
    assert "not known at all" in d.reason


@pytest.mark.parametrize(
    "name",
    [
        "mcp__tmux-mcp__execute-command",
        "mcp__tmux-mcp__get-command-result",
        "mcp__rag-kernel__rag_wait",
        "Read",
        "Edit",
        "Bash",
        "Grep",
        "ToolSearch",
        "WebSearch",
    ],
)
def test_declared_transports_are_allowed(name):
    assert decide("transport", _ev(name)).allow is True


def test_refusal_names_the_sanctioned_alternative():
    d = decide("transport", _ev("mcp__whatever__read"))

    assert "mcp__tmux-mcp__" in d.reason
    assert "rag_wait" in d.reason


def test_refusal_tells_the_caller_to_declare_not_to_hand_edit():
    d = decide("transport", _ev("mcp__whatever__read"))

    assert "meta.transport_policy.allowlist" in d.reason or "allowlist" in d.reason
    assert "second source of truth" in d.reason


def test_missing_tool_name_is_allowed_rather_than_bricking_the_session():
    """A payload shape the gate does not understand must not stop the session."""
    assert decide("transport", {"tool_input": {}}).allow is True


# ---------------------------------------------------------------------------
# the RAG is the authority, the projection is a cache
# ---------------------------------------------------------------------------

def test_projection_overrides_the_builtin_fallback(tmp_path):
    _write_projection(tmp_path, [r"^only_this_one$"])

    assert decide("transport", _ev("only_this_one"), project_root=tmp_path).allow is True
    # tmux is in the BUILTIN list but not in this projection: the projection wins.
    assert decide("transport", _ev("mcp__tmux-mcp__execute-command"),
                  project_root=tmp_path).allow is False


def test_missing_projection_fails_soft_to_the_builtin(tmp_path):
    d = decide("transport", _ev("mcp__tmux-mcp__execute-command"), project_root=tmp_path)

    assert d.allow is True


def test_corrupt_projection_fails_soft_and_says_so(tmp_path):
    d = tmp_path / ".claude"
    d.mkdir()
    (d / "transport_allowlist.json").write_text("{not json", encoding="utf-8")

    verdict = decide("transport", _ev("mcp__nope__x"), project_root=tmp_path)

    assert verdict.allow is False
    assert "projection-unreadable" in verdict.reason, (
        "a corrupted projection silently reverts policy to the builtin; the "
        "refusal must name which source is actually in force"
    )


def test_empty_projection_falls_back_rather_than_denying_everything(tmp_path):
    _write_projection(tmp_path, [])

    assert decide("transport", _ev("Read"), project_root=tmp_path).allow is True


def test_uncompilable_pattern_in_projection_does_not_crash_the_gate(tmp_path):
    _write_projection(tmp_path, [r"^Read$", r"^(unclosed"])

    # falls back wholesale, because a projection that will not compile is not
    # a policy anyone authored on purpose
    assert decide("transport", _ev("Read"), project_root=tmp_path).allow is True


def test_builtin_fallback_covers_the_sanctioned_shell():
    assert any("tmux-mcp" in p for p in DEFAULT_TRANSPORT_ALLOWLIST)


# ---------------------------------------------------------------------------
# post-compliance — the after half
# ---------------------------------------------------------------------------

def test_post_audit_never_refuses():
    """The side effect already happened; a deny here would be theatre."""
    d = decide("post-transport-audit", _ev("mcp__anything__at_all", tool_response="x"))

    assert d.allow is True


def test_post_audit_flags_an_undeclared_tool_that_ran():
    """The one question the pre-gate cannot ask about itself."""
    d = decide("post-transport-audit", _ev("mcp__Desktop_Commander__read_file",
                                           tool_response="contents"))

    assert "HOOK-COVERAGE HOLE" in d.context
    assert "not covering this call path" in d.context


def test_post_audit_blames_the_layer_not_the_tool():
    d = decide("post-transport-audit", _ev("mcp__Desktop_Commander__read_file"))

    assert "the matcher, the wiring or the client is the defect" in d.context.replace("\n", " ")


def test_post_audit_is_quiet_on_a_declared_call():
    d = decide("post-transport-audit", _ev("mcp__tmux-mcp__execute-command",
                                           tool_response="ok"))

    assert d.context == ""


def test_post_audit_does_not_scan_tool_output(monkeypatch):
    """Substring-scanning results was cut on purpose: a gate that guesses at
    meaning from text will be wrong loudly and then be ignored."""
    d = decide("post-transport-audit",
               _ev("Bash", tool_response="wrote RAG_MASTER.json.tmp and failed"))

    assert d.context == "", "Bash is declared; nothing about its OUTPUT is this gate's business"


# ---------------------------------------------------------------------------
# envelope shape — only PreToolUse can refuse
# ---------------------------------------------------------------------------

def test_pretooluse_envelope_carries_a_permission_decision():
    payload = Decision("transport", False, reason="nope").as_hook_json("PreToolUse")

    assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("event", ["PostToolUse", "UserPromptSubmit", "Stop", "SessionStart"])
def test_non_pretooluse_envelopes_carry_context_not_a_verdict(event):
    payload = Decision("stop-seal", True, context="heads up").as_hook_json(event)

    out = payload["hookSpecificOutput"]
    assert out["hookEventName"] == event
    assert out["additionalContext"] == "heads up"
    assert "permissionDecision" not in out


def test_every_gate_declares_the_event_it_answers():
    assert set(GATES) == set(hook_guard._EVENT_FOR_GATE)


def test_only_pretooluse_gates_can_refuse():
    refusing = {g for g in GATES if hook_guard._EVENT_FOR_GATE[g] == "PreToolUse"}

    assert refusing == {"poll", "sandbox-state", "canonical-read", "transport"}


# ---------------------------------------------------------------------------
# scope — the layer must stay small on purpose
# ---------------------------------------------------------------------------

def test_the_layer_holds_only_boundary_gates():
    """Operator ruling S197: anything a kernel verb can enforce belongs in the
    verb, not here. If this set grows, the growth needs a reason that survives
    the question "why can't session-start refuse this?" — see
    HOOK-TO-VERB-MIGRATION."""
    assert set(GATES) == {
        "poll", "sandbox-state", "canonical-read", "transport",
        "deploy-parity", "post-transport-audit",
    }


@pytest.mark.parametrize("removed", ["prompt-frame", "stop-seal", "session-boot"])
def test_hooks_that_duplicate_a_verb_stay_removed(removed):
    """These were wired at S197 and cut in the same session. Re-adding one is
    a decision, not a convenience: it must fail this test first."""
    assert removed not in GATES
    with pytest.raises(ValueError, match="unknown gate"):
        decide(removed, {})


# ---------------------------------------------------------------------------
# the layer as a whole
# ---------------------------------------------------------------------------

def test_selftest_covers_every_declared_gate():
    _failures, lines = selftest()
    covered = {g for g in GATES if any(f" {g}:" in ln for ln in lines)}

    assert covered == set(GATES), (
        f"gates with no selftest probe: {sorted(set(GATES) - covered)}. A gate "
        f"nobody probes is indistinguishable from a gate that stopped running."
    )


def test_selftest_passes_clean():
    failures, _lines = selftest()

    assert failures == 0


def test_disable_env_is_honoured_and_announces_itself(monkeypatch):
    monkeypatch.setenv("RAG_HOOK_GUARD_DISABLED", "1")

    d = decide("transport", _ev("mcp__Desktop_Commander__read_file"))

    assert d.allow is True
    assert "layer disabled" in d.reason


def test_projection_constant_points_inside_dot_claude():
    assert ".claude" in TRANSPORT_ALLOWLIST_PROJECTION
