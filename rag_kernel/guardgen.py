"""TLA+ -> Python transition-guard generator for the RAG Runtime Kernel.

Deterministic, zero-LLM, stdlib-only. Parses the formal model
(formal/RAGKernel.tla) and emits rag_kernel/generated_guards.py:

  * GENERATED_TRANSITIONS  -- the legal transition table, transcribed from the
                              model's AllowedTargets CASE expression. This makes
                              the runtime transition table a *derived artifact*
                              of the verified model, not a hand-maintained mirror.
  * KernelContext          -- the model's state variables as a typed dataclass.
  * guard_<action>(...)    -- one enabling-guard function per TLA+ action, whose
                              body is the conjunction of that action's parsed
                              precondition conjuncts.

Why this exists (FV-PHASE3)
---------------------------
Before this generator, state_machine.TRANSITIONS and the .tla AllowedTargets
block were two hand-kept copies of the same data (the .tla literally says
"Direct transcription of TRANSITIONS"). Two copies of a truth drift silently.
Generating the runtime structure FROM the formally-verified model closes that
drift class: TLC proves the model's safety/liveness, and the runtime guards are
mechanically derived from the same source.

Design philosophy
------------------
CS lens: the parser handles a *closed, regular subset* of TLA+ -- not a full
grammar. Any precondition conjunct it does not recognize raises
UnsupportedPredicate (fail-loud), so a new model precondition can never be
silently dropped into a permissive guard. Output is byte-deterministic (no
embedded timestamp) so `--check` reliably detects drift and regeneration
produces no spurious diffs.

ML lens: generation is a build step, not a runtime path -- zero token cost,
consistent with the zero-touch-bootstrap principle ("rules as Python defaults,
no LLM"). The emitted guards are pure functions and trivially context-fit.

Usage
-----
    python -m rag_kernel.guardgen --tla formal/RAGKernel.tla \\
        --out rag_kernel/generated_guards.py
    python -m rag_kernel.guardgen --tla formal/RAGKernel.tla --check
    python -m rag_kernel.guardgen --tla formal/RAGKernel.tla --print

@rag-kernel-manifest
{
  "module": "rag_kernel.guardgen",
  "capability": "guard_generation",
  "description": "Deterministic TLA+ -> Python transition-guard code generator",
  "exports": ["parse_tla", "emit_module", "TlaModel", "Action", "Predicate",
              "UnsupportedPredicate", "GENERATOR_VERSION"],
  "use_when": "Regenerating generated_guards.py after the formal model changes",
  "never_bypass": false
}
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Bump when the emitted-module format changes (forces regen / --check mismatch).
GENERATOR_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class TlaParseError(Exception):
    """Raised when the .tla structure cannot be parsed."""


class UnsupportedPredicate(Exception):
    """Raised when a precondition conjunct is not in the recognized grammar.

    Fail-loud by design: silently dropping a precondition would emit a guard
    that permits an illegal transition. A new model precondition must be added
    to the grammar explicitly.
    """


# ---------------------------------------------------------------------------
# Parsed model representation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Predicate:
    """A single recognized precondition conjunct.

    kind identifies the grammar rule; args carries its parameters.
    text is the original TLA+ conjunct, preserved for the failure reason.
    """

    kind: str
    args: tuple = ()
    text: str = ""


@dataclass
class Action:
    """A TLA+ action (a disjunct of Next) and its parsed preconditions."""

    name: str
    takes_target: bool
    preconditions: list[Predicate] = field(default_factory=list)


@dataclass
class TlaModel:
    """The subset of the model the generator needs to emit guards."""

    states: list[str]
    terminal_states: list[str]
    transitions: dict[str, frozenset[str]]
    actions: list[Action]
    source_sha256: str = ""
    source_name: str = ""


# ---------------------------------------------------------------------------
# Comment stripping
# ---------------------------------------------------------------------------

_BLOCK_COMMENT = re.compile(r"\(\*.*?\*\)", re.DOTALL)
_LINE_COMMENT = re.compile(r"\\\*.*")  # backslash-star to end of line


def _strip_comments(text: str) -> str:
    """Remove TLA+ block comments (* *) and line comments \\* ...

    Block comments in RAGKernel.tla are not nested, so a non-greedy match is
    correct. Line comments start with backslash-star; the regex matches that
    exact token so operators like \\in / \\notin are untouched.
    """
    text = _BLOCK_COMMENT.sub("", text)
    out_lines = []
    for line in text.splitlines():
        out_lines.append(_LINE_COMMENT.sub("", line).rstrip())
    return "\n".join(out_lines)


# ---------------------------------------------------------------------------
# Operator extraction
# ---------------------------------------------------------------------------

# Matches an operator header at column 0:  Name ==   or   Name(args) ==
_OP_HEADER = re.compile(r"^([A-Za-z_]\w*)\s*(\([^)]*\))?\s*==", re.MULTILINE)


def _extract_operators(text: str) -> dict[str, dict]:
    """Split a comment-stripped module into operator bodies.

    Returns {name: {"params": [..], "body": "..."}} keyed by operator name.
    """
    ops: dict[str, dict] = {}
    matches = list(_OP_HEADER.finditer(text))
    for i, m in enumerate(matches):
        name = m.group(1)
        params_raw = m.group(2) or ""
        params = [
            p.strip()
            for p in params_raw.strip("()").split(",")
            if p.strip()
        ]
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        ops[name] = {"params": params, "body": body}
    return ops


def _parse_state_set(literal: str) -> list[str]:
    """Parse a `{A, B, C}` literal into an ordered list of names."""
    inner = literal.strip()
    if not (inner.startswith("{") and inner.endswith("}")):
        raise TlaParseError(f"expected a set literal, got: {literal!r}")
    inner = inner[1:-1].strip()
    if not inner:
        return []
    return [tok.strip() for tok in inner.split(",") if tok.strip()]


# ---------------------------------------------------------------------------
# AllowedTargets CASE parsing -> transition table
# ---------------------------------------------------------------------------

_CASE_ARM = re.compile(
    r"s\s*=\s*(\w+)\s*->\s*(\{[^}]*\})",
    re.DOTALL,
)


def _parse_allowed_targets(body: str) -> dict[str, frozenset[str]]:
    """Parse the AllowedTargets(s) == CASE ... body into a transition table."""
    arms = _CASE_ARM.findall(body)
    if not arms:
        raise TlaParseError("AllowedTargets: no CASE arms found")
    table: dict[str, frozenset[str]] = {}
    for src, targets_literal in arms:
        targets = _parse_state_set(targets_literal)
        table[src] = frozenset(targets)
    return table


# ---------------------------------------------------------------------------
# Precondition conjunct parsing -> Predicate grammar
# ---------------------------------------------------------------------------

def _split_conjuncts(body: str) -> list[str]:
    """Return the leading precondition conjuncts of an action body.

    Conjuncts begin with `/\\` at the start of a (stripped) line. Collection
    stops at the first *effect* conjunct -- one that assigns a primed variable
    (contains `'`) or is an UNCHANGED clause. In this model all preconditions
    are single-line and precede every effect, so single-line scanning is sound.
    """
    preconds: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line.startswith("/\\"):
            continue
        conj = line[2:].strip()
        if not conj:
            continue
        if "'" in conj or conj.startswith("UNCHANGED"):
            break  # first effect reached -> preconditions are complete
        preconds.append(conj)
    return preconds


# Grammar patterns. Each returns a Predicate or raises UnsupportedPredicate.
_RE_PROPOSAL_EQ = re.compile(r"^proposalStatus\s*=\s*(\w+)$")
_RE_STATE_EQ = re.compile(r"^state\s*=\s*(\w+)$")
_RE_STATE_IN_SET = re.compile(r"^state\s*\\in\s*(\{[^}]*\})$")
_RE_STATE_IN_NAMED = re.compile(r"^state\s*\\in\s*([A-Z]\w+)$")
_RE_STATE_NOTIN_NAMED = re.compile(r"^state\s*\\notin\s*([A-Z]\w+)$")
_RE_LEGAL = re.compile(r"^IsLegalTransition\(\s*state\s*,\s*(\w+)\s*\)$")
_RE_WALSEQ_LT = re.compile(r"^WALSeq\s*<\s*MaxWALSeq$")
_RE_WALLEN_GE = re.compile(r"^Len\(wal\)\s*>=\s*(\d+)$")
_RE_TARGET_IN_ALLOWED = re.compile(
    r"^(\w+)\s*\\in\s*AllowedTargets\(\s*(\w+)\s*\)$"
)


def _parse_predicate(conj: str) -> Predicate:
    """Classify one precondition conjunct against the closed grammar."""
    c = conj.strip()

    if c == "~crashed":
        return Predicate("NOT_CRASHED", text=conj)
    if c == "crashed":
        return Predicate("CRASHED", text=conj)

    m = _RE_PROPOSAL_EQ.match(c)
    if m:
        return Predicate("PROPOSAL_STATUS_EQ", (m.group(1),), conj)

    m = _RE_STATE_EQ.match(c)
    if m:
        return Predicate("STATE_EQ", (m.group(1),), conj)

    m = _RE_STATE_IN_SET.match(c)
    if m:
        members = tuple(_parse_state_set(m.group(1)))
        return Predicate("STATE_IN_SET", members, conj)

    m = _RE_STATE_NOTIN_NAMED.match(c)
    if m:
        return Predicate("STATE_NOTIN_NAMED", (m.group(1),), conj)

    m = _RE_STATE_IN_NAMED.match(c)
    if m:
        return Predicate("STATE_IN_NAMED", (m.group(1),), conj)

    m = _RE_LEGAL.match(c)
    if m:
        return Predicate("LEGAL_TRANSITION", (m.group(1),), conj)

    if _RE_WALSEQ_LT.match(c):
        return Predicate("WALSEQ_LT_MAX", text=conj)

    m = _RE_WALLEN_GE.match(c)
    if m:
        return Predicate("WALLEN_GE", (int(m.group(1)),), conj)

    m = _RE_TARGET_IN_ALLOWED.match(c)
    if m:
        return Predicate("TARGET_IN_ALLOWED", (m.group(1), m.group(2)), conj)

    raise UnsupportedPredicate(
        f"unrecognized precondition conjunct: {conj!r}\n"
        f"Add a grammar rule to rag_kernel.guardgen._parse_predicate before "
        f"regenerating."
    )


# Predicate references the action's `target` parameter?
_TARGET_REF_KINDS = {"LEGAL_TRANSITION", "TARGET_IN_ALLOWED"}


def _predicate_uses_target(p: Predicate) -> bool:
    if p.kind == "LEGAL_TRANSITION":
        return p.args[0] == "target"
    if p.kind == "TARGET_IN_ALLOWED":
        return p.args[0] == "target"
    return False


# ---------------------------------------------------------------------------
# Action discovery (model-driven, from the Next disjunction)
# ---------------------------------------------------------------------------

_CAP_IDENT = re.compile(r"\b([A-Z][A-Za-z0-9]+)\b")


def _is_action_body(body: str) -> bool:
    """Action bodies assign primed variables or use UNCHANGED."""
    return ("'" in body) or ("UNCHANGED" in body)


def _discover_actions(ops: dict[str, dict]) -> list[str]:
    """Action names are the operators referenced by Next that have action bodies."""
    if "Next" not in ops:
        raise TlaParseError("no Next operator found")
    referenced = set(_CAP_IDENT.findall(ops["Next"]["body"]))
    actions = [
        name
        for name in referenced
        if name in ops and _is_action_body(ops[name]["body"])
    ]
    # Stable, deterministic order: by name.
    return sorted(actions)


# ---------------------------------------------------------------------------
# Top-level parse
# ---------------------------------------------------------------------------

def parse_tla(text: str, *, source_name: str = "", source_sha256: str = "") -> TlaModel:
    """Parse a RAGKernel.tla module into a TlaModel."""
    clean = _strip_comments(text)
    ops = _extract_operators(clean)

    if "States" not in ops:
        raise TlaParseError("no States definition found")
    states = _parse_state_set(ops["States"]["body"])

    terminal_states: list[str] = []
    if "TerminalStates" in ops:
        terminal_states = _parse_state_set(ops["TerminalStates"]["body"])

    if "AllowedTargets" not in ops:
        raise TlaParseError("no AllowedTargets operator found")
    transitions = _parse_allowed_targets(ops["AllowedTargets"]["body"])

    # Validate: every CASE source is a declared state; every target is too.
    state_set = set(states)
    for src, targets in transitions.items():
        if src not in state_set:
            raise TlaParseError(f"AllowedTargets source {src!r} not in States")
        bad = set(targets) - state_set
        if bad:
            raise TlaParseError(f"AllowedTargets[{src}] has unknown targets: {bad}")
    missing = state_set - set(transitions)
    if missing:
        raise TlaParseError(f"AllowedTargets missing entries for: {sorted(missing)}")

    actions: list[Action] = []
    for name in _discover_actions(ops):
        op = ops[name]
        takes_target = "target" in op["params"]
        preconds = [_parse_predicate(c) for c in _split_conjuncts(op["body"])]
        # If any precondition references `target`, the guard must accept it.
        if any(_predicate_uses_target(p) for p in preconds):
            takes_target = True
        actions.append(Action(name=name, takes_target=takes_target,
                              preconditions=preconds))

    return TlaModel(
        states=states,
        terminal_states=terminal_states,
        transitions=transitions,
        actions=actions,
        source_sha256=source_sha256,
        source_name=source_name,
    )


# ---------------------------------------------------------------------------
# Emission: Predicate -> Python expression + failure reason
# ---------------------------------------------------------------------------

def _py_expr(p: Predicate) -> str:
    """Render a predicate as a Python boolean expression over `ctx`/`target`."""
    k = p.kind
    if k == "NOT_CRASHED":
        return "not ctx.crashed"
    if k == "CRASHED":
        return "ctx.crashed"
    if k == "PROPOSAL_STATUS_EQ":
        return f"ctx.proposal_status == {p.args[0]!r}"
    if k == "STATE_EQ":
        return f"ctx.state == {p.args[0]!r}"
    if k == "STATE_IN_SET":
        members = ", ".join(repr(s) for s in p.args)
        return f"ctx.state in ({members},)" if len(p.args) == 1 else \
               f"ctx.state in ({members})"
    if k == "STATE_IN_NAMED":
        return f"ctx.state in {_named_set_const(p.args[0])}"
    if k == "STATE_NOTIN_NAMED":
        return f"ctx.state not in {_named_set_const(p.args[0])}"
    if k == "LEGAL_TRANSITION":
        ref = "target" if p.args[0] == "target" else f"ctx.{_snake(p.args[0])}"
        return f"{ref} in GENERATED_TRANSITIONS.get(ctx.state, frozenset())"
    if k == "WALSEQ_LT_MAX":
        return "ctx.wal_len < ctx.max_wal_seq"
    if k == "WALLEN_GE":
        return f"ctx.wal_len >= {p.args[0]}"
    if k == "TARGET_IN_ALLOWED":
        ref = "target" if p.args[0] == "target" else f"ctx.{_snake(p.args[0])}"
        return f"{ref} in GENERATED_TRANSITIONS.get({p.args[1]!r}, frozenset())"
    raise UnsupportedPredicate(f"no emitter for predicate kind {k!r}")


def _named_set_const(name: str) -> str:
    """Map a TLA+ named set to its generated module constant."""
    mapping = {
        "TerminalStates": "TERMINAL_STATES",
        "CrashEligibleStates": "CRASH_ELIGIBLE_STATES",
        "States": "STATES",
    }
    if name not in mapping:
        raise UnsupportedPredicate(f"unknown named set {name!r}")
    return mapping[name]


def _snake(camel: str) -> str:
    """CamelCase -> snake_case, acronym-aware.

    proposalTarget -> proposal_target
    StageProposal  -> stage_proposal
    WALCompaction  -> wal_compaction   (acronym run kept together)
    """
    # Boundary between an acronym run and a following Word: WALCompaction -> WAL_Compaction
    s = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", camel)
    # Boundary between lowercase/digit and an uppercase: stageProposal -> stage_Proposal
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", s)
    return s.lower()


# ---------------------------------------------------------------------------
# Emission: full module
# ---------------------------------------------------------------------------

def _emit_transitions(transitions: dict[str, frozenset[str]],
                      states: list[str]) -> str:
    lines = ["GENERATED_TRANSITIONS: dict[str, frozenset[str]] = {"]
    for s in states:  # deterministic order = declaration order in States
        targets = sorted(transitions[s])
        if targets:
            members = ", ".join(repr(t) for t in targets)
            lines.append(f"    {s!r}: frozenset({{{members}}}),")
        else:
            lines.append(f"    {s!r}: frozenset(),")
    lines.append("}")
    return "\n".join(lines)


def _emit_guard(action: Action) -> str:
    sig = ("def guard_{name}(ctx: KernelContext, target: str) -> GuardResult:"
           if action.takes_target else
           "def guard_{name}(ctx: KernelContext) -> GuardResult:")
    fn = _snake(action.name)
    lines = [sig.format(name=fn)]
    lines.append(f'    """Enabling guard for TLA+ action {action.name}.')
    lines.append("")
    if action.preconditions:
        lines.append("    Preconditions (from the model):")
        for p in action.preconditions:
            # Escape backslashes so TLA+ operators (\in, \notin) embedded in
            # the docstring don't trigger SyntaxWarning: invalid escape sequence.
            safe_text = p.text.replace("\\", "\\\\")
            lines.append(f"      - {safe_text}")
    else:
        lines.append("    No preconditions in the model (always enabled).")
    lines.append('    """')
    if not action.preconditions:
        lines.append("    return (True, '')")
        return "\n".join(lines)
    for p in action.preconditions:
        expr = _py_expr(p)
        reason = f"{action.name} precondition failed: {p.text}"
        lines.append(f"    if not ({expr}):")
        lines.append(f"        return (False, {reason!r})")
    lines.append("    return (True, '')")
    return "\n".join(lines)


