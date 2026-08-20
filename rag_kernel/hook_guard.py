"""HOOK-ENFORCEMENT-LAYER (S195) — process rules that REFUSE instead of remind.

The finding that produced this module: every recurring defect in this project's
error log is a rule the agent had loaded, could recite, and broke anyway. E-081
(never poll a running command) recurred as E-116 and again as E-128. E-071
(canonical state is read through the governed kernel, never a sandbox shell)
recurred through five sessions. The rules were never wrong and were never
forgotten — they were unenforceable. A rule that lives only in a prompt is a
hope; the agent is the thing being governed AND the thing checking the
governance, which is not a control.

This module moves those rules OUT of the context window and into the tool layer,
where a hook fires from configuration before the call happens and can return a
refusal the agent cannot talk its way past. Four gates, each tracing to a real
logged defect:

  ``poll``            PreToolUse on tmux ``get-command-result`` — refuses a
                      second query against the same command id inside the
                      cooldown, which is the machine-visible signature of
                      polling.                       Retires E-081/E-116/E-128.
  ``sandbox-state``   PreToolUse on Bash — refuses a sandbox shell that names a
                      canonical state file.                     Retires E-071.
  ``canonical-read``  PreToolUse on Read/Edit/Write — refuses a direct read or
                      hand-edit of RAG_MASTER.json. Boot rule 1 and the
                      tool_contract's "every canonical write goes through a
                      governed verb", both prose until now.
  ``deploy-parity``   PostToolUse on an edit to ``rag_kernel/*.py`` — reports
                      deployed-vs-committed drift AT THE EDIT, not at the seal.
                      Non-blocking by design: the edit is legitimate; the
                      silence afterwards is what costs.
  ``wait-duration``   PostToolUse on a blocking wait — reads the elapsed time out
                      of the wait's OWN output and records the ones that returned
                      instantly. The recording half of the fan-out refusal that
                      ``poll`` then makes at PreToolUse; neither event can see
                      enough on its own.        Retires POLL-GATE-BLIND-TO-WAIT-
                      FANOUT-S208 / WAIT-FOR-USED-AS-A-POLL-S198.

GATE-OR-HOPE-PRINCIPLE, stated honestly for this module. The three PreToolUse
gates are machine-gated: decidable predicates over the hook payload, no judgement
call, and their refusal is the only exit. The ONE hope is the layer's own
liveness — a hook process that cannot start cannot refuse anything, and this
module deliberately FAILS OPEN (allow + a loud stderr line) rather than
fail-closed, because a crashing guard that denies every Read would brick the
session it is meant to protect. That hope is not left unmeasured: ``selftest()``
drives every gate through a known-bad payload and asserts the refusal, so the
question "are the gates still gating" has a command instead of an opinion.

ML lens: this is the neurosymbolic split. The model proposes the action; a
symbolic, deterministic layer decides admissibility. Nothing here consumes a
token or asks the model to remember anything, which is precisely why it holds
when the context window is full and the session is nine hours old.

CS lens: a reference monitor. Complete mediation (every matched call passes
through it), tamper-resistance (configuration, not context), and a decision
function that is pure — ``decide()`` takes state and an event and returns a
verdict, so the policy is unit-testable without a live agent.

@rag-kernel-manifest
{
  "module": "rag_kernel.hook_guard",
  "capability": "process_enforcement",
  "description": "Claude Code PreToolUse/PostToolUse hook decision engine: refuses polling of a running tmux command, sandbox-shell access to canonical state, and direct read/hand-edit of RAG_MASTER.json; reports deploy-parity drift at the moment of the edit (HOOK-ENFORCEMENT-LAYER, S195)",
  "exports": ["GATES", "Decision", "decide", "run_gate", "selftest",
              "CANONICAL_FILES", "POLL_COOLDOWN_SECONDS", "HOOK_GUARD_VERSION",
              "WAIT_INSTANT_SECONDS", "WAIT_FANOUT_LIMIT",
              "WAIT_FANOUT_WINDOW_SECONDS"],
  "use_when": "Wiring or testing the .claude/settings.json enforcement layer, or asking whether a process rule is gated or merely hoped",
  "never_bypass": true
}
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

# Bump when a gate's verdict for a given payload changes — a hook whose policy
# moved without a version is indistinguishable from a hook that stopped running.
HOOK_GUARD_VERSION = "1.10.0"  # S209: waits carried by a shell are seen too

#: SCOPE OF THIS LAYER (operator ruling, S197) — deliberately small.
#:
#: A hook earns its cost only at the boundary where a NON-DETERMINISTIC actor
#: chooses an action. Everything a kernel verb can enforce belongs in the verb:
#: a verb is deterministic, unit-tested, fails closed, and travels to every
#: clone. A hook is vendor-specific, fails OPEN by declared choice, runs under a
#: 10s timeout on the host interpreter, and does not travel at all. Duplicating
#: a verb's invariant in a hook trades a strong guarantee for a weak one and
#: calls it defence in depth.
#:
#: So the gates below are exactly the facts that are invisible to the kernel
#: because the failure IS the bypass — the verb never ran, so it cannot report:
#:   * which transport the model reached for   (E-133)
#:   * that the model is polling a running job (E-081)
#:   * that a file tool was aimed at canonical state (E-071)
#: Plus one post-check that is a self-test of the layer rather than a policy:
#: an undeclared tool that ran anyway proves the pre-gate is not covering that
#: call path.
#:
#: S197 briefly wired prompt-frame / stop-seal / session-boot here and then
#: removed them: each checked something `session-start` or `session-end` can
#: refuse outright, so as hooks they were prose reminders in gate clothing.
#: That migration is tracked, not forgotten — see HOOK-TO-VERB-MIGRATION.
GATES: tuple[str, ...] = (
    "poll", "sandbox-state", "canonical-read",  # boundary-only, S195
    "unbounded-wait",                           # boundary-only, S206
    "tmux-heredoc",                             # boundary-only, S206
    "stop-status",                              # Stop, S206
    "transport",                                # boundary-only, default-deny, S197
    "deploy-parity", "post-transport-audit",    # PostToolUse
    "wait-duration",                            # PostToolUse, S209
)

#: TRANSPORT-ALLOWLIST (E-133, S197) — the inversion that the other four gates
#: are missing. They are DENYLISTS bound to tool NAMES that someone enumerated
#: in advance: `get-command-result`, `Bash`, `Read|Edit|Write`. A tool surface
#: that grows whenever an MCP server is connected cannot be policed that way.
#: S197 proved it: an agent read the host config through `mcp__Desktop_Commander__
#: read_file`, which matches no matcher, so no gate was consulted at all. The
#: layer did not fail open — it was never called. Only the operator noticed,
#: which is the definition of a hope rather than a gate.
#:
#: So this gate is the complement: under a catch-all matcher, a tool must be
#: NAMED HERE to run. An unknown transport is refused by construction, and
#: connecting a new server is a deliberate act that has to pass through the
#: declaration rather than an accident that silently widens the attack surface.
#:
#: This tuple is the FALLBACK only. The authority is the RAG rule
#: `operating_protocol.transport_allowlist` — corrected S206: this comment named
#: `meta.transport_policy.allowlist`, a key that does not exist in this RAG and
#: that nothing reads, while the projection header and the renderer both name the
#: rule. A comment that misdirects the next reader to a non-existent authority is
#: the same defect class as a rule with no enforcer. Projected to
#: `.claude/transport_allowlist.json` by `tools/render_transport_allowlist.py`.
#: The projection exists because the hook must answer in milliseconds with no
#: kernel import and no lock; it is a cache of a RAG fact, never a second
#: source of it, and `audit` fails on drift between the two.
DEFAULT_TRANSPORT_ALLOWLIST: tuple[str, ...] = (
    # the sanctioned shell — every governed verb rides this
    r"^mcp__tmux-mcp__",
    # the kernel's own agent-facing server (AGENT-SIDE-WAIT-GAP, S197)
    r"^mcp__rag-kernel__",
    # first-party file and search tools; the canonical-read gate narrows these
    r"^(Read|Edit|Write|MultiEdit|NotebookEdit|Glob|Grep|LS)$",
    # planning / task surface, no host reach
    r"^(Task|TaskCreate|TaskUpdate|TaskList|TaskGet|TaskStop|ToolSearch)$",
    r"^(WebSearch|WebFetch)$",
    # The ATOMIC single-command fallback named by tool_hierarchy. Not a
    # substitute for the PRIMARY: it strips &&/;/| and $(), so a compound
    # command silently becomes a different command.
    r"^mcp__wsl-exec__",
    # ^Bash$ WAS HERE AND WAS REMOVED (S206, TRANSPORT-RULE-HAS-NO-ENFORCER-S206).
    # tool_hierarchy declares exactly one shell order -- tmux-mcp (PRIMARY) >
    # wsl-exec (ATOMIC fallback) > PowerShell (LAST RESORT) -- and the
    # first-party Bash tool is not in it at any position. Allowing it here made
    # the hierarchy a declaration with no enforcer, and MEASURED S206 that is
    # not a theoretical gap: an agent that had loaded the rule and been
    # corrected twice still reached for Bash, because the host system prompt
    # tells every agent to prefer it. The allowlist now mirrors the hierarchy
    # one-for-one so the two cannot disagree.
    # S206, GATE-CONTRADICTS-ITS-OWN-RULE-POWERSHELL-S206: tool_hierarchy names
    # PowerShell as the LAST RESORT and as THE recovery path when the WSL
    # transport is down. Refusing it left this deployment's single point of
    # failure with its emergency exit disabled — WSL died twice on 2026-08-17.
    # Narrowed by the same sandbox-state gate as Bash.
    r"^PowerShell$",
)

#: Projection path, relative to the project root.
TRANSPORT_ALLOWLIST_PROJECTION = os.path.join(".claude", "transport_allowlist.json")

#: Which Claude Code event each gate answers. Only PreToolUse can refuse; the
#: rest inject context. Keeping the mapping as data rather than a conditional
#: means adding a gate cannot silently mis-declare its own event.
_EVENT_FOR_GATE: dict[str, str] = {
    "poll": "PreToolUse",
    "sandbox-state": "PreToolUse",
    "unbounded-wait": "PreToolUse",
    "tmux-heredoc": "PreToolUse",
    "stop-status": "Stop",
    "canonical-read": "PreToolUse",
    "transport": "PreToolUse",
    "deploy-parity": "PostToolUse",
    "post-transport-audit": "PostToolUse",
    "wait-duration": "PostToolUse",
}

#: Files that ARE the canonical state. Naming one of these from a sandbox shell
#: or a file tool is the E-071 tool_hierarchy violation, whatever the intent.
CANONICAL_FILES: frozenset[str] = frozenset({
    "RAG_MASTER.json",
    "RAG_MASTER.json.bak",
    "RAG_COLD.json",
    "RAG_COLD.json.bak",
})

#: Seconds within which a second query against the SAME command id is polling.
#: One check after a single long wait is the sanctioned pattern and stays legal;
#: the second check twenty seconds later is the thing that cost E-128.
POLL_COOLDOWN_SECONDS = float(os.environ.get("RAG_HOOK_POLL_COOLDOWN", "25"))

# ---------------------------------------------------------------------------
# POLL-GATE-BLIND-TO-WAIT-FANOUT-S208 — the second blindness of the wait guard
# ---------------------------------------------------------------------------
#
# The S198 fix keyed the wait half of the poll gate on the TARGET FILE, for a
# good reason: over MCP a wait is capped by the client timeout, so one long job
# legitimately chains several waits and repetition alone is not the defect.
#
# It is also exactly why the gate never fired. Measured on the S207 final turn:
# 66 of 113 tool calls were waits, 45% of them returned in under a second, and
# the gate fired ZERO times — because every wait named a DIFFERENT file, so
# nothing ever repeated. Same-target hammering was closed; fan-out was wide open.
#
# The invariant that survives both shapes is not repetition, it is DURATION. A
# blocking wait that returns in half a millisecond did not block: the sentinel
# was already in the file before the call was made, so the call bought a round
# trip and no information. That is a poll wearing the anti-polling verb, and it
# is measurable — the wait verb prints its own elapsed time in its own output.
#
# So the gate is split across two events, which is what the S208 note prescribed:
#   PostToolUse `wait-duration` sees ``tool_response`` and RECORDS instant returns;
#   PreToolUse  `poll` REFUSES the next wait once enough of them pile up,
#   regardless of which files they named.
#
# ONE REFUSAL PER BURST, deliberately: the refusal clears the window, so a
# genuinely long wait issued right after it proceeds. A gate that bricks the
# session it protects gets switched off by the first person it inconveniences.

#: A wait that returned in under this many seconds did not wait.
WAIT_INSTANT_SECONDS = float(os.environ.get("RAG_HOOK_WAIT_INSTANT", "1.0"))

#: How many instant returns inside the window make a fan-out rather than luck.
WAIT_FANOUT_LIMIT = int(os.environ.get("RAG_HOOK_WAIT_FANOUT", "4"))

#: Seconds the fan-out is counted over. Bound to the prune horizon on purpose —
#: `_prune` drops window entries older than ten cooldowns, so a wider window here
#: would be documented but not real.
WAIT_FANOUT_WINDOW_SECONDS = float(
    os.environ.get("RAG_HOOK_WAIT_WINDOW", str(POLL_COOLDOWN_SECONDS * 10))
)

#: Reserved key in the poll-window state file. Not a command id, so it can never
#: collide with one: every real key is `wait:<path>` or a tmux command id.
_WAIT_RETURNS_KEY = "__wait_instant_returns__"

#: The wait verb prints its own elapsed time: `wait-for: FOUND after 0.0s (1 polls)`.
#: Reading the duration out of the RESPONSE rather than timing the hook keeps the
#: measurement inside the thing being measured and works over any transport.
_WAIT_ELAPSED = re.compile(r"\bafter\s+([0-9]+(?:\.[0-9]+)?)\s*s\b", re.I)

#: Tool names each gate mediates. Matched case-sensitively against ``tool_name``
#: as Claude Code reports it; the settings.json matcher narrows first, this is
#: the belt to that braces (a mis-scoped matcher must not silently disable a
#: gate — it must fail to match and the gate simply allows, never crashes).
#: The kernel's own blocking-wait surfaces. Named separately from the tmux
#: result-reader because they are keyed differently: a command id there, the
#: TARGET FILE here.
_WAIT_TOOLS = re.compile(r"rag_wait|wait-for", re.I)

# WAIT-CARRIED-BY-A-SHELL-S209. The tool NAME is not where a wait announces
# itself. Measured over the whole S209 transcript: 59 blocking waits reported a
# duration and NONE of them arrived under a matching tool name -- every one rode
# inside `mcp__wsl-exec__execute_command` or `mcp__tmux-mcp__execute-command`
# with `rag_kernel wait-for ...` in the command string, because the CLI is what
# the no-polling rule tells the agent to use over MCP. A gate keyed on the name
# therefore saw zero of the traffic it was built to refuse, which is the S198
# blindness one layer out: right predicate, wrong field.
#
# Tighter than the tool-name match on purpose: this decides a REFUSAL, so it
# requires an actual invocation (`rag_kernel wait-for`, `rag_wait`) rather than
# the words appearing anywhere -- a grep for "wait-for" is not a wait.
_WAIT_IN_COMMAND = re.compile(r"rag_kernel\s+wait-for\b|\brag_wait\b", re.I)
_WAIT_TARGET_IN_COMMAND = re.compile(r"wait-for\s+(\S+)", re.I)


def _wait_invocation(name: str, tool_input: dict) -> "tuple[bool, str]":
    """Is this call a blocking wait, and on what target? -> ``(is_wait, target)``.

    Checks the tool name first, then the TEXT of the call, so a wait carried by
    a generic shell tool is seen. Returning the target as well keeps the two
    callers from re-deriving it differently.
    """
    if _WAIT_TOOLS.search(name or ""):
        return True, _wait_target(tool_input)
    for value in (tool_input or {}).values():
        if isinstance(value, str) and _WAIT_IN_COMMAND.search(value):
            m = _WAIT_TARGET_IN_COMMAND.search(value)
            return True, (m.group(1).strip("'\"") if m else "")
    return False, ""

_POLL_TOOLS = re.compile(r"get-?command-?result", re.I)
_SHELL_TOOLS = re.compile(r"(^Bash$)|(bash$)", re.I)
_FILE_TOOLS = re.compile(r"^(Read|Edit|Write|NotebookEdit|MultiEdit)$")
#: Two-copy sources: edited in the worktree, RUN from the deployment. S201 added
#: scripts/ — grand_audit.py became a two-copy asset the moment it was banked
#: into git, and an ungated pair is exactly the drift this gate exists to catch.
_KERNEL_SOURCE = re.compile(r"(?:rag_kernel|scripts)[/\\][^/\\]+\.py$")

#: A shell segment that invokes the kernel's OWN CLI (S201). Naming canonical
#: state on such a segment IS the governed path — it takes the lock, appends the
#: WAL, rotates the .bak — so refusing it refuses boot rule 1 itself.
#:
#: MEASURED S201, the layer's first live firing in its existence: this gate
#: refused `python -m rag_kernel session-start --rag RAG_MASTER.json`, which
#: CLAUDE.md §1 mandates as the ONLY sanctioned boot path. The predicate was
#: "the command string contains the filename" and never asked what named it, so
#: the first thing the enforcement layer ever did was block the protocol it
#: exists to enforce. A gate that fires is not yet a gate that is right.
_GOVERNED_VERB = re.compile(
    r"(?:^|[\s;&|(])(?:python[0-9._]*(?:\.exe)?|py)\s+(?:-\S+\s+)*-m\s+rag_kernel(?:\s|$)"
    r"|(?:^|[\s;&|(])rag[-_]kernel(?:\s|$)",
    re.I,
)

#: Shell separators that begin a NEW command. Each segment is judged alone, so
#: `cat RAG_MASTER.json && python -m rag_kernel status` is still refused for its
#: first half — the governed verb in the second half does not launder the first.
_SHELL_SEGMENT = re.compile(r"\|\||&&|[;&|\n]")

_ALLOW_ENV = "RAG_HOOK_GUARD_DISABLED"


@dataclass(frozen=True)
class Decision:
    """A gate's verdict. ``allow`` is the only thing the caller may act on."""

    gate: str
    allow: bool
    reason: str = ""
    context: str = ""

    def as_hook_json(self, event_name: str) -> dict:
        """Render the verdict in Claude Code's hook output contract.

        Only PreToolUse carries a permission decision. Every other event can
        inject context but cannot refuse, which is a real limit of the layer
        and the reason the universal coverage is split: default-deny happens
        BEFORE the call, and everything after it can only make a violation
        loud. Rendering a deny on a post-event would be a refusal that refuses
        nothing.
        """
        if event_name != "PreToolUse":
            out: dict[str, Any] = {"hookEventName": event_name}
            if self.context:
                out["additionalContext"] = self.context
            return {"hookSpecificOutput": out}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny" if not self.allow else "allow",
                "permissionDecisionReason": self.reason,
            }
        }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _tool_name(event: dict) -> str:
    return str(event.get("tool_name") or "")


