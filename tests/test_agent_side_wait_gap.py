"""AGENT-SIDE-WAIT-GAP (S197) — the agent's blocking read, `rag_wait`.

Context this suite is pinning
-----------------------------
`wait_primitive` gave the SHELL a blocking wait at S180. The AGENT still had
none: its only wait was `mcp__tmux-mcp__get-command-result`, which is
poll-based. So the no-polling rule (E-081 -> E-116 -> E-128) was unfollowable
with the tools on hand, and S195 could only build a hook that REFUSES a second
poll — a gate standing in for a missing capability.

`rag_wait` is that capability. These tests pin the properties that make it a
fix rather than a second gate:

  * REGISTERED — advertised in tools/list with a schema, or the agent cannot
    discover it and falls back to polling
  * PRE-BOOT — dispatches without touching KernelApp, so it answers on a clone
    with no RAG, which is the case it primarily exists for. A KernelApp that
    explodes on any attribute access proves the independence.
  * TIMEOUT IS isError — `wait_for` RETURNS TIMEOUT rather than raising, so the
    generic handler would have reported an expired wait as success. This is the
    single most dangerous failure mode of the tool and gets the most coverage.
  * BOUNDED — emission and timeout are capped at the transport, because the
    agent's context, not the job, is what an agent-facing wait spends
  * HONEST ABOUT ITS LIMIT — a timeout beyond the MCP client's own request
    ceiling is named (PLAN-FEASIBILITY-GATE), not silently attempted
  * USAGE != TIMEOUT — a malformed request must never be mistakable for a job
    that ran and did not finish

The suite never sleeps: every wait is driven by an injected clock inside
`wait_primitive`, or satisfied before the call.
"""

from __future__ import annotations

import json

import pytest

from rag_kernel import mcp_transport
from rag_kernel.mcp_transport import TOOLS, MCPServer


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class _ExplodingApp:
    """A KernelApp stand-in that fails on ANY use.

    If `rag_wait` ever routes through the app, these tests stop passing — which
    is the point. The pre-boot guarantee is only real if it is enforced.
    """

    def __getattr__(self, name):  # pragma: no cover - defensive
        raise AssertionError(
            f"rag_wait must not touch KernelApp (attempted .{name}); it has to "
            "answer before boot, on a deployment with no RAG on disk."
        )


def _server() -> MCPServer:
    import io

    return MCPServer(_ExplodingApp(), input_stream=io.StringIO(), output_stream=io.StringIO())


def _call(args: dict) -> dict:
    return _server()._handle_tools_call({"name": "rag_wait", "arguments": args})


def _payload(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


def _tool_def() -> dict:
    return next(t for t in TOOLS if t["name"] == "rag_wait")


# ---------------------------------------------------------------------------
# registration — undiscoverable is indistinguishable from absent
# ---------------------------------------------------------------------------

def test_rag_wait_is_advertised_in_tools_list():
    names = [t["name"] for t in TOOLS]
    assert "rag_wait" in names, (
        "rag_wait missing from TOOLS: an agent that cannot see the blocking "
        "wait will poll, which is the defect this closes."
    )


def test_rag_wait_schema_requires_path_and_timeout():
    schema = _tool_def()["inputSchema"]
    assert set(schema["required"]) == {"path", "timeout_s"}
    for optional in ("contains", "emit_lines", "poll_ms"):
        assert optional in schema["properties"]


def test_rag_wait_description_steers_off_polling_and_toward_contains():
    desc = _tool_def()["description"]
    assert "get-command-result" in desc, "must name the tool it replaces"
    assert "contains" in desc, "must steer callers to token mode over existence"


def test_manifest_tool_list_matches_advertised_tools():
    """The in-file manifest is the reuse registry's view; drift makes it lie."""
    manifest = json.loads(mcp_transport.__doc__.split("@rag-kernel-manifest", 1)[1])
    assert set(manifest["tools"]) == {t["name"] for t in TOOLS}


# ---------------------------------------------------------------------------
# the success path
# ---------------------------------------------------------------------------

def test_found_returns_tail_and_is_not_an_error(tmp_path):
    log = tmp_path / "job.log"
    log.write_text("line one\nline two\nDONE\n", encoding="utf-8")

    result = _call({"path": str(log), "timeout_s": 5, "contains": "DONE", "emit_lines": 2})

    assert "isError" not in result
    payload = _payload(result)
    assert payload["outcome"] == "FOUND"
    assert payload["ok"] is True
    assert payload["emitted"] == ["line two", "DONE"]
    assert "render" in payload, "one round-trip must yield the human summary too"


def test_found_costs_zero_agent_round_trips(tmp_path):
    """An already-satisfied sentinel returns on the first internal poll."""
    log = tmp_path / "job.log"
    log.write_text("DONE\n", encoding="utf-8")

    payload = _payload(_call({"path": str(log), "timeout_s": 5, "contains": "DONE"}))

    assert payload["polls"] == 1
    assert payload["waited_s"] == 0.0


def test_pre_boot_dispatch_never_touches_kernelapp(tmp_path):
    """_ExplodingApp raises on any attribute; reaching FOUND proves isolation."""
    log = tmp_path / "job.log"
    log.write_text("DONE\n", encoding="utf-8")

    payload = _payload(_call({"path": str(log), "timeout_s": 5, "contains": "DONE"}))

    assert payload["outcome"] == "FOUND"


# ---------------------------------------------------------------------------
# the dangerous path — an expired wait must never read as success
# ---------------------------------------------------------------------------

def test_timeout_is_marked_iserror(tmp_path):
    missing = tmp_path / "never_written.log"

    result = _call({"path": str(missing), "timeout_s": 0.05, "poll_ms": 10})

    assert result.get("isError") is True, (
        "wait_for RETURNS TIMEOUT rather than raising, so a transport that "
        "leans on the generic try/except reports an expired wait as success. "
        "That is the failure this assertion exists to prevent."
    )
    payload = _payload(result)
    assert payload["outcome"] == "TIMEOUT"
    assert payload["ok"] is False


def test_timeout_on_file_that_exists_without_the_token(tmp_path):
    """The redirection race: `> out.txt` creates the file before any output."""
    log = tmp_path / "job.log"
    log.write_text("still working...\n", encoding="utf-8")

    result = _call({"path": str(log), "timeout_s": 0.05, "contains": "DONE", "poll_ms": 10})

    assert result.get("isError") is True
    assert _payload(result)["outcome"] == "TIMEOUT"


def test_timeout_render_tells_the_agent_not_to_relaunch(tmp_path):
    missing = tmp_path / "never.log"

    payload = _payload(_call({"path": str(missing), "timeout_s": 0.05, "poll_ms": 10}))

    assert "Do NOT re-launch it blindly" in payload["render"]


# ---------------------------------------------------------------------------
# usage errors are a different category from timeouts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "args",
    [
        {"path": "", "timeout_s": 5},
        {"path": None, "timeout_s": 5},
        {"path": "x", "timeout_s": 0},
        {"path": "x", "timeout_s": -1},
        {"path": "x", "timeout_s": 5, "contains": ""},
        {"path": "x", "timeout_s": 5, "poll_ms": 0},
    ],
)
def test_malformed_requests_are_usage_errors_not_timeouts(args):
    result = _call(args)

    assert result.get("isError") is True
    payload = _payload(result)
    assert "error" in payload
    assert "outcome" not in payload, (
        "a malformed request never ran, so it must not carry a wait outcome "
        "that could be read as 'the job did not finish'"
    )


