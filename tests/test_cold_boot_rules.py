"""COLD-BOOT-HAS-NO-RULES-S205 — a REFUSED boot must still govern the agent.

MEASURED S205 by reading the successor's transcript after its boot was refused:
with no session open it had NO operating_protocol, and it broke three rules it
had never been given — host-scratchpad working directory, polling (13 rag_wait
against 4 get-command-result), and a git-worktree hunt by guesswork. The
``pov_roles`` frame already rendered before the gate (the S176 fix); the RULES
did not, because they render at step 3b, behind the refusal.

These tests pin the property that fix depends on, which is NOT "the text is
pretty" but:

  * every boot-critical rule PRESENT in the RAG is emitted in substance, not
    named — a summary is what the refused agent effectively already had;
  * the render says plainly that no session is open and no governed write may
    be made, so the block can never be mistaken for a successful boot;
  * an absent boot-critical key is a LOUD gap, never a silent omission;
  * the render reaches the agent on the REAL refusal path, not just when called
    directly — and on the boot-audit refusal too, because fixing one exit and
    leaving the other is the same hole with a smaller mouth;
  * the boot-critical list has exactly one definition in the project.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from rag_kernel.__main__ import (
    BOOT_CRITICAL_RULES,
    _render_cold_boot_rules,
    cmd_session_start,
)
from rag_kernel import __main__ as kmain


BANNER = "[COLD-BOOT-RULES] NO SESSION IS OPEN"


def _rag_with_rules(**overrides) -> dict:
    """Minimal RAG carrying every boot-critical rule with a distinguishable body."""
    op = {k: f"BODY-OF-{k} and then some further prose." for k in BOOT_CRITICAL_RULES}
    op.update(overrides.pop("operating_protocol", {}))
    rag = {
        "operating_protocol": op,
        "meta": {"written_by_session": "S1"},
        "pov_roles": ["Role A", "Role B"],
        "pov_mandate": {"count": 2, "mode": "strict"},
    }
    rag.update(overrides)
    return rag


# ---------------------------------------------------------------------------
# the render itself
# ---------------------------------------------------------------------------

def test_every_boot_critical_rule_is_emitted_in_substance():
    out = _render_cold_boot_rules(_rag_with_rules(), "S9", reason="gate failed")
    for key in BOOT_CRITICAL_RULES:
        assert f"- {key}: BODY-OF-{key}" in out, f"{key} named but not stated"


def test_render_states_no_session_and_forbids_governed_writes():
    out = _render_cold_boot_rules(_rag_with_rules(), "S9", reason="gate failed")
    assert BANNER in out
    assert "S9 was NOT started" in out
    assert "make NO governed canonical write" in out
    # The refusal must not become a licence for the E-071-class workaround.
    assert "do NOT read RAG_MASTER.json directly" in out
    assert "session-resume" in out


def test_reason_is_carried_into_the_render():
    out = _render_cold_boot_rules(
        _rag_with_rules(), "S9", reason="carry-forward gate FAILED — synthetic finding"
    )
    assert "synthetic finding" in out


def test_missing_boot_critical_key_is_a_loud_gap_not_a_silent_omission():
    rag = _rag_with_rules()
    del rag["operating_protocol"]["no_polling"]
    out = _render_cold_boot_rules(rag, "S9", reason="gate failed")
    assert "RENDER GAP" in out
    assert "no_polling" in out
    assert "Do NOT read their absence here as their absence in policy." in out


def test_empty_rag_renders_the_banner_and_all_keys_as_gaps():
    """The RAG-unreadable exit still has to tell the agent it is ungoverned."""
    out = _render_cold_boot_rules({}, "S9", reason="RAG unreadable")
    assert BANNER in out
    assert "RENDER GAP" in out
    for key in BOOT_CRITICAL_RULES:
        assert key in out


def test_dict_valued_rule_is_flattened_not_dropped():
    """tool_hierarchy is a dict of sub-rules; a str-only render would emit nothing."""
    rag = _rag_with_rules()
    rag["operating_protocol"]["tool_hierarchy"] = {
        "file_read_write_list": "File tools first",
        "pytest_and_testing": "tmux-mcp primary",
    }
    out = _render_cold_boot_rules(rag, "S9", reason="gate failed")
    assert "file_read_write_list: File tools first" in out
    assert "pytest_and_testing: tmux-mcp primary" in out
    assert "RENDER GAP" not in out


def test_long_rule_is_truncated_and_says_so():
    rag = _rag_with_rules()
    rag["operating_protocol"]["strict_obey"] = "word " * 2000
    out = _render_cold_boot_rules(rag, "S9", reason="gate failed")
    assert "…[truncated — full text renders on a SUCCESSFUL boot]" in out


# ---------------------------------------------------------------------------
# the REAL refusal paths
# ---------------------------------------------------------------------------

def _boot_args(rag_path: Path) -> argparse.Namespace:
    return argparse.Namespace(
        rag=rag_path, session_id="S9", gc_path=None, no_boot_audit=True,
        strict=False, git_head=None, no_gc=True, force=False, attest=None,
        no_attest_gate=False, no_auto_reconcile=True,
    )


@pytest.fixture()
def rag_dir(tmp_path: Path) -> Path:
    d = tmp_path / "RAG"
    d.mkdir()
    (d / "RAG_MASTER.json").write_text(
        json.dumps(_rag_with_rules()), encoding="utf-8"
    )
    return d


def test_refused_carry_forward_gate_still_renders_the_rules(
    rag_dir: Path, monkeypatch, capsys
):
    monkeypatch.setattr(
        kmain, "_carry_forward_gate",
        lambda *a, **k: (False, ["synthetic asserted finding"]),
    )
    rc = cmd_session_start(_boot_args(rag_dir / "RAG_MASTER.json"))
    out = capsys.readouterr().out
    assert rc == 1, "the gate must still REFUSE — rendering rules is not passing"
    assert BANNER in out
    assert "synthetic asserted finding" in out
    assert "BODY-OF-no_polling" in out
    # and it must NOT look like a successful boot. Asserted against the exact
    # success markers, not the bare word READY — the banner legitimately contains
    # it while TELLING the agent that READY has not printed.
    assert "[4/4] Attestation REQUIRED" not in out
    assert "--attest" not in out
    assert "Session S9 READY" not in out


def test_refused_boot_audit_axis1_also_renders_the_rules(
    rag_dir: Path, monkeypatch, capsys
):
    monkeypatch.setattr(kmain, "_carry_forward_gate", lambda *a, **k: (True, []))
    monkeypatch.setattr(
        kmain, "_boot_axis1_audit", lambda *a, **k: ("FAIL", ["tmux transport dead"])
    )
    args = _boot_args(rag_dir / "RAG_MASTER.json")
    args.no_boot_audit = False
    rc = cmd_session_start(args)
    out = capsys.readouterr().out
    assert rc == 1
    assert BANNER in out
    assert "tmux transport dead" in out
    assert "BODY-OF-scratch_storage" in out


def test_refused_boot_banks_the_rules_in_the_boot_log(rag_dir: Path, monkeypatch):
    """A transport that truncates the live emission must still leave them on disk."""
    monkeypatch.setattr(
        kmain, "_carry_forward_gate", lambda *a, **k: (False, ["synthetic finding"])
    )
    cmd_session_start(_boot_args(rag_dir / "RAG_MASTER.json"))
    log = rag_dir / ".boot" / "session_start_S9.log"
    assert log.is_file()
    assert BANNER in log.read_text(encoding="utf-8")


def test_successful_boot_does_not_emit_the_cold_block(rag_dir: Path, monkeypatch, capsys):
    monkeypatch.setattr(kmain, "_carry_forward_gate", lambda *a, **k: (True, []))
    rc = cmd_session_start(_boot_args(rag_dir / "RAG_MASTER.json"))
    out = capsys.readouterr().out
    assert rc == 0
    assert BANNER not in out
    assert "--attest" in out


# ---------------------------------------------------------------------------
# one definition, not two
# ---------------------------------------------------------------------------

def test_scratch_storage_and_no_polling_are_boot_critical():
    """The two rules the host system prompt actively contradicts."""
    assert "scratch_storage" in BOOT_CRITICAL_RULES
    assert "no_polling" in BOOT_CRITICAL_RULES


def test_claude_md_renderer_imports_the_kernels_list_and_keeps_no_copy():
    """POLICY-LIVES-IN-CODE-NOT-IN-THE-RAG-S204 in miniature: two copies of
    'which rules an agent cannot boot without' would drift from the day the
    second one was written."""
    src = (
        Path(__file__).resolve().parent.parent / "scripts" / "render_claude_md.py"
    ).read_text(encoding="utf-8")
    assert "from rag_kernel.__main__ import BOOT_CRITICAL_RULES" in src
    assert "BOOT_CRITICAL = (" not in src, "the renderer re-grew its own copy"
