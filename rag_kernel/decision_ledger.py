"""Operator decisions as first-class, governed records (S181, B5).

WHY THIS EXISTS (item DECISION-LEDGER-PRIMITIVE)
-------------------------------------------------
Nothing in the kernel recorded an OPERATOR RULING as a durable object. Rulings
lived in chat, were paraphrased into a session summary if the agent remembered,
and were then re-asked in a later session because no successor could find them.
Triaged UNIVERSAL from the clone's own S14 finding (Rule 15 lane A).

The distinction this module enforces is the one E-092 was logged for:

    A MEASURED FACT is banked without consultation.
    A RULING BETWEEN REAL ALTERNATIVES is the operator's, and once given it is
    STATE -- not a memory, not a line in a summary.

So a decision record carries the alternatives that were actually on the table.
A "decision" with one option is not a decision, it is a control being disguised
as a preference, and this module refuses to record it.

BINDING
-------
A decision may BIND tracked_item ids. Binding is checked: an id that does not
resolve is a fail-loud refusal, so the ledger cannot accumulate rulings about
items that never existed. This is the hook DIRECTIVE-SUPERSEDE-PATH needs -- a
directive re-ruling becomes a decision that supersedes an earlier one, instead of
a gate bypassed by hand.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Optional

#: Top-level RAG key holding the ledger.
LEDGER_KEY = "decision_ledger"
_ID_RE = re.compile(r"^DEC-(\d{4})$")


class DecisionError(Exception):
    """Fail-loud condition raised by the decision ledger."""


@dataclass
class Decision:
    id: str
    session: str
    utc: str
    question: str
    options: list[str]
    chosen: str
    rationale: str = ""
    binds: list[str] = field(default_factory=list)
    supersedes: Optional[str] = None

    def to_record(self) -> dict:
        rec = {
            "id": self.id,
            "session": self.session,
            "utc": self.utc,
            "question": self.question,
            "options": list(self.options),
            "chosen": self.chosen,
        }
        if self.rationale:
            rec["rationale"] = self.rationale
        if self.binds:
            rec["binds"] = list(self.binds)
        if self.supersedes:
            rec["supersedes"] = self.supersedes
        return rec


def read_ledger(rag: dict) -> list[dict]:
    ledger = rag.get(LEDGER_KEY)
    return ledger if isinstance(ledger, list) else []


def next_decision_id(rag: dict) -> str:
    highest = 0
    for rec in read_ledger(rag):
        m = _ID_RE.match(str(rec.get("id", "")))
        if m:
            highest = max(highest, int(m.group(1)))
    return f"DEC-{highest + 1:04d}"


def _tracked_ids(rag: dict) -> set[str]:
    items = rag.get("tracked_items")
    if not isinstance(items, list):
        return set()
    return {i.get("id") for i in items if isinstance(i, dict) and i.get("id")}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record_decision(
    rag: dict,
    *,
    session: str,
    question: str,
    options: Iterable[str],
    chosen: str,
    rationale: str = "",
    binds: Optional[Iterable[str]] = None,
    supersedes: Optional[str] = None,
    now: Optional[str] = None,
) -> Decision:
    """Append a governed operator ruling. Mutates ``rag``; caller writes atomically.

    Refuses, fail-loud, when:
      * fewer than two distinct options were on the table (not a decision);
      * the chosen value is not one of them (a ruling nobody was offered);
      * a bound tracked_item id does not resolve;
      * ``supersedes`` names a decision that is not in the ledger.
    """
    question = (question or "").strip()
    chosen = (chosen or "").strip()
    if not question:
        raise DecisionError("a decision needs the question it answered")
    if not chosen:
        raise DecisionError("a decision needs the option that was chosen")

    opts: list[str] = []
    for raw in options or []:
        text = str(raw).strip()
        if text and text not in opts:
            opts.append(text)
    if len(opts) < 2:
        raise DecisionError(
            "a decision requires at least TWO distinct alternatives — a single "
            "option is a mandatory control being offered as a preference (E-092), "
            "which is banked, not asked"
        )
    if chosen not in opts:
        raise DecisionError(
            f"chosen value {chosen!r} is not among the options offered "
            f"({', '.join(opts)}) — a ruling nobody was offered is not a ruling"
        )

    bind_list = [str(b).strip() for b in (binds or []) if str(b).strip()]
    if bind_list:
        known = _tracked_ids(rag)
        unknown = [b for b in bind_list if b not in known]
        if unknown:
            raise DecisionError(
                "decision binds unknown tracked_item id(s): " + ", ".join(unknown)
            )

    if supersedes:
        if supersedes not in {str(r.get("id")) for r in read_ledger(rag)}:
            raise DecisionError(
                f"supersedes names a decision not in the ledger: {supersedes}"
            )

    decision = Decision(
        id=next_decision_id(rag),
        session=session,
        utc=now or _now(),
        question=question,
        options=opts,
        chosen=chosen,
        rationale=(rationale or "").strip(),
        binds=bind_list,
        supersedes=supersedes,
    )
    rag.setdefault(LEDGER_KEY, []).append(decision.to_record())
    return decision


def decisions_for(rag: dict, item_id: str) -> list[dict]:
    """Every ruling that binds ``item_id``, oldest first."""
    return [r for r in read_ledger(rag) if item_id in (r.get("binds") or [])]


def superseded_ids(rag: dict) -> set[str]:
    return {
        str(r["supersedes"]) for r in read_ledger(rag) if r.get("supersedes")
    }


def live_decisions(rag: dict) -> list[dict]:
    """Rulings that have not been superseded by a later one."""
    dead = superseded_ids(rag)
    return [r for r in read_ledger(rag) if str(r.get("id")) not in dead]


def audit_ledger(rag: dict) -> list[str]:
    """Return a list of findings; empty means clean. Wired into the RAG audit."""
    findings: list[str] = []
    known = _tracked_ids(rag)
    seen: set[str] = set()
    ids = {str(r.get("id")) for r in read_ledger(rag)}
    for rec in read_ledger(rag):
        rid = str(rec.get("id", ""))
        if not _ID_RE.match(rid):
            findings.append(f"decision {rid!r}: malformed id (expected DEC-NNNN)")
        if rid in seen:
            findings.append(f"decision {rid!r}: duplicate id")
        seen.add(rid)
        opts = rec.get("options") or []
        if len(opts) < 2:
            findings.append(f"decision {rid}: fewer than two alternatives recorded")
        if rec.get("chosen") not in opts:
            findings.append(f"decision {rid}: chosen value is not among its options")
        for bound in rec.get("binds") or []:
            if bound not in known:
                findings.append(
                    f"decision {rid}: binds unknown tracked_item {bound!r}"
                )
        sup = rec.get("supersedes")
        if sup and str(sup) not in ids:
            findings.append(f"decision {rid}: supersedes unknown decision {sup!r}")
        if sup and str(sup) == rid:
            findings.append(f"decision {rid}: supersedes itself")
    return findings


def render_ledger(rag: dict, *, limit: int = 0, live_only: bool = False) -> str:
    """Bounded, deterministic render (Rule 17)."""
    records = live_decisions(rag) if live_only else read_ledger(rag)
    lines = [
        f"decision ledger — {len(records)} "
        f"{'live' if live_only else 'total'} ruling(s)"
    ]
    shown = records if limit <= 0 else records[-limit:]
    for rec in shown:
        lines.append("")
        lines.append(f"  {rec.get('id')} [{rec.get('session')}] {rec.get('utc')}")
        lines.append(f"    Q: {str(rec.get('question'))[:160]}")
        for opt in rec.get("options") or []:
            mark = ">" if opt == rec.get("chosen") else " "
            lines.append(f"     {mark} {opt[:120]}")
        if rec.get("rationale"):
            lines.append(f"    why: {str(rec['rationale'])[:200]}")
        if rec.get("binds"):
            lines.append(f"    binds: {', '.join(rec['binds'])}")
        if rec.get("supersedes"):
            lines.append(f"    supersedes: {rec['supersedes']}")
    if len(records) > len(shown):
        lines.append(f"\n  … {len(records) - len(shown)} earlier (raise --limit)")
    return "\n".join(lines)