_HEADER_TEMPLATE = '''"""GENERATED FILE -- DO NOT EDIT BY HAND.

Auto-generated by rag_kernel.guardgen (generator v{gen_version}) from the
formally-verified TLA+ model. Regenerate with:

    python -m rag_kernel.guardgen --tla {source_name} --out <this file>

Provenance
----------
  source         : {source_name}
  source sha256  : {source_sha}
  generator      : rag_kernel.guardgen v{gen_version}

This module is the runtime projection of the model's transition table and
action enabling-conditions. Editing it by hand defeats the formal-verification
guarantee: the model (checked by TLC) is the single source of truth. To change
a guard, change formal/RAGKernel.tla, re-run TLC, then regenerate.

@rag-kernel-manifest
{{
  "module": "rag_kernel.generated_guards",
  "capability": "generated_guards",
  "description": "TLA+-derived transition table and action enabling-guards",
  "exports": ["GENERATED_TRANSITIONS", "KernelContext", "GuardResult",
              "ACTION_GUARDS", "legal_transition", "SOURCE_SHA256",
              "GUARDS_SELF_SHA256", "verify_self"],
  "use_when": "Enforcing structurally-verified state transitions (FV-PHASE4)",
  "never_bypass": true,
  "generated": true
}}
"""

from __future__ import annotations

from dataclasses import dataclass

# SHA-256 of the source .tla this module was generated from. Compared at
# import/verify time to detect model/code drift (needs the .tla to recompute).
SOURCE_SHA256 = "{source_sha}"
# SHA-256 of THIS module's own guard tables (STATES / TERMINAL_STATES /
# GENERATED_TRANSITIONS / ACTION_GUARDS), baked at generation time. Lets a
# DEPLOYED package that ships no formal/RAGKernel.tla self-verify its guard
# integrity from baked provenance -- see verify_self() at the foot of this file.
GUARDS_SELF_SHA256 = "{guards_self_sha}"
GENERATOR_VERSION = "{gen_version}"

GuardResult = tuple[bool, str]
'''