def _tool_input(event: dict) -> dict:
    ti = event.get("tool_input")
    return ti if isinstance(ti, dict) else {}


def _names_canonical(text: str) -> Optional[str]:
    """The canonical filename ``text`` refers to, or None.

    Basename match, so an absolute path, a relative path and a bare filename all
    resolve the same way — a guard that could be stepped around by writing
    ``./RAG_MASTER.json`` would be theatre.
    """
    if not text:
        return None
    for name in sorted(CANONICAL_FILES, key=len, reverse=True):
        if re.search(rf"(^|[\s\"'=/\\]){re.escape(name)}($|[\s\"';:,)])", text):
            return name
    return None


def _state_base(state_dir: Optional[Path]) -> Path:
    return Path(state_dir) if state_dir else Path(
        os.environ.get("RAG_HOOK_STATE_DIR", Path.home() / ".rag_kernel_hooks")
    )


def _state_path(state_dir: Optional[Path]) -> Path:
    return _state_base(state_dir) / "poll_state.json"


#: HOOKS-LIVENESS-PROOF (S199). The file a running hook stamps, and the only
#: evidence anything outside this process can have that the layer FIRED.
HEARTBEAT_NAME = "hook_heartbeat.json"


def heartbeat_path(state_dir: Optional[Path] = None) -> Path:
    return _state_base(state_dir) / HEARTBEAT_NAME


