"""RUNBOOK-TABLE-NO-INVARIANT (S187) — measured tables that cannot go stale silently.

@rag-kernel-manifest
{
  "module": "rag_kernel.measured",
  "capability": "measured_doc_provenance",
  "description": "Machine invariant for measured tables inside documents: a MEASURED provenance stamp records the runtime/spec the numbers were measured against, and the auditor fails loud when the live runtime has moved past it — replacing the hand-run 're-measure before you trust this document' instruction that three consecutive runbook revisions ignored",
  "exports": ["MEASURED_RE", "Measurement", "scan_measurements",
              "stale_measurements", "format_stamp", "MEASURED_DOC_VERSION"]
}

THE DEFECT
----------
``RUNBOOK_CLONE_INIT_S179.md`` carries a table of MEASURED facts — how many keys
``init`` seeds, how many adopt, how many are blocked. Those numbers are true when
written and false the moment the spec or the rules move. The document knows this:
its §0.4 is titled "RE-MEASURE BEFORE YOU TRUST THIS DOCUMENT". That instruction is
prose, and prose is not a gate:

  * rev-2 shipped stale inside its own session (written before S179 authored the 8
    universal rules, never re-revised) — banked as E-091;
  * rev-3 went stale too: S181–S182 amended the rules and added ``py_script_mandate``,
    moving all four counts (``adopt 21→29, identical 8→0, untouched 22→23``);
  * rev-4 declares "Source of authority: runtime **v0.4.49**, spec **v3.2.8**" and
    a preflight row expecting ``0.4.49 3.2.8``. The live runtime is **0.4.53**.
    Four consecutive revisions, four staleness events, zero detections.

THE INVARIANT
-------------
Re-deriving every measured NUMBER would mean running ``init`` into a temp dir on
every audit — expensive, environment-dependent, and itself a thing that can rot.
So this does not re-measure. It checks PROVENANCE, which is decidable in constant
time and catches every one of the four historical failures:

    a measured table is TRUSTWORTHY only while the runtime and spec it was
    measured against are still the live ones.

A document declares that with one stamp::

    <!-- MEASURED: session=S183 runtime=0.4.49 spec=3.2.8 -->

When the live runtime/spec moves past the stamp, the numbers below it are UNVERIFIED
— not necessarily wrong, but no longer evidenced — and the auditor says so. The
answer is to re-measure and re-stamp, which is exactly what §0.4 asked a human to
remember and no human did.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

#: Bump when the stamp grammar or the staleness rule changes.
MEASURED_DOC_VERSION = "1.0.0"

#: The provenance stamp. Deliberately a comment, so it renders as nothing in every
#: Markdown viewer while remaining a first-class machine fact. Fields are order-free
#: and each is optional EXCEPT that a stamp with no version fields at all cannot go
#: stale and is therefore reported as unusable rather than silently accepted.
MEASURED_RE = re.compile(
    r"<!--\s*MEASURED:\s*(?P<body>[^>]*?)\s*-->",
    re.IGNORECASE,
)
_FIELD_RE = re.compile(r"(?P<key>[a-z_]+)\s*=\s*(?P<value>[^\s]+)", re.IGNORECASE)


def _semver_tuple(value: str) -> "tuple[int, ...]":
    """Parse ``0.4.49`` / ``v0.4.49`` to a comparable tuple; ``()`` if unparseable."""
    m = re.match(r"v?(\d+(?:\.\d+)*)", str(value).strip())
    if not m:
        return ()
    return tuple(int(p) for p in m.group(1).split("."))


@dataclass(frozen=True)
class Measurement:
    """One provenance stamp found in a document."""

    path: str
    line: int
    session: str = ""
    runtime: str = ""
    spec: str = ""

    @property
    def has_version_anchor(self) -> bool:
        """True iff the stamp anchors to something that can actually move."""
        return bool(self.runtime or self.spec)

    def staleness(self, *, live_runtime: str = "", live_spec: str = "") -> list[str]:
        """Return one reason per anchor that the live world has moved past.

        Only a STRICTLY NEWER live version is staleness. An older live runtime means
        the reader is behind the document, which is a deployment question, not a
        document defect — and flagging it would make every clone's audit red.
        """
        reasons: list[str] = []
        if self.runtime and live_runtime:
            stamped, live = _semver_tuple(self.runtime), _semver_tuple(live_runtime)
            if stamped and live and live > stamped:
                reasons.append(
                    f"measured against runtime {self.runtime}, live runtime is {live_runtime}"
                )
        if self.spec and live_spec:
            stamped, live = _semver_tuple(self.spec), _semver_tuple(live_spec)
            if stamped and live and live > stamped:
                reasons.append(
                    f"measured against spec {self.spec}, live spec is {live_spec}"
                )
        return reasons


def scan_measurements(text: str, *, path: str = "") -> list[Measurement]:
    """Extract every ``MEASURED:`` stamp from a document, in line order."""
    out: list[Measurement] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        m = MEASURED_RE.search(raw)
        if not m:
            continue
        fields = {
            f.group("key").lower(): f.group("value")
            for f in _FIELD_RE.finditer(m.group("body"))
        }
        out.append(Measurement(
            path=path,
            line=lineno,
            session=fields.get("session", ""),
            runtime=fields.get("runtime", ""),
            spec=fields.get("spec", ""),
        ))
    return out


def stale_measurements(
    docs: Iterable[Path | str],
    *,
    live_runtime: str = "",
    live_spec: str = "",
) -> "list[tuple[Measurement, list[str]]]":
    """Return ``(measurement, reasons)`` for every stamp the live world has outrun.

    Unreadable files are skipped rather than raising: a document that cannot be read
    is the file system's problem, not a staleness claim.
    """
    findings: "list[tuple[Measurement, list[str]]]" = []
    for doc in docs:
        p = Path(doc)
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for meas in scan_measurements(text, path=str(p)):
            if not meas.has_version_anchor:
                findings.append((meas, ["stamp anchors to no runtime/spec version — "
                                        "it can never be detected as stale"]))
                continue
            reasons = meas.staleness(live_runtime=live_runtime, live_spec=live_spec)
            if reasons:
                findings.append((meas, reasons))
    return findings


def format_stamp(*, session: str, runtime: str, spec: str = "") -> str:
    """Render the stamp a re-measuring session should paste into its document."""
    parts = [f"session={session}", f"runtime={runtime}"]
    if spec:
        parts.append(f"spec={spec}")
    return "<!-- MEASURED: " + " ".join(parts) + " -->"