# Runtime self-verification, emitted verbatim at the foot of generated_guards.py.
# It reconstructs the SAME canonical payload string that ``canonical_guard_payload``
# builds from the model at generation time (see the byte-for-byte-matching logic
# there), hashes it, and compares to the baked GUARDS_SELF_SHA256. A test pins
# verify_self() == True on the committed file, so any divergence fails loud.
_SELF_VERIFY_TEMPLATE = '''

# ---------------------------------------------------------------------------
# Baked self-verification (deployed packages ship no .tla to recompute against)
# ---------------------------------------------------------------------------

def _guards_payload() -> str:
    """Canonical, order-stable serialization of this module's guard tables.

    Must match ``guardgen.canonical_guard_payload`` byte-for-byte so the baked
    GUARDS_SELF_SHA256 verifies at runtime without the formal source.
    """
    _lines = []
    _lines.append("STATES=" + ",".join(sorted(STATES)))
    _lines.append("TERMINAL=" + ",".join(sorted(TERMINAL_STATES)))
    for _k in sorted(GENERATED_TRANSITIONS):
        _lines.append("T:" + _k + "=" + ",".join(sorted(GENERATED_TRANSITIONS[_k])))
    for _name, _meta in sorted(ACTION_GUARDS.items()):
        _lines.append("A:" + _name + "=" + ("1" if _meta[1] else "0"))
    return "\\n".join(_lines)


def verify_self() -> bool:
    """True iff the in-memory guard tables match the baked GUARDS_SELF_SHA256.

    Lets a DEPLOYED package prove its own guard integrity from baked provenance:
    a post-generation hand-edit to STATES / TERMINAL_STATES / GENERATED_TRANSITIONS
    / ACTION_GUARDS changes the payload and fails this check. Returns False (never
    raises) on any mismatch or missing bake.
    """
    import hashlib as _hashlib
    if not GUARDS_SELF_SHA256:
        return False
    _got = _hashlib.sha256(_guards_payload().encode("utf-8")).hexdigest()
    return _got == GUARDS_SELF_SHA256
'''