#: HEARTBEAT-PROVENANCE (S200). The only value of `source` that proves the layer
#: fired for real. Anything else — a test, a selftest, a hand-run probe — is a
#: measurement OF the gate, not an execution BY the client, and must never be
#: readable as liveness. See the S200 finding below.
LIVE_HEARTBEAT_SOURCE = "hook_entry"


def _heartbeat_source(explicit: Optional[str]) -> str:
    """Classify who is stamping. Explicit wins; pytest is detected, not trusted.

    S200 measured the failure this exists to stop: `tests/
    test_hook_enforcement_layer.py` called ``hook_entry.main(["--gate",
    "canonical-read"])`` with no state-dir override, so EVERY pytest run wrote
    the production heartbeat. `check_hook_layer_live` then read it and reported
    the layer live — in a client that has never executed a single hook. The
    suite was manufacturing the evidence the audit consumed, which is why eight
    sessions of "2,802 green" proved nothing and refused nothing.

    Detection is defence in depth behind the conftest fixture that pins
    RAG_HOOK_STATE_DIR: a heartbeat that labels itself is safe even when it
    lands somewhere it should not.
    """
    if explicit:
        return explicit
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return "test"
    return LIVE_HEARTBEAT_SOURCE


def record_heartbeat(gate: str, *, state_dir: Optional[Path] = None,
                     now: Optional[float] = None,
                     source: Optional[str] = None) -> None:
    """Stamp that a hook actually executed. Never raises.

    WHY THIS EXISTS, measured S199. `hook-guard --selftest` passes every gate,
    including `poll: mcp__tmux-mcp__get-command-result -> DENY`. In the SAME
    session, roughly forty consecutive polls of single command ids were made and
    not one was refused. Both facts are true because the layer is wired through
    `.claude/settings.json`, which the vendor client running this session does
    not read. The gate was real, tested, deployed — and reached nothing.

    That is the shape of failure this project calls hope: a guard whose absence
    is indistinguishable from its silence. A heartbeat makes the two different.
    Nothing can force a vendor to run a hook; the kernel CAN refuse to pretend
    one ran. See drift_audit.check_hook_layer_live for the clause that reads it.
    """
    try:
        import time
        path = heartbeat_path(state_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "last_utc": (now if now is not None else time.time()),
            "gate": gate,
            "pid": os.getpid(),
            "source": _heartbeat_source(source),
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(path)
    except Exception:  # noqa: BLE001 — a heartbeat must never break a tool call
        pass


def _load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(path: Path, state: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        tmp.replace(path)
    except OSError:  # a guard that cannot persist still decides this call
        pass


def _prune(state: dict, now: float) -> dict:
    """Drop entries older than ten cooldowns — the file is a window, not a log."""
    horizon = now - (POLL_COOLDOWN_SECONDS * 10)
    return {k: v for k, v in state.items()
            if isinstance(v, dict) and float(v.get("last", 0)) >= horizon}


def _wait_target(tool_input: dict) -> str:
    """The file a wait call names, however the transport spells the argument."""
    for key in ("path", "file", "filename", "sentinel", "target"):
        value = tool_input.get(key)
        if value:
            return str(value).strip()
    return ""


def _response_text(event: dict) -> str:
    """``tool_response`` as text, whatever shape the client wrapped it in."""
    resp = event.get("tool_response")
    if isinstance(resp, str):
        return resp
    if isinstance(resp, dict):
        for key in ("output", "stdout", "text", "content", "result"):
            value = resp.get(key)
            if isinstance(value, str):
                return value
        return json.dumps(resp)
    if isinstance(resp, list):
        return " ".join(str(part) for part in resp)
    return "" if resp is None else str(resp)


def _wait_elapsed_seconds(event: dict) -> Optional[float]:
    """How long a wait ACTUALLY blocked, read out of its own output. -> ``None``
    when the response says nothing about it, which is never treated as instant:
    an unmeasured wait must not be charged as a violation."""
    match = _WAIT_ELAPSED.search(_response_text(event))
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _instant_returns(state: dict, now: float) -> list:
    """The recorded instant returns still inside the fan-out window."""
    entry = state.get(_WAIT_RETURNS_KEY)
    if not isinstance(entry, dict):
        return []
    kept = []
    for stamp in entry.get("stamps") or []:
        if not isinstance(stamp, (list, tuple)) or len(stamp) != 2:
            continue
        try:
            when = float(stamp[0])
        except (TypeError, ValueError):
            continue
        if now - when <= WAIT_FANOUT_WINDOW_SECONDS:
            kept.append([when, str(stamp[1])])
    return kept


# ---------------------------------------------------------------------------
# gates
# ---------------------------------------------------------------------------

def _refuse_wait_fanout(*, state_dir: Optional[Path],
                        now: float) -> Optional[Decision]:
    """Refuse the next wait when enough recent ones returned instantly.

    POLL-GATE-BLIND-TO-WAIT-FANOUT-S208. Returns ``None`` when the call may
    proceed. The window is CLEARED on refusal — one refusal per burst — so the
    very next wait, which may be a genuinely long one, is not collateral damage.
    """
    path = _state_path(state_dir)
    state = _prune(_load_state(path), now)
    stamps = _instant_returns(state, now)
    if len(stamps) < WAIT_FANOUT_LIMIT:
        return None
    targets = {t for _, t in stamps if t}
    state.pop(_WAIT_RETURNS_KEY, None)
    _save_state(path, state)
    return Decision(
        "poll", False,
        reason=(
            f"POLL-GUARD FAN-OUT (POLL-GATE-BLIND-TO-WAIT-FANOUT-S208, "
            f"WAIT-FOR-USED-AS-A-POLL-S198): your last {len(stamps)} blocking "
            f"waits returned in under {WAIT_INSTANT_SECONDS:g}s each, across "
            f"{len(targets)} different file(s), inside the last "
            f"{WAIT_FANOUT_WINDOW_SECONDS:g}s. A wait that returns instantly did "
            f"not wait: the sentinel was already in the file before you called, "
            f"so the call bought a round-trip and no information. That is "
            f"polling wearing the anti-polling verb — and it passed the old "
            f"guard precisely because each wait named a DIFFERENT file, so "
            f"nothing ever repeated.\n"
            f"  REPAIR 1, the shape that cannot fan out: launch and wait in ONE "
            f"call with `python -m rag_kernel run` (see `run --help`) — it "
            f"returns DONE/FAILED/TIMEOUT/DIED plus a bounded tail, and there is "
            f"deliberately no intermediate handle to poll.\n"
            f"  REPAIR 2, when the job has already finished: READ the output "
            f"file. Do not wait for a sentinel you have already been told about.\n"
            f"  This refusal cleared the window: the next wait is allowed, so a "
            f"genuinely long wait is not blocked by this."
        ),
    )


def _gate_wait_duration(event: dict, *, state_dir: Optional[Path] = None,
                        now: float = 0.0, **_: Any) -> Decision:
    """PostToolUse: record how long each blocking wait ACTUALLY blocked.

    The recording half of POLL-GATE-BLIND-TO-WAIT-FANOUT-S208. It never refuses —
    by the time a PostToolUse hook runs the round-trip has already been spent —
    it writes the one fact PreToolUse cannot observe on its own, and warns as the
    count approaches the limit so the refusal is never a surprise.

    An unmeasured wait is recorded as nothing at all. A response that does not
    state its elapsed time must not be charged as instant: the gate is allowed to
    miss a violation, and is not allowed to invent one.
    """
    is_wait, target = _wait_invocation(_tool_name(event), _tool_input(event))
    if not is_wait:
        return Decision("wait-duration", True)
    elapsed = _wait_elapsed_seconds(event)
    if elapsed is None or elapsed >= WAIT_INSTANT_SECONDS:
        return Decision("wait-duration", True)

    path = _state_path(state_dir)
    state = _prune(_load_state(path), now)
    stamps = _instant_returns(state, now)
    stamps.append([now, target])
    stamps = stamps[-50:]
    state[_WAIT_RETURNS_KEY] = {"last": now, "count": len(stamps), "stamps": stamps}
    _save_state(path, state)

    if len(stamps) < WAIT_FANOUT_LIMIT - 1:
        return Decision("wait-duration", True)
    remaining = WAIT_FANOUT_LIMIT - len(stamps)
    if remaining > 0:
        tail = (f"{remaining} more and the next wait is REFUSED "
                f"(POLL-GATE-BLIND-TO-WAIT-FANOUT-S208).")
    else:
        tail = ("the next wait is REFUSED "
                "(POLL-GATE-BLIND-TO-WAIT-FANOUT-S208).")
    return Decision(
        "wait-duration", True,
        context=(
            f"WAIT-FAN-OUT: that wait returned after {elapsed:g}s — it did not "
            f"wait, the sentinel was already there. {len(stamps)} such instant "
            f"returns in the last {WAIT_FANOUT_WINDOW_SECONDS:g}s; {tail} "
            f"Launch and wait in ONE call instead: `python -m rag_kernel run`."
        ),
    )


def _gate_poll(event: dict, *, state_dir: Optional[Path] = None,
               now: float = 0.0, **_: Any) -> Decision:
    """Refuse a repeat query against a command id inside the cooldown.

    E-081 -> E-116 -> E-128: the same defect three times, each time by an agent
    that had the rule loaded. The tell is machine-visible — two calls naming one
    command id, seconds apart — so it is checkable, so it is now checked.
    """
    name = _tool_name(event)
    ti = _tool_input(event)

    # WAIT-FOR-USED-AS-A-POLL-S198, first of its three blindnesses. This gate
    # matched only the tmux result-reader, so the kernel's OWN wait verb -- the
    # thing the poll refusal recommends -- passed untouched and became the
    # polling instrument. Keyed on the TARGET FILE, because the verb repeating
    # is not the defect: over MCP rag_wait is capped by the client timeout
    # (~30s), so a long job legitimately chains several waits and those are far
    # outside any cooldown. The defect is a wait that returned instantly being
    # re-issued against the same file seconds later.
    is_wait, target = _wait_invocation(name, ti)
    if is_wait:
        # POLL-GATE-BLIND-TO-WAIT-FANOUT-S208. Checked BEFORE the per-target
        # cooldown, because the whole point is that the target does not repeat.
        fanout = _refuse_wait_fanout(state_dir=state_dir, now=now)
        if fanout is not None:
            return fanout
        if not target:
            return Decision("poll", True)
        cmd_id = "wait:" + target
    elif _POLL_TOOLS.search(name):
        cmd_id = str(ti.get("commandId") or ti.get("command_id") or "").strip()
        if not cmd_id:
            return Decision("poll", True)
    else:
        return Decision("poll", True)

    path = _state_path(state_dir)
    state = _prune(_load_state(path), now)
    prior = state.get(cmd_id)
    if isinstance(prior, dict):
        elapsed = now - float(prior.get("last", 0))
        if elapsed < POLL_COOLDOWN_SECONDS:
            count = int(prior.get("count", 1))
            # The refusal is recorded too: an agent that hammers the gate should
            # see the count climb rather than get a fresh window each attempt.
            state[cmd_id] = {"last": now, "count": count + 1}
            _save_state(path, state)
            return Decision(
                "poll", False,
                reason=(
                    f"POLL-GUARD (E-081/E-116/E-128): you already queried command "
                    f"{cmd_id} {elapsed:.0f}s ago; this is attempt {count + 1}. "
                    f"Polling a running command is refused, not discouraged. "
                    f"Use the blocking read instead — run, in a SECOND pane: "
                    f"`python -m rag_kernel wait-for <sentinel file> --timeout N "
                    f"--contains DONE --emit 20`. It blocks server-side and "
                    f"returns the tail in ONE round-trip. If you have no sentinel "
                    f"file, relaunch the job as "
                    f"`... > .boot/job.txt 2>&1; echo QQ_JOB_DONE_QQ >> "
                    f".boot/job.txt`."
                ),
            )
    state[cmd_id] = {"last": now, "count": int((prior or {}).get("count", 0)) + 1}
    _save_state(path, state)
    return Decision("poll", True)


def _gate_sandbox_state(event: dict, **_: Any) -> Decision:
    """Refuse a shell that reaches canonical state OUTSIDE a governed verb (E-071).

    The rule was never "do not name RAG_MASTER.json". It was "reach state only
    through the path that takes the lock, appends the WAL and rotates the .bak".
    So the predicate is per shell segment: does THIS segment name canonical state
    without being the kernel's own CLI. `cat RAG_MASTER.json` is refused;
    `python -m rag_kernel session-start --rag RAG_MASTER.json` is the governed
    path and is allowed — see _GOVERNED_VERB for why that distinction had to be
    made at S201.
    """
    if not _SHELL_TOOLS.search(_tool_name(event)):
        return Decision("sandbox-state", True)
    command = str(_tool_input(event).get("command") or "")
    hit: Optional[str] = None
    for segment in _SHELL_SEGMENT.split(command):
        named = _names_canonical(segment)
        if named and not _GOVERNED_VERB.search(segment):
            hit = named
            break
    if not hit:
        return Decision("sandbox-state", True)
    return Decision(
        "sandbox-state", False,
        reason=(
            f"TOOL-HIERARCHY (E-071): this shell command names {hit}, which is "
            f"canonical state, OUTSIDE a governed kernel verb. Refused. Read "
            f"state with `rag_kernel session-start` (boot) or `rag_kernel items` "
            f"/ `report` (mid-session), and WRITE it only through a governed verb "
            f"(add / note / priority / start / resolve / defer / reopen / discard "
            f"/ supersede / checkpoint). Atomicity, the WAL append, the checksum "
            f"and the .bak rotation are preconditions of the write, not "
            f"follow-ups. Invoking the kernel CLI in this same command is allowed "
            f"and is the intended path."
        ),
    )


#: An unbounded wait loop written by hand instead of using the wait verb.
#: `while`/`until` around a `sleep` with no iteration cap runs forever by
#: construction; `wait-for` cannot, because --timeout is mandatory.
_HANDROLLED_WAIT = re.compile(
    r"\b(while|until)\b[^\n]*?;\s*do\b[^\n]*?\bsleep\b", re.I | re.S)
#: `pgrep -f <pattern>` matches against FULL command lines including the shell
#: process that is running the pgrep, so a loop waiting on its own pattern waits
#: on itself. This is not hypothetical: S206 hung on exactly this, forever.
_PGREP_SELFMATCH = re.compile(r"\bpgrep\b[^\n]*\-\w*f", re.I)


def _gate_unbounded_wait(event: dict, **_: Any) -> Decision:
    """Refuse a hand-rolled wait loop. The wait is a verb, not a control flow.

    S206, the third cold-start casualty in three sessions. An agent whose boot
    had not completed — so it held none of the rules — needed to wait for a
    detached job and wrote::

        while pgrep -f "rag_kernel session-start" >/dev/null; do sleep 3; done

    Two independent defects in one line. The loop has no timeout, so nothing can
    end it but a human. And ``pgrep -f`` matches full command lines, so the
    pattern matched the very bash process evaluating it: the loop waited on
    itself and could never exit. The operator had to kill it by hand, which is
    precisely the manual-rescue pattern Rule 45 exists to abolish.

    Rule 44 (no_polling) already forbids this in words. Words did not reach the
    agent, because rules are delivered at attestation and this agent never got
    there — COLD-BOOT-HAS-NO-RULES-S205. So the rule is made a refusal, which
    needs no delivery: the gate fires whether or not the agent has been told.

    DECIDABLE PREDICATE, no judgement: a shell command containing a while/until
    loop whose body sleeps, or any ``pgrep -f``. Bounded loops (``for i in
    $(seq 1 20)``) are untouched, and so is every use of ``wait-for``/``rag_wait``.
    """
    if not _SHELL_TOOLS.search(_tool_name(event)):
        return Decision("unbounded-wait", True)
    command = str(_tool_input(event).get("command") or "")
    if not command:
        return Decision("unbounded-wait", True)

    handrolled = bool(_HANDROLLED_WAIT.search(command))
    selfmatch = bool(_PGREP_SELFMATCH.search(command))
    if not (handrolled or selfmatch):
        return Decision("unbounded-wait", True)

    why = []
    if handrolled:
        why.append("a while/until loop whose body sleeps has NO timeout — only a "
                   "human can end it")
    if selfmatch:
        why.append("`pgrep -f` matches full command lines INCLUDING the shell "
                   "running it, so a loop waiting on its own pattern waits on "
                   "itself forever (measured: S206 hung on exactly this)")
    return Decision(
        "unbounded-wait", False,
        reason=(
            "NO-POLLING (Rule 44): " + "; and ".join(why) + ". Refused. THE WAIT "
            "IS A VERB, NOT CONTROL FLOW — launch the job detached to a file that "
            "ends with a DISTINCTIVE completion token, then block ONCE:\n"
            "  nohup bash -c '<job> > /path/log 2>&1; echo SUITE-DONE >> /path/log' &\n"
            "  python -m rag_kernel wait-for /path/log --timeout 900 "
            "--contains SUITE-DONE --emit 20\n"
            "`--timeout` is mandatory there, so that wait cannot hang. Pick a "
            "token that cannot appear in ordinary output: S203 used 'D' and the "
            "wait matched the first capital D and returned instantly."
        ),
    )


def _gate_canonical_read(event: dict, **_: Any) -> Decision:
    """Refuse a direct read or hand-edit of the canonical RAG via a file tool.

    Boot rule 1 (state is loaded by ``session-start``, never by opening the file)
    and tool_contract clause 1 (every canonical write goes through a governed
    verb) were both prose, enforced by the agent's own good intentions, which is
    the definition of ungated.
    """
    name = _tool_name(event)
    if not _FILE_TOOLS.match(name):
        return Decision("canonical-read", True)
    ti = _tool_input(event)
    target = str(ti.get("file_path") or ti.get("path") or ti.get("notebook_path") or "")
    hit = _names_canonical(target) or (
        os.path.basename(target) if os.path.basename(target) in CANONICAL_FILES else None
    )
    if not hit:
        return Decision("canonical-read", True)
    writing = name in ("Edit", "Write", "MultiEdit", "NotebookEdit")
    return Decision(
        "canonical-read", False,
        reason=(
            f"CANONICAL-STATE-GUARD: {name} on {hit} is refused. "
            + (
                "Hand-editing canonical state is outside the tool contract even "
                "when the intended edit is correct — use the governed verb for "
                "the change you want (add / note / priority / start / resolve / "
                "defer / reopen / discard / supersede / add-rule / update-rule / "
                "meta), which writes atomically with a WAL append and a .bak "
                "rotation."
                if writing else
                "State is loaded by `rag_kernel session-start` (boot) and read by "
                "`rag_kernel items` / `report` / `decisions` (mid-session). A "
                "direct read of the canonical RAG is an E-071-class violation "
                "because it bypasses the render that makes the answer canonical."
            )
        ),
    )


#: UNCOMMITTED-WORK-HAS-NO-GATE-S206. Silent below WARN, warns from WARN, and
#: from LOUD it stops describing the problem and prints the command. The numbers
#: are the operator's: three edits is a working set, eleven is a session's work
#: standing on one power cut.
_UNCOMMITTED_WARN = 3
_UNCOMMITTED_LOUD = 10


def _uncommitted_count(worktree: Path) -> Optional[int]:
    """Number of dirty paths in ``worktree``. None when git cannot answer.

    None is NOT zero and must never be rendered as "clean": an unanswerable
    probe is the SELF-CERTIFYING-EVIDENCE-GATE-S201 shape, where a check that
    cannot measure reports success. The caller stays silent instead.
    """
    try:
        r = subprocess.run(
            ["git", "status", "--porcelain"], cwd=str(worktree),
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return len([ln for ln in r.stdout.splitlines() if ln.strip()])


def _uncommitted_context(worktree: Path, rag_dir: Optional[Path]) -> str:
    """The at-the-edit nag, or '' while the working set is still small."""
    n = _uncommitted_count(worktree)
    if n is None or n < _UNCOMMITTED_WARN:
        return ""
    msg_path = (rag_dir / ".boot" / "commit_msg.txt") if rag_dir else Path(".boot/commit_msg.txt")
    head = (
        f"UNCOMMITTED-WORK: {n} uncommitted change(s) in the kernel worktree. "
        "Work that is not committed does not survive a client restart, and this "
        "session's own predecessor lost a transfer to exactly that "
        "(E-109/E-123)."
    )
    if n <= _UNCOMMITTED_LOUD:
        return head + " Commit at a natural boundary rather than at the seal."
    return (
        head + " THIS IS PAST THE POINT WHERE IT IS A PREFERENCE. Write the "
        f"message to a file, then run, in ONE tmux command:\n"
        f'  cd "{worktree}" && git add -A && git commit -F "{msg_path}"\n'
        "The message goes through a FILE, never a heredoc: tmux-mcp appends its "
        "completion echo to the delimiter line and the shell hangs "
        "(TMUX-HEREDOC-HANGS-THE-SHELL-S206)."
    )


def _gate_deploy_parity(event: dict, *, project_root: Optional[Path] = None,
                        **_: Any) -> Decision:
    """Report deployed-vs-committed kernel drift at the edit, not at the seal.

    Non-blocking. Editing kernel source is the job; discovering six hours later
    that the running kernel was never the edited one is the defect.
    """
    name = _tool_name(event)
    if name not in ("Edit", "Write", "MultiEdit"):
        return Decision("deploy-parity", True)
    target = str(_tool_input(event).get("file_path") or "")
    if not _KERNEL_SOURCE.search(target):
        return Decision("deploy-parity", True)
    twin = _deployed_twin(Path(target), project_root)

    # UNCOMMITTED-WORK-HAS-NO-GATE-S206. Counted on EVERY kernel-source edit,
    # not only when parity is broken: an agent that re-deploys diligently and
    # never commits is the exact case that lost a transfer, and it would sail
    # past a check folded into the parity-failure branch.
    wt = Path(target).resolve()
    for parent in wt.parents:
        if (parent / ".git").exists():
            wt = parent
            break
    else:
        wt = None
    rag_dir = None
    if twin is not None and "RAG" in twin.parts:
        rag_dir = Path(*twin.parts[: twin.parts.index("RAG") + 1])
    nag = _uncommitted_context(wt, rag_dir) if wt is not None else ""

    if twin is None:
        return Decision("deploy-parity", True, context=nag)
    try:
        same = twin.read_bytes() == Path(target).read_bytes()
    except OSError:
        return Decision("deploy-parity", True, context=nag)
    if same:
        return Decision("deploy-parity", True, context=nag)
    parity = (
        f"DEPLOY-PARITY: {os.path.basename(target)} now differs from the "
        f"deployed copy at {twin}. The kernel you are RUNNING is not the "
        f"kernel you just edited — re-deploy before you measure anything "
        f"against it, or the measurement describes the old build."
    )
    return Decision("deploy-parity", True,
                    context=parity + (("\n" + nag) if nag else ""))


def _deployed_twin(edited: Path, project_root: Optional[Path]) -> Optional[Path]:
    """The other copy of an edited kernel file: worktree <-> deployment.

    Resolution is by declared root, never by guessing: the project root is the
    directory holding both ``RAG`` and ``GIT WORKTREES``. WORKTREE-PATH-UNRECORDED
    is the tracked item that will replace this walk with a recorded fact.
    """
    root = Path(project_root) if project_root else None
    if root is None:
        env = os.environ.get("RAG_KERNEL_PROJECT_ROOT")
        if env:
            root = Path(env)
    if root is None:
        for parent in edited.resolve().parents:
            if (parent / "RAG").is_dir() and (parent / "GIT WORKTREES").is_dir():
                root = parent
                break
    if root is None:
        return None
    try:
        rel = edited.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    parts = rel.parts
    if not parts:
        return None
    if parts[0] == "RAG":
        for wt in sorted((root / "GIT WORKTREES").glob("*")):
            cand = wt.joinpath(*parts[1:])
            if cand.exists():
                return cand
        return None
    if parts[0] == "GIT WORKTREES" and len(parts) > 2:
        cand = root.joinpath("RAG", *parts[2:])
        return cand if cand.exists() else None
    return None


def _load_transport_allowlist(project_root: Optional[Path]) -> tuple[tuple[str, ...], str]:
    """Return (patterns, source). RAG projection if readable, else the fallback.

    Fail-soft to the built-in tuple on ANY problem: a malformed projection must
    not brick every tool call in the session. The cost of that choice is that a
    corrupted projection silently reverts policy, so the source is reported in
    the refusal text and asserted by ``selftest``.
    """
    if project_root is None:
        return DEFAULT_TRANSPORT_ALLOWLIST, "builtin(no-root)"
    path = Path(project_root) / TRANSPORT_ALLOWLIST_PROJECTION
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        patterns = tuple(str(p) for p in data["allowlist"])
        if not patterns:
            return DEFAULT_TRANSPORT_ALLOWLIST, "builtin(empty-projection)"
        for p in patterns:
            re.compile(p)
        return patterns, f"rag-projection({path.name})"
    except Exception:
        return DEFAULT_TRANSPORT_ALLOWLIST, "builtin(projection-unreadable)"


def _gate_transport(event: dict, *, project_root: Optional[Path] = None,
                    **_: Any) -> Decision:
    """Refuse any tool not on the declared transport allowlist (E-133).

    Runs under a catch-all matcher, so it sees every call. This is the only
    gate in the layer whose default answer is NO, and that is deliberate: the
    other four can each be walked around by reaching for a transport nobody
    thought to enumerate, which is exactly what happened at S197.
    """
    name = _tool_name(event)
    if not name:
        # No tool name means a payload shape this gate does not understand.
        # Refusing here would brick the session on a vendor format change.
        return Decision("transport", True)

    patterns, source = _load_transport_allowlist(project_root)
    for pat in patterns:
        try:
            if re.search(pat, name):
                return Decision("transport", True)
        except re.error:
            continue

    return Decision(
        "transport", False,
        reason=(
            f"TRANSPORT-ALLOWLIST (E-133): '{name}' is not a declared transport "
            f"for this deployment [source: {source}]. Refused by default, not "
            f"because this tool is known to be harmful but because it is not "
            f"known at all — the S197 breach went through a file-reader MCP that "
            f"no matcher named, so no gate was ever consulted.\n"
            f"  Shell, git, tests, kernel verbs -> mcp__tmux-mcp__ (PRIMARY).\n"
            f"  Blocking wait on a detached job -> rag_wait, not a second poll.\n"
            f"  Canonical state -> `rag_kernel` verbs over tmux, never a file tool.\n"
            f"If this transport genuinely belongs here, DECLARE it: add the "
            f"pattern to operating_protocol.transport_allowlist in the RAG "
            f"(`rag_kernel add-rule transport_allowlist --value-file ... "
            f"--allow-overwrite`), bump the declared pattern count in that rule, "
            f"then re-render with `python tools/render_transport_allowlist.py`. "
            f"Editing the projection by hand recreates the second source of "
            f"truth this gate exists to prevent."
        ),
    )


# ---------------------------------------------------------------------------
# universal coverage — the AFTER half, and the turn boundaries
# ---------------------------------------------------------------------------

def _gate_post_transport_audit(event: dict, *, project_root: Optional[Path] = None,
                               **_: Any) -> Decision:
    """PostToolUse: did an UNDECLARED tool run anyway?

    This is the only post-check in the layer, and it is a self-test rather than
    a policy. It asks one question the pre-gate cannot ask about itself: if a
    tool executed that the allowlist does not name, then the PreToolUse gate did
    not see that call path -- a missing matcher, a disabled layer, or a client
    that skipped the hook. That is a hole in the enforcement surface, and it is
    only observable from the far side of the call.

    It never refuses. A PostToolUse deny is theatre: the side effect already
    happened. What it can do is surface the hole in the same turn, instead of
    leaving it for the operator to find three sessions later -- which is exactly
    the shape of E-116 -> E-128, and of E-133.

    Deliberately NOT here: scanning tool output for suspicious substrings. That
    was in the first draft and it was pattern-matching prose, guessing at
    meaning from text. A gate that guesses is a gate that will be wrong loudly
    and then be ignored.
    """
    name = _tool_name(event)
    if not name:
        return Decision("post-transport-audit", True)

    patterns, source = _load_transport_allowlist(project_root)
    if any(_safe_search(p, name) for p in patterns):
        return Decision("post-transport-audit", True)

    return Decision(
        "post-transport-audit", True,
        context=(
            f"HOOK-COVERAGE HOLE (E-133): '{name}' executed but is not declared "
            f"in the transport allowlist [source: {source}]. The PreToolUse "
            f"transport gate did not stop it, which means the layer is not "
            f"covering this call path — the matcher, the wiring or the client "
            f"is the defect, not this tool. Do not proceed as if the call was "
            f"sanctioned; report the hole."
        ),
    )


def _safe_search(pattern: str, text: str) -> bool:
    try:
        return bool(re.search(pattern, text))
    except re.error:
        return False


#: The PRIMARY transport, by tool name. Deliberately NOT _SHELL_TOOLS: that one
#: matches Bash-shaped tools, and this defect is specific to how tmux-mcp wraps
#: what it sends.
_TMUX_TOOLS = re.compile(r"tmux-mcp", re.I)

#: A here-document opener: ``<<WORD``, ``<< "WORD"``, ``<<-WORD``. The negative
#: lookahead spares ``<<<`` (a here-STRING), which is single-line and safe, and
#: the required identifier start spares a numeric left-shift.
_HEREDOC = re.compile(r"<<(?!<)-?\s*[\"']?[A-Za-z_][A-Za-z0-9_]*")


def _gate_tmux_heredoc(event: dict, **_: Any) -> Decision:
    """Refuse a here-document sent through tmux-mcp. It cannot terminate.

    MEASURED S206, twice, on the PRIMARY transport. tmux-mcp sends every command
    wrapped as ``echo TMUX_MCP_START; <command>; echo TMUX_MCP_DONE``. When the
    command ends in a heredoc, the closing delimiter therefore arrives as
    ``DELIM; echo TMUX_MCP_DONE_...`` and never matches, so the here-document
    never closes and the pane sits at a continuation prompt. Sending the bare
    delimiter afterwards does NOT recover it -- that follow-up is wrapped
    identically. The only exit found was a new window.

    WHY A GATE AND NOT A NOTE. The tool's own description warns about this, and
    the agent hit it anyway, twice, in one session. Worse, the failure RECRUITS:
    both times the pull was to retry on the Bash tool, where heredocs do work --
    and there a multi-line payload crosses Windows stdin in cp1252 and silently
    corrupts every non-ASCII byte, which is how an em-dash destroyed a patch
    earlier in the same session. So a stalled heredoc does not just waste a
    round-trip, it pushes the agent onto the transport tool_hierarchy excludes.

    DECIDABLE PREDICATE, no judgement: a tmux-mcp command containing a heredoc
    opener. Here-strings (``<<<``) are single-line and pass untouched.
    """
    if not _TMUX_TOOLS.search(_tool_name(event)):
        return Decision("tmux-heredoc", True)
    command = str(_tool_input(event).get("command") or "")
    if not _HEREDOC.search(command):
        return Decision("tmux-heredoc", True)
    return Decision(
        "tmux-heredoc", False,
        reason=(
            "TMUX-HEREDOC (TMUX-HEREDOC-HANGS-THE-SHELL-S206): this command "
            "carries a here-document, and tmux-mcp appends its own completion "
            "echo to every line it sends -- including your delimiter -- so the "
            "here-document can never terminate and the pane will hang at a "
            "continuation prompt. Re-sending the delimiter does not free it.\n"
            "  Multi-line CONTENT goes through the file tools (Write/Edit), "
            "which tool_hierarchy already names first for file content.\n"
            "  tmux then runs a SINGLE-LINE invocation of that file:\n"
            "    python .boot/<script>.py        (not an inline script)\n"
            "    git commit -F .boot/<msg>.txt   (not a heredoc message)\n"
            "Do NOT retry this on the Bash tool: it is not a declared transport "
            "(TRANSPORT-RULE-HAS-NO-ENFORCER-S206), and a multi-line payload "
            "there is decoded as cp1252 and silently corrupts non-ASCII text."
        ),
    )


#: A detached job is "in flight" while its output file exists and does NOT yet
#: carry its completion sentinel. The convention is the project's own: every
#: detached run appends a distinctive QQ_..._QQ token as its last line.
_SENTINEL = re.compile(r"QQ_[A-Z0-9_]+_QQ")
_INFLIGHT_MAX_AGE_S = 3600

# STOP-GATE-NOISE-AND-UNSENTINELABLE-S208, half one. The sentinel convention is
# satisfiable only by the AGENT, who owns the command line that produces a file.
# Files the KERNEL writes are unreachable by it: `close_commit_S208.txt` is
# authored by session-end inside the close order, so no agent can append to it,
# and the gate pointed at it until the one-hour cutoff on every close.
#
# The obvious repair -- have the kernel append its own sentinel -- is WRONG for
# this file specifically, and the reason is worth keeping: it is a git commit
# MESSAGE, passed to `git commit -F`, so a QQ token would land in the project's
# history forever. Exclusion is the correct half of the fix here.
_KERNEL_AUTHORED = re.compile(r"^(close_commit_S\d+|session_start_S\d+)\.txt$")

# Half two: the gate keyed on the PRESENCE of a flagged set and never asked
# whether it had CHANGED. It fired nine consecutive times on one finished file
# after the agent had already given the full status block. An unchanged repeat is
# not a safety signal, it is noise, and noise is how a gate stops being read.
_STOP_LAST_KEY = "__stop_status_last__"


#: A file that has not grown for this long is no longer plausibly WRITING. It may
#: still be a live job that is merely quiet, so it is still reported -- but it is
#: reported as unverified rather than asserted to be running. See _inflight_jobs.
_QUIET_S = 120


def _inflight_jobs(rag_dir: Path) -> list[str]:
    """Output files under .boot/ that carry no completion sentinel.

    NARROWED (S207). The predicate was "no QQ_..._QQ token" alone, which cannot
    tell a job that is still running from one that finished without the token. It
    fired three times on the S206 review pass and twice on S207, every time on
    output already read and acted upon -- and a gate that cries wolf is one agents
    learn to scroll past, which is indistinguishable from a gate never wired.

    THE FIX IS TO THE CLAIM, NOT THE SENSITIVITY. Loosening the detector was the
    tempting move and it is the wrong one: a long pytest run can sit silent for
    minutes, so "quiet means finished" would make this gate MISS a genuinely live
    job. A false negative on a safety gate is worse than a false alarm. So every
    sentinel-less file is still reported; what changes is that a file quiet for
    longer than _QUIET_S is labelled as such instead of being asserted to be in
    flight, and the label names the remedy: append a QQ_<NAME>_DONE_QQ line.
    """
    out: list[str] = []
    boot = rag_dir / ".boot"
    try:
        entries = sorted(boot.glob("*.txt"))
    except OSError:
        return out
    now = time.time()
    for f in entries:
        try:
            st = f.stat()
            if now - st.st_mtime > _INFLIGHT_MAX_AGE_S:
                continue
            tail = f.read_text(encoding="utf-8", errors="replace")[-4000:]
        except OSError:
            continue
        if _SENTINEL.search(tail):
            continue
        if "wait-for" in f.name or st.st_size == 0:
            continue
        if _KERNEL_AUTHORED.match(f.name):
            continue
        quiet = now - st.st_mtime
        if quiet > _QUIET_S:
            out.append(f"{f.name} (no sentinel; quiet {int(quiet // 60)}m — "
                       f"finished without one, or died)")
        else:
            out.append(f.name)
    return out


def _unsealed_session(rag_dir: Path) -> Optional[str]:
    """The session id that is open and NOT sealed, or None.

    MEASURED S206: the agent announced "ready to transfer" without ever calling
    session-end. Nothing objected, and nothing COULD: the claim was prose in a
    chat window while ``transfer_ready`` sat false in the close marker, and no
    gate compared the two. The whole close ritual is mechanised inside the verb,
    but the decision to invoke the verb was left to the agent -- which is the
    Rule 45 failure in its purest form, one layer above the ritual it protects.

    Reads the marker only; never writes, never seals, never guesses.
    """
    try:
        import json as _json
        with open(rag_dir / "RAG_MASTER.json", "r", encoding="utf-8-sig") as fh:
            hot = _json.load(fh)
    except (OSError, ValueError):
        return None
    marker = hot.get("session_close")
    if not isinstance(marker, dict):
        return None
    if marker.get("transfer_ready") is True:
        return None
    sid = marker.get("session")
    return str(sid) if sid else None


def _gate_stop_status(event: dict, *, project_root: Optional[Path] = None,
                      state_dir: Optional[Path] = None, now: float = 0.0,
                      **_: Any) -> Decision:
    """At a turn boundary, refuse a SILENT stop while state is at risk.

    AGENT-STOPS-WITHOUT-A-STATUS-S206. The operator's standing requirement is
    that any halt tells them, without being asked: what is banked, what is in
    flight and where its output is, what is NOT committed and therefore at risk,
    the next action, and whether they are needed. Objections come AFTER that
    block, never instead of it.

    Measured S206: the agent stopped repeatedly with a job still running and no
    way for the operator to know whether it was working or wedged. A report was
    eventually produced only after the operator asked -- three times.

    WHAT THIS CAN AND CANNOT DO, stated rather than implied. A hook cannot read
    the assistant's prose, so it cannot verify that a status block was WRITTEN.
    It can verify the two facts that make a silent stop dangerous, and both are
    decidable: uncommitted changes in the kernel worktree, and detached jobs
    whose sentinel has not landed. When either holds it injects the checklist at
    the exact moment of stopping. That is a DETECTOR-WITH-DELIVERY, not a proof
    of compliance, and it is recorded as such under GATE-OR-HOPE-PRINCIPLE.
    """
    root = Path(project_root) if project_root else _project_root_from_env()
    if root is None:
        return Decision("stop-status", True)
    rag_dir = root / "RAG"
    repo = None
    wt = root / "GIT WORKTREES"
    try:
        for cand in sorted(wt.glob("*")):
            if (cand / ".git").exists():
                repo = cand
                break
    except OSError:
        repo = None

    n = _uncommitted_count(repo) if repo is not None else None
    jobs = _inflight_jobs(rag_dir)
    unsealed = _unsealed_session(rag_dir)
    if not jobs and not n and not unsealed:
        return Decision("stop-status", True)

    bits = []
    if unsealed:
        bits.append(
            f"session {unsealed} is OPEN and NOT SEALED (transfer_ready=false) — "
            "you may not declare readiness to transfer; the close is a VERB, "
            "`rag_kernel session-end`, not a statement in chat"
        )
    if n:
        bits.append(f"{n} uncommitted change(s) in the kernel worktree — AT RISK")
    if jobs:
        # Wording matters here (S207): the old line asserted "still in flight" for
        # every sentinel-less file, including ones already finished and read. An
        # assertion the agent can see is false teaches it to discount the whole
        # gate. This states what was actually observed and lets the entries above
        # carry the quiet-time qualifier.
        bits.append("job output with no completion sentinel: " + ", ".join(jobs[:5]))

    # UNCHANGED-REPEAT SUPPRESSION (STOP-GATE-NOISE-AND-UNSENTINELABLE-S208).
    # Keyed on the FLAGGED SET, not on time: if nothing has changed since the
    # last firing, the agent has already been told and told the operator. The
    # moment anything moves -- a new job file, a different commit count, a seal
    # appearing or vanishing -- the block returns in full. Silence here is not a
    # weaker gate; it is the same gate declining to say a thing twice.
    fingerprint = "|".join(sorted(bits))
    path = _state_path(state_dir)
    state = _prune(_load_state(path), now)
    prior = state.get(_STOP_LAST_KEY)
    repeated = isinstance(prior, dict) and prior.get("fingerprint") == fingerprint
    state[_STOP_LAST_KEY] = {"last": now, "fingerprint": fingerprint,
                             "count": int((prior or {}).get("count", 0)) + 1}
    _save_state(path, state)
    if repeated:
        return Decision("stop-status", True)

    return Decision(
        "stop-status", True,
        context=(
            "STOP-STATUS (AGENT-STOPS-WITHOUT-A-STATUS-S206): "
            + "; ".join(bits) + ".\n"
            "Do not stop silently. State, in this order, before anything else:\n"
            "  1. what is BANKED (commits, by sha)\n"
            "  2. what is IN FLIGHT and WHERE its output is\n"
            "  3. what is NOT committed and therefore at risk\n"
            "  4. the NEXT ACTION\n"
            "  5. whether the OPERATOR is needed\n"
            "Objections and caveats come AFTER that block, never instead of it."
        ),
    )


def _project_root_from_env() -> Optional[Path]:
    env = os.environ.get("RAG_KERNEL_PROJECT_ROOT")
    if env and Path(env).is_dir():
        return Path(env)
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "RAG").is_dir() and (parent / "GIT WORKTREES").is_dir():
            return parent
    return None


_GATE_FUNCS = {
    "poll": _gate_poll,
    "sandbox-state": _gate_sandbox_state,
    "unbounded-wait": _gate_unbounded_wait,
    "tmux-heredoc": _gate_tmux_heredoc,
    "stop-status": _gate_stop_status,
    "canonical-read": _gate_canonical_read,
    "deploy-parity": _gate_deploy_parity,
    "transport": _gate_transport,
    "post-transport-audit": _gate_post_transport_audit,
    "wait-duration": _gate_wait_duration,
}


# ---------------------------------------------------------------------------
# public entry points
# ---------------------------------------------------------------------------

def decide(gate: str, event: dict, *, state_dir: Optional[Path] = None,
           project_root: Optional[Path] = None,
           now: Optional[float] = None) -> Decision:
    """Pure policy: given a gate and a hook payload, return the verdict.

    Every branch is a total function over the payload — no clock beyond ``now``,
    no filesystem beyond the poll window and the parity twin — so the policy is
    testable without an agent, which is what makes "are the gates gating" a
    question with an answer.
    """
    if gate not in _GATE_FUNCS:
        raise ValueError(f"unknown gate {gate!r}; known: {', '.join(GATES)}")
    if os.environ.get(_ALLOW_ENV):
        return Decision(gate, True, reason=f"{_ALLOW_ENV} set — layer disabled")
    return _GATE_FUNCS[gate](
        event, state_dir=state_dir, project_root=project_root,
        now=time.time() if now is None else now,
    )


def run_gate(gate: str, raw: str, *, state_dir: Optional[Path] = None,
             project_root: Optional[Path] = None,
             out=None, err=None, now: Optional[float] = None,
             source: Optional[str] = None) -> int:
    """Read one hook payload, emit the hook-contract response, return exit code.

    FAIL-OPEN, declared. If the payload cannot be parsed or a gate raises, this
    allows the call and says so on stderr. A reference monitor that bricks the
    session it protects gets switched off by the first person it inconveniences,
    and a switched-off gate enforces nothing at all. ``selftest`` is how that
    trade stops being invisible.
    """
    out = out or sys.stdout
    err = err or sys.stderr
    # HOOKS-LIVENESS-PROOF: stamped FIRST, before any parse can fail. The
    # question this answers is not "did the gate allow" but "did the layer run
    # at all", and a heartbeat written only on the success path cannot tell a
    # dead layer from a malformed payload.
    record_heartbeat(gate, state_dir=state_dir, now=now, source=source)
    try:
        event = json.loads(raw) if raw.strip() else {}
        if not isinstance(event, dict):
            raise ValueError("hook payload is not an object")
        decision = decide(gate, event, state_dir=state_dir,
                          project_root=project_root, now=now)
    except Exception as exc:  # fail-open, loudly
        print(f"[hook_guard:{gate}] FAILED OPEN — {type(exc).__name__}: {exc}",
              file=err)
        return 0
    event_name = _EVENT_FOR_GATE.get(gate, "PreToolUse")
    if not decision.allow or decision.context:
        json.dump(decision.as_hook_json(event_name), out)
        out.write("\n")
    return 0


def selftest(*, state_dir: Optional[Path] = None) -> tuple[int, list[str]]:
    """Drive every gate through a known-bad payload and assert the refusal.

    This is the measurement that converts "the hooks are installed" from a claim
    into a fact. Returns ``(failures, lines)``.
    """
    lines: list[str] = []
    failures = 0
    now = 1_000_000.0

    checks = [
        ("poll", {"tool_name": "mcp__tmux-mcp__get-command-result",
                  "tool_input": {"commandId": "selftest-id"}}, False),
        ("tmux-heredoc", {"tool_name": "mcp__tmux-mcp__execute-command",
                          "tool_input": {"command": "cat > f <<EOF\nx\nEOF"}},
         False),
        ("tmux-heredoc", {"tool_name": "mcp__tmux-mcp__execute-command",
                          "tool_input": {"command": "python .boot/p.py"}}, True),
        # Stop cannot refuse, so the selftest asserts the only thing that IS
        # assertable here: the gate answers, and answers allow.
        ("stop-status", {}, True),
        ("unbounded-wait", {"tool_name": "Bash", "tool_input": {"command":
          'while pgrep -f "rag_kernel session-start"; do sleep 3; done'}}, False),
        ("sandbox-state", {"tool_name": "Bash",
                           "tool_input": {"command": "cat RAG_MASTER.json"}}, False),
        ("canonical-read", {"tool_name": "Read",
                            "tool_input": {"file_path": "/x/RAG/RAG_MASTER.json"}}, False),
        ("canonical-read", {"tool_name": "Edit",
                            "tool_input": {"file_path": "/x/RAG/RAG_MASTER.json"}}, False),
        ("unbounded-wait", {"tool_name": "Bash", "tool_input": {"command":
          'while pgrep -f "rag_kernel session-start"; do sleep 3; done'}}, False),
        ("sandbox-state", {"tool_name": "Bash",
                           "tool_input": {"command": "ls /tmp"}}, True),
        ("canonical-read", {"tool_name": "Read",
                            "tool_input": {"file_path": "/x/README.md"}}, True),
        # E-133: the exact tool that walked through the layer at S197. If this
        # probe ever passes as `allow`, the allowlist has been widened and the
        # breach is reachable again.
        ("transport", {"tool_name": "mcp__Desktop_Commander__read_file",
                       "tool_input": {"path": "C:/x"}}, False),
        ("transport", {"tool_name": "mcp__tmux-mcp__execute-command",
                       "tool_input": {"command": "ls"}}, True),
        # The PostToolUse gates cannot refuse, so their probes assert they RUN
        # and stay allow-shaped. deploy-parity had NO probe from S195 until
        # S197 — an unprobed gate is indistinguishable from one that stopped
        # running, which is the precise thing selftest exists to rule out.
        ("post-transport-audit", {"tool_name": "mcp__tmux-mcp__execute-command",
                                  "tool_response": "ok"}, True),
        ("deploy-parity", {"tool_name": "Edit",
                           "tool_input": {"file_path": "/x/rag_kernel/api.py"}}, True),
        # POLL-GATE-BLIND-TO-WAIT-FANOUT-S208. Every primed wait below names a
        # DIFFERENT file, which is exactly the traffic that walked through the
        # S198 guard untouched. If this probe ever reads `allow`, the fan-out
        # hole is open again.
        ("poll", {"tool_name": "mcp__rag-kernel__rag_wait",
                  "tool_input": {"path": "/x/fanout-next.txt"}}, False),
        # A wait that actually blocked is recorded as nothing and refuses nothing.
        ("wait-duration", {"tool_name": "mcp__rag-kernel__rag_wait",
                           "tool_input": {"path": "/x/slow.txt"},
                           "tool_response":
                               "wait-for: FOUND after 41.7s (167 polls)"}, True),
    ]
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        sd = Path(state_dir) if state_dir else Path(td)
        # prime the poll window so the poll check exercises the REFUSAL path
        decide("poll", checks[0][1], state_dir=sd, now=now)
        # prime the fan-out window: WAIT_FANOUT_LIMIT instant returns, each on a
        # different file, recorded through the PostToolUse gate exactly as a live
        # session would record them.
        for i in range(WAIT_FANOUT_LIMIT):
            decide("wait-duration",
                   {"tool_name": "mcp__rag-kernel__rag_wait",
                    "tool_input": {"path": f"/x/fanout-{i}.txt"},
                    "tool_response": "wait-for: FOUND after 0.0s (1 polls)"},
                   state_dir=sd, now=now)
        for gate, event, want_allow in checks:
            got = decide(gate, event, state_dir=sd, now=now + 1)
            ok = got.allow is want_allow
            failures += 0 if ok else 1
            lines.append(
                f"  [{'PASS' if ok else 'FAIL'}] {gate}: "
                f"{event.get('tool_name') or '<turn-boundary>'} -> "
                f"{'allow' if got.allow else 'DENY'} "
                f"(expected {'allow' if want_allow else 'DENY'})"
            )
    lines.insert(0, f"hook_guard selftest — v{HOOK_GUARD_VERSION}, "
                    f"{len(checks)} probe(s), {failures} failure(s)")
    return failures, lines
