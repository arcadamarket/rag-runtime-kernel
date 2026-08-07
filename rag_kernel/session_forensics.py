"""@rag-kernel-manifest
{
  "module": "rag_kernel.session_forensics",
  "capability": "session_conduct_forensics",
  "description": "Renders a session's CONDUCT from its own log — wall time, governed-invocation count, failed verbs, silent gaps, repeat-call bursts, and double-seal detection — so a root-cause report about the session is sourced rather than remembered (SELF-DIAGNOSIS-UNSOURCED, AGENT-POLL-DISCIPLINE).",
  "exports": ["ForensicsError", "SessionForensics", "analyze_log",
              "analyze_file", "render_text", "GAP_SECONDS", "BURST_SECONDS"]
}

Session-conduct forensics — the machine half of an honest self-report.

THE DEFECT
----------
S187 was asked, by an operator who was already angry, why the seal was taking so
long. It answered that six illegal ``OPEN -> RESOLVED`` transitions were "the real
delay and it's on me". The session log — which the agent was writing at the time —
says those six calls span **five seconds** inside a **four hour** session whose five
silent gaps total 195 minutes. The stated cause was wrong by roughly three orders of
magnitude, and it was wrong in the direction that sounds like accountability.

That is not a lie; it is a guess wearing the costume of a confession. Banked as
SELF-DIAGNOSIS-UNSOURCED, and it is the same family as E-084 (a verdict issued from
an incomplete lookup) turned on the agent's own conduct.

WHY A MODULE AND NOT A RULE
---------------------------
"Cite the log before you explain yourself" is a rule you can violate silently, i.e.
a hope (GATE-OR-HOPE-PRINCIPLE). You cannot mechanically detect a wrong
explanation in prose. What you CAN do is make the true numbers cost one command and
emit them at close, so the operator sees the session's actual shape next to whatever
the agent says about it. A narrative that contradicts the rendered facts stops being
plausible.

WHAT IT MEASURES
----------------
* **wall time** and **governed-invocation count** — the denominator every claim needs;
* **failed invocations**, grouped by verb, with their real elapsed cost;
* **silent gaps** over :data:`GAP_SECONDS` — where the time actually went, which in
  S187 was 81 percent of the session and in no way resembled the stated cause;
* **repeat bursts** — the same verb N+ times inside :data:`BURST_SECONDS`, the
  machine-visible shadow of polling (AGENT-POLL-DISCIPLINE / E-081 class);
* **double seals** — more than one ``session_end``, and whether state was mutated
  between them (the E-099 defect).

It measures conduct, never intent, and it reports "no signal" rather than guessing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from rag_kernel.drift_store import DriftStoreError

__all__ = [
    "ForensicsError",
    "SessionForensics",
    "analyze_log",
    "analyze_file",
    "render_text",
    "GAP_SECONDS",
    "BURST_SECONDS",
]


class ForensicsError(DriftStoreError):
    """Raised when a session log is absent or unparseable."""


#: A quiet stretch longer than this is reported as a gap. Ten minutes is well past
#: any single governed verb (the slowest, a full audit over a 433 KB RAG, is ~60s),
#: so a gap this long is un-governed time and belongs in the account.
GAP_SECONDS = 600

#: A verb repeated inside this window counts toward a burst. Polling is
#: characteristically tight — S180's E-093 was ten checks against one blocking wait.
BURST_SECONDS = 120

#: Repeats within BURST_SECONDS before it is reported.
BURST_MIN_REPEATS = 4

#: Verbs whose repetition is legitimate rather than suspicious: `wait-for` blocks
#: server-side (it IS the anti-poll primitive) and the state machine's two-step
#: transitions are supposed to come in pairs.
_BURST_EXEMPT = frozenset({"wait-for", "run", "start", "resolve"})


@dataclass
class SessionForensics:
    """Everything the log can say about how a session was conducted."""

    session_id: str = ""
    records: int = 0
    invocations: int = 0
    failures: list[dict] = field(default_factory=list)
    gaps: list[dict] = field(default_factory=list)
    bursts: list[dict] = field(default_factory=list)
    session_ends: list[dict] = field(default_factory=list)
    mutations_after_first_end: list[str] = field(default_factory=list)
    started: Optional[datetime] = None
    ended: Optional[datetime] = None

    @property
    def wall_seconds(self) -> float:
        if not (self.started and self.ended):
            return 0.0
        return (self.ended - self.started).total_seconds()

    @property
    def gap_seconds(self) -> float:
        return sum(g["seconds"] for g in self.gaps)

    @property
    def gap_share(self) -> float:
        """Fraction of the session spent in silent gaps. 0.0 when unknowable."""
        return (self.gap_seconds / self.wall_seconds) if self.wall_seconds else 0.0

    @property
    def failure_seconds(self) -> float:
        """Wall time actually spent inside failed invocations.

        The number S187 needed and did not look up. Retries feel expensive and are
        usually cheap; gaps feel like nothing and are usually where the time went.
        """
        return sum(f.get("seconds") or 0.0 for f in self.failures)

    @property
    def double_sealed(self) -> bool:
        return len(self.session_ends) > 1


def _parse_ts(raw: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


def _verb(rec: dict) -> str:
    data = rec.get("data") or {}
    msg = rec.get("msg") or ""
    cmd = data.get("command") or data.get("verb") or data.get("tool") or ""
    if isinstance(cmd, str) and cmd and cmd != "cli":
        return cmd
    # bootstrap logger records the verb in the message as "cli <verb>"
    if isinstance(msg, str) and msg.startswith("cli "):
        return msg[4:].strip()
    return str(cmd or msg or "?").strip()


def _rc(rec: dict) -> Optional[int]:
    """Exit code of a logged invocation, across the shapes the logger has used.

    The bootstrap logger writes ``status: "exit 1"`` (a rendered string), while other
    call sites write ``exit_code: 1``. Reading only the int form is how the first
    version of this module reported "failed calls: none" against the S187 log — a
    forensics tool that cannot see failures is worse than none, because it produces
    a confident wrong answer, which is the very defect it exists to catch.
    """
    data = rec.get("data") or {}
    for key in ("exit_code", "rc", "status", "result"):
        val = data.get(key)
        if isinstance(val, bool):
            continue
        if isinstance(val, int):
            return val
        if isinstance(val, str):
            token = val.strip().lower()
            if token.startswith("exit"):
                token = token[4:].strip()
            if token.lstrip("-").isdigit():
                return int(token)
    return None


def analyze_log(records: Iterable[dict]) -> SessionForensics:
    """Compute conduct facts from parsed log records. Pure; total; never raises."""
    rows = [r for r in records if isinstance(r, dict)]
    out = SessionForensics(records=len(rows))
    if not rows:
        return out

    out.session_id = str(rows[0].get("sid") or "")
    stamped = [(r, _parse_ts(str(r.get("ts") or ""))) for r in rows]
    stamped = [(r, t) for r, t in stamped if t is not None]
    if not stamped:
        return out
    out.started = stamped[0][1]
    out.ended = stamped[-1][1]

    prev: Optional[tuple[dict, datetime]] = None
    recent: list[tuple[str, datetime]] = []
    first_end_seq: Optional[int] = None

    for rec, ts in stamped:
        event = rec.get("event")
        if event == "session_end":
            out.session_ends.append({"seq": rec.get("seq"), "ts": rec.get("ts")})
            if first_end_seq is None:
                first_end_seq = rec.get("seq")
        elif event == "tool_invocation":
            out.invocations += 1
            verb = _verb(rec)
            rc = _rc(rec)
            secs = (rec.get("data") or {}).get("duration_ms")
            secs = (secs / 1000.0) if isinstance(secs, (int, float)) else None
            if rc not in (0, None):
                out.failures.append({
                    "seq": rec.get("seq"), "verb": verb,
                    "rc": rc, "ts": rec.get("ts"), "seconds": secs,
                })
            if first_end_seq is not None:
                out.mutations_after_first_end.append(verb)

            # burst detection over a sliding window
            recent = [(v, t) for v, t in recent
                      if (ts - t).total_seconds() <= BURST_SECONDS]
            recent.append((verb, ts))
            if verb not in _BURST_EXEMPT:
                same = [t for v, t in recent if v == verb]
                if len(same) >= BURST_MIN_REPEATS:
                    span = (same[-1] - same[0]).total_seconds()
                    if not out.bursts or out.bursts[-1]["verb"] != verb:
                        out.bursts.append({
                            "verb": verb, "count": len(same),
                            "seconds": round(span, 1), "ts": rec.get("ts"),
                        })
                    else:
                        out.bursts[-1]["count"] = len(same)
                        out.bursts[-1]["seconds"] = round(span, 1)

        if prev is not None:
            delta = (ts - prev[1]).total_seconds()
            if delta > GAP_SECONDS:
                out.gaps.append({
                    "from_seq": prev[0].get("seq"), "to_seq": rec.get("seq"),
                    "from_ts": prev[0].get("ts"), "to_ts": rec.get("ts"),
                    "seconds": delta,
                })
        prev = (rec, ts)

    return out


def analyze_file(path: Path | str) -> SessionForensics:
    """Analyze a ``session_log_<sid>.jsonl`` file. Fail loud when absent."""
    p = Path(path)
    if not p.exists():
        raise ForensicsError(f"session log not found: {p}")
    rows: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue          # a torn last line must not lose the whole session
    if not rows:
        raise ForensicsError(f"session log has no parseable records: {p}")
    return analyze_log(rows)


def _hms(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def render_text(f: SessionForensics) -> str:
    """Render the conduct facts as bounded, operator-readable lines."""
    L: list[str] = []
    ap = L.append
    ap(f"=== SESSION CONDUCT — {f.session_id or '(unknown)'} "
       f"(rendered from the log, not from memory) ===")
    ap(f"  wall time        : {_hms(f.wall_seconds)}")
    ap(f"  governed calls   : {f.invocations} ({f.records} log records)")

    if f.failures:
        by_verb: dict[str, list[dict]] = {}
        for x in f.failures:
            by_verb.setdefault(x["verb"], []).append(x)
        parts = ", ".join(f"{v} x{len(xs)}" for v, xs in sorted(by_verb.items()))
        ap(f"  failed calls     : {len(f.failures)} ({parts})")
        ap(f"                     total {_hms(f.failure_seconds)} of wall time"
           f" — retries are usually cheap; check the gaps before blaming them")
    else:
        ap("  failed calls     : none")

    if f.gaps:
        ap(f"  silent gaps      : {len(f.gaps)} over {GAP_SECONDS // 60}m, "
           f"totalling {_hms(f.gap_seconds)} ({f.gap_share:.0%} of the session)")
        for g in sorted(f.gaps, key=lambda x: -x["seconds"])[:5]:
            ap(f"      {_hms(g['seconds']):>7}  seq {g['from_seq']} -> {g['to_seq']}")
        ap("                     THIS is where the time went. Any account of the"
           " session that does not explain these is not an account.")
    else:
        ap("  silent gaps      : none")

    if f.bursts:
        ap(f"  repeat bursts    : {len(f.bursts)} — the machine-visible shadow of"
           f" polling (E-081 class)")
        for b in f.bursts[:5]:
            ap(f"      {b['verb']} x{b['count']} in {b['seconds']}s")
    else:
        ap("  repeat bursts    : none detected")

    if f.double_sealed:
        ap(f"  SEALS            : {len(f.session_ends)} session_end records "
           f"— DOUBLE SEAL (E-099)")
        if f.mutations_after_first_end:
            uniq = sorted(set(f.mutations_after_first_end))
            ap(f"      {len(f.mutations_after_first_end)} invocation(s) AFTER the "
               f"first seal: {', '.join(uniq)}")
            ap("      the first seal attested a state that then changed")
    elif f.session_ends:
        ap("  SEALS            : 1 (clean)")

    return "\n".join(L)
