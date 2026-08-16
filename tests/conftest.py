"""Test-wide isolation of host state the suite must never write.

HEARTBEAT-FORGERY (S200) — the defect this file closes, measured, not supposed:

`drift_audit.check_hook_layer_live` exists to answer the one question a selftest
cannot — has a hook gate ever ACTUALLY run against a real tool call? It answers
by reading `~/.rag_kernel_hooks/hook_heartbeat.json`.

`tests/test_hook_enforcement_layer.py` called
`hook_entry.main(["--gate", "canonical-read"])` with no state-dir override. That
path resolves to the operator's REAL home directory, so every `pytest` run
stamped the production heartbeat. The audit then read it and reported the hook
layer live — in a client that has never executed a single hook in its life.

S200 caught it with the heartbeat still on disk: gate `canonical-read`, matching
the test payload exactly. Consequence: for eight sessions the suite manufactured
the evidence the audit consumed, "2,802 green" proved nothing about enforcement,
and the agent-conduct gates refused nothing while reporting full coverage.

The rule this file makes structural: A TEST MAY NEVER WRITE A FACT THE AUDIT
READS. Isolation is autouse and session-scoped because an opt-in fixture is a
thing a future test can forget, and forgetting is what happened.
"""

from __future__ import annotations

import os
import tempfile

import pytest


#: Every host-state escape hatch the kernel honours. A new one belongs here the
#: same day it is added; that is cheaper than the session that finds it missing.
_ISOLATED_ENV = ("RAG_HOOK_STATE_DIR",)


@pytest.fixture(scope="session", autouse=True)
def _isolate_host_state():
    """Point all hook state at a throwaway dir for the whole test session.

    Session-scoped rather than per-test: hook state is a cross-call window (the
    poll gate is stateful by design), so per-test teardown would also erase the
    behaviour some tests exist to exercise. Tests that need their own window
    still pass an explicit `state_dir=tmp_path`.
    """
    with tempfile.TemporaryDirectory(prefix="rag_kernel_test_hooks_") as td:
        saved = {k: os.environ.get(k) for k in _ISOLATED_ENV}
        for key in _ISOLATED_ENV:
            os.environ[key] = td
        try:
            yield td
        finally:
            for key, val in saved.items():
                if val is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = val
