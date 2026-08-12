"""ORPHAN-SESSION GUARD — the S197 production regression, pinned.

What happened
-------------
S197 registered `python -m rag_kernel mcp` as an auto-start MCP server so the
agent could reach `rag_wait`. `cmd_mcp` at the time read:

    app = KernelApp(project, session_id=args.session_id)   # args.session_id None
    app.boot()                                             # eager
    ...
    finally: app.close()                                   # tidy close

With no `--session-id`, `KernelApp` mints one from the clock. So every launch of
the client:

  * minted a timestamp-shaped session id (`S1786495473201`),
  * opened `session_log_<that id>.jsonl`,
  * took the project lock,
  * wrote WAL entries from a fresh allocator starting at seq 1,
  * and closed cleanly on exit.

Six of those accumulated before the next session's boot audit caught it. The
damage was not the logs — it was that the WAL ended up non-monotonic (two
servers each starting at seq 1, one leaving a gap), and that every orphan closed
*cleanly*, so `uncheckpointed_session` reported five sessions that "ran to a
clean close" and never checkpointed. Every entry in the WAL turned out to be
artefact; not one real governed write was in it.

The fix, and what these tests hold in place
-------------------------------------------
Constructing `KernelApp` is cheap and side-effect free — `SessionLogger` opens
no file until boot. Booting is what costs. So:

  * boot is LAZY: only the `rag_boot` tool boots, never server startup;
  * boot is REFUSED unless the caller passed an explicit `--session-id`, so a
    server start cannot manufacture a session nobody asked for;
  * close is SYMMETRIC with boot, so an unbooted server exiting cannot write the
    tidy `session_end` that made the orphans look legitimate;
  * `rag_wait` — the reason an agent wants this server at all — is stateless and
    must keep working with no boot whatsoever.

The last point is the one worth restating: if `rag_wait` ever starts needing a
booted app, the fix above silently becomes a regression, because the tool that
justifies the server would stop working on a server started the safe way.
"""

from __future__ import annotations

import io
import json

import pytest

from rag_kernel.mcp_transport import MCPServer


class _SpyApp:
    """KernelApp stand-in that records boots and closes without performing them."""

    def __init__(self) -> None:
        self.boots = 0
        self.closes = 0

    def boot(self) -> dict:
        self.boots += 1
        return {"status": "OK", "state": "READY"}

    def close(self) -> dict:
        self.closes += 1
        return {"status": "OK"}


def _server(explicit: bool) -> tuple[MCPServer, _SpyApp]:
    app = _SpyApp()
    return MCPServer(app, input_stream=io.StringIO(), output_stream=io.StringIO(),
                     session_id_explicit=explicit), app


def _call(server: MCPServer, name: str, args: dict | None = None) -> dict:
    return server._handle_tools_call({"name": name, "arguments": args or {}})


def _payload(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


# ---------------------------------------------------------------------------
# the guard itself
# ---------------------------------------------------------------------------

def test_constructing_the_server_does_not_boot():
    """Server startup must be inert. This is the whole regression in one line."""
    _srv, app = _server(explicit=False)

    assert app.boots == 0


def test_rag_boot_is_refused_without_an_explicit_session_id():
    srv, app = _server(explicit=False)

    result = _call(srv, "rag_boot")

    assert result["isError"] is True
    assert app.boots == 0, "a refused boot must not have booted anything"
    assert "timestamp-shaped session id" in _payload(result)["error"]


def test_refusal_names_the_sanctioned_alternative():
    srv, _app = _server(explicit=False)

    err = _payload(_call(srv, "rag_boot"))["error"]

    assert "session-start" in err


def test_rag_boot_is_allowed_when_the_session_was_named():
    srv, app = _server(explicit=True)

    result = _call(srv, "rag_boot")

    assert "isError" not in result
    assert app.boots == 1
    assert srv.booted is True


def test_booted_flag_starts_false():
    srv, _app = _server(explicit=True)

    assert srv.booted is False, (
        "cmd_mcp closes only if this is True; if it starts True an unbooted "
        "server writes a clean session_end for a session that never opened — "
        "which is precisely what made the orphan logs look legitimate"
    )


def test_refused_boot_leaves_booted_false():
    srv, _app = _server(explicit=False)

    _call(srv, "rag_boot")

    assert srv.booted is False


# ---------------------------------------------------------------------------
# the tool the server exists for must survive the guard
# ---------------------------------------------------------------------------

def test_rag_wait_works_on_an_unbooted_server(tmp_path):
    """If this ever fails, the guard has broken the server's reason to exist."""
    log = tmp_path / "job.log"
    log.write_text("DONE\n", encoding="utf-8")
    srv, app = _server(explicit=False)

    payload = _payload(_call(srv, "rag_wait", {
        "path": str(log), "timeout_s": 5, "contains": "DONE",
    }))

    assert payload["outcome"] == "FOUND"
    assert app.boots == 0


def test_tools_list_is_available_without_booting():
    srv, app = _server(explicit=False)

    tools = srv._handle_tools_list({})["tools"]

    assert any(t["name"] == "rag_wait" for t in tools)
    assert app.boots == 0


# ---------------------------------------------------------------------------
# stateful tools still route to the app once booted
# ---------------------------------------------------------------------------

def test_a_stateful_tool_still_reaches_the_app_after_a_named_boot():
    srv, app = _server(explicit=True)
    _call(srv, "rag_boot")

    assert app.boots == 1
    assert srv.booted is True


@pytest.mark.parametrize("explicit", [True, False])
def test_unknown_tool_never_boots(explicit):
    srv, app = _server(explicit=explicit)

    result = _call(srv, "rag_definitely_not_a_tool")

    assert result["isError"] is True
    assert app.boots == 0
