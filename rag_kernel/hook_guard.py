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
              "CANONICAL_FILES", "POLL_COOLDOWN_SECONDS", "HOOK_GUARD_VERSION"],
  "use_when": "Wiring or testing the .claude/settings.json enforcement layer, or asking whether a process rule is gated or merely hoped",
  "never_bypass": true
}
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

# Bump when a gate's verdict for a given payload changes — a hook whose policy
# moved without a version is indistinguishable from a hook that stopped running.
HOOK_GUARD_VERSION = "1.1.0"

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
    "transport",                                # boundary-only, default-deny, S197
    "deploy-parity", "post-transport-audit",    # PostToolUse
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
#: This tuple is the FALLBACK only. The authority is the RAG
#: (`meta.transport_policy.allowlist`), projected to
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
    # Bash is allowlisted here and narrowed by the sandbox-state gate, which
    # already refuses it when it names canonical state.
    r"^Bash$",
)

#: Projection path, relative to the project root.
TRANSPORT_ALLOWLIST_PROJECTION = os.path.join(".claude", "transport_allowlist.json")

#: Which Claude Code event each gate answers. Only PreToolUse can refuse; the
#: rest inject context. Keeping the mapping as data rather than a conditional
#: means adding a gate cannot silently mis-declare its own event.
_EVENT_FOR_GATE: dict[str, str] = {
    "poll": "PreToolUse",
    "sandbox-state": "PreToolUse",
    "canonical-read": "PreToolUse",
    "transport": "PreToolUse",
    "deploy-parity": "PostToolUse",
    "post-transport-audit": "PostToolUse",
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

#: Tool names each gate mediates. Matched case-sensitively against ``tool_name``
#: as Claude Code reports it; the settings.json matcher narrows first, this is
#: the belt to that braces (a mis-scoped matcher must not silently disable a
#: gate — it must fail to match and the gate simply allows, never crashes).
_POLL_TOOLS = re.compile(r"get-?command-?result", re.I)
_SHELL_TOOLS = re.compile(r"(^Bash$)|(bash$)", re.I)
_FILE_TOOLS = re.compile(r"^(Read|Edit|Write|NotebookEdit|MultiEdit)$")
_KERNEL_SOURCE = re.compile(r"rag_kernel[/\\][^/\\]+\.py$")

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


def _state_path(state_dir: Optional[Path]) -> Path:
    base = Path(state_dir) if state_dir else Path(
        os.environ.get("RAG_HOOK_STATE_DIR", Path.home() / ".rag_kernel_hooks")
    )
    return base / "poll_state.json"


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


# ---------------------------------------------------------------------------
# gates
# ---------------------------------------------------------------------------

def _gate_poll(event: dict, *, state_dir: Optional[Path] = None,
               now: float = 0.0, **_: Any) -> Decision:
    """Refuse a repeat query against a command id inside the cooldown.

    E-081 -> E-116 -> E-128: the same defect three times, each time by an agent
    that had the rule loaded. The tell is machine-visible — two calls naming one
    command id, seconds apart — so it is checkable, so it is now checked.
    """
    if not _POLL_TOOLS.search(_tool_name(event)):
        return Decision("poll", True)
    ti = _tool_input(event)
    cmd_id = str(ti.get("commandId") or ti.get("command_id") or "").strip()
    if not cmd_id:
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
                    f"`python3 -m rag_kernel wait-for <sentinel file> --timeout N "
                    f"--contains DONE --emit 20`. It blocks server-side and "
                    f"returns the tail in ONE round-trip. If you have no sentinel "
                    f"file, relaunch the job as "
                    f"`... > /tmp/job.txt 2>&1; echo DONE >> /tmp/job.txt`."
                ),
            )
    state[cmd_id] = {"last": now, "count": int((prior or {}).get("count", 0)) + 1}
    _save_state(path, state)
    return Decision("poll", True)


def _gate_sandbox_state(event: dict, **_: Any) -> Decision:
    """Refuse a sandbox shell that names a canonical state file (E-071).

    The governed transports (tmux-mcp, the kernel's own verbs) are not matched by
    this gate, which is the whole point: the rule was never "do not read state",
    it was "read it through the path that takes the lock, appends the WAL and
    rotates the backup".
    """
    if not _SHELL_TOOLS.search(_tool_name(event)):
        return Decision("sandbox-state", True)
    command = str(_tool_input(event).get("command") or "")
    hit = _names_canonical(command)
    if not hit:
        return Decision("sandbox-state", True)
    return Decision(
        "sandbox-state", False,
        reason=(
            f"TOOL-HIERARCHY (E-071): this shell is the Cowork sandbox and the "
            f"command names {hit}, which is canonical state. Refused. Read state "
            f"with `rag_kernel session-start` / `rag_kernel items` and WRITE it "
            f"only through a governed verb (add / note / priority / start / "
            f"resolve / defer / reopen / discard / supersede / checkpoint), "
            f"executed over tmux-mcp. Atomicity, the WAL append, the checksum and "
            f"the .bak rotation are preconditions of the write, not follow-ups."
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
    if twin is None:
        return Decision("deploy-parity", True)
    try:
        same = twin.read_bytes() == Path(target).read_bytes()
    except OSError:
        return Decision("deploy-parity", True)
    if same:
        return Decision("deploy-parity", True)
    return Decision(
        "deploy-parity", True,
        context=(
            f"DEPLOY-PARITY: {os.path.basename(target)} now differs from the "
            f"deployed copy at {twin}. The kernel you are RUNNING is not the "
            f"kernel you just edited — re-deploy before you measure anything "
            f"against it, or the measurement describes the old build."
        ),
    )


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
            f"pattern to meta.transport_policy.allowlist in the RAG and re-render "
            f"the projection. Editing the projection by hand recreates the second "
            f"source of truth this gate exists to prevent."
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


_GATE_FUNCS = {
    "poll": _gate_poll,
    "sandbox-state": _gate_sandbox_state,
    "canonical-read": _gate_canonical_read,
    "deploy-parity": _gate_deploy_parity,
    "transport": _gate_transport,
    "post-transport-audit": _gate_post_transport_audit,
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
             out=None, err=None, now: Optional[float] = None) -> int:
    """Read one hook payload, emit the hook-contract response, return exit code.

    FAIL-OPEN, declared. If the payload cannot be parsed or a gate raises, this
    allows the call and says so on stderr. A reference monitor that bricks the
    session it protects gets switched off by the first person it inconveniences,
    and a switched-off gate enforces nothing at all. ``selftest`` is how that
    trade stops being invisible.
    """
    out = out or sys.stdout
    err = err or sys.stderr
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
        ("sandbox-state", {"tool_name": "Bash",
                           "tool_input": {"command": "cat RAG_MASTER.json"}}, False),
        ("canonical-read", {"tool_name": "Read",
                            "tool_input": {"file_path": "/x/RAG/RAG_MASTER.json"}}, False),
        ("canonical-read", {"tool_name": "Edit",
                            "tool_input": {"file_path": "/x/RAG/RAG_MASTER.json"}}, False),
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
    ]
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        sd = Path(state_dir) if state_dir else Path(td)
        # prime the poll window so the poll check exercises the REFUSAL path
        decide("poll", checks[0][1], state_dir=sd, now=now)
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