def canonical_guard_payload(
    states,
    terminal_states,
    transitions,
    actions,
) -> str:
    """Canonical serialization of the guard tables, hashed into GUARDS_SELF_SHA256.

    Kept byte-for-byte identical to the emitted ``_guards_payload`` so the deployed
    package's ``verify_self`` reproduces the same hash. ``actions`` is an iterable
    of ``(name, takes_target_bool)`` pairs; ``transitions`` a ``dict[str, iterable]``.
    """
    lines = []
    lines.append("STATES=" + ",".join(sorted(states)))
    lines.append("TERMINAL=" + ",".join(sorted(terminal_states)))
    for k in sorted(transitions):
        lines.append("T:" + k + "=" + ",".join(sorted(transitions[k])))
    for name, tt in sorted(actions):
        lines.append("A:" + name + "=" + ("1" if tt else "0"))
    return "\n".join(lines)


def emit_module(model: TlaModel) -> str:
    """Emit the full generated_guards.py source text (byte-deterministic)."""
    self_payload = canonical_guard_payload(
        model.states,
        model.terminal_states,
        model.transitions,
        [(a.name, bool(a.takes_target)) for a in model.actions],
    )
    guards_self_sha = hashlib.sha256(self_payload.encode("utf-8")).hexdigest()

    parts: list[str] = []
    parts.append(_HEADER_TEMPLATE.format(
        gen_version=GENERATOR_VERSION,
        source_name=model.source_name or "formal/RAGKernel.tla",
        source_sha=model.source_sha256,
        guards_self_sha=guards_self_sha,
    ))

    # State-set constants.
    states_members = ", ".join(repr(s) for s in model.states)
    term_members = ", ".join(repr(s) for s in model.terminal_states)
    parts.append("\n\nSTATES = frozenset({" + states_members + "})")
    parts.append("TERMINAL_STATES = frozenset({" + term_members + "})")
    parts.append("CRASH_ELIGIBLE_STATES = STATES - TERMINAL_STATES")

    # Transition table.
    parts.append("\n\n" + _emit_transitions(model.transitions, model.states))

    # Context dataclass.
    parts.append('''

@dataclass(frozen=True)
class KernelContext:
    """Snapshot of the model's state variables, consumed by the guards.

    Mirrors the VARIABLES block of RAGKernel.tla:
      state, proposalStatus, proposalTarget, wal (via wal_len), crashed,
      plus the MaxWALSeq bound.
    """

    state: str
    proposal_status: str = "NONE"
    proposal_target: str = "BOOTING"
    wal_len: int = 0
    crashed: bool = False
    max_wal_seq: int = 8


def legal_transition(from_state: str, to_state: str) -> bool:
    """True iff from_state -> to_state is in the generated transition table."""
    return to_state in GENERATED_TRANSITIONS.get(from_state, frozenset())''')

    # Guard functions.
    for action in model.actions:
        parts.append("\n\n" + _emit_guard(action))

    # Action registry.
    parts.append("\n\n# Action name -> (guard callable, takes_target).")
    reg_lines = ["ACTION_GUARDS: dict[str, tuple] = {"]
    for action in model.actions:
        fn = _snake(action.name)
        reg_lines.append(
            f"    {action.name!r}: (guard_{fn}, {action.takes_target!r}),"
        )
    reg_lines.append("}")
    parts.append("\n".join(reg_lines))

    # Baked self-verification helpers (verify_self / _guards_payload).
    parts.append(_SELF_VERIFY_TEMPLATE)

    parts.append("")  # trailing newline
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load_model(tla_path: Path) -> TlaModel:
    raw = tla_path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    text = raw.decode("utf-8")
    return parse_tla(text, source_name=tla_path.name, source_sha256=sha)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rag_kernel.guardgen",
        description="Generate Python transition guards from a TLA+ model.",
    )
    parser.add_argument("--tla", required=True, help="path to RAGKernel.tla")
    parser.add_argument("--out", help="output path for generated_guards.py")
    parser.add_argument("--print", action="store_true", dest="print_only",
                       help="print generated source to stdout")
    parser.add_argument("--check", action="store_true",
                       help="exit 1 if --out differs from freshly generated "
                            "output (drift detection)")
    args = parser.parse_args(argv)

    tla_path = Path(args.tla)
    if not tla_path.is_file():
        print(f"error: TLA+ file not found: {tla_path}", file=sys.stderr)
        return 2

    try:
        model = _load_model(tla_path)
        source = emit_module(model)
    except (TlaParseError, UnsupportedPredicate) as exc:
        print(f"generation failed: {exc}", file=sys.stderr)
        return 3

    if args.print_only:
        sys.stdout.write(source)
        return 0

    if args.check:
        if not args.out:
            print("error: --check requires --out", file=sys.stderr)
            return 2
        out_path = Path(args.out)
        if not out_path.is_file():
            print(f"drift: {out_path} does not exist", file=sys.stderr)
            return 1
        current = out_path.read_text(encoding="utf-8")
        if current != source:
            print(f"drift: {out_path} differs from generated output "
                  f"(model changed without regeneration?)", file=sys.stderr)
            return 1
        print(f"ok: {out_path} matches model {tla_path.name} "
              f"(sha {model.source_sha256[:12]})")
        return 0

    if args.out:
        out_path = Path(args.out)
        out_path.write_text(source, encoding="utf-8")
        print(f"wrote {out_path} from {tla_path.name} "
              f"({len(model.actions)} guards, "
              f"{len(model.transitions)} states, "
              f"sha {model.source_sha256[:12]})")
        return 0

    sys.stdout.write(source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
