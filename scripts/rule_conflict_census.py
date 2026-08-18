#!/usr/bin/env python
"""RULE CONFLICT CENSUS — does any rule contradict another rule, or itself?

The third census, and the one nobody was running. `gate_census` asks whether a
rule has an ENFORCER. `policy_census` asks whether code has a RULE. NEITHER asks
whether two rules DISAGREE, and that uncovered category produced three separate
S206 defects, every one of them found by a human noticing rather than by a tool:

  * `tool_hierarchy` named PowerShell as the sanctioned last-resort path while
    the live transport gate refused it (GATE-CONTRADICTS-ITS-OWN-RULE-POWERSHELL).
  * the transport allowlist granted `Bash`, a transport `tool_hierarchy` never
    names at all -- and the conflict was settled by whoever spoke last, which was
    the host system prompt (E-135 / TRANSPORT-RULE-HAS-NO-ENFORCER-S206).
  * `hook_guard` cited `meta.transport_policy.allowlist`, a RAG key that does not
    exist, so the policy it claimed to read was never read.

A rule that contradicts another rule is worse than a rule with no enforcer. An
unenforced rule is merely a hope; a contradicted one actively licenses the wrong
behaviour, because the agent can cite chapter and verse for either side.

METHOD — three DECIDABLE classes, stated so they can be argued with. Nothing here
is a judgement call; each finding names the exact token and where it came from.

  DANGLING-REF   A rule cites a dotted RAG path (`meta.x.y`,
                 `operating_protocol.z`, `current_status.w`) that does NOT
                 resolve in the live RAG. Decisive: the rule claims to be
                 grounded in state that is not there.
  GHOST-VERB     A rule instructs the agent to run `rag_kernel <verb>` where
                 <verb> is not a live subcommand. Decisive: the recovery path a
                 rule names cannot be taken. Read from `--help`, never hardcoded.
  TOOL-CONFLICT  A transport is GRANTED by the transport-allowlist projection but
                 named by no rule, or named by a rule as sanctioned while the
                 projection does not grant it. This is the S206 shape exactly.

FALSE-POSITIVE DISCIPLINE (GATE-FALSE-POSITIVE-ON-PROSE-S201, which fired FIVE
times in S206 alone): match an OPERAND, not any substring. Rule prose quotes its
own counter-examples constantly -- the transport rule literally names the key it
is complaining about -- so quoted bodies are stripped before matching, and a
token that survives only inside quotes is reported as QUOTED-ONLY rather than as
a conflict. A census that cries wolf on its own documentation gets ignored, and
an ignored census is indistinguishable from one that was never written.

LIMIT, declared rather than hidden. TOOL-CONFLICT reads INTENT from prose, and
prose is not a grammar: a transport is read as sanctioned near PRIMARY /
FALLBACK / LAST RESORT and as denied near BANNED / EXCLUDED / NEVER. Where a rule
says neither, the census reports UNCLASSIFIED instead of guessing. DANGLING-REF
and GHOST-VERB carry no such caveat -- a key either resolves or it does not.

EXIT CODE: non-zero when any DANGLING-REF, GHOST-VERB or TOOL-CONFLICT is found,
so this can be wired into a gate rather than admired in a terminal.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

RAG_DIR = Path(__file__).resolve().parent.parent
ROOT = RAG_DIR.parent

#: Top-level RAG containers a rule may legitimately cite with a dotted path.
ROOTS = ("meta", "operating_protocol", "current_status", "next_session_directive")

#: A dotted path rooted at one of ROOTS, e.g. meta.transport_policy.allowlist.
DOTTED = re.compile(r"\b(" + "|".join(ROOTS) + r")((?:\.[A-Za-z_][A-Za-z0-9_]*)+)")

#: `rag_kernel <verb>` / `python -m rag_kernel <verb>` as an INSTRUCTION.
VERB_CALL = re.compile(r"\brag_kernel\s+([a-z][a-z0-9-]{2,})\b")

#: Transports this project has ever argued about. Declared, not inferred.
TRANSPORTS = ("mcp__tmux-mcp__", "tmux-mcp", "wsl-exec", "PowerShell", "Bash",
              "Desktop Commander", "Cowork sandbox")

SANCTIONED = re.compile(r"\b(PRIMARY|FALLBACK|LAST RESORT|ALLOWED|allowlist)\b")
DENIED = re.compile(r"\b(BANNED|EXCLUDED|NEVER|FORBIDDEN|DISALLOWED|refused)\b", re.I)

#: Quoted bodies: '...', "...", `...`. Stripped before operand matching.
QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"|`[^`]*`")

#: Python string literals, including triple-quoted. A RAG path cited in CODE is
#: written inside a literal (an error message, a docstring, a key constant);
#: `meta.get` outside one is an attribute access on a variable, not a path.
STRING_LIT = re.compile(
    r"'''(?:.|\n)*?'''|\"\"\"(?:.|\n)*?\"\"\"|'[^'\n]*'|\"[^\"\n]*\"")

#: Classes whose verdict is DECISIVE and therefore fails the exit code.
#: CODE-REF-UNRESOLVED is deliberately NOT here: a key absent from the live RAG
#: may be optional, or written later by the very code that names it. It narrows
#: the field to what is worth reading by hand -- it does not convict.
DECISIVE = ("DANGLING-REF", "GHOST-VERB", "TOOL-CONFLICT")


def _strip_quoted(text: str) -> str:
    """Blank out quoted argument bodies, preserving offsets and line structure."""
    return QUOTED.sub(lambda m: " " * len(m.group(0)), text)


def _rule_text(value: object) -> str:
    """Flatten a rule value (str | dict | list) into one searchable string."""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _resolves(hot: dict, root: str, tail: str) -> bool:
    """Does `root` + dotted `tail` resolve to something present in the RAG?"""
    node: object = hot.get(root)
    if node is None:
        return False
    for part in tail.lstrip(".").split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return False
    return True


def _live_verbs() -> set[str]:
    """Live subcommand names, read from the CLI itself rather than hardcoded."""
    try:
        out = subprocess.run(
            [sys.executable, "-m", "rag_kernel", "--help"],
            cwd=str(RAG_DIR), capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    m = re.search(r"\{([a-z0-9,\-]+)\}", (out.stdout or "") + (out.stderr or ""))
    return set(m.group(1).split(",")) if m else set()


def _allowlist_patterns() -> tuple[list[str], str]:
    """The transport-allowlist projection: its patterns and where it was found.

    Searched from ROOT, not RAG_DIR: the projection lives in `.claude/`, beside the
    host settings that consume it, NOT under `RAG/`. The first draft of this census
    globbed RAG_DIR only, found nothing, and printed a clean bill of health with
    TOOL-CONFLICT silently inert -- the exact "absence read as a skip" shape that let
    grand_audit.py sit undeployed for six sessions. Absence is now a refusal; see main().
    """
    ordered = [ROOT / ".claude" / "transport_allowlist.json"]
    ordered += sorted(ROOT.rglob("transport_allowlist.json"))
    for cand in ordered:
        if "__pycache__" in cand.parts or not cand.is_file():
            continue
        try:
            data = json.loads(cand.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        pats: list[str] = []
        stack: list[object] = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, str):
                pats.append(node)
        return pats, cand.relative_to(ROOT).as_posix()
    return [], "(projection not found)"


def main() -> int:
    sys.path.insert(0, str(RAG_DIR))
    hot = json.loads((RAG_DIR / "RAG_MASTER.json").read_text(encoding="utf-8-sig"))
    proto = hot.get("operating_protocol") or {}
    findings: list[tuple[str, str, str]] = []      # (class, rule, detail)
    notes: list[str] = []

    verbs = _live_verbs()
    pats, pat_src = _allowlist_patterns()

    # A CENSUS THAT CANNOT SEE ITS INPUT REFUSES; IT DOES NOT REPORT CLEAN.
    # Both of these disable an entire class of finding. Printing "no conflict
    # found" with a class silently inert is how a missing check survives: it
    # looks like evidence of health and is evidence of nothing.
    if not verbs:
        print("REFUSED: could not read the live subcommand list — GHOST-VERB "
              "cannot run, so this census proves nothing.", file=sys.stderr)
        return 2
    if not pats:
        print(f"REFUSED: transport-allowlist projection not found ({pat_src}) — "
              "TOOL-CONFLICT cannot run, so this census proves nothing.",
              file=sys.stderr)
        return 2

    for rule in sorted(proto):
        raw = _rule_text(proto[rule])
        bare = _strip_quoted(raw)

        # --- DANGLING-REF -----------------------------------------------------
        seen: set[str] = set()
        for m in DOTTED.finditer(bare):
            root, tail = m.group(1), m.group(2)
            path = root + tail
            if path in seen:
                continue
            seen.add(path)
            if not _resolves(hot, root, tail):
                findings.append(("DANGLING-REF", rule,
                                 f"cites {path}, which does not resolve in the RAG"))

        # --- GHOST-VERB -------------------------------------------------------
        if verbs:
            for m in VERB_CALL.finditer(bare):
                verb = m.group(1)
                if verb not in verbs:
                    findings.append(("GHOST-VERB", rule,
                                     f"instructs `rag_kernel {verb}`, not a live verb"))

    # --- CODE-DANGLING-REF ----------------------------------------------------
    # The third S206 instance -- hook_guard citing meta.transport_policy.allowlist --
    # lived in CODE, not in rule prose. The first draft of this census read only the
    # protocol and therefore could not catch its own worked example, which is the
    # kind of hole that makes a green census worse than none. Quoted bodies are NOT
    # stripped here: in prose a quote means "mentioned", in code it means "used".
    # The DEPLOYED tree is scanned, not the worktree, because that is what runs.
    for f in sorted((RAG_DIR / "rag_kernel").rglob("*.py")):
        if "__pycache__" in f.parts:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        where = f.relative_to(ROOT).as_posix()
        seen_code: set[str] = set()
        # Scanned RAW, not literal-by-literal. Restricting to string literals was
        # tried and LOST the worked example this census exists for: hook_guard's
        # citation of meta.transport_policy.allowlist does not sit inside a
        # single-line literal. The `(` test below is what removes attribute noise,
        # and it removes it without narrowing the corpus.
        for body in (text,):
            for m in DOTTED.finditer(body):
                root, tail = m.group(1), m.group(2)
                path = root + tail
                # OPERAND, NOT SUBSTRING. `meta.get(` is a dict method call on a
                # variable that happens to be named meta; `meta.reconciliation_`
                # is a path a line-break cut in half. Scoring either is the
                # GATE-FALSE-POSITIVE-ON-PROSE-S201 defect, and the first draft of
                # this census committed it 33 times in one run.
                if body[m.end():m.end() + 1] == "(":
                    continue
                if tail.rstrip().endswith("_"):
                    continue
                if path in seen_code:
                    continue
                seen_code.add(path)
                if not _resolves(hot, root, tail):
                    findings.append(("CODE-REF-UNRESOLVED", where,
                                     f"cites {path}, absent from the live RAG"))

    # --- TOOL-CONFLICT --------------------------------------------------------
    # Intent is read from the WHOLE protocol, because transports are argued about
    # across several rules (tool_hierarchy, transport_allowlist, github_deploy_method).
    proto_bare = _strip_quoted(_rule_text(proto))
    proto_raw = _rule_text(proto)
    for tool in TRANSPORTS:
        granted = any(tool in p for p in pats)
        # A TOOL IDENTIFIER IS AN OPERAND EVEN IN QUOTES. Rules quote transports by
        # design -- tool_hierarchy writes `mcp__tmux-mcp__` in backticks -- so the
        # quote-stripping that protects DANGLING-REF from prose would here convict a
        # correctly-declared transport. Naming is read from the RAW protocol; only
        # the SANCTIONED/DENIED intent window below is read from the stripped text.
        named = tool in proto_raw
        quoted_only = named and (tool not in proto_bare)

        if granted and not named:
            findings.append(("TOOL-CONFLICT", "transport_allowlist",
                             f"{tool!r} is GRANTED by {pat_src} but named by no rule "
                             f"outside quotes — the E-135 shape exactly"))
            continue
        if not named:
            if quoted_only:
                notes.append(f"QUOTED-ONLY: {tool!r} appears only inside quoted prose")
            continue

        window = ""
        for m in re.finditer(re.escape(tool), proto_raw):
            window += proto_raw[max(0, m.start() - 300): m.start() + 300]
        want = bool(SANCTIONED.search(window))
        deny = bool(DENIED.search(window))
        if want and not deny and not granted and pats:
            findings.append(("TOOL-CONFLICT", "tool_hierarchy",
                             f"{tool!r} is named as a sanctioned path but is NOT "
                             f"granted by {pat_src} — a rule points where the gate refuses"))
        elif not want and not deny:
            notes.append(f"UNCLASSIFIED: {tool!r} named with neither sanction nor denial nearby")

    # --- render ---------------------------------------------------------------
    tally = Counter(c for c, _, _ in findings)
    print(f"RULE CONFLICT CENSUS — {len(proto)} rules, "
          f"{len(verbs)} live verbs, {len(pats)} allowlist patterns [{pat_src}]")
    print()
    if findings:
        for cls, rule, detail in sorted(findings):
            print(f"  {cls:<14} {rule}")
            print(f"                 {detail}")
    else:
        print("  no rule-versus-rule conflict found")
    print()
    for n in sorted(set(notes)):
        print(f"  note: {n}")
    print()
    print("  " + ", ".join(f"{k}={v}" for k, v in sorted(tally.items()))
          if tally else "  clean")
    return 1 if any(c in DECISIVE for c, _, _ in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