def test_non_numeric_timeout_is_rejected_before_the_state_machine():
    payload = _payload(_call({"path": "x", "timeout_s": "soon"}))

    assert "timeout_s must be a number" in payload["error"]


def test_non_integer_emit_lines_is_rejected():
    payload = _payload(_call({"path": "x", "timeout_s": 5, "emit_lines": "lots"}))

    assert "emit_lines must be an integer" in payload["error"]


# ---------------------------------------------------------------------------
# bounds — an agent-facing wait spends context, not just time
# ---------------------------------------------------------------------------

def test_emit_lines_is_capped_at_the_transport(tmp_path):
    log = tmp_path / "job.log"
    log.write_text("\n".join(f"line {i}" for i in range(5000)) + "\nDONE\n", encoding="utf-8")

    payload = _payload(
        _call({"path": str(log), "timeout_s": 5, "contains": "DONE", "emit_lines": 10_000})
    )

    assert len(payload["emitted"]) == mcp_transport._WAIT_MAX_EMIT_LINES


def test_timeout_beyond_the_agent_ceiling_is_refused_not_attempted():
    result = _call({"path": "x", "timeout_s": mcp_transport._WAIT_MAX_TIMEOUT_S + 1})

    assert result.get("isError") is True
    assert "hung job, not a slow one" in _payload(result)["error"]


def test_timeout_at_exactly_the_ceiling_is_allowed(tmp_path):
    """The bound is a ceiling, not a fencepost off-by-one."""
    log = tmp_path / "job.log"
    log.write_text("DONE\n", encoding="utf-8")

    payload = _payload(
        _call(
            {
                "path": str(log),
                "timeout_s": mcp_transport._WAIT_MAX_TIMEOUT_S,
                "contains": "DONE",
            }
        )
    )

    assert payload["outcome"] == "FOUND"


# ---------------------------------------------------------------------------
# honesty about limits the kernel does not control
# ---------------------------------------------------------------------------

def test_long_wait_names_the_client_timeout_it_cannot_raise(tmp_path):
    log = tmp_path / "job.log"
    log.write_text("DONE\n", encoding="utf-8")

    payload = _payload(
        _call(
            {
                "path": str(log),
                "timeout_s": mcp_transport._CLIENT_TIMEOUT_HINT_S + 1,
                "contains": "DONE",
            }
        )
    )

    assert "MCP_TIMEOUT" in payload["client_timeout_note"], (
        "PLAN-FEASIBILITY-GATE: a limit the kernel cannot lift must be stated "
        "at the call, not discovered when the client cuts the request off."
    )


def test_short_wait_carries_no_client_timeout_noise(tmp_path):
    log = tmp_path / "job.log"
    log.write_text("DONE\n", encoding="utf-8")

    payload = _payload(_call({"path": str(log), "timeout_s": 1, "contains": "DONE"}))

    assert "client_timeout_note" not in payload


def test_existence_mode_carries_the_race_advisory(tmp_path):
    log = tmp_path / "job.log"
    log.write_text("anything\n", encoding="utf-8")

    payload = _payload(_call({"path": str(log), "timeout_s": 5}))

    assert "advisory" in payload
    assert "empty file" in payload["advisory"]


def test_contains_mode_carries_no_advisory(tmp_path):
    log = tmp_path / "job.log"
    log.write_text("DONE\n", encoding="utf-8")

    payload = _payload(_call({"path": str(log), "timeout_s": 5, "contains": "DONE"}))

    assert "advisory" not in payload


# ---------------------------------------------------------------------------
# dispatch integrity
# ---------------------------------------------------------------------------

def test_unknown_tool_still_errors_cleanly():
    result = _server()._handle_tools_call({"name": "rag_nope", "arguments": {}})

    assert result["isError"] is True
    assert "Unknown tool" in _payload(result)["error"]
