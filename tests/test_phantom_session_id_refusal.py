"""PHANTOM-SESSION-ID-S1786488555313 (S198): AUTO-SID-DERIVE refuses bad shapes.

The defect was not that a millisecond timestamp got into
``meta.written_by_session`` — that will happen again from some fallback
somewhere. The defect was that ``_derive_next_sid`` INCREMENTED it. ``\\d+$``
matched, so ``S1786488555313`` became ``S1786488555314`` and the phantom
acquired the appearance of a governed id: it was stamped into a session log and
three WAL entries before the close audit refused.

Refusing costs an operator one explicit argument. Deriving costs a successor a
forensic session.

SCOPE NOTE, because it deviates from the ledger item on purpose: the item asked
for the literal ``S<n>`` shape. Enforcing that would have broken
``test_auto_sid_derive.py::test_derive_preserves_zero_pad_and_prefix``, which
pins ``SESS0099 -> SESS0100`` because clone deployments number sessions with
their own prefix. The prefix was never the defect. The guard therefore requires
an alphabetic prefix and BOUNDS THE COUNTER at nine digits, which is what
actually excludes the epoch fallbacks.
"""

from __future__ import annotations

import json

import pytest

from rag_kernel.__main__ import _derive_next_sid, _next_session_id


def _rag(tmp_path, written_by):
    path = tmp_path / "RAG_MASTER.json"
    meta = {} if written_by is None else {"written_by_session": written_by}
    path.write_text(json.dumps({"meta": meta}), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# the refusal
# --------------------------------------------------------------------------

@pytest.mark.parametrize("malformed", [
    "S1786488555313",   # the actual phantom: a millisecond epoch timestamp
    "1786488555313",    # same, without the S
    "S1786488555",      # a SECOND-precision epoch, the other fallback shape
    "S197-next",        # the fallback shape _next_session_id emits on no-digits
    "S197a",
    "S 197",
    "S-197",
    "S197.1",
    "",
    "   ",
])
def test_derive_refuses_malformed_written_by_session(tmp_path, malformed):
    assert _derive_next_sid(_rag(tmp_path, malformed)) is None


def test_derive_refuses_the_exact_id_that_blocked_the_s197_seal(tmp_path):
    """Named explicitly so a future rewrite cannot lose the regression."""
    assert _derive_next_sid(_rag(tmp_path, "S1786488555313")) is None


def test_the_old_behaviour_would_have_derived_a_phantom_successor():
    """Proof the guard is load-bearing: the increment rule alone happily obliges."""
    assert _next_session_id("S1786488555313") == "S1786488555314"


# --------------------------------------------------------------------------
# ...without breaking the normal path
# --------------------------------------------------------------------------

@pytest.mark.parametrize("written_by,expected", [
    ("S1", "S2"),
    ("S9", "S10"),
    ("S197", "S198"),
    ("S099", "S100"),      # zero-padding is preserved by the increment rule
    ("S1000000", "S1000001"),   # headroom: the bound is not a growth ceiling
    ("SESS0099", "SESS0100"),   # clones number sessions with their own prefix
])
def test_derive_accepts_canonical_ids(tmp_path, written_by, expected):
    assert _derive_next_sid(_rag(tmp_path, written_by)) == expected


def test_derive_still_returns_none_on_missing_key(tmp_path):
    assert _derive_next_sid(_rag(tmp_path, None)) is None


def test_derive_still_returns_none_on_unreadable_rag(tmp_path):
    missing = tmp_path / "nope.json"
    assert _derive_next_sid(missing) is None

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert _derive_next_sid(broken) is None


def test_derived_id_is_itself_canonical(tmp_path):
    """A guard that accepts an id but emits a non-canonical one is no guard."""
    from rag_kernel.__main__ import _SESSION_ID_RE

    derived = _derive_next_sid(_rag(tmp_path, "S197"))
    assert derived is not None
    assert _SESSION_ID_RE.match(derived)
