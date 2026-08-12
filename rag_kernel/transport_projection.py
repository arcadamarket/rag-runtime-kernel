"""Transport-allowlist projection: the RAG rule, rendered for the hook layer.

PROJECTION-DRIFT-UNGATED (S198)
-------------------------------
S197 declared ``operating_protocol.transport_allowlist`` as the authority for
which agent-facing transports may run, and shipped
``tools/render_transport_allowlist.py`` to project it into
``.claude/transport_allowlist.json`` so the PreToolUse hook can read it in
milliseconds with no kernel import and no lock.

Two things were wrong with that arrangement, and this module is the fix for
both:

1. **The projection was detectable-but-ungated.** ``--check`` existed and
   nothing called it, so drift between the rule and its projection was a
   condition someone might notice rather than one the auditor refuses. The
   rule's first draft even asserted "audit fails on drift", which was false
   when written — precisely the defect E-132 names.
2. **The checker did not travel.** The renderer lived only in the deployment's
   ``RAG/tools/`` directory: untracked by git, absent from the worktree,
   outside the test gate, and therefore invisible to every clone. A gate that
   exists in exactly one working copy is not a gate, it is a local habit.

Moving the logic into the package fixes the direction of dependency as well as
the coverage: ``drift_audit`` can now assert the projection from the HOT dict it
already holds — no second RAG read, no lock, no subprocess — and
``tools/render_transport_allowlist.py`` becomes a thin shim over the same code
that the auditor runs. One implementation, two callers, one test suite.

WHY A PROJECTION IS ALLOWED TO EXIST AT ALL, restated so the next reader does
not have to reconstruct it: the hook runs on every tool call, before any kernel
process exists, and cannot afford to parse the RAG or take its lock. That is a
performance boundary, not a governance one — and a performance boundary may
cache an authority so long as the cache is *verifiable*. The projection carries
the sha256 of the source rule text, which is what makes verification decidable,
and this module is what makes it happen every audit instead of never.

Deterministic, stdlib-only, pure over inputs. No writes except the explicit
``render`` call, which is atomic (tmp + parse-back + ``os.replace``).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

RULE_KEY = "transport_allowlist"
PROJECTION_REL = Path(".claude") / "transport_allowlist.json"

#: The declared patterns are parsed out of the rule text between this marker and
#: the next sentence-cap marker. Keeping the machine-readable list INSIDE the
#: human rule means the prose and the enforcement cannot drift apart -- there is
#: only one place to edit, and editing it is a governed write.
_PATTERNS_MARKER = "DECLARED PATTERNS"
_PATTERNS_END = "ADDING A TRANSPORT"

#: ``^`` followed by either a parenthesised alternation (optionally ``$``-anchored)
#: or a bare token. Ordered so the group form wins where both could match.
_PATTERN_RE = re.compile(r"\^(?:\([^)]*\)\$?|[A-Za-z0-9_\-|.\\$]+)")


class ProjectionError(ValueError):
    """The rule text cannot be projected — a refusal, not a drift finding.

    Raised (never ``SystemExit``) so that both callers can decide what to do:
    the CLI shim exits non-zero, the auditor turns it into an ERROR finding.
    A library that calls ``sys.exit`` cannot be audited by anything.
    """


def rule_text_from_hot(hot: dict) -> "str | None":
    """Return the declared rule text from a loaded HOT dict, or ``None``.

    ``None`` means the deployment does not declare the rule, which is a valid
    state (not every clone wires a transport allowlist) and must self-skip
    rather than fail. Non-string rule values are canonicalised the same way the
    renderer canonicalises them, so the sha256 is stable across both callers.
    """
    protocol = (hot or {}).get("operating_protocol") or {}
    rule = protocol.get(RULE_KEY)
    if not rule:
        return None
    return rule if isinstance(rule, str) else json.dumps(rule, sort_keys=True)


def rule_text_from_rag(rag_path: Path | str) -> str:
    """Read the declaration straight off disk (renderer path only).

    NOTE ON TOOL HIERARCHY: this is a RENDERER entry point, not a state
    briefing. It runs as a build step over a file it does not mutate, which is
    why it may open the RAG directly where an agent may not. The auditor does
    NOT use this function — it uses ``rule_text_from_hot`` over the dict the
    kernel already loaded, so the audit adds no second read and no lock.
    """
    data = json.loads(Path(rag_path).read_text(encoding="utf-8-sig"))
    text = rule_text_from_hot(data)
    if text is None:
        raise ProjectionError(
            f"operating_protocol.{RULE_KEY} is not declared in {rag_path}. "
            f"Declare it with `rag_kernel add-rule {RULE_KEY} --value-file ...` "
            f"before rendering a projection of it."
        )
    return text


def extract_patterns(rule_text: str) -> list[str]:
    """Pull the regex patterns out of the declared rule text.

    UNDER-EXTRACTION IS THE DANGEROUS FAILURE, not over-extraction. A parser
    that drops a pattern silently NARROWS an allowlist, which reads as "the
    gate is working" right up until it refuses a tool the operator declared.
    The first version of this function did precisely that: its character class
    excluded parentheses, so every alternation form -- ``^(Read|Edit|...)$``,
    the majority of the list -- vanished and only three of six patterns
    survived. Nothing failed; the policy just quietly shrank.

    Two defences, because the parse cannot be trusted on its own:
      1. the pattern regex accepts alternation groups explicitly;
      2. the rule DECLARES its own count and a mismatch is fatal, so the next
         parser bug is loud instead of silent.
    """
    start = rule_text.find(_PATTERNS_MARKER)
    if start < 0:
        raise ProjectionError(f"rule text has no '{_PATTERNS_MARKER}' section.")
    end = rule_text.find(_PATTERNS_END, start)
    segment = rule_text[start : end if end > 0 else len(rule_text)]

    declared_n = None
    m_count = re.search(r"(\d+)\s+PATTERNS", segment)
    if m_count:
        declared_n = int(m_count.group(1))

    patterns: list[str] = []
    for raw in _PATTERN_RE.findall(segment):
        pat = raw.rstrip(".,;")
        try:
            re.compile(pat)
        except re.error:
            continue  # not a pattern, just prose that happened to start with ^
        if pat not in patterns:
            patterns.append(pat)

    if not patterns:
        raise ProjectionError(
            "extracted zero patterns. An empty allowlist denies every tool "
            "call; refusing to write it."
        )
    if declared_n is not None and len(patterns) != declared_n:
        raise ProjectionError(
            f"the rule declares {declared_n} patterns but the parser extracted "
            f"{len(patterns)}: {patterns}. A silent mismatch here narrows or "
            f"widens the allowlist without anyone deciding to. Fix the rule "
            f"text or the parser; do not render."
        )
    return patterns


def rule_sha256(rule_text: str) -> str:
    """The identity of the rule text the projection was built from."""
    return hashlib.sha256(rule_text.encode("utf-8")).hexdigest()


def build(rule_text: str) -> dict:
    """The projection document for a given rule text. Pure except for the clock."""
    return {
        "$comment": [
            "PROJECTION — DO NOT HAND-EDIT.",
            f"Authority: operating_protocol.{RULE_KEY} in RAG_MASTER.json.",
            "Regenerate: python tools/render_transport_allowlist.py",
            "Verify:     rag_kernel audit  (gated since S198), or --check",
            "Editing this file instead of the rule recreates the second source",
            "of truth the rule exists to prevent (DEC-0009 / S178).",
        ],
        "rule_key": RULE_KEY,
        "rule_sha256": rule_sha256(rule_text),
        "rendered_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "allowlist": extract_patterns(rule_text),
    }


def projection_path(root: Path | str) -> Path:
    return Path(root) / PROJECTION_REL


def drift_reasons(rule_text: "str | None", root: Path | str) -> list[str]:
    """Every way the on-disk projection disagrees with the declared rule.

    Returns ``[]`` when they agree, and ``[]`` when ``rule_text`` is ``None``
    (the deployment declares no allowlist — nothing to project, nothing to
    check). Every other return value is a divergence the caller must surface;
    the auditor renders each as an ERROR.

    The MISSING-projection case is drift, not a skip: a declared rule with no
    rendered projection means the hook layer is reading nothing while the RAG
    says a policy is in force — the exact "declared but not running" shape that
    ACTIVATION-GAP-S197 exists to name.
    """
    if rule_text is None:
        return []

    out_path = projection_path(root)
    if not out_path.exists():
        return [
            f"the rule is declared but no projection exists at {PROJECTION_REL} "
            f"— the hook layer has nothing to enforce; run "
            f"`python tools/render_transport_allowlist.py`"
        ]

    try:
        have = json.loads(out_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"projection at {PROJECTION_REL} is unreadable/unparseable ({exc})"]

    try:
        fresh = build(rule_text)
    except ProjectionError as exc:
        return [f"the declared rule cannot be projected: {exc}"]

    reasons: list[str] = []
    if have.get("rule_sha256") != fresh["rule_sha256"]:
        reasons.append(
            f"projection at {PROJECTION_REL} was rendered from a different rule "
            f"text (sha {str(have.get('rule_sha256'))[:12]} != live "
            f"{fresh['rule_sha256'][:12]})"
        )
    if have.get("allowlist") != fresh["allowlist"]:
        reasons.append(
            f"projection allowlist differs from the declared rule — "
            f"projection={have.get('allowlist')} declared={fresh['allowlist']}"
        )
    return reasons


def render(rule_text: str, root: Path | str) -> Path:
    """Write the projection atomically. Returns the path written."""
    out_path = projection_path(root)
    fresh = build(rule_text)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(fresh, indent=2) + "\n", encoding="utf-8")
    json.loads(tmp.read_text(encoding="utf-8"))  # parse-back gate before swap
    os.replace(tmp, out_path)
    return out_path
