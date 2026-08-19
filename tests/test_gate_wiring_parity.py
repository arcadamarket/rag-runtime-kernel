"""GATE-WIRING PARITY — a declared gate that no configuration invokes is not a gate.

GATES-DECLARED-BUT-NOT-WIRED-S206, measured: `hook_guard.GATES` listed 9 gates while
`.claude/settings.json` invoked 7. The two missing ones -- `tmux-heredoc` and
`stop-status` -- were written, unit-tested, deployed AND version-bumped in the same
session that forgot to wire them, and the `Stop` event had no entry at all. A gate
reachable from no configuration is indistinguishable from a gate never written, and
the HOOK_GUARD_VERSION bump made it worse by recording a policy change that could
not take effect.

WHY NOTHING CAUGHT IT, which is the part worth keeping: `hook-guard --selftest`
drives `decide()` directly. Every gate therefore passes selftest whether or not any
configuration reaches it -- the suite proved the gate's LOGIC and never asked whether
the gate was CONNECTED. Nothing compared the declaration against the wiring.

This test is that comparison, in both directions:

  * every name in GATES is invoked by some hook entry  -- catches a gate built and
    forgotten, the S206 defect exactly;
  * every `--gate X` in the wiring names a real gate   -- catches a typo, or wiring
    left pointing at a gate that was later renamed or removed, which fails OPEN and
    silently: the hook errors or no-ops and the boundary stops being guarded.

Applying the standard this project judges work by: could a tired agent add a tenth
gate, unit-test it, deploy it, and ship without wiring it, and have nothing refuse?
Before this test, yes -- that is the measured history. Now the suite goes red, and
since a green suite is a precondition of the seal, the close refuses. The refusal is
the deliverable; wiring the gate is merely the fix.

SCOPE, declared rather than hidden: this asserts that the wiring NAMES the gate. It
does not prove the hook executes -- the client must actually read settings.json, and
S197-S200 measured a client that did not. Execution is proven by the live-refusal
check, not here. Naming is necessary, not sufficient, and this test claims only the
necessary half.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

#: `... hook_entry.py --gate <name>` as it appears in a hook command string.
GATE_FLAG = re.compile(r"--gate\s+([A-Za-z0-9][A-Za-z0-9._-]*)")


def _settings_path() -> Path | None:
    """Locate the DEPLOYED .claude/settings.json by walking up from this file.

    It lives at the project root, beside RAG/ and the worktree -- not inside the
    worktree -- so a relative guess would break the moment the tree is re-laid out.
    Returns None in a bare clone, which has no deployment to check.
    """
    for parent in Path(__file__).resolve().parents:
        cand = parent / ".claude" / "settings.json"
        if cand.is_file():
            return cand
    return None


def _wired_gates(settings: Path) -> set[str]:
    data = json.loads(settings.read_text(encoding="utf-8-sig"))
    found: set[str] = set()
    for entries in (data.get("hooks") or {}).values():
        for entry in entries or ():
            for hook in entry.get("hooks") or ():
                command = hook.get("command") or ""
                found.update(GATE_FLAG.findall(command))
    return found


def _declared_gates() -> set[str]:
    from rag_kernel import hook_guard

    return set(hook_guard.GATES)


def test_every_declared_gate_is_wired() -> None:
    settings = _settings_path()
    if settings is None:
        pytest.skip("no deployed .claude/settings.json (bare clone): nothing to check")
    missing = sorted(_declared_gates() - _wired_gates(settings))
    assert not missing, (
        f"gate(s) declared in hook_guard.GATES but invoked by NO hook entry in "
        f"{settings}: {missing}. A gate no configuration reaches never runs, and "
        f"selftest cannot tell the difference because it calls decide() directly. "
        f"Wire it, then verify with a live refusal -- not with a passing test."
    )


def test_every_wired_gate_is_declared() -> None:
    settings = _settings_path()
    if settings is None:
        pytest.skip("no deployed .claude/settings.json (bare clone): nothing to check")
    unknown = sorted(_wired_gates(settings) - _declared_gates())
    assert not unknown, (
        f"hook wiring in {settings} invokes --gate name(s) that hook_guard.GATES "
        f"does not declare: {unknown}. This fails OPEN: the boundary looks guarded "
        f"in the config and is not guarded in fact. Fix the name, or drop the entry."
    )


def test_wiring_is_not_empty() -> None:
    """A settings file that wires NOTHING must not read as parity.

    Both assertions above are vacuously satisfiable by an empty `hooks` block on one
    side and an empty GATES on the other. Absence is a refusal, never a skip.
    """
    settings = _settings_path()
    if settings is None:
        pytest.skip("no deployed .claude/settings.json (bare clone): nothing to check")
    assert _declared_gates(), "hook_guard.GATES is empty -- nothing declares a gate"
    assert _wired_gates(settings), (
        f"{settings} invokes no --gate at all: the hook layer is inert. This is the "
        f"state S197-S200 measured and mistook for coverage."
    )
